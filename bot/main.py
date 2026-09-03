import asyncio
import contextlib
import datetime
import hmac
import html
import logging
import os
import shutil
import signal
import sys
import traceback

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    ErrorEvent,
)
from aiohttp import web

from bot.config import (
    ADMIN_IDS,
    BOT_TOKEN,
    DB_PATH,
    FOLLOWUP_DELAY_DAYS,
    LOG_LEVEL,
    MANAGER_DASHBOARD_HOST,
    MANAGER_DASHBOARD_PORT,
    MEDIA_DIR,
    MEDIA_RETENTION_DAYS,
    REDIS_URL,
    TICKET_TIMEOUT,
    WEBHOOK_HOST,
    WEBHOOK_PATH,
    WEBHOOK_PORT,
    WEBHOOK_SECRET_TOKEN,
    WEBHOOK_URL,
)
from bot.database import Database
from bot.handlers import admin, client, engineer, manager, relay
from bot.keyboards import followup_kb
from bot.logging_config import setup_logging
from bot.middlewares import DbSessionMiddleware, RoleMiddleware, ThrottlingMiddleware
from bot.web_dashboard import create_app as create_dashboard_app

logger = logging.getLogger(__name__)
# При DEBUG видим все входящие события aiogram в консоли
if LOG_LEVEL == "DEBUG":
    logging.getLogger('aiogram.event').setLevel(logging.DEBUG)

def get_storage():
    """Возвращает хранилище FSM: RedisStorage, если задан REDIS_URL, иначе MemoryStorage."""
    if REDIS_URL:
        from aiogram.fsm.storage.redis import RedisStorage
        from redis import asyncio as aioredis
        redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        logger.info("Используется RedisStorage для FSM")
        return RedisStorage(redis=redis_client)
    logger.info(
        "Используется MemoryStorage для FSM. Состояния диалогов будут потеряны "
        "при перезапуске бота. Для продакшена рекомендуется задать REDIS_URL."
    )
    return MemoryStorage()

# Команды, доступные всем пользователям
DEFAULT_COMMANDS = [
    BotCommand(command="start", description="🔄 Запустить бота"),
    BotCommand(command="help", description="❓ Помощь"),
    BotCommand(command="cancel", description="❌ Отменить действие"),
]

# Команды, доступные только администраторам
ADMIN_COMMANDS = [
    BotCommand(command="admin", description="🛠 Админ-панель"),
    BotCommand(command="add_admin", description="➕ Добавить админа"),
    BotCommand(command="del_admin", description="➖ Удалить админа"),
    BotCommand(command="add_eng", description="➕ Добавить инженера"),
    BotCommand(command="del_eng", description="➖ Удалить инженера"),
    BotCommand(command="bulk_add_eng", description="📦 Массовое добавление инженеров"),
]

# Команды, доступные руководителям (дополнительно к общим)
MANAGER_COMMANDS = [
    BotCommand(command="manager", description="👔 Панель руководителя"),
]

async def get_all_admin_ids(db: Database) -> set:
    """
    Возвращает множество ID администраторов: из .env (ADMIN_IDS) и из БД.
    Ошибки БД не критичны — возвращаем хотя бы ADMIN_IDS из конфигурации.
    """
    admin_ids = set(ADMIN_IDS)
    try:
        db_admins = await db.get_admins()
        admin_ids.update(row['user_id'] for row in db_admins)
    except Exception as e:
        logger.warning(f"Не удалось получить админов из БД: {e}")
    return admin_ids


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

    # Команды для руководителей из .env (MANAGER_IDS) — админские + панель руководителя
    from bot.config import MANAGER_IDS
    for mgr_id in MANAGER_IDS:
        try:
            await bot.set_my_commands(
                DEFAULT_COMMANDS + ADMIN_COMMANDS + MANAGER_COMMANDS,
                scope=BotCommandScopeChat(chat_id=mgr_id)
            )
        except Exception as e:
            logger.warning(f"Не удалось установить команды для руководителя {mgr_id}: {e}")

    logger.info("Меню команд установлено (общие + админские + руководителя).")


async def error_handler(event: ErrorEvent, bot: Bot, db: Database):
    """
    Global error handler. Catches all exceptions.
    The error `TypeError: ... missing 1 required positional argument: 'exception'`
    from your logs indicates an old handler signature was used. The correct aiogram 3
    signature is `(event: ErrorEvent)`, with the exception in `event.exception`.
    """
    logger.error(f"Критическая ошибка: {event.exception}", exc_info=True)
    # Собираем админов из .env и из БД
    admin_ids = await get_all_admin_ids(db)

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
                try:
                    await bot.send_message(admin_id, error_message)
                except Exception as e:
                    logger.warning(f"Не удалось уведомить администратора {admin_id} о критической ошибке: {e}")
        except Exception as e:
            logger.error(f"Не удалось подготовить уведомление администраторов об ошибке: {e}")


async def main():
    # Инициализация логирования (консоль + файл с ротацией) из конфигурации
    setup_logging()

    # Database initialization
    db = Database(DB_PATH)
    await db.connect()
    await db.init_db()
    await db.migrate()
    # Сбрасываем «зависшие» сессии после перезапуска: если бот упал, пока заявка
    # была in_progress, старый session_started_at начислил бы часы простоя как работу.
    await db.reset_stale_sessions()

    # Bot and Dispatcher initialization
    # Глобальная настройка HTML-разметки: все сообщения по умолчанию парсятся как HTML
    # Ограничиваем таймаут HTTP-запросов к Telegram: если long-poll (getUpdates) «зависнет»
    # на сетевом уровне, aiohttp оборвёт запрос, aiogram залогирует ошибку и повторит
    # запрос с backoff — бот не останется навсегда без обновлений.
    session = AiohttpSession(timeout=30)
    bot = Bot(token=BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=get_storage())

    # Устанавливаем меню команд (общие + админские)
    await setup_commands(bot, db)

    # Error handler
    @dp.error()
    async def error_handler_wrapper(event: ErrorEvent, bot: Bot):
        await error_handler(event, bot, db)
    # Register Middlewares
    dp.update.middleware(DbSessionMiddleware(db))
    dp.update.middleware(RoleMiddleware())
    dp.update.middleware(ThrottlingMiddleware(interval=0.5))

    # Register Routers (order matters for relay)
    dp.include_router(admin.router)
    dp.include_router(engineer.router)
    dp.include_router(client.router)
    dp.include_router(manager.router)
    dp.include_router(relay.router)

    # Запускаем веб-дашборд руководителя в фоне
    dashboard_app = create_dashboard_app(db)
    dashboard_runner = web.AppRunner(dashboard_app)
    await dashboard_runner.setup()
    dashboard_site = web.TCPSite(
        dashboard_runner, MANAGER_DASHBOARD_HOST, MANAGER_DASHBOARD_PORT
    )
    await dashboard_site.start()
    logger.info(
        "Веб-дашборд руководителя запущен на http://%s:%s",
        MANAGER_DASHBOARD_HOST,
        MANAGER_DASHBOARD_PORT,
    )

    # Запускаем фоновую задачу контроля таймаутов заявок
    timeout_task = asyncio.create_task(ticket_timeout_watcher(bot, db))
    # Фоновая задача-«пульс»: регулярно обновляет .heartbeat. Внешний watchdog
    # (watchdog.sh) по этому файлу определяет, жив ли event loop, и при зависании
    # автоматически перезапускает контейнер.
    heartbeat_task = asyncio.create_task(heartbeat_writer())
    # Фоновая задача повторного опроса клиентов после завершения заявок.
    followup_task = asyncio.create_task(followup_watcher(bot, db))
    # Фоновая задача очистки старых медиафайлов (выключена по умолчанию, см. MEDIA_RETENTION_DAYS).
    media_cleanup_task = asyncio.create_task(media_cleanup_watcher(db))

    try:
        if WEBHOOK_URL:
            # Webhook-режим с аутентификацией
            from aiogram.webhook.aiohttp_server import (
                SimpleRequestHandler,
                setup_application,
            )
            from aiohttp.web import middleware

            await bot.set_webhook(
                f"{WEBHOOK_URL}{WEBHOOK_PATH}",
                secret_token=WEBHOOK_SECRET_TOKEN if WEBHOOK_SECRET_TOKEN else None,
            )
            app = web.Application()

            # Middleware для проверки секретного токена (если задан).
            # Telegram передаёт токен в заголовке X-Telegram-Bot-Api-Secret-Token.
            # Без этого любой, кто знает URL, может слать фейковые обновления боту.
            if WEBHOOK_SECRET_TOKEN:
                @middleware
                async def secret_token_middleware(request, handler):
                    header_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
                    if not hmac.compare_digest(header_token, WEBHOOK_SECRET_TOKEN):
                        logger.warning(
                            "Webhook: неверный или отсутствующий secret token "
                            "(запрос с %s)", request.remote
                        )
                        return web.Response(status=403, text="Forbidden")
                    return await handler(request)
                app.middlewares.append(secret_token_middleware)

            webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
            webhook_requests_handler.register(app, path=WEBHOOK_PATH)
            setup_application(app, dp, bot=bot)
            logger.info(f"Бот запущен через webhook: {WEBHOOK_URL}{WEBHOOK_PATH}"
                        f" (auth: {'включена' if WEBHOOK_SECRET_TOKEN else 'отключена'})")
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
        heartbeat_task.cancel()
        followup_task.cancel()
        media_cleanup_task.cancel()
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
            # Берём только просроченные заявки, по которым ещё не отправлялось уведомление
            # (дедупликация: не спамим админам повторно каждые N минут).
            expired = await db.get_expired_open_tickets_not_escalated(TICKET_TIMEOUT)
            for ticket in expired:
                log_msg = (
                    f"⏰ <b>Заявка #{ticket['id']} не взята в работу!</b>\n\n"
                    f"🏢 <b>Компания/Город:</b> {html.escape(str(ticket['company_city'] or '—'))}\n"
                    f"🔧 <b>Станок:</b> {html.escape(str(ticket['machine_info'] or '—'))}\n"
                    f"📝 <b>Проблема:</b> {html.escape(str(ticket['problem'] or '—'))}\n"
                    f"📞 <b>Контакты:</b> {html.escape(str(ticket['contact'] or '—'))}"
                )
                # Уведомляем админов из .env и из БД
                admin_ids = await get_all_admin_ids(db)

                for admin_id in admin_ids:
                    try:
                        await bot.send_message(admin_id, log_msg)
                    except Exception as e:
                        logger.warning(f"Не удалось уведомить админа {admin_id} о просроченной заявке #{ticket['id']}: {e}")

                # Уведомляем самого клиента о том, что заявка ещё не принята в работу
                if ticket['client_id']:
                    try:
                        await bot.send_message(
                            ticket['client_id'],
                            f"⏰ <b>Ваша заявка #{ticket['id']} пока не принята в работу.</b>\n\n"
                            "Дежурный инженер скоро подключится. Если ситуация срочная — позвоните "
                            "по телефону <b>8 800 777-38-56</b>."
                        )
                    except Exception as e:
                        logger.warning(f"Не удалось уведомить клиента {ticket['client_id']} о просрочке заявки #{ticket['id']}: {e}")

                # Помечаем заявку как эскалированную, чтобы не уведомлять повторно
                await db.mark_ticket_escalated(ticket['id'])
        except Exception as e:
            logger.error(f"Ошибка в фоновой задаче timeout-проверки: {e}")
        await asyncio.sleep(check_interval)


async def run_followup_check(bot: Bot, db: Database) -> None:
    """Один проход follow-up: рассылает опросы по завершённым заявкам.

    Выделен отдельно от цикла, чтобы его можно было тестировать без реального ожидания.
    """
    tickets = await db.get_tickets_for_followup(FOLLOWUP_DELAY_DAYS)
    for ticket in tickets:
        if not ticket['client_id']:
            logger.error("Followup: у заявки #%s нет client_id — опрос пропущен", ticket['id'])
            continue
        # Проставляем ПЕРЕД отправкой — см. комментарий в docstring followup_watcher.
        await db.mark_followup_sent(ticket['id'])
        try:
            await bot.send_message(
                ticket['client_id'],
                f"👋 <b>Здравствуйте!</b>\n\n"
                f"Недавно мы завершили вашу заявку #{ticket['id']}. "
                "Проверьте, пожалуйста, как работает оборудование:",
                reply_markup=followup_kb(ticket['id']),
            )
        except Exception as e:
            logger.error(
                "Не удалось отправить повторный опрос клиенту %s по заявке #%s: %s",
                ticket['client_id'], ticket['id'], e,
            )


async def followup_watcher(bot: Bot, db: Database):
    """Фоновая задача: раз в час рассылает повторные опросы по завершённым заявкам.

    Опрашиваются завершённые заявки старше FOLLOWUP_DELAY_DAYS без followup_sent_at.
    Факт отправки проставляется ДО вызова send_message, чтобы:
      - заблокировавший бота клиент не вызывал бесконечный retry каждый час;
      - сбой сети не приводил к повторным отправкам (идемпотентность при рестарте).
    При ошибке отправки факт логируется на уровне ERROR — данные не теряются молча.
    """
    FOLLOWUP_INTERVAL = 3600  # 1 час
    while True:
        try:
            await run_followup_check(bot, db)
        except Exception as e:
            logger.error(f"Ошибка в фоновой задаче followup-опроса: {e}")
        await asyncio.sleep(FOLLOWUP_INTERVAL)


async def run_media_cleanup(db: Database) -> None:
    """Один проход очистки: удаляет media/ticket_<id>/ для давно закрытых заявок.

    Выделен отдельно от цикла, чтобы его можно было тестировать без реального ожидания.
    Ничего не делает, если MEDIA_RETENTION_DAYS <= 0 (выключено по умолчанию).
    """
    if MEDIA_RETENTION_DAYS <= 0:
        return
    ticket_ids = await db.get_ticket_ids_for_media_cleanup(MEDIA_RETENTION_DAYS)
    for ticket_id in ticket_ids:
        ticket_dir = os.path.join(MEDIA_DIR, f"ticket_{ticket_id}")
        if not os.path.isdir(ticket_dir):
            continue
        try:
            await asyncio.to_thread(shutil.rmtree, ticket_dir)
            logger.info("Удалены локальные медиафайлы заявки #%s (%s)", ticket_id, ticket_dir)
        except OSError as e:
            logger.error("Не удалось удалить медиафайлы заявки #%s (%s): %s", ticket_id, ticket_dir, e)


async def media_cleanup_watcher(db: Database):
    """Фоновая задача: раз в сутки удаляет локальные медиафайлы давно закрытых заявок.

    Выключена по умолчанию (MEDIA_RETENTION_DAYS=0) — это необратимая операция,
    включать нужно осознанно (см. комментарий у MEDIA_RETENTION_DAYS в config.py).
    """
    CLEANUP_INTERVAL = 86400  # 1 сутки
    while True:
        try:
            await run_media_cleanup(db)
        except Exception as e:
            logger.error(f"Ошибка в фоновой задаче очистки медиафайлов: {e}")
        await asyncio.sleep(CLEANUP_INTERVAL)


def _write_heartbeat(filepath: str, data: str) -> None:
    """Write heartbeat timestamp to file (runs in thread to avoid I/O blocking)."""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(data)


async def heartbeat_writer():
    """
    Периодически пишет текущее время (UTC) в файл .heartbeat.

    Файл служит «пульсом» бота: даже если входящих сообщений нет, задача
    продолжает обновлять файл, пока работает event loop. Внешний watchdog
    (watchdog.sh) перезапускает контейнер, если heartbeat не обновлялся дольше
    заданного лимита (по умолчанию 120 секунд).
    """
    heartbeat_file = os.getenv("HEARTBEAT_FILE", ".heartbeat")
    try:
        interval = int(os.getenv("HEARTBEAT_INTERVAL", "5"))
    except ValueError:
        interval = 5
    while True:
        try:
            content = datetime.datetime.now(datetime.timezone.utc).isoformat()
            await asyncio.to_thread(_write_heartbeat, heartbeat_file, content)
        except OSError as e:
            logger.warning(f"Heartbeat: не удалось записать {heartbeat_file}: {e}")
        await asyncio.sleep(interval)


async def shutdown():
    """Корректное завершение работы бота."""
    logger.info("Бот останавливается...")

def _handle_signal():
    """Обработчик сигналов завершения: прерывает основной event loop."""
    logger.info("Получен сигнал завершения — останавливаем бота...")
    # Прерываем главную задачу (main) через отмену всех задач текущего loop
    for task in asyncio.all_tasks():
        task.cancel()


if __name__ == '__main__':
    try:
        # Graceful shutdown по SIGINT/SIGTERM (недоступно в Windows)
        if sys.platform != "win32":
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError):
                    loop.add_signal_handler(sig, _handle_signal)
            try:
                loop.run_until_complete(main())
            finally:
                loop.close()
        else:
            # Windows не поддерживает add_signal_handler — используем asyncio.run
            asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")
    except asyncio.CancelledError:
        print("Бот остановлен по сигналу")
