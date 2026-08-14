#!/usr/bin/env bash
# ============================================================
#  Скрипт управления Hotline Service Bot (Docker)
#  Использование: ./manage.sh {start|stop|restart|logs|status|update}
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE="docker compose"
CONTAINER="hotline-bot"

ensure_files() {
    # Docker создаёт каталоги вместо файлов, если файлов нет — создаём заранее
    [[ -f bot.log ]] || touch bot.log
    [[ -f hotline_service.db ]] || touch hotline_service.db
    [[ -f .env.txt ]] || touch .env.txt
    [[ -f .heartbeat ]] || touch .heartbeat
    [[ -d media ]] || mkdir -p media
}

case "${1:-}" in
    start)
        ensure_files
        $COMPOSE up -d --build
        echo "✅ Контейнер $CONTAINER запущен"
        ;;
    stop)
        $COMPOSE down
        echo "⏹ Контейнер $CONTAINER остановлен"
        ;;
    restart)
        ensure_files
        $COMPOSE down
        $COMPOSE up -d --build
        echo "🔄 Контейнер $CONTAINER перезапущен"
        ;;
    logs)
        docker logs -f --tail 50 "$CONTAINER"
        ;;
    status)
        docker ps --filter "name=$CONTAINER" --format "table {{.Names}}\t{{.Status}}"
        echo "--- Последние логи ---"
        docker logs --tail 10 "$CONTAINER" 2>&1 || true
        ;;
    update)
        echo "📥 Обновление кода из репозитория..."
        git pull --ff-only origin main
        ensure_files
        $COMPOSE up -d --build
        echo "✅ Обновление завершено"
        ;;
    *)
        echo "Использование: $0 {start|stop|restart|logs|status|update}"
        exit 1
        ;;
esac