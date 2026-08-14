#!/usr/bin/env bash
# ============================================================
#  Watchdog для Hotline Service Bot
#
#  Запускается из cron каждые N минут. Проверяет здоровье бота и
#  при необходимости перезапускает контейнер. Решает две задачи:
#
#  1) Защита от «зависшего» long-poll (getUpdates).
#     Бот штатно пишет файл-«пульс» .heartbeat каждые HEARTBEAT_INTERVAL
#     секунд (см. bot/main.py). Если файл не обновлялся дольше
#     HB_MAX_AGE — event loop завис, контейнер перезапускается.
#
#  2) Защита от дублей поллинга (TelegramConflictError).
#     Если в bot.log появился конфликт поллинга (второй экземпляр с тем
#     же токеном), перезапускаем контейнер — единственный «лишний»
#     поллинг, который мы можем устранить локально.
#
#  Использование:
#     ./watchdog.sh            — обычная проверка
#     ./watchdog.sh --now      — показать статус (только вручную, не из cron)
# ============================================================
set -u

CONTAINER="hotline-bot"
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
HEARTBEAT_FILE="$BASE_DIR/.heartbeat"
LOG_FILE="$BASE_DIR/bot.log"

# Максимально допустимый возраст heartbeat (секунды). Бот обновляет его раз
# в 5 секунд, поэтому лимит 180 сек означает явное зависание event loop.
HB_MAX_AGE="${HB_MAX_AGE:-180}"

CONFLICT_MARKER="TelegramConflictError"

# Файл-маркер: храним дату последнего конфликта, чтобы не перезапускать
# контейнер в бесконечном цикле, если конфликт появился давно и уже «прошёл».
CONFLICT_STAMP="$BASE_DIR/.watchdog_conflict_stamp"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

# ---------- 1) Проверка heartbeat (зависший event loop) ----------
if [[ -f "$HEARTBEAT_FILE" ]]; then
    now_epoch=$(date +%s)
    hb_epoch=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null || echo 0)
    age=$((now_epoch - hb_epoch))

    if (( age > HB_MAX_AGE )); then
        log "Heartbeat устарел на ${age} сек (> ${HB_MAX_AGE}). Перезапускаю контейнер $CONTAINER."
        docker restart "$CONTAINER"
        exit 0
    fi
else
    log "Файл heartbeat отсутствует: $HEARTBEAT_FILE"
fi

# ---------- 2) Проверка конфликта поллинга в свежих логах ----------
# Реагируем только на конфликты, возникшие недавно (не старше CONFLICT_MAX_AGE
# секунд). Старые записи в bot.log (например, конфликт при прошлом деплое)
# игнорируются, чтобы не перезапускать контейнер без причины.
CONFLICT_MAX_AGE="${CONFLICT_MAX_AGE:-600}"

if [[ -f "$LOG_FILE" ]]; then
    # Ищем конфликт в последних 500 строках лога
    last_conflict_line=$(tail -n 500 "$LOG_FILE" | grep "$CONFLICT_MARKER" | tail -n 1)

    if [[ -n "$last_conflict_line" ]]; then
        # Извлекаем timestamp вида "YYYY-MM-DD HH:MM:SS,mmm" из начала строки
        conflict_ts=$(echo "$last_conflict_line" | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}' | head -n 1)

        if [[ -n "$conflict_ts" ]]; then
            # Лог пишется в локальном времени сервера, поэтому date без -u
            conflict_epoch=$(date -d "$conflict_ts" +%s 2>/dev/null || echo 0)
            now_epoch=$(date +%s)
            conflict_age=$((now_epoch - conflict_epoch))

            if (( conflict_age <= CONFLICT_MAX_AGE )); then
                log "Обнаружен свежий конфликт поллинга (${conflict_age} сек назад): $last_conflict_line"

                # Предотвращаем бесконечный цикл: если этот конфликт уже обрабатывали
                if [[ -f "$CONFLICT_STAMP" ]]; then
                    prev_stamp=$(cat "$CONFLICT_STAMP" 2>/dev/null || true)
                    if [[ -n "$prev_stamp" && "$last_conflict_line" == *"$prev_stamp"* ]]; then
                        exit 0
                    fi
                fi

                log "Перезапускаю контейнер $CONTAINER из-за TelegramConflictError."
                docker restart "$CONTAINER"
                echo "$last_conflict_line" > "$CONFLICT_STAMP"
                exit 0
            fi
        fi
    fi
fi

# Всё в порядке — ничего не делаем
exit 0