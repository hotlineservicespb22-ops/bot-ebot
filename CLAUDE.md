# Hotline Service Bot — Память проекта (CLAUDE.md)

> Этот файл автоматически читается Cline при работе в этой папке. Хранит ключевой контекст проекта.

## Общее описание

**Hotline Service Bot** — Telegram-бот на Python (`aiogram 3.x`) для сервисной службы по диагностике и ремонту станков с ЧПУ. Принимает заявки от клиентов, распределяет их между инженерами, ведёт диалог клиент↔инженер, собирает оценки, даёт админ-панель со статистикой и экспортом.

## Репозиторий и Git

- **Локальный путь:** `/home/dvl/bot-ebot`
- **Remote:** `git@hotline-bot:hotlineservicespb22-ops/bot-ebot.git`
- **Ветка:** `main` (синхронизирована с `origin/main`)
- **Последний коммит:** `a22117a` — «Обновление бота: FAQ, логирование, миграции, сервисы; обновлены хендлеры и тесты» (автор Vadim, 14.08.2026)
- **Правило:** по завершении любой задачи сразу коммитить все изменения (`git add -A && git commit`), не оставлять незакоммиченных правок. Сообщение коммита — осмысленное, на русском.

## Стек и окружение

- Python 3.10+ (Docker-образ `python:3.12-slim`)
- `aiogram 3.x`, `aiosqlite`, `python-dotenv`
- БД: SQLite (`hotline_service.db`)
- FSM: Redis (`hotline-redis`, `redis:7-alpine`) или MemoryStorage
- Медиа: папка `media/ticket_<id>/`
- Логи: `bot.log` (ротация 5 МБ × 3)
- Развёрнут в Docker: контейнер `hotline-bot` (образ `bot-ebot-bot`, статус healthy), compose-проект `bot-ebot`, конфиг `docker-compose.yml`

## Структура кода (`bot/`)

- `main.py` — точка входа, поллинг/webhook, FSM-хранилище
- `config.py` — конфигурация (env-переменные)
- `database.py` — работа с SQLite
- `migrations.py` — миграции БД
- `bitrix.py` — интеграция с Битрикс24 (создание задач)
- `faq.py` — частые вопросы
- `keyboards.py` — клавиатуры
- `media.py` — работа с медиафайлами
- `services.py` — бизнес-логика
- `middlewares.py` — middleware
- `logging_config.py` — настройка логирования
- `handlers/` — `admin.py`, `client.py`, `engineer.py`, `relay.py`

## Роли пользователей

- **Клиент** — создаёт заявки (FSM-диалог из 4 шагов), смотрит FAQ, историю, общается с инженером, оценивает работу (1–5 звёзд).
- **Инженер** — берёт заявки в работу, общается с клиентом, завершает/отменяет заявки.
- **Администратор** — управляет инженерами/админами, статистика, экспорт CSV, уведомления о новых и просроченных заявках.

## Ключевые env-переменные

- `BOT_TOKEN`, `ADMIN_IDS` (через запятую)
- `DB_PATH`, `TICKET_TIMEOUT` (по умолч. 300 сек), `MEDIA_DIR`
- `REDIS_URL` (если пусто — MemoryStorage)
- `WEBHOOK_URL` / `WEBHOOK_PATH` / `WEBHOOK_HOST` / `WEBHOOK_PORT`
- `FSM_TIMEOUT` (по умолч. 1800 сек), `LOG_LEVEL`, `LOG_FILE`
- Битрикс24: `BITRIX_WEBHOOK_URL`, `BITRIX_CREATED_BY` (фикс. 414), `BITRIX_TASK_PRIORITY`, `BITRIX_TASK_DEADLINE_HOURS`, `BITRIX_DISK_FOLDER_ID`, `BITRIX_ATTACH_FILES`

## Интеграция с Битрикс24

При взятии заявки инженером бот создаёт **задачу** (`tasks.task.add`) в Битрикс24. Постановщик всегда 414. Ответственный — `bitrix_user_id` инженера (команда `/set_bitrix <tg_id> <bitrix_id>`). Файлы заявки загружаются на диск и прикрепляются (если `BITRIX_ATTACH_FILES=1`). Если webhook не задан или нет `bitrix_user_id` — интеграция отключена/пропускается без блокировки взятия заявки.

## Важные команды админа

- `/add_eng <ID> <Имя>`, `/del_eng <ID>`, `/list_eng`
- `/add_admin <ID>`, `/del_admin <ID>`, `/list_admin`
- `/stats`, `/export [YYYY-MM-DD YYYY-MM-DD]`
- `/set_bitrix <tg_id> <bitrix_id>`

## Запуск

```bash
# вручную
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 -m bot.main

# Docker
docker compose up -d
```

## Важные замечания

- Не запускать поллинг и webhook одновременно с одним токеном (конфликт `getUpdates` / `TelegramConflictError`).
- SQLite создаётся автоматически, миграции применяются автоматически.
- В Docker монтируются: `bot.log`, `media/`, `.env.txt`, `hotline_service.db`.