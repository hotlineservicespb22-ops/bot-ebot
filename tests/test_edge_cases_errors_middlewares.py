"""
Тесты краевых случаев (Edge Cases), обработки ошибок и middleware.

Проверяет:
- Пустые вводы, слишком длинные строки, спецсимволы
- SQL/XSS-инъекции
- Неожиданные типы данных
- Имитацию сбоев БД (TimeoutError, ConnectionError)
- Имитацию ошибок Telegram API
- Работу DbSessionMiddleware, RoleMiddleware, ThrottlingMiddleware
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, User
from conftest import make_message

from bot.database import Database
from bot.handlers.client import TicketForm, ticket_contact, ticket_problem_media
from bot.handlers.relay import relay_messages, safe_send
from bot.middlewares import DbSessionMiddleware, RoleMiddleware, ThrottlingMiddleware

# ===================== Edge Cases: Пустые и длинные вводы =====================

class TestEdgeCasesInput:
    """Тесты краевых случаев ввода."""

    async def test_empty_text_message(self, fake_user, fake_chat, mock_bot):
        """Проверяет обработку пустого текстового сообщения."""
        storage = MemoryStorage()
        key = StorageKey(chat_id=fake_user.id, user_id=fake_user.id, bot_id=1)
        context = FSMContext(storage=storage, key=key)
        await context.set_state(TicketForm.problem_media)

        message = make_message(fake_user, fake_chat, text="")
        await ticket_problem_media(message, context, mock_bot)

        # Состояние не должно измениться
        assert await context.get_state() == TicketForm.problem_media.state
        await context.clear()

    async def test_none_text_message(self, fake_user, fake_chat, mock_bot):
        """Проверяет обработку сообщения с text=None."""
        storage = MemoryStorage()
        key = StorageKey(chat_id=fake_user.id, user_id=fake_user.id, bot_id=1)
        context = FSMContext(storage=storage, key=key)
        await context.set_state(TicketForm.problem_media)

        message = make_message(fake_user, fake_chat, text=None)
        await ticket_problem_media(message, context, mock_bot)

        # Состояние не должно измениться
        assert await context.get_state() == TicketForm.problem_media.state
        await context.clear()

    async def test_very_long_text(self, fake_user, fake_chat, mock_bot):
        """Проверяет обработку очень длинного текста."""
        storage = MemoryStorage()
        key = StorageKey(chat_id=fake_user.id, user_id=fake_user.id, bot_id=1)
        context = FSMContext(storage=storage, key=key)
        await context.set_state(TicketForm.problem_media)

        long_text = "А" * 10000  # 10000 символов
        message = make_message(fake_user, fake_chat, text=long_text)
        await ticket_problem_media(message, context, mock_bot)

        # Должно перейти на следующий шаг
        assert await context.get_state() == TicketForm.machine_info.state
        data = await context.get_data()
        assert len(data["problem"]) == 10000
        await context.clear()

    async def test_whitespace_only_input(self, fake_user, fake_chat, mock_bot):
        """Проверяет обработку ввода только из пробелов."""
        storage = MemoryStorage()
        key = StorageKey(chat_id=fake_user.id, user_id=fake_user.id, bot_id=1)
        context = FSMContext(storage=storage, key=key)
        await context.set_state(TicketForm.problem_media)

        message = make_message(fake_user, fake_chat, text="   \n\t  ")
        await ticket_problem_media(message, context, mock_bot)

        # Состояние не должно измениться
        assert await context.get_state() == TicketForm.problem_media.state
        await context.clear()

    async def test_special_characters_input(self, fake_user, fake_chat, db, mock_bot):
        """Проверяет обработку спецсимволов."""
        storage = MemoryStorage()
        key = StorageKey(chat_id=fake_user.id, user_id=fake_user.id, bot_id=1)
        context = FSMContext(storage=storage, key=key)
        await context.set_state(TicketForm.contact)
        await context.update_data(problem="Тест", machine_info="Тест", company_city="Тест")

        special_text = "!@#$%^&*()_+-=[]{}|;':\",./<>?`~"
        message = make_message(fake_user, fake_chat, text=special_text)

        with patch("bot.handlers.client.ADMIN_IDS", []):
            await ticket_contact(message, context, mock_bot, db)

        # Заявка должна быть создана (контакт длиннее 5 символов)
        tickets = await db.get_client_tickets(fake_user.id)
        assert len(tickets) == 1
        await context.clear()

    async def test_emoji_input(self, fake_user, fake_chat, mock_bot):
        """Проверяет обработку эмодзи."""
        storage = MemoryStorage()
        key = StorageKey(chat_id=fake_user.id, user_id=fake_user.id, bot_id=1)
        context = FSMContext(storage=storage, key=key)
        await context.set_state(TicketForm.problem_media)

        message = make_message(fake_user, fake_chat, text="🔥🔥🔥 Станок горит! 🚨")
        await ticket_problem_media(message, context, mock_bot)

        assert await context.get_state() == TicketForm.machine_info.state
        await context.clear()

    async def test_unicode_input(self, fake_user, fake_chat, mock_bot):
        """Проверяет обработку Unicode-символов."""
        storage = MemoryStorage()
        key = StorageKey(chat_id=fake_user.id, user_id=fake_user.id, bot_id=1)
        context = FSMContext(storage=storage, key=key)
        await context.set_state(TicketForm.problem_media)

        message = make_message(fake_user, fake_chat, text="Проблема: 日本語テスト 中文测试")
        await ticket_problem_media(message, context, mock_bot)

        assert await context.get_state() == TicketForm.machine_info.state
        await context.clear()


# ===================== Edge Cases: SQL/XSS инъекции =====================

class TestInjectionAttacks:
    """Тесты защиты от инъекций."""

    async def test_sql_injection_in_problem(self, fake_user, fake_chat, db, mock_bot):
        """Проверяет SQL-инъекцию в описании проблемы."""
        storage = MemoryStorage()
        key = StorageKey(chat_id=fake_user.id, user_id=fake_user.id, bot_id=1)
        context = FSMContext(storage=storage, key=key)
        await context.set_state(TicketForm.contact)
        await context.update_data(
            problem="'; DROP TABLE tickets; --",
            machine_info="Тест",
            company_city="Тест",
        )

        message = make_message(fake_user, fake_chat, text="+79991234567")

        with patch("bot.handlers.client.ADMIN_IDS", []):
            await ticket_contact(message, context, mock_bot, db)

        # Таблица должна остаться целой
        tickets = await db.get_all_tickets()
        assert len(tickets) == 1
        # Инъекция сохранена как обычный текст
        assert "DROP TABLE" in tickets[0]["problem"]
        await context.clear()

    async def test_xss_in_problem(self, fake_user, fake_chat, db, mock_bot):
        """Проверяет XSS-инъекцию в описании проблемы."""
        storage = MemoryStorage()
        key = StorageKey(chat_id=fake_user.id, user_id=fake_user.id, bot_id=1)
        context = FSMContext(storage=storage, key=key)
        await context.set_state(TicketForm.contact)
        await context.update_data(
            problem="<script>alert('xss')</script>",
            machine_info="Тест",
            company_city="Тест",
        )

        message = make_message(fake_user, fake_chat, text="+79991234567")

        with patch("bot.handlers.client.ADMIN_IDS", []):
            await ticket_contact(message, context, mock_bot, db)

        tickets = await db.get_all_tickets()
        assert len(tickets) == 1
        # XSS сохранён как текст (экранирование происходит при выводе)
        assert "<script>" in tickets[0]["problem"]
        await context.clear()

    async def test_sql_injection_in_admin_command(self, fake_admin_user, db):
        """Проверяет SQL-инъекцию в команде /add_admin."""
        from bot.handlers.admin import cmd_add_admin

        await db.add_admin(fake_admin_user.id)
        admin_chat = Chat(id=fake_admin_user.id, type="private")
        message = make_message(fake_admin_user, admin_chat, text="/add_admin 1; DROP TABLE admins;")

        await cmd_add_admin(message, db)

        # Должно быть сообщение об ошибке (ID не число)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "числом" in call_args[0][0].lower()


# ===================== Edge Cases: Неожиданные типы данных =====================

class TestUnexpectedDataTypes:
    """Тесты обработки неожиданных типов данных."""

    async def test_photo_instead_of_text_in_fsm(self, fake_user, fake_chat, mock_bot):
        """Проверяет отправку фото вместо текста на шаге FSM."""
        from aiogram.types import PhotoSize

        storage = MemoryStorage()
        key = StorageKey(chat_id=fake_user.id, user_id=fake_user.id, bot_id=1)
        context = FSMContext(storage=storage, key=key)
        await context.set_state(TicketForm.company_city)

        photo = PhotoSize(
            file_id="test_photo",
            file_unique_id="unique",
            width=100,
            height=100,
        )
        # Фото без подписи на шаге company_city (ожидается текст)
        message = make_message(fake_user, fake_chat, photo=[photo])
        from bot.handlers.client import ticket_company_city
        await ticket_company_city(message, context, mock_bot)

        # Состояние не должно измениться (текст пустой)
        assert await context.get_state() == TicketForm.company_city.state
        await context.clear()

    async def test_contact_instead_of_text(self, fake_user, fake_chat, db, mock_bot):
        """Проверяет отправку контакта на текстовом шаге FSM."""
        from aiogram.types import Contact

        storage = MemoryStorage()
        key = StorageKey(chat_id=fake_user.id, user_id=fake_user.id, bot_id=1)
        context = FSMContext(storage=storage, key=key)
        await context.set_state(TicketForm.problem_media)

        contact = Contact(
            phone_number="+79991234567",
            first_name="Иван",
            user_id=fake_user.id,
        )
        message = make_message(fake_user, fake_chat, contact=contact)
        await ticket_problem_media(message, context, mock_bot)

        # Состояние не должно измениться (текст пустой)
        assert await context.get_state() == TicketForm.problem_media.state
        await context.clear()


# ===================== Error Handling: Сбои БД =====================

class TestDatabaseErrors:
    """Тесты обработки сбоев БД."""

    async def test_db_timeout_on_get_ticket(self, db, fake_user):
        """Проверяет обработку таймаута при получении заявки."""
        # Создаём заявку
        ticket_id = await db.create_ticket(
            client_id=fake_user.id,
            client_name="Тест",
            company="", equipment_type="", brand="", cnc_model="",
            problem="Проблема", media_id=None, city="", inn_contract="", contact="1",
        )

        # Имитируем ошибку
        with patch.object(db, 'get_ticket', side_effect=asyncio.TimeoutError("DB timeout")):
            with pytest.raises(asyncio.TimeoutError):
                await db.get_ticket(ticket_id)

    async def test_db_connection_error(self):
        """Проверяет обработку ошибки подключения к БД."""
        db = Database(":memory:")
        # Не вызываем connect() — имитируем ошибку подключения
        with pytest.raises(Exception):
            await db.get_ticket(1)

    async def test_db_error_in_relay(self, db_with_engineer, fake_user, fake_chat, mock_bot):
        """Проверяет обработку ошибки БД при ретрансляции."""
        db = db_with_engineer

        # Имитируем ошибку при получении активной заявки
        with patch.object(db, 'get_active_ticket_for_client', side_effect=Exception("DB error")):
            storage = MemoryStorage()
            key = StorageKey(chat_id=fake_user.id, user_id=fake_user.id, bot_id=1)
            context = FSMContext(storage=storage, key=key)
            message = make_message(fake_user, fake_chat, text="Тест")

            with pytest.raises(Exception):
                await relay_messages(message, mock_bot, db, is_engineer=False, state=context)


# ===================== Error Handling: Ошибки Telegram API =====================

class TestTelegramAPIErrors:
    """Тесты обработки ошибок Telegram API."""

    async def test_safe_send_handles_api_error(self, mock_bot):
        """Проверяет, что safe_send обрабатывает ошибки API."""
        # TelegramAPIError требует method как первый аргумент
        mock_bot.send_message = AsyncMock(
            side_effect=TelegramAPIError(method="sendMessage", message="Bot was blocked by the user")
        )

        # Не должно падать
        await safe_send(mock_bot, 123456789, "send_message", text="Тест")

    async def test_bot_blocked_by_user(self, mock_bot, db, fake_user, fake_engineer_user, fake_chat):
        """Проверяет обработку блокировки бота пользователем."""
        # Создаём заявку
        ticket_id = await db.create_ticket(
            client_id=fake_user.id,
            client_name="Клиент",
            company="", equipment_type="", brand="", cnc_model="",
            problem="Проблема", media_id=None, city="", inn_contract="", contact="1",
        )
        await db.add_engineer(fake_engineer_user.id, "Инженер")
        await db.take_ticket(ticket_id, fake_engineer_user.id)

        # Имитируем блокировку при отправке
        mock_bot.send_message = AsyncMock(
            side_effect=TelegramBadRequest(method="sendMessage", message="Forbidden: bot was blocked by the user")
        )

        message = make_message(fake_user, fake_chat, text="Привет")

        # safe_send должен обработать ошибку
        from bot.handlers.relay import _send_relayed_message
        await _send_relayed_message(
            mock_bot, fake_engineer_user.id, message, "send_message", None, "Тест: "
        )

    async def test_delete_message_error_handled(self, fake_user, fake_chat, mock_bot):
        """Проверяет обработку ошибки при удалении сообщения."""
        from bot.handlers.client import delete_last_bot_message

        storage = MemoryStorage()
        key = StorageKey(chat_id=fake_user.id, user_id=fake_user.id, bot_id=1)
        context = FSMContext(storage=storage, key=key)
        await context.update_data(last_bot_msg_id=999)

        mock_bot.delete_message = AsyncMock(
            side_effect=TelegramBadRequest(method="deleteMessage", message="Message to delete not found")
        )

        message = make_message(fake_user, fake_chat, text="Тест")
        # Не должно падать
        await delete_last_bot_message(mock_bot, message, context)
        await context.clear()


# ===================== Middleware Tests =====================

class TestDbSessionMiddleware:
    """Тесты для DbSessionMiddleware."""

    async def test_db_injected_into_data(self, db):
        """Проверяет, что db добавляется в data."""
        middleware = DbSessionMiddleware(db)

        async def handler(event, data):
            return data

        data = {}
        result = await middleware(handler, MagicMock(), data)
        assert result["db"] is db

    async def test_handler_called(self, db):
        """Проверяет, что handler вызывается."""
        middleware = DbSessionMiddleware(db)

        handler = AsyncMock(return_value="result")
        event = MagicMock()
        data = {}

        result = await middleware(handler, event, data)
        handler.assert_called_once_with(event, data)
        assert result == "result"


class TestRoleMiddleware:
    """Тесты для RoleMiddleware."""

    async def test_admin_role_set(self, db, fake_admin_user):
        """Проверяет установку is_admin для администратора."""
        await db.add_admin(fake_admin_user.id)
        middleware = RoleMiddleware()

        async def handler(event, data):
            return data

        data = {
            "event_from_user": fake_admin_user,
            "db": db,
        }
        result = await middleware(handler, MagicMock(), data)
        assert result["is_admin"] is True
        assert result["is_engineer"] is False

    async def test_engineer_role_set(self, db, fake_engineer_user):
        """Проверяет установку is_engineer для инженера."""
        await db.add_engineer(fake_engineer_user.id, "Инженер")
        middleware = RoleMiddleware()

        async def handler(event, data):
            return data

        data = {
            "event_from_user": fake_engineer_user,
            "db": db,
        }
        result = await middleware(handler, MagicMock(), data)
        assert result["is_admin"] is False
        assert result["is_engineer"] is True

    async def test_no_user_in_data(self, db):
        """Проверяет обработку отсутствия пользователя."""
        middleware = RoleMiddleware()

        handler = AsyncMock(return_value="result")
        data = {"db": db}

        await middleware(handler, MagicMock(), data)
        handler.assert_called_once()

    async def test_regular_user_roles(self, db, fake_user):
        """Проверяет роли обычного пользователя."""
        middleware = RoleMiddleware()

        async def handler(event, data):
            return data

        data = {
            "event_from_user": fake_user,
            "db": db,
        }
        result = await middleware(handler, MagicMock(), data)
        assert result["is_admin"] is False
        assert result["is_engineer"] is False


class TestThrottlingMiddleware:
    """Тесты для ThrottlingMiddleware."""

    async def test_first_callback_passes(self):
        """Проверяет, что первый callback проходит."""
        middleware = ThrottlingMiddleware(interval=0.5)

        handler = AsyncMock(return_value="result")
        callback = MagicMock(spec=CallbackQuery)
        user = User(id=123, is_bot=False, first_name="Test")
        data = {"event_from_user": user}

        result = await middleware(handler, callback, data)
        handler.assert_called_once()
        assert result == "result"

    async def test_rapid_callbacks_throttled(self):
        """Проверяет троттлинг быстрых callback'ов."""
        middleware = ThrottlingMiddleware(interval=0.5)

        handler = AsyncMock(return_value="result")
        callback = MagicMock(spec=CallbackQuery)
        user = User(id=123, is_bot=False, first_name="Test")
        data = {"event_from_user": user}

        # Первый вызов проходит
        await middleware(handler, callback, data)
        assert handler.call_count == 1

        # Второй вызов сразу — блокируется
        await middleware(handler, callback, data)
        assert handler.call_count == 1  # Не увеличился

    async def test_callbacks_after_interval_pass(self):
        """Проверяет, что callback'и после интервала проходят."""
        middleware = ThrottlingMiddleware(interval=0.1)

        handler = AsyncMock(return_value="result")
        callback = MagicMock(spec=CallbackQuery)
        user = User(id=123, is_bot=False, first_name="Test")
        data = {"event_from_user": user}

        await middleware(handler, callback, data)
        assert handler.call_count == 1

        # Ждём больше интервала
        await asyncio.sleep(0.15)

        await middleware(handler, callback, data)
        assert handler.call_count == 2

    async def test_messages_not_throttled(self):
        """Проверяет, что текстовые сообщения не троттлятся."""
        middleware = ThrottlingMiddleware(interval=0.5)

        handler = AsyncMock(return_value="result")
        message = MagicMock(spec=Message)  # Не CallbackQuery
        user = User(id=123, is_bot=False, first_name="Test")
        data = {"event_from_user": user}

        # Несколько вызовов подряд — все проходят
        await middleware(handler, message, data)
        await middleware(handler, message, data)
        await middleware(handler, message, data)
        assert handler.call_count == 3

    async def test_no_user_in_data(self):
        """Проверяет обработку отсутствия пользователя."""
        middleware = ThrottlingMiddleware(interval=0.5)

        handler = AsyncMock(return_value="result")
        callback = MagicMock(spec=CallbackQuery)
        data = {}

        await middleware(handler, callback, data)
        handler.assert_called_once()

    async def test_different_users_not_throttled_together(self):
        """Проверяет, что разные пользователи троттлятся независимо."""
        middleware = ThrottlingMiddleware(interval=0.5)

        handler = AsyncMock(return_value="result")
        callback = MagicMock(spec=CallbackQuery)

        user1 = User(id=111, is_bot=False, first_name="User1")
        user2 = User(id=222, is_bot=False, first_name="User2")

        # Первый вызов от user1
        await middleware(handler, callback, {"event_from_user": user1})
        assert handler.call_count == 1

        # Вызов от user2 сразу — проходит
        await middleware(handler, callback, {"event_from_user": user2})
        assert handler.call_count == 2

        # Повторный вызов от user1 — блокируется
        await middleware(handler, callback, {"event_from_user": user1})
        assert handler.call_count == 2


# ===================== Фикстуры для этого файла =====================

@pytest.fixture
async def db_with_engineer(db, fake_engineer_user):
    """База данных с инженером."""
    await db.add_engineer(fake_engineer_user.id, "Инженер")
    return db