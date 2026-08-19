"""
Тесты для главного модуля бота (bot/main.py).

Покрывает:
- get_storage (MemoryStorage vs RedisStorage)
- setup_commands (установка команд)
- error_handler (глобальный обработчик ошибок)
- ticket_timeout_watcher (эскалация просроченных заявок)
"""
import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.main import (
    error_handler,
    get_all_admin_ids,
    get_storage,
    main,
    setup_commands,
    ticket_timeout_watcher,
)


@pytest.fixture
def mock_bot():
    """Создаёт мокированный Bot."""
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    bot.set_my_commands = AsyncMock()
    bot.set_webhook = AsyncMock()
    bot.get_me = AsyncMock(return_value=MagicMock(id=123))
    return bot


class TestGetStorage:
    def test_memory_storage_when_no_redis(self):
        with patch("bot.main.REDIS_URL", ""):
            storage = get_storage()
            assert storage.__class__.__name__ == "MemoryStorage"

    def test_redis_storage_when_redis_configured(self):
        with patch("bot.main.REDIS_URL", "redis://localhost:6379/0"), \
             patch("aiogram.fsm.storage.redis.RedisStorage") as MockRedisStorage, \
             patch("redis.asyncio.from_url") as mock_from_url:
            mock_redis = MagicMock()
            mock_from_url.return_value = mock_redis
            MockRedisStorage.return_value = MagicMock()
            storage = get_storage()
            assert storage is MockRedisStorage.return_value
            mock_from_url.assert_called_once_with("redis://localhost:6379/0", decode_responses=True)


class TestSetupCommands:
    async def test_sets_default_commands(self, mock_bot, db):
        with patch("bot.main.ADMIN_IDS", []), \
             patch("bot.main.DEFAULT_COMMANDS", [MagicMock()]), \
             patch("bot.main.ADMIN_COMMANDS", [MagicMock()]):
            await setup_commands(mock_bot, db)
            mock_bot.set_my_commands.assert_awaited()

    async def test_sets_admin_commands_for_env_admins(self, mock_bot, db):
        with patch("bot.main.ADMIN_IDS", [99999]), \
             patch("bot.main.DEFAULT_COMMANDS", [MagicMock()]), \
             patch("bot.main.ADMIN_COMMANDS", [MagicMock()]):
            await setup_commands(mock_bot, db)
            # Проверяем, что вызывался set_my_commands для админа
            assert mock_bot.set_my_commands.await_count >= 2

    async def test_sets_admin_commands_for_db_admins(self, mock_bot, db):
        await db.add_admin(123456789)
        with patch("bot.main.ADMIN_IDS", []):
            await setup_commands(mock_bot, db)
            # set_my_commands вызывался и для default, и для db-админа
            assert mock_bot.set_my_commands.await_count >= 2


class TestGetAllAdminIds:
    async def test_combines_env_and_db_admins(self, db):
        await db.add_admin(111)
        with patch("bot.main.ADMIN_IDS", [222]):
            result = await get_all_admin_ids(db)
            assert 111 in result
            assert 222 in result

    async def test_returns_env_admins_on_db_error(self, mock_bot):
        bad_db = AsyncMock()
        bad_db.get_admins = AsyncMock(side_effect=Exception("db error"))
        with patch("bot.main.ADMIN_IDS", [333]):
            result = await get_all_admin_ids(bad_db)
            assert 333 in result


class TestErrorHandler:
    async def test_sends_error_to_admins(self, mock_bot, db):
        from aiogram.types import ErrorEvent

        error_event = MagicMock(spec=ErrorEvent)
        error_event.exception = ValueError("test error")
        error_event.exception.__traceback__ = None

        with patch("bot.main.ADMIN_IDS", [123456789]):
            await error_handler(error_event, mock_bot, db)
            mock_bot.send_message.assert_awaited()


class TestTicketTimeoutWatcher:
    async def test_escalates_expired_tickets(self, mock_bot, db):
        # Создаём просроченную заявку
        ticket_id = await db.create_ticket(
            client_id=123456789,
            client_name="Иван",
            company="ООО",
            equipment_type="станок",
            brand="Wattsan",
            cnc_model="1610",
            problem="Проблема",
            media_id=None,
            city="Москва",
            inn_contract="123",
            contact="+7999",
        )

        # Симулируем просроченную заявку
        expired_ticket = {
            "id": ticket_id,
            "client_id": 123456789,
            "company_city": "Москва",
            "machine_info": "Станок",
            "problem": "Поломка",
            "contact": "+7999",
        }

        # Мокаем метод с дедупликацией и прерываем после одного прохода
        with patch("bot.main.ADMIN_IDS", [123456789]), \
             patch.object(db, "get_expired_open_tickets_not_escalated", new=AsyncMock(return_value=[expired_ticket])), \
             patch.object(db, "mark_ticket_escalated", new=AsyncMock()) as mock_escalate, \
             patch("bot.main.TICKET_TIMEOUT", 5), \
             patch("asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
            with pytest.raises(asyncio.CancelledError):
                await ticket_timeout_watcher(mock_bot, db)
            # Уведомление отправлено
            mock_bot.send_message.assert_awaited()
            # И заявка помечена как эскалированная (дедупликация)
            mock_escalate.assert_awaited_once_with(ticket_id)

    async def test_does_not_re_notify_already_escalated(self, mock_bot, db):
        """Проверяет, что уже эскалированные заявки не уведомляются повторно."""
        with patch("bot.main.ADMIN_IDS", [123456789]), \
             patch.object(db, "get_expired_open_tickets_not_escalated", new=AsyncMock(return_value=[])), \
             patch("bot.main.TICKET_TIMEOUT", 5), \
             patch("asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
            with pytest.raises(asyncio.CancelledError):
                await ticket_timeout_watcher(mock_bot, db)
            mock_bot.send_message.assert_not_awaited()


class TestErrorHandlerWithTraceback:
    async def test_sends_error_with_traceback(self, mock_bot, db):
        """Проверяет формирование traceback в уведомлении об ошибке."""
        from aiogram.types import ErrorEvent

        error_event = MagicMock(spec=ErrorEvent)
        try:
            raise ValueError("тестовая ошибка")
        except ValueError as exc:
            error_event.exception = exc

        with patch("bot.main.ADMIN_IDS", [123456789]):
            await error_handler(error_event, mock_bot, db)
            mock_bot.send_message.assert_awaited()
            sent_text = mock_bot.send_message.call_args[0][1]
            assert "ValueError" in sent_text  # тип ошибки в HTML-тегах <code>
            assert "Критическая ошибка" in sent_text


class TestMainWebhook:
    async def test_webhook_branch_runs(self):
        """Проверяет, что webhook-ветка main() запускает aiohttp-сервер."""
        # Мокаем DB
        mock_db = AsyncMock()
        mock_db.connect = AsyncMock()
        mock_db.init_db = AsyncMock()
        mock_db.migrate = AsyncMock()
        mock_db.close = AsyncMock()
        mock_db.get_admins = AsyncMock(return_value=[])

        # Мокаем Bot и Dispatcher
        mock_bot = AsyncMock()
        mock_bot.set_webhook = AsyncMock()
        mock_bot.delete_webhook = AsyncMock()
        mock_bot.session = AsyncMock()
        mock_bot.session.close = AsyncMock()

        mock_dp = AsyncMock()
        mock_dp.start_polling = AsyncMock()
        # dp.error() используется как декоратор — должен возвращать callable-декоратор
        mock_dp.error = MagicMock(return_value=lambda f: f)
        # update.middleware / include_router — синхронные цепочки, не должны быть корутинами
        mock_dp.update = MagicMock()
        mock_dp.include_router = MagicMock()
        mock_dp.update.middleware = MagicMock()

        fake_event = asyncio.Event()
        fake_event.wait = AsyncMock(side_effect=KeyboardInterrupt)

        # Мокаем aiohttp-компоненты, чтобы не поднимать реальный сервер
        mock_runner = AsyncMock()
        mock_site = AsyncMock()
        mock_site.start = AsyncMock()
        mock_app = MagicMock()
        mock_handler = MagicMock()

        with patch("bot.main.Database", return_value=mock_db), \
             patch("bot.main.Bot", return_value=mock_bot), \
             patch("bot.main.Dispatcher", return_value=mock_dp), \
             patch("bot.main.WEBHOOK_URL", "https://example.com"), \
             patch("bot.main.WEBHOOK_PATH", "/webhook"), \
             patch("bot.main.WEBHOOK_HOST", "0.0.0.0"), \
             patch("bot.main.WEBHOOK_PORT", 8080), \
             patch("bot.main.get_storage", return_value=MagicMock()), \
             patch("bot.main.setup_commands", new=AsyncMock()), \
             patch("bot.main.setup_logging", new=MagicMock()), \
             patch("asyncio.Event", return_value=fake_event), \
             patch("aiogram.webhook.aiohttp_server.SimpleRequestHandler", return_value=mock_handler), \
             patch("aiogram.webhook.aiohttp_server.setup_application", return_value=None), \
             patch("aiohttp.web.Application", return_value=mock_app), \
             patch("aiohttp.web.AppRunner", return_value=mock_runner), \
             patch("aiohttp.web.TCPSite", return_value=mock_site):
            # Имитируем выход из цикла через KeyboardInterrupt после старта сайта
            mock_runner.setup = AsyncMock()
            mock_runner.start = AsyncMock()
            mock_handler.register = MagicMock()

            with patch("bot.main.shutdown", new=AsyncMock()):
                # Основной цикл уйдёт в asyncio.Event().wait(), который сразу бросит KeyboardInterrupt
                with contextlib.suppress(KeyboardInterrupt):
                    await main()
                # # Ожидаемое прерывание цикла webhook-сервера (принято)

            mock_bot.set_webhook.assert_awaited_once()
            mock_bot.delete_webhook.assert_awaited_once()
            # Теперь два TCPSite: дашборд на MANAGER_DASHBOARD_PORT + webhook на WEBHOOK_PORT
            assert mock_site.start.await_count == 2
            mock_db.close.assert_awaited_once()
            mock_bot.session.close.assert_awaited_once()
