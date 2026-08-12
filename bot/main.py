import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramConflictError
from aiogram.types import ErrorEvent, BotCommand, BotCommandScopeChat, BotCommandScopeDefault
import traceback
import html

from bot.config import BOT_TOKEN, DB_PATH, ADMIN_IDS, TICKET_TIMEOUT, REDIS_URL, WEBHOOK_URL, WEBHOOK_PATH, WEBHOOK_HOST, WEBHOOK_PORT
from bot.database import Database
from bot.middlewares import DbSessionMiddleware, RoleMiddleware, ThrottlingMiddleware
from bot.handlers import client, engineer, admin, relay

# Настройка логирования для отладки
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
# Включаем DEBUG-логи для aiogram, чтобы видеть все входящие события в консоли
logging.getLogger('aiogram.event').setLevel(logging.DEBUG)

# Логирование в файл с ротацией
from logging.handlers import RotatingFileHandler
file_handler = RotatingFileHandler(
    "bot.log",
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=3,
    encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)
root_logger = logging.getLogger()
root_logger.addHandler(file_handler)

def get_storage():
    """Возвращает хранилище FSM: RedisStorage, если задан REDIS_URL, иначе MemoryStorage."""
    if REDIS_URL:
        from aiogram.fsm.storage.redis import RedisStorage
        from redis import asyncio as aioredis
        redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        logger.info("Используется RedisStorage для FSM")
        return RedisStorage(redis=redis_client)
    logger.info("Используется MemoryStorage для FSM (REDIS_URL не задан)")
    return MemoryStorage()

# FSM timeout with MemoryStorage is complex and requires custom state tracking or a different storage backend.
# For this refactoring, we rely on explicit /cancel and inline cancel buttons.

# Команды, доступные всем пользователям
DEFAULT_COMMANDS = [
    BotCommand(command="start", description="🔄 Запустить бота"),
    BotCommand(command="help", description="❓ Помощь"),
    BotCommand(command="my_requests", description="📋 Мои заявки"),
    BotCommand(command="cancel", description="❌ Отменить действие"),
]

# Команды, доступные только администраторам
ADMIN_COMMANDS = [
    BotCommand(command="admin", description="🛠 Админ-панель"),
    BotCommand(command="stats", description="📊 Статистика"),
    BotCommand(command="export", description="📥 Экспорт CSV"),
    BotCommand(command="add_admin", description="➕ Добавить админа"),
    BotCommand(command="del_admin", description="➖ Удалить админа"),
    BotCommand(command="list_admin", description="👑 Список админов"),
    BotCommand(command="add_eng", description="➕ Добавить инженера"),
    BotCommand(command="del_eng", description="➖ Удалить инженера"),
    BotCommand(command="list_eng", description="👥 Список инженеров"),
    BotCommand(command="bulk_add_eng", description="📦 Массовое добавление инженеров"),
    BotCommand(command="set_bitrix", description="🔗 Привязка к Битрикс24"),
    BotCommand(command="my_tickets", description="📋 Мои заявки в работе"),
]

async def setup_commands(bot: Bot, db: Database):
    """Устанавливает меню команд для всех пользователей и отдельно для админов."""
    # Общие команды для всех
    await bot.set_my_commands(DEFAULT_COMMANDS, scope=BotCommandScopeDefault())

    # Команды для админов из .env (ADMIN_IDS)
    for admin_id in ADMIN_IDS:
        try:
            await bot.set_my_commands(
                DEFAULT_COMMANDS + ADMIN_COMMANDS,
                scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except Exception as e:
            logger.warning(f"Не удалось установить команды для админа {admin_id}: {e}")

    # Команды для админов из БД
    db_admins = await db.get_admins()
    for row in db_admins:
        admin_id = row['user_id']
        if admin_id in ADMIN_IDS:
            continue  # Уже установили выше
        try:
            await bot.set_my_commands(
                DEFAULT_COMMANDS + ADMIN_COMMANDS,
                scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except Exception as e:
            logger.warning(f"Не удалось установить команды для админа {admin_id}: {e}")

    logger.info("Меню команд установлено (общие + админские).")

async def main():
    # Database initialization
    db = Database(DB_PATH)
    await db.connect()
    await db.init_db()
    await db.migrate()

    # Bot and Dispatcher initialization
    # Глобальная настройка HTML-разметки: все сообщения по умолчанию парсятся как HTML
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=get_storage())

    # Устанавливаем меню команд (общие + админские)
    await setup_commands(bot, db)

    # Error handler
    @dp.error()
    async def error_handler(event: ErrorEvent, bot: Bot):
        """
        Global error handler. Catches all exceptions.
        The error `TypeError: ... missing 1 required positional argument: 'exception'`
        from your logs indicates an old handler signature was used. The correct aiogram 3
        signature is `(event: ErrorEvent)`, with the exception in `event.exception`.
        """
        logger.error(f"Критическая ошибка: {event.exception}", exc_info=True)
        # Собираем админов из .env и из БД
        admin_ids = set(ADMIN_IDS)
        try:
            db_admins = await db.get_admins()
            admin_ids.update(row['user_id'] for row in db_admins)
        except Exception as e:
            logger.warning(f"Не удалось получить админов из БД для уведомления об ошибке: {e}")

        if admin_ids:
            try:
                # Используем traceback из самого исключения (в aiogram 3 format_exc() может быть пустым)
                tb_lines = traceback.format_exception(type(event.exception), event.exception, event.exception.__traceback__)
                tb_formatted = "".join(tb_lines)
                # Обрезаем traceback, чтобы не превысить лимит Telegram (4096 символов)
                if len(tb_formatted) > 3000:
                    tb_formatted = tb_formatted[-3000:]
                # Using HTML for safe formatting.
                error_message = (
                    f"<b>❌ Критическая ошибка</b>\n\n"
                    f"<b>Тип:</b> <code>{html.escape(type(event.exception).__name__)}</code>\n"
                    f"<b>Ошибка:</b> <code>{html.escape(str(event.exception))}</code>\n\n"
                    f"<b>Traceback:</b>\n<pre>{html.escape(tb_formatted)}</pre>"
                )
                # Обрезаем итоговое сообщение до безопасной длины (4000 символов)
                if len(error_message) > 4000:
                    error_message = error_message[:4000] + "\n...(обрезано)"
                for admin_id in admin_ids:
                    await bot.send_message(admin_id, error_message)
            except Exception as e:
                logger.error(f"Не удалось уведомить администраторов об ошибке: {e}")
    # Register Middlewares
    dp.update.middleware(DbSessionMiddleware(db))
    dp.update.middleware(RoleMiddleware())
    dp.update.middleware(ThrottlingMiddleware(interval=0.5))

    # Register Routers (order matters for relay)
    dp.include_router(admin.router)
    dp.include_router(engineer.router)
    dp.include_router(client.router)
    dp.include_router(relay.router)

    # Запускаем фоновую задачу контроля таймаутов заявок
    timeout_task = asyncio.create_task(ticket_timeout_watcher(bot, db))

    try:
        if WEBHOOK_URL:
            # Webhook-режим
            from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
            from aiohttp import web

            await bot.set_webhook(f"{WEBHOOK_URL}{WEBHOOK_PATH}")
            app = web.Application()
            webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
            webhook_requests_handler.register(app, path=WEBHOOK_PATH)
            setup_application(app, dp, bot=bot)
            logger.info(f"Бот запущен через webhook: {WEBHOOK_URL}{WEBHOOK_PATH}")
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, host=WEBHOOK_HOST, port=WEBHOOK_PORT)
            await site.start()
            # Держим сервер запущенным
            await asyncio.Event().wait()
        else:
            logger.info("Бот сервисной службы успешно запущен...")
            await dp.start_polling(bot, skip_updates=True)
    except TelegramConflictError:
        logger.critical("Конфликт поллинга: Бот уже запущен в другом месте! Закройте зависшие процессы.")
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске: {type(e).__name__}: {e}")
    finally:
        timeout_task.cancel()
        if WEBHOOK_URL:
            await bot.delete_webhook()
        await shutdown()
        await db.close()
        await bot.session.close()

async def ticket_timeout_watcher(bot: Bot, db: Database):
    """
    Фоновая задача: проверяет заявки в статусе 'open', которые висят дольше TICKET_TIMEOUT,
    и эскалирует их администраторам.
    """
    check_interval = min(max(TICKET_TIMEOUT // 2, 30), 300)  # От 30 сек до 5 мин
    while True:
        try:
            expired = await db.get_expired_open_tickets(TICKET_TIMEOUT)
            for ticket in expired:
                log_msg = (
                    f"⏰ <b>Заявка #{ticket['id']} не взята в работу!</b>\n\n"
                    f"🏢 <b>Компания/Город:</b> {html.escape(str(ticket['company_city'] or '—'))}\n"
                    f"🔧 <b>Станок:</b> {html.escape(str(ticket['machine_info'] or '—'))}\n"
                    f"📝 <b>Проблема:</b> {html.escape(str(ticket['problem'] or '—'))}\n"
                    f"📞 <b>Контакты:</b> {html.escape(str(ticket['contact'] or '—'))}"
                )
                # Уведомляем админов из .env и из БД
                admin_ids = set(ADMIN_IDS)
                try:
                    db_admins = await db.get_admins()
                    admin_ids.update(row['user_id'] for row in db_admins)
                except Exception as e:
                    logger.warning(f"Не удалось получить админов из БД для уведомления о просроченной заявке: {e}")

                for admin_id in admin_ids:
                    try:
                        await bot.send_message(admin_id, log_msg)
                    except Exception as e:
                        logger.warning(f"Не удалось уведомить админа {admin_id} о просроченной заявке #{ticket['id']}: {e}")
        except Exception as e:
            logger.error(f"Ошибка в фоновой задаче timeout-проверки: {e}")
        await asyncio.sleep(check_interval)


async def shutdown():
    """Корректное завершение работы бота."""
    logger.info("Бот останавливается...")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")
