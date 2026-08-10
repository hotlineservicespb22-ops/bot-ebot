import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent
import traceback
import html

from bot.config import BOT_TOKEN, DB_PATH, ADMIN_IDS
from bot.database import Database
from bot.middlewares import DbSessionMiddleware, RoleMiddleware
from bot.handlers import client, engineer, admin, relay

# Настройка логирования для отладки
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
# Включаем DEBUG-логи для aiogram, чтобы видеть все входящие события в консоли
logging.getLogger('aiogram.event').setLevel(logging.DEBUG)

# FSM timeout with MemoryStorage is complex and requires custom state tracking or a different storage backend.
# For this refactoring, we rely on explicit /cancel and inline cancel buttons.
async def main():
    # Database initialization
    db = Database(DB_PATH)
    await db.connect()
    await db.init_db()
    await db.migrate()

    # Bot and Dispatcher initialization
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # Error handler
    @dp.error()
    async def error_handler(event: ErrorEvent):
        """
        Global error handler. Catches all exceptions.
        The error `TypeError: ... missing 1 required positional argument: 'exception'`
        from your logs indicates an old handler signature was used. The correct aiogram 3
        signature is `(event: ErrorEvent)`, with the exception in `event.exception`.
        """
        logger.error(f"Критическая ошибка: {event.exception}", exc_info=True)
        if ADMIN_IDS:
            try:
                tb_formatted = traceback.format_exc()
                # Using HTML for safe formatting.
                error_message = (
                    f"<b>❌ Критическая ошибка</b>\n\n"
                    f"<b>Тип:</b> <code>{html.escape(type(event.exception).__name__)}</code>\n"
                    f"<b>Ошибка:</b> <code>{html.escape(str(event.exception))}</code>\n\n"
                    f"<b>Traceback:</b>\n<pre>{html.escape(tb_formatted)}</pre>"
                )
                for admin_id in ADMIN_IDS:
                    await bot.send_message(admin_id, error_message, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Не удалось уведомить администраторов об ошибке: {e}")
    # Register Middlewares
    dp.update.middleware(DbSessionMiddleware(db))
    dp.update.middleware(RoleMiddleware())

    # Register Routers (order matters for relay)
    dp.include_router(admin.router)
    dp.include_router(engineer.router)
    dp.include_router(client.router)
    dp.include_router(relay.router)

    try:
        logger.info("Бот сервисной службы успешно запущен...")
        await dp.start_polling(bot, skip_updates=True)
    finally:
        await db.close()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
