import asyncio
import contextlib
import datetime
import uuid as _uuid

import aiosqlite

from bot.migrations import mark_applied, run_migrations


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: aiosqlite.Connection | None = None
        self.lock = asyncio.Lock()

    async def connect(self):
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        # PRAGMA-оптимизации: внешние ключи, WAL для конкурентности, таймаут блокировки
        await self.conn.execute("PRAGMA foreign_keys=ON")
        await self.conn.execute("PRAGMA busy_timeout=5000")
        with contextlib.suppress(Exception):
            await self.conn.execute("PRAGMA journal_mode=WAL")  # Для in-memory БД WAL недоступен — игнорируем
        await self.conn.commit()

    async def close(self):
        if self.conn:
            await self.conn.close()

    async def init_db(self):
        async with self.lock:
            # Engineers table
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS engineers (
                    user_id INTEGER PRIMARY KEY,
                    name TEXT,
                    is_active INTEGER DEFAULT 1,
                    bitrix_user_id INTEGER
                )
            """)
            # Admins table (optional but good for /add_admin)
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY
                )
            """)
            # Tickets table
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uuid TEXT UNIQUE,
                    client_id INTEGER,
                    client_name TEXT,
                    company TEXT,
                    equipment_type TEXT,
                    brand TEXT,
                    cnc_model TEXT,
                    machine_info TEXT,
                    company_city TEXT,
                    problem TEXT,
                    media_id TEXT,
                    city TEXT,
                    inn_contract TEXT,
                    contact TEXT,
                    status TEXT DEFAULT 'open',
                    engineer_id INTEGER NULL
                )
            """)
            # Messages table (история переписки)
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER,
                    sender_id INTEGER,
                    sender_role TEXT,
                    text TEXT,
                    media_type TEXT,
                    created_at TEXT,
                    FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
                )
            """)
            # Ratings table (оценки клиентов)
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS ratings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER UNIQUE,
                    client_id INTEGER,
                    rating INTEGER,
                    comment TEXT,
                    created_at TEXT,
                    FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
                )
            """)
            # Media table (сохранённые фото/видео/документы заявки)
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER,
                    file_id TEXT,
                    file_type TEXT,
                    file_path TEXT,
                    sender_id INTEGER,
                    sender_role TEXT,
                    created_at TEXT,
                    FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
                )
            """)
            # Ticket notifications table (message_id уведомлений, отправленных инженерам)
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS ticket_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER,
                    engineer_id INTEGER,
                    message_id INTEGER,
                    created_at TEXT,
                    FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
                )
            """)
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_ticket_notifications_ticket ON ticket_notifications(ticket_id)")
            # Relay message map table (message_id отправленного сообщения -> ticket_id)
            # Используется для сопоставления ответа (reply) инженера с конкретной заявкой.
            await self.conn.execute("""
                CREATE TABLE IF NOT EXISTS relay_message_map (
                    message_id INTEGER PRIMARY KEY,
                    ticket_id INTEGER,
                    receiver_id INTEGER,
                    created_at TEXT,
                    FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
                )
            """)
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_relay_message_map_ticket ON relay_message_map(ticket_id)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_relay_message_map_receiver ON relay_message_map(receiver_id)")
            # Indexes
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_client ON tickets(client_id)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_engineer ON tickets(engineer_id)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_ticket ON messages(ticket_id)")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_media_ticket ON media(ticket_id)")

            # Базовая схема (версия 1) создана — помечаем её в реестре миграций.
            from bot.migrations import ensure_schema_migrations_table
            await ensure_schema_migrations_table(self.conn)
            await mark_applied(self.conn, 1, "base_schema")
            await self.conn.commit()

    async def migrate(self):
        async with self.lock:
            # Применяем версионированные (пост-релизные) миграции.
            await run_migrations(self.conn)

    # Engineer CRUD
    async def add_engineer(self, user_id: int, name: str):
        async with self.lock:
            # Используем ON CONFLICT DO UPDATE, чтобы НЕ сбрасывать bitrix_user_id
            # при повторном добавлении инженера.
            await self.conn.execute(
                """
                INSERT INTO engineers (user_id, name, is_active)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    name = excluded.name,
                    is_active = 1
                """,
                (user_id, name)
            )
            await self.conn.commit()

    async def delete_engineer(self, user_id: int):
        async with self.lock:
            await self.conn.execute("DELETE FROM engineers WHERE user_id = ?", (user_id,))
            await self.conn.commit()

    async def get_engineers(self) -> list[aiosqlite.Row]:
        async with self.lock:
            cursor = await self.conn.execute("SELECT * FROM engineers WHERE is_active = 1")
            return await cursor.fetchall()

    async def get_all_engineers(self) -> list[aiosqlite.Row]:
        """Возвращает всех инженеров (включая неактивных)."""
        async with self.lock:
            cursor = await self.conn.execute("SELECT * FROM engineers ORDER BY name")
            return await cursor.fetchall()

    async def set_engineer_active(self, user_id: int, is_active: int):
        """Включает/выключает инженера как дежурного (is_active: 1 или 0)."""
        async with self.lock:
            await self.conn.execute(
                "UPDATE engineers SET is_active = ? WHERE user_id = ?",
                (is_active, user_id)
            )
            await self.conn.commit()

    async def is_engineer(self, user_id: int) -> bool:
        async with self.lock:
            cursor = await self.conn.execute("SELECT 1 FROM engineers WHERE user_id = ? AND is_active = 1", (user_id,))
            return await cursor.fetchone() is not None

    async def has_active_tickets(self, user_id: int) -> bool:
        """Проверяет, есть ли у пользователя активные заявки (in_progress) как у инженера."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT 1 FROM tickets WHERE engineer_id = ? AND status = 'in_progress' LIMIT 1",
                (user_id,)
            )
            return await cursor.fetchone() is not None

    async def set_bitrix_user_id(self, user_id: int, bitrix_user_id: int):
        """Устанавливает соответствие инженера бота пользователю Битрикс24."""
        async with self.lock:
            await self.conn.execute(
                "UPDATE engineers SET bitrix_user_id = ? WHERE user_id = ?",
                (bitrix_user_id, user_id)
            )
            await self.conn.commit()

    async def get_bitrix_user_id(self, user_id: int) -> int | None:
        """Возвращает ID пользователя Битрикс24 для инженера (или None).

        Не фильтрует по is_active — бывший инженер должен иметь
        возможность работать с уже созданной задачей Битрикс24.
        """
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT bitrix_user_id FROM engineers WHERE user_id = ?",
                (user_id,)
            )
            row = await cursor.fetchone()
            return row['bitrix_user_id'] if row and row['bitrix_user_id'] is not None else None

    async def get_engineer_name(self, user_id: int) -> str | None:
        """Возвращает имя инженера по его Telegram ID (или None).

        Не фильтрует по is_active — бывший инженер с активными заявками
        должен отображаться под своим именем в чате с клиентом.
        """
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT name FROM engineers WHERE user_id = ?",
                (user_id,)
            )
            row = await cursor.fetchone()
            return row['name'] if row and row['name'] else None

    # Admin CRUD
    async def add_admin(self, user_id: int):
        async with self.lock:
            await self.conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
            await self.conn.commit()

    async def delete_admin(self, user_id: int):
        async with self.lock:
            await self.conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            await self.conn.commit()

    async def get_admins(self) -> list[aiosqlite.Row]:
        async with self.lock:
            cursor = await self.conn.execute("SELECT * FROM admins")
            return await cursor.fetchall()

    async def is_admin(self, user_id: int) -> bool:
        # Check both hardcoded and DB
        from bot.config import ADMIN_IDS
        if user_id in ADMIN_IDS:
            return True
        async with self.lock:
            cursor = await self.conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
            return await cursor.fetchone() is not None

    # Manager check (только через .env)
    async def is_manager(self, user_id: int) -> bool:
        """Проверяет, является ли пользователь руководителем. Только через MANAGER_IDS из .env."""
        from bot.config import MANAGER_IDS
        return user_id in MANAGER_IDS

    # Ticket CRUD
    async def create_ticket(
        self,
        client_id: int,
        client_name: str,
        company: str,
        equipment_type: str,
        brand: str,
        cnc_model: str,
        problem: str,
        media_id: str | None,
        city: str,
        inn_contract: str,
        contact: str,
        machine_info: str = '',
        company_city: str = '',
        machine_media_id: str | None = None
    ) -> int:
        async with self.lock:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            ticket_uuid = str(_uuid.uuid4())
            cursor = await self.conn.execute(
                """INSERT INTO tickets (
                    uuid, client_id, client_name, company, equipment_type, brand,
                    cnc_model, machine_info, company_city, problem, media_id, city, inn_contract, contact, machine_media_id, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ticket_uuid, client_id, client_name, company, equipment_type, brand,
                    cnc_model, machine_info, company_city, problem, media_id, city, inn_contract, contact, machine_media_id, now
                )
            )
            ticket_id = cursor.lastrowid
            await self.conn.commit()
            return ticket_id

    async def get_ticket(self, ticket_id: int) -> aiosqlite.Row | None:
        async with self.lock:
            cursor = await self.conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
            return await cursor.fetchone()

    async def get_ticket_by_uuid(self, ticket_uuid: str) -> aiosqlite.Row | None:
        """Возвращает заявку по UUID (для безопасного доступа извне)."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM tickets WHERE uuid = ?", (ticket_uuid,)
            )
            return await cursor.fetchone()

    async def take_ticket(self, ticket_id: int, engineer_id: int) -> bool:
        async with self.lock:
            cursor = await self.conn.execute(
                "UPDATE tickets SET status = 'in_progress', engineer_id = ? WHERE id = ? AND status = 'open'",
                (engineer_id, ticket_id)
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def close_ticket(self, ticket_id: int, status: str, comment: str = None) -> bool:
        """Закрывает заявку. Возвращает True, если заявка была закрыта (False — если уже закрыта)."""
        async with self.lock:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            # Проверяем, что заявка ещё не закрыта (защита от повторного закрытия)
            cursor = await self.conn.execute(
                "UPDATE tickets SET status = ?, close_comment = ?, closed_at = ? "
                "WHERE id = ? AND status IN ('open', 'in_progress')",
                (status, comment, now, ticket_id)
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def get_active_ticket_for_client(self, client_id: int) -> aiosqlite.Row | None:
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM tickets WHERE client_id = ? AND status IN ('open', 'in_progress') ORDER BY id DESC LIMIT 1",
                (client_id,)
            )
            return await cursor.fetchone()

    async def get_client_tickets(self, client_id: int, limit: int = 10, offset: int = 0) -> list[aiosqlite.Row]:
        """Возвращает историю заявок клиента (последние N, с пагинацией offset)."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM tickets WHERE client_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (client_id, limit, offset)
            )
            return await cursor.fetchall()

    async def get_active_tickets_for_engineer(self, engineer_id: int) -> list[aiosqlite.Row]:
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM tickets WHERE engineer_id = ? AND status = 'in_progress' ORDER BY id DESC",
                (engineer_id,)
            )
            return await cursor.fetchall()

    async def get_open_tickets(self) -> list[aiosqlite.Row]:
        """Возвращает нераспределенные заявки (статус 'open', без инженера)."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM tickets WHERE status = 'open' AND engineer_id IS NULL ORDER BY id DESC"
            )
            return await cursor.fetchall()

    async def get_all_tickets(self) -> list[aiosqlite.Row]:
        async with self.lock:
            cursor = await self.conn.execute("SELECT * FROM tickets")
            return await cursor.fetchall()

    async def get_ticket_status_counts(self) -> dict:
        """
        Возвращает количество заявок по статусам одним SQL-запросом,
        не загружая все строки в память (оптимизация для статистики).
        """
        async with self.lock:
            cursor = await self.conn.execute(
                """
                SELECT status, COUNT(*) as cnt
                FROM tickets
                GROUP BY status
                """
            )
            rows = await cursor.fetchall()
            counts = {row['status']: row['cnt'] for row in rows}
            return {
                'total': sum(counts.values()),
                'open': counts.get('open', 0),
                'in_progress': counts.get('in_progress', 0),
                'completed': counts.get('completed', 0),
                'canceled': counts.get('canceled', 0),
            }

    async def get_tickets_by_period(self, start_date: str, end_date: str) -> list[aiosqlite.Row]:
        """Возвращает заявки, созданные в заданном периоде (ISO-даты)."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM tickets WHERE created_at >= ? AND created_at <= ? ORDER BY id DESC",
                (start_date, end_date)
            )
            return await cursor.fetchall()

    async def get_expired_open_tickets(self, timeout_seconds: int) -> list[aiosqlite.Row]:
        """
        Возвращает заявки в статусе 'open', которые висят дольше timeout_seconds.
        """
        async with self.lock:
            cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=timeout_seconds)).isoformat()
            cursor = await self.conn.execute(
                "SELECT * FROM tickets WHERE status = 'open' AND engineer_id IS NULL AND created_at < ?",
                (cutoff,)
            )
            return await cursor.fetchall()

    async def get_expired_open_tickets_not_escalated(self, timeout_seconds: int) -> list[aiosqlite.Row]:
        """
        Возвращает просроченные заявки, по которым ещё НЕ отправлялось
        уведомление администраторам (дедупликация спама из фоновой задачи).
        """
        async with self.lock:
            cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=timeout_seconds)).isoformat()
            cursor = await self.conn.execute(
                """
                SELECT t.* FROM tickets t
                LEFT JOIN ticket_escalations e ON e.ticket_id = t.id
                WHERE t.status = 'open' AND t.engineer_id IS NULL
                  AND t.created_at < ? AND e.ticket_id IS NULL
                """,
                (cutoff,)
            )
            return await cursor.fetchall()

    async def mark_ticket_escalated(self, ticket_id: int) -> None:
        """Отмечает, что по просроченной заявке уже отправлено уведомление."""
        async with self.lock:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            await self.conn.execute(
                "INSERT OR IGNORE INTO ticket_escalations (ticket_id, notified_at) VALUES (?, ?)",
                (ticket_id, now)
            )
            await self.conn.commit()

    async def clear_ticket_escalations(self, ticket_id: int) -> None:
        """Снимает отметку эскалации (например, при повторном открытии заявки)."""
        async with self.lock:
            await self.conn.execute(
                "DELETE FROM ticket_escalations WHERE ticket_id = ?",
                (ticket_id,)
            )
            await self.conn.commit()

    # Ratings CRUD
    async def save_rating(self, ticket_id: int, client_id: int, rating: int, comment: str = None):
        """Сохраняет оценку клиента по завершённой заявке.

        Используется INSERT OR IGNORE, чтобы повторная оценка не перезаписывала предыдущую.
        При оценке ≤ 3 заявка автоматически помечается как «проблемная» (low_rated_tickets).
        """
        async with self.lock:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            await self.conn.execute(
                "INSERT OR IGNORE INTO ratings (ticket_id, client_id, rating, comment, created_at) VALUES (?, ?, ?, ?, ?)",
                (ticket_id, client_id, rating, comment, now)
            )
            # Триггер: оценка ≤ 3 → заявка попадает в дашборд руководителя
            if rating <= 3:
                await self.conn.execute(
                    "INSERT OR IGNORE INTO low_rated_tickets (ticket_id, rating, flagged_at) VALUES (?, ?, ?)",
                    (ticket_id, rating, now)
                )
            await self.conn.commit()

    async def update_rating_comment(self, ticket_id: int, comment: str):
        """Обновляет комментарий к оценке заявки."""
        async with self.lock:
            await self.conn.execute(
                "UPDATE ratings SET comment = ? WHERE ticket_id = ?",
                (comment, ticket_id)
            )
            await self.conn.commit()

    async def get_rating_for_ticket(self, ticket_id: int) -> aiosqlite.Row | None:
        """Возвращает оценку по заявке."""
        async with self.lock:
            cursor = await self.conn.execute("SELECT * FROM ratings WHERE ticket_id = ?", (ticket_id,))
            return await cursor.fetchone()

    async def get_avg_rating(self) -> float | None:
        """Средняя оценка по всем заявкам."""
        async with self.lock:
            cursor = await self.conn.execute("SELECT AVG(rating) as avg_rating FROM ratings")
            row = await cursor.fetchone()
            return row['avg_rating'] if row else None

    async def get_avg_resolution_time(self) -> float | None:
        """Среднее время решения заявки (в часах) для завершённых заявок."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT AVG((julianday(closed_at) - julianday(created_at)) * 24) as avg_hours "
                "FROM tickets WHERE status = 'completed' AND created_at IS NOT NULL AND closed_at IS NOT NULL"
            )
            row = await cursor.fetchone()
            return row['avg_hours'] if row else None

    async def get_tickets_by_city(self) -> list[aiosqlite.Row]:
        """Статистика заявок по городам."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT company_city, COUNT(*) as cnt FROM tickets WHERE company_city IS NOT NULL AND company_city != '' GROUP BY company_city ORDER BY cnt DESC LIMIT 10"
            )
            return await cursor.fetchall()

    # Messages CRUD
    async def save_message(self, ticket_id: int, sender_id: int, sender_role: str, text: str, media_type: str = None):
        """Сохраняет сообщение переписки в историю."""
        async with self.lock:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            await self.conn.execute(
                "INSERT INTO messages (ticket_id, sender_id, sender_role, text, media_type, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (ticket_id, sender_id, sender_role, text, media_type, now)
            )
            await self.conn.commit()

    async def get_messages_for_ticket(self, ticket_id: int) -> list[aiosqlite.Row]:
        """Возвращает историю переписки по заявке."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM messages WHERE ticket_id = ? ORDER BY created_at ASC",
                (ticket_id,)
            )
            return await cursor.fetchall()

    async def get_ticket_chat_text_only(self, ticket_id: int) -> list[aiosqlite.Row]:
        """Возвращает переписку по заявке БЕЗ медиа-сообщений (только текст)."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM messages WHERE ticket_id = ? AND (media_type IS NULL OR media_type = '') "
                "ORDER BY created_at ASC",
                (ticket_id,)
            )
            return await cursor.fetchall()

    # Low-Rated Tickets (дашборд руководителя)
    async def get_low_rated_tickets(
        self, limit: int = 50, offset: int = 0
    ) -> list[aiosqlite.Row]:
        """Возвращает список проблемных заявок (оценка ≤ 3) с данными об инженере и клиенте."""
        async with self.lock:
            cursor = await self.conn.execute("""
                SELECT
                    t.id, t.uuid, t.client_name, t.company_city, t.machine_info,
                    t.problem, t.status, t.created_at,
                    r.rating, r.comment as rating_comment,
                    e.name as engineer_name,
                    (SELECT COUNT(*) FROM messages WHERE ticket_id = t.id) as message_count
                FROM low_rated_tickets lrt
                JOIN tickets t ON lrt.ticket_id = t.id
                LEFT JOIN ratings r ON t.id = r.ticket_id
                LEFT JOIN engineers e ON t.engineer_id = e.user_id
                ORDER BY lrt.flagged_at DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))
            return await cursor.fetchall()

    async def get_low_rated_count(self) -> int:
        """Возвращает общее количество проблемных заявок."""
        async with self.lock:
            cursor = await self.conn.execute("SELECT COUNT(*) as cnt FROM low_rated_tickets")
            row = await cursor.fetchone()
            return row['cnt'] if row else 0

    async def get_low_rated_avg_rating(self) -> float | None:
        """Средняя оценка среди проблемных заявок."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT AVG(rating) as avg_rating FROM low_rated_tickets"
            )
            row = await cursor.fetchone()
            return row['avg_rating'] if row else None

    async def get_low_rated_this_week_count(self) -> int:
        """Количество проблемных заявок за последние 7 дней."""
        async with self.lock:
            cursor = await self.conn.execute("""
                SELECT COUNT(*) as cnt FROM low_rated_tickets
                WHERE flagged_at >= datetime('now', '-7 days')
            """)
            row = await cursor.fetchone()
            return row['cnt'] if row else 0

    
    async def get_dashboard_data(self) -> dict:
        """Агрегирует данные для дашборда: KPI, графики, рейтинги."""
        async with self.lock:
            # Всего/открыто/в работе/закрыто
            cursor = await self.conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as open_count,
                    SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) as in_progress,
                    SUM(CASE WHEN status IN ('completed','canceled') THEN 1 ELSE 0 END) as closed
                FROM tickets
            """)
            row = await cursor.fetchone()
            
            # Заявки по дням (последние 14 дней)
            cursor = await self.conn.execute("""
                SELECT DATE(created_at) as day, COUNT(*) as cnt
                FROM tickets WHERE created_at IS NOT NULL
                GROUP BY day ORDER BY day DESC LIMIT 14
            """)
            daily = await cursor.fetchall()
            daily_labels = [r['day'] for r in reversed(daily)]
            daily_counts = [r['cnt'] for r in reversed(daily)]
            
            # Инженеры: нагрузка + среднее время
            cursor = await self.conn.execute("""
                SELECT e.name, e.user_id,
                    SUM(CASE WHEN t.status='in_progress' THEN 1 ELSE 0 END) as active,
                    COUNT(t.id) as total_tickets,
                    AVG(CASE WHEN t.closed_at IS NOT NULL AND t.created_at IS NOT NULL
                        THEN (julianday(t.closed_at) - julianday(t.created_at)) * 24 END) as avg_hours
                FROM engineers e
                LEFT JOIN tickets t ON e.user_id = t.engineer_id
                GROUP BY e.user_id, e.name
                ORDER BY active DESC
            """)
            engs = await cursor.fetchall()
            
            eng_names = [r['name'] or f"ID:{r['user_id']}" for r in engs]
            eng_loads = [r['active'] or 0 for r in engs]
            eng_avg_times = [round(r['avg_hours'] or 0, 1) for r in engs]
            eng_colors = ['#e94560','#0f9b58','#f0a500','#4361ee','#7209b7','#f72585','#4cc9f0'][:len(engs)]
            
            # Рейтинги
            cursor = await self.conn.execute("""
                SELECT e.name, AVG(r.rating) as avg_r, COUNT(r.id) as cnt
                FROM ratings r
                JOIN tickets t ON r.ticket_id = t.id
                JOIN engineers e ON t.engineer_id = e.user_id
                GROUP BY e.name ORDER BY avg_r DESC LIMIT 10
            """)
            ratings = [{'name': r['name'], 'rating': round(r['avg_r'], 1), 'count': r['cnt']} for r in await cursor.fetchall()]
            
            # Города
            cursor = await self.conn.execute("""
                SELECT company_city, COUNT(*) as cnt FROM tickets
                WHERE company_city IS NOT NULL AND company_city != ''
                GROUP BY company_city ORDER BY cnt DESC LIMIT 10
            """)
            cities = [{'city': r['company_city'], 'cnt': r['cnt']} for r in await cursor.fetchall()]

            # SLA-воронка + время реакции
            cursor = await self.conn.execute("""
                SELECT SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) as f_open,
                    SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) as f_prog,
                    SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as f_done,
                    SUM(CASE WHEN status='canceled' THEN 1 ELSE 0 END) as f_cancel,
                    ROUND(AVG(CASE WHEN status IN ('in_progress','completed','canceled')
                        AND created_at IS NOT NULL
                        THEN (julianday(COALESCE(closed_at, datetime('now'))) - julianday(created_at))*24 END),1) as avg_h
                FROM tickets
            """)
            sla = await cursor.fetchone()

            cursor = await self.conn.execute("""
                SELECT COUNT(*) as cnt, ROUND(AVG((julianday(closed_at)-julianday(created_at))*24*60),0) as avg_min
                FROM tickets WHERE status IN ('in_progress','completed','canceled')
                  AND created_at IS NOT NULL AND closed_at IS NOT NULL AND engineer_id IS NOT NULL
            """)
            reaction = await cursor.fetchone()

            # Типы оборудования
            cursor = await self.conn.execute("""
                SELECT machine_info, COUNT(*) as cnt FROM tickets
                WHERE machine_info IS NOT NULL AND machine_info != ''
                GROUP BY machine_info ORDER BY cnt DESC LIMIT 20
            """)
            ekw = {'Фрезерный': ['фрезер','m3','m1','nc'], 'Лазерный CO2': ['co2','лазер','laser','трубка','0404','0606','1010','1610'],
                   'Металлорез': ['металлорез','волокон','fiber'], 'Плазморез': ['плазма'], 'Токарный': ['токар']}
            ec, eo = {}, 0
            for r in await cursor.fetchall():
                info = (r['machine_info'] or '').lower()
                for label, keys in ekw.items():
                    if any(k in info for k in keys):
                        ec[label] = ec.get(label, 0) + r['cnt']; break
                else: eo += r['cnt']
            if eo: ec['Другое'] = eo
            equipment = [{'type': k, 'cnt': v} for k, v in sorted(ec.items(), key=lambda x: -x[1])]

            # Топ проблем
            cursor = await self.conn.execute("""
                SELECT problem, COUNT(*) as cnt FROM tickets
                WHERE problem IS NOT NULL AND problem != ''
                GROUP BY problem ORDER BY cnt DESC LIMIT 20
            """)
            pkw = {'Не включается': ['не включает','ошибк','alarm','не запуск'],
                   'Круги/эллипс': ['круг','эллипс','овал'],
                   'Двигатель/редуктор': ['двигател','редуктор','шаговый','мотор'],
                   'Люфт/точность': ['люфт','неточность','допуск','перекос'],
                   'Резка/кромка': ['резк','кромка','нагар','контур'],
                   'Юстировка': ['юстировк','центровк','луч'],
                   'Охлаждение': ['чиллер','охлажден','помпа'],
                   'ШВП/направляющие': ['швп','направляющ','винт'],
                   'Контроллер': ['контроллер','плат','электроник','питани'],
                   'Шпиндель': ['шпиндель']}
            pc, po = {}, 0
            for r in await cursor.fetchall():
                text = (r['problem'] or '').lower()
                for label, keys in pkw.items():
                    if any(k in text for k in keys):
                        pc[label] = pc.get(label, 0) + r['cnt']; break
                else: po += r['cnt']
            if po: pc['Прочее'] = po
            problems = [{'name': k, 'cnt': v} for k, v in sorted(pc.items(), key=lambda x: -x[1])]

            # Оценки, тренд, дни недели, повторы
            cursor = await self.conn.execute(
                "SELECT rating, COUNT(*) as cnt FROM ratings WHERE rating BETWEEN 1 AND 5 GROUP BY rating ORDER BY rating")
            rd = {r['rating']: r['cnt'] for r in await cursor.fetchall()}
            ratings_dist = [rd.get(i, 0) for i in range(1, 6)]

            cursor = await self.conn.execute(
                "SELECT CASE WHEN created_at>=datetime('now','-7 days') THEN 't' ELSE 'p' END as w, COUNT(*) as cnt FROM tickets WHERE created_at>=datetime('now','-14 days') GROUP BY w")
            wd = {r['w']: r['cnt'] for r in await cursor.fetchall()}
            this_week, prev_week = wd.get('t', 0), wd.get('p', 0)

            cursor = await self.conn.execute(
                "SELECT CAST(strftime('%w',created_at) AS INTEGER) as dow, COUNT(*) as cnt FROM tickets WHERE created_at IS NOT NULL GROUP BY dow ORDER BY dow")
            dd = {r['dow']: r['cnt'] for r in await cursor.fetchall()}
            day_names = ['Вс','Пн','Вт','Ср','Чт','Пт','Сб']
            dow_load = [dd.get(i, 0) for i in range(7)]

            cursor = await self.conn.execute(
                "SELECT COUNT(DISTINCT client_id) as total, COUNT(DISTINCT CASE WHEN tc>1 THEN client_id END) as rep FROM (SELECT client_id,COUNT(*) as tc FROM tickets GROUP BY client_id)")
            rep = await cursor.fetchone()
        
# Заявки с перепиской для дашборда (последние 30, любые статусы)
            cursor = await self.conn.execute("""
                SELECT t.id, t.client_name, t.company_city, t.machine_info,
                    t.problem, t.status, t.created_at,
                    e.name as engineer_name,
                    r.rating, r.comment as rating_comment
                FROM tickets t
                LEFT JOIN engineers e ON t.engineer_id = e.user_id
                LEFT JOIN ratings r ON t.id = r.ticket_id
                ORDER BY t.id DESC LIMIT 30
            """)
            tickets_raw = await cursor.fetchall()

            tickets_with_chat = []
            for tk in tickets_raw:
                c = await self.conn.execute(
                    "SELECT sender_role, text, created_at FROM messages "
                    "WHERE ticket_id = ? AND (media_type IS NULL OR media_type = '') "
                    "ORDER BY created_at ASC",
                    (tk['id'],)
                )
                msgs = await c.fetchall()
                chat = [
                    {
                        'role': m['sender_role'],
                        'text': m['text'] or '',
                        'time': (m['created_at'] or '')[:16].replace('T', ' ')
                    }
                    for m in msgs
                ]
                stars = '★' * (tk['rating'] or 0)
                tickets_with_chat.append({
                    'id': tk['id'],
                    'status': tk['status'],
                    'client': tk['client_name'] or '—',
                    'city': tk['company_city'] or '—',
                    'machine': tk['machine_info'] or '—',
                    'problem': tk['problem'] or '—',
                    'engineer': tk['engineer_name'] or '—',
                    'stars': stars,
                    'rating': tk['rating'] or 0,
                    'comment': tk['rating_comment'] or '',
                    'created': (tk['created_at'] or '')[:10],
                    'chat': chat,
                })
        return {
            'total': row['total'],
            'open': row['open_count'],
            'in_progress': row['in_progress'],
            'closed': row['closed'],
            'daily_labels': daily_labels,
            'daily_counts': daily_counts,
            'eng_names': eng_names,
            'eng_loads': eng_loads,
            'eng_avg_times': eng_avg_times,
            'eng_colors': eng_colors,
            'ratings': ratings,
            'cities': cities,
            'status_flow': [sla['f_open'], sla['f_prog'], sla['f_done'], sla['f_cancel']],
            'sla_avg_h': sla['avg_h'] or 0,
            'reaction_min': reaction['avg_min'] or 0, 'reaction_cnt': reaction['cnt'] or 0,
            'equipment': equipment, 'problems': problems,
            'ratings_dist': ratings_dist,
            'this_week': this_week, 'prev_week': prev_week,
            'dow_load': dow_load, 'day_names': day_names,
            'repeat_pct': round((rep['rep'] or 0) / max(rep['total'] or 1, 1) * 100),
            'total_clients': rep['total'] or 0, 'repeat_clients': rep['rep'] or 0,
            'tickets_with_chat': tickets_with_chat,
        }

    async def get_engineer_stats(self) -> list[aiosqlite.Row]:
        """
        Retrieves statistics for each engineer, including active and closed tickets.
        """
        async with self.lock:
            query = """
                SELECT
                    e.user_id,
                    e.name,
                    SUM(CASE WHEN t.status = 'in_progress' THEN 1 ELSE 0 END) as active_count,
                    SUM(CASE WHEN t.status IN ('completed', 'canceled') THEN 1 ELSE 0 END) as closed_count
                FROM
                    engineers e
                LEFT JOIN
                    tickets t ON e.user_id = t.engineer_id
                GROUP BY
                    e.user_id, e.name
                ORDER BY
                    e.name;
            """
            cursor = await self.conn.execute(query)
            return await cursor.fetchall()

    # Media CRUD
    async def save_media(self, ticket_id: int, file_id: str, file_type: str, file_path: str, sender_id: int, sender_role: str):
        """Сохраняет информацию о медиафайле заявки."""
        async with self.lock:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            await self.conn.execute(
                "INSERT INTO media (ticket_id, file_id, file_type, file_path, sender_id, sender_role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticket_id, file_id, file_type, file_path, sender_id, sender_role, now)
            )
            await self.conn.commit()

    async def get_media_for_ticket(self, ticket_id: int) -> list[aiosqlite.Row]:
        """Возвращает список медиафайлов заявки."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM media WHERE ticket_id = ? ORDER BY created_at ASC, id ASC",
                (ticket_id,)
            )
            return await cursor.fetchall()

    # Ticket Notifications CRUD
    async def save_ticket_notification(self, ticket_id: int, engineer_id: int, message_id: int):
        """Сохраняет message_id уведомления о заявке, отправленного инженеру."""
        async with self.lock:
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            await self.conn.execute(
                "INSERT INTO ticket_notifications (ticket_id, engineer_id, message_id, created_at) VALUES (?, ?, ?, ?)",
                (ticket_id, engineer_id, message_id, now)
            )
            await self.conn.commit()

    async def get_ticket_notifications(self, ticket_id: int) -> list[aiosqlite.Row]:
        """Возвращает список уведомлений о заявке, отправленных инженерам."""
        async with self.lock:
            cursor = await self.conn.execute(
                "SELECT * FROM ticket_notifications WHERE ticket_id = ?",
                (ticket_id,)
            )
            return await cursor.fetchall()

    async def delete_ticket_notifications(self, ticket_id: int):
        """Удаляет все уведомления о заявке (после того, как заявка взята/закрыта)."""
        async with self.lock:
            await self.conn.execute(
                "DELETE FROM ticket_notifications WHERE ticket_id = ?",
                (ticket_id,)
            )
            await self.conn.commit()

    # ─── DAO-свойства для структурированного доступа ─────────────────────
    # Позволяют обращаться к методам через db.engineers.add(...),
    # db.tickets.create(...) и т.д. вместо flat-методов db.add_engineer(...).
    # Обратная совместимость: старые методы (db.add_engineer и др.) продолжают работать.

    class _EngineerAccess:
        """DAO-обёртка для методов работы с инженерами."""
        def __init__(self, db: "Database"):
            self._db = db
        add = property(lambda self: self._db.add_engineer)
        delete = property(lambda self: self._db.delete_engineer)
        get_active = property(lambda self: self._db.get_engineers)
        get_all = property(lambda self: self._db.get_all_engineers)
        set_active = property(lambda self: self._db.set_engineer_active)
        is_engineer = property(lambda self: self._db.is_engineer)
        has_active_tickets = property(lambda self: self._db.has_active_tickets)
        set_bitrix_id = property(lambda self: self._db.set_bitrix_user_id)
        get_bitrix_id = property(lambda self: self._db.get_bitrix_user_id)
        get_name = property(lambda self: self._db.get_engineer_name)

    class _AdminAccess:
        """DAO-обёртка для методов работы с администраторами."""
        def __init__(self, db: "Database"):
            self._db = db
        add = property(lambda self: self._db.add_admin)
        delete = property(lambda self: self._db.delete_admin)
        get_all = property(lambda self: self._db.get_admins)
        is_admin = property(lambda self: self._db.is_admin)

    class _LowRatedAccess:
        """DAO-обёртка для методов работы с проблемными заявками."""
        def __init__(self, db: "Database"):
            self._db = db
        get_list = property(lambda self: self._db.get_low_rated_tickets)
        count = property(lambda self: self._db.get_low_rated_count)
        avg_rating = property(lambda self: self._db.get_low_rated_avg_rating)
        this_week = property(lambda self: self._db.get_low_rated_this_week_count)
        chat_text_only = property(lambda self: self._db.get_ticket_chat_text_only)

    class _TicketAccess:
        """DAO-обёртка для методов работы с заявками."""
        def __init__(self, db: "Database"):
            self._db = db
        create = property(lambda self: self._db.create_ticket)
        get = property(lambda self: self._db.get_ticket)
        get_by_uuid = property(lambda self: self._db.get_ticket_by_uuid)
        take = property(lambda self: self._db.take_ticket)
        close = property(lambda self: self._db.close_ticket)
        get_for_client = property(lambda self: self._db.get_active_ticket_for_client)
        get_client_history = property(lambda self: self._db.get_client_tickets)
        get_for_engineer = property(lambda self: self._db.get_active_tickets_for_engineer)
        get_open = property(lambda self: self._db.get_open_tickets)
        get_all = property(lambda self: self._db.get_all_tickets)
        status_counts = property(lambda self: self._db.get_ticket_status_counts)
        get_by_period = property(lambda self: self._db.get_tickets_by_period)
        get_expired = property(lambda self: self._db.get_expired_open_tickets)
        get_expired_not_escalated = property(lambda self: self._db.get_expired_open_tickets_not_escalated)
        mark_escalated = property(lambda self: self._db.mark_ticket_escalated)
        clear_escalations = property(lambda self: self._db.clear_ticket_escalations)

    class _MessageAccess:
        """DAO-обёртка для методов работы с сообщениями."""
        def __init__(self, db: "Database"):
            self._db = db
        save = property(lambda self: self._db.save_message)
        get_for_ticket = property(lambda self: self._db.get_messages_for_ticket)

    class _RatingAccess:
        """DAO-обёртка для методов работы с оценками."""
        def __init__(self, db: "Database"):
            self._db = db
        save = property(lambda self: self._db.save_rating)
        update_comment = property(lambda self: self._db.update_rating_comment)
        get_for_ticket = property(lambda self: self._db.get_rating_for_ticket)
        avg = property(lambda self: self._db.get_avg_rating)
        avg_resolution_time = property(lambda self: self._db.get_avg_resolution_time)
        by_city = property(lambda self: self._db.get_tickets_by_city)

    class _StatsAccess:
        """DAO-обёртка для методов статистики и дашборда."""
        def __init__(self, db: "Database"):
            self._db = db
        dashboard = property(lambda self: self._db.get_dashboard_data)
        engineer_stats = property(lambda self: self._db.get_engineer_stats)

    class _MediaAccess:
        """DAO-обёртка для методов работы с медиафайлами."""
        def __init__(self, db: "Database"):
            self._db = db
        save = property(lambda self: self._db.save_media)
        get_for_ticket = property(lambda self: self._db.get_media_for_ticket)

    class _NotificationAccess:
        """DAO-обёртка для методов работы с уведомлениями."""
        def __init__(self, db: "Database"):
            self._db = db
        save = property(lambda self: self._db.save_ticket_notification)
        get_for_ticket = property(lambda self: self._db.get_ticket_notifications)
        delete_for_ticket = property(lambda self: self._db.delete_ticket_notifications)

    @property
    def engineers(self) -> _EngineerAccess:
        return self._EngineerAccess(self)

    @property
    def admins(self) -> _AdminAccess:
        return self._AdminAccess(self)

    @property
    def low_rated(self) -> _LowRatedAccess:
        return self._LowRatedAccess(self)

    @property
    def tickets(self) -> _TicketAccess:
        return self._TicketAccess(self)

    @property
    def messages(self) -> _MessageAccess:
        return self._MessageAccess(self)

    @property
    def ratings(self) -> _RatingAccess:
        return self._RatingAccess(self)

    @property
    def stats(self) -> _StatsAccess:
        return self._StatsAccess(self)

    @property
    def media(self) -> _MediaAccess:
        return self._MediaAccess(self)

    @property
    def notifications(self) -> _NotificationAccess:
        return self._NotificationAccess(self)
