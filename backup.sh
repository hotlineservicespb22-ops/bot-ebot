#!/usr/bin/env bash
# ============================================================
#  Резервное копирование Hotline Service Bot
#
#  Копирует БД (hotline_service.db) и медиафайлы (media/) в backups/,
#  с ретеншеном — старые копии удаляются автоматически.
#
#  БД копируется через встроенный sqlite3.backup() ВНУТРИ контейнера
#  (docker exec), а не с хоста напрямую — так гарантированно виден
#  весь WAL-журнал (то, что бот успел записать, но ещё не закоммитил
#  в основной файл .db), и бэкап безопасен при работающем боте.
#
#  Использование:
#     ./backup.sh               — разовый запуск (обычно из cron)
#     ./backup.sh --now         — то же самое, для запуска вручную
#
#  Пример добавления в cron (ежедневно в 03:00):
#     0 3 * * * /home/dvl/bot-ebot/backup.sh >> /home/dvl/bot-ebot/.backup.log 2>&1
#
#  ВАЖНО: это ЛОКАЛЬНОЕ резервное копирование (на тот же диск). Оно
#  защищает от битой миграции, случайного удаления и повреждения БД,
#  но НЕ защищает от отказа самого диска/сервера. Для полноценной
#  защиты добавьте синхронизацию backups/ на другой хост или в
#  объектное хранилище (rsync/rclone) — это уже зависит от того, какое
#  хранилище доступно, и здесь не настроено.
# ============================================================
set -euo pipefail

CONTAINER="hotline-bot"
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="$BASE_DIR/backups"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

mkdir -p "$BACKUP_DIR"

# ---------- 1) База данных ----------
if docker ps --filter "name=${CONTAINER}" --format '{{.Names}}' | grep -q "^${CONTAINER}\$"; then
    log "Снимаю бэкап БД через sqlite3.backup() внутри контейнера $CONTAINER..."
    docker exec "$CONTAINER" python3 -c "
import sqlite3
src = sqlite3.connect('/app/hotline_service.db')
dst = sqlite3.connect('/tmp/hotline_backup.db')
with dst:
    src.backup(dst)
src.close()
dst.close()
"
    docker cp "${CONTAINER}:/tmp/hotline_backup.db" "$BACKUP_DIR/hotline_service_${TIMESTAMP}.db"
    docker exec "$CONTAINER" rm -f /tmp/hotline_backup.db
    gzip -f "$BACKUP_DIR/hotline_service_${TIMESTAMP}.db"
    log "БД сохранена: $BACKUP_DIR/hotline_service_${TIMESTAMP}.db.gz"
else
    log "Контейнер $CONTAINER не запущен — бэкап БД пропущен."
fi

# ---------- 2) Медиафайлы ----------
if [[ -d "$BASE_DIR/media" ]] && [[ -n "$(ls -A "$BASE_DIR/media" 2>/dev/null)" ]]; then
    log "Архивирую media/..."
    tar -czf "$BACKUP_DIR/media_${TIMESTAMP}.tar.gz" -C "$BASE_DIR" media
    log "Медиафайлы сохранены: $BACKUP_DIR/media_${TIMESTAMP}.tar.gz"
else
    log "Каталог media/ пуст или отсутствует — архивирование пропущено."
fi

# ---------- 3) Ретеншен: удаляем бэкапы старше RETENTION_DAYS ----------
find "$BACKUP_DIR" -maxdepth 1 -name "hotline_service_*.db.gz" -mtime +"$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -maxdepth 1 -name "media_*.tar.gz" -mtime +"$RETENTION_DAYS" -delete

log "Готово."
