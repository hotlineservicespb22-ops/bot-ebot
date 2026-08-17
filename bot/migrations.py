"""
Версионированная система миграций базы данных SQLite.

Миграции нумеруются и применяются по порядку. Применённые версии
отслеживаются в таблице ``schema_migrations``.

Версия 1 считается базовой (создаётся при инициализации схемы в Database.init_db).
Начиная с версии 2, изменения применяются точечно и идемпотентно
(проверка существования колонки перед ALTER), что безопасно для уже существующих БД.
"""
import datetime
import logging

logger = logging.getLogger(__name__)


async def _get_columns(conn, table: str) -> set:
    """Возвращает множество имён колонок таблицы."""
    cursor = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return {row['name'] for row in rows}


async def _ensure_column(conn, table: str, column: str, col_type: str) -> None:
    """Добавляет колонку, если её ещё нет (идемпотентно)."""
    columns = await _get_columns(conn, table)
    if column not in columns:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        logger.info("Миграция: добавлена колонка %s.%s (%s)", table, column, col_type)


async def _migration_v2_ticket_fields(conn) -> None:
    """
    v2: Дополнительные поля заявок, добавленные после первоначального релиза.
    """
    for col, col_type in (
        ("close_comment", "TEXT"),
        ("created_at", "TEXT"),
        ("closed_at", "TEXT"),
        ("machine_media_id", "TEXT"),
    ):
        await _ensure_column(conn, "tickets", col, col_type)
    # Индекс на created_at создаём после того, как колонка гарантированно существует
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_created_at ON tickets(created_at)")


async def _migration_v3_engineer_bitrix(conn) -> None:
    """
    v3: Соответствие инженеров бота пользователям Битрикс24.
    """
    await _ensure_column(conn, "engineers", "bitrix_user_id", "INTEGER")


async def _migration_v4_ticket_escalations(conn) -> None:
    """
    v4: Дедупликация уведомлений о просроченных заявках.

    Хранит факт уже отправленного уведомления администраторам о том,
    что заявка долго не была взята в работу. Предотвращает спам-повторы
    из фоновой задачи ticket_timeout_watcher.
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ticket_escalations (
            ticket_id INTEGER PRIMARY KEY,
            notified_at TEXT
        )
    """)


async def _migration_v5_ticket_engineer_index(conn) -> None:
    """
    v5: Индекс на engineer_id в таблице tickets.

    Ускоряет запросы has_active_tickets, get_active_tickets_for_engineer
    и get_engineer_stats при большом количестве заявок.
    """
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_engineer_id ON tickets(engineer_id)")


# Реестр миграций: version -> (название, функция)
MIGRATIONS = {
    2: ("ticket_extra_fields", _migration_v2_ticket_fields),
    3: ("engineer_bitrix_user_id", _migration_v3_engineer_bitrix),
    4: ("ticket_escalations", _migration_v4_ticket_escalations),
    5: ("ticket_engineer_index", _migration_v5_ticket_engineer_index),
}


async def ensure_schema_migrations_table(conn) -> None:
    """Создаёт таблицу учёта миграций (если её нет)."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT,
            applied_at TEXT
        )
    """)


async def mark_applied(conn, version: int, name: str) -> None:
    """Помечает миграцию как применённую."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    await conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
        (version, name, now),
    )


async def get_applied_versions(conn) -> set:
    """Возвращает множество применённых версий миграций."""
    cursor = await conn.execute("SELECT version FROM schema_migrations")
    rows = await cursor.fetchall()
    return {row['version'] for row in rows}


async def run_migrations(conn) -> list[int]:
    """
    Применяет все ещё не применённые миграции.

    Возвращает список только что применённых версий. Миграции идемпотентны,
    поэтому повторный запуск на уже готовой схеме безопасен.
    """
    await ensure_schema_migrations_table(conn)
    applied = await get_applied_versions(conn)
    applied_now = []

    for version in sorted(MIGRATIONS):
        if version in applied:
            continue
        name, migration_fn = MIGRATIONS[version]
        try:
            await migration_fn(conn)
        except Exception as e:
            logger.error("Ошибка применения миграции v%s (%s): %s", version, name, e)
            raise
        await mark_applied(conn, version, name)
        await conn.commit()
        applied_now.append(version)
        logger.info("Применена миграция v%s (%s)", version, name)

    if not applied_now:
        logger.info("Миграции не требуются — схема актуальна.")
    return applied_now