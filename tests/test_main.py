"""
Тесты для главного модуля бота (bot/main.py).

Покрывает:
- get_storage (MemoryStorage vs RedisStorage)
- setup_commands (установка команд)
- error_handler (глобальный обработчик ошибок)
- ticket_timeout_watcher (эскалация просроченных заявок)
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.main import (
    error_handler,
    get_all_admin_ids,
    get_storage,
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
            "company_city": "Москва",
            "machine_info": "Станок",
            "problem": "Поломка",
            "contact": "+7999",
        }

        with patch("bot.main.ADMIN_IDS", [123456789]), \
             patch.object(db, "get_expired_open_tickets", new=AsyncMock(return_value=[expired_ticket])), \
             patch("bot.main.TICKET_TIMEOUT", 5), \
             patch("asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
            with pytest.raises(asyncio.CancelledError):
                await ticket_timeout_watcher(mock_bot, db)
            mock_bot.send_message.assert_awaited()
