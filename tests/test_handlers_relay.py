"""
Интеграционные тесты для хендлеров ретрансляции (bot/handlers/relay.py).

Проверяет:
- Ретрансляцию сообщений клиент → инженер
- Ретрансляцию сообщений инженер → клиент
- Выбор активной заявки (callback select)
- Навигацию по списку заявок (view, prev, next, back_to_list)
- Просмотр истории переписки (history)
- Переключение на заявку (redirect)
- Обработку больших файлов
"""
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Document, PhotoSize
from conftest import make_message

from bot.handlers.relay import (
    _require_engineer,
    list_my_tickets,
    list_open_tickets,
    navigate_ticket,
    noop,
    redirect_to_ticket,
    relay_messages,
    select_ticket_for_reply,
    show_ticket_history,
    view_ticket,
)
from bot.keyboards import TicketCallback

# ===================== Вспомогательные фикстуры =====================

@pytest.fixture
async def relay_fsm_context(fake_engineer_user):
    """FSM-контекст для relay-тестов."""
    storage = MemoryStorage()
    key = StorageKey(chat_id=fake_engineer_user.id, user_id=fake_engineer_user.id, bot_id=1)
    context = FSMContext(storage=storage, key=key)
    yield context
    await context.clear()


@pytest.fixture
async def client_fsm_context(fake_user):
    """FSM-контекст для клиента."""
    storage = MemoryStorage()
    key = StorageKey(chat_id=fake_user.id, user_id=fake_user.id, bot_id=1)
    context = FSMContext(storage=storage, key=key)
    yield context
    await context.clear()


@pytest.fixture
async def db_with_active_ticket(db, fake_user, fake_engineer_user):
    """База данных с активной заявкой (в работе у инженера)."""
    ticket_id = await db.create_ticket(
        client_id=fake_user.id,
        client_name="Клиент Тест",
        company="ООО Тест",
        equipment_type="",
        brand="",
        cnc_model="",
        problem="Проблема со станком",
        media_id=None,
        city="Москва",
        inn_contract="",
        contact="+79991234567",
        machine_info="Wattsan 1610",
        company_city="Москва",
    )
    await db.add_engineer(fake_engineer_user.id, "Инженер Тест")
    await db.take_ticket(ticket_id, fake_engineer_user.id)
    return db, ticket_id


# ===================== Тесты ретрансляции клиент → инженер =====================

class TestClientToEngineerRelay:
    """Тесты для ретрансляции сообщений от клиента инженеру."""

    async def test_client_message_relayed_to_engineer(
        self, db_with_active_ticket, fake_user, fake_chat, mock_bot, client_fsm_context
    ):
        """Проверяет ретрансляцию сообщения клиента инженеру."""
        db, ticket_id = db_with_active_ticket

        message = make_message(fake_user, fake_chat, text="Здравствуйте, когда сможете приехать?")
        await relay_messages(message, mock_bot, db, is_engineer=False, state=client_fsm_context)

        # Сообщение должно быть отправлено инженеру
        mock_bot.send_message.assert_called()

        # Сообщение должно быть сохранено в историю
        messages = await db.get_messages_for_ticket(ticket_id)
        assert len(messages) == 1
        assert messages[0]["sender_role"] == "client"

    async def test_client_message_to_open_ticket(
        self, db, fake_user, fake_chat, mock_bot, client_fsm_context
    ):
        """Проверяет сообщение клиента по заявке без назначенного инженера."""
        ticket_id = await db.create_ticket(
            client_id=fake_user.id,
            client_name="Клиент",
            company="",
            equipment_type="",
            brand="",
            cnc_model="",
            problem="Проблема",
            media_id=None,
            city="",
            inn_contract="",
            contact="12345",
        )

        message = make_message(fake_user, fake_chat, text="Когда начнут работать?")
        await relay_messages(message, mock_bot, db, is_engineer=False, state=client_fsm_context)

        # Должно быть сообщение об ожидании
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "ожидает назначения" in call_args[0][0].lower()

        # Сообщение должно быть сохранено в историю
        messages = await db.get_messages_for_ticket(ticket_id)
        assert len(messages) == 1

    async def test_client_photo_relayed(
        self, db_with_active_ticket, fake_user, fake_chat, mock_bot, client_fsm_context
    ):
        """Проверяет ретрансляцию фото от клиента."""
        db, ticket_id = db_with_active_ticket

        photo = PhotoSize(
            file_id="test_photo_id",
            file_unique_id="unique",
            width=800,
            height=600,
            file_size=1024,
        )
        message = make_message(fake_user, fake_chat, caption="Фото станка", photo=[photo])

        with patch("bot.handlers.relay.save_media_file", new_callable=AsyncMock) as mock_save:
            mock_save.return_value = "media/ticket_1/001_photo.jpg"
            await relay_messages(message, mock_bot, db, is_engineer=False, state=client_fsm_context)

        # Фото должно быть отправлено инженеру
        mock_bot.send_photo.assert_called()

    async def test_client_large_file_rejected(
        self, db_with_active_ticket, fake_user, fake_chat, mock_bot, client_fsm_context
    ):
        """Проверяет отклонение большого файла от клиента."""
        db, ticket_id = db_with_active_ticket

        # Используем model_construct для обхода валидации frozen-модели
        doc = Document.model_construct(
            file_id="large_doc_id",
            file_unique_id="unique",
            file_name="big.zip",
            file_size=25 * 1024 * 1024,  # 25 MB > 20 MB лимит
        )
        message = make_message(fake_user, fake_chat, document=doc)

        await relay_messages(message, mock_bot, db, is_engineer=False, state=client_fsm_context)

        # Должно быть сообщение об ошибке
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "слишком большой" in call_args[0][0].lower()


# ===================== Тесты ретрансляции инженер → клиент =====================

class TestEngineerToClientRelay:
    """Тесты для ретрансляции сообщений от инженера клиенту."""

    async def test_engineer_message_relayed_to_client(
        self, db_with_active_ticket, fake_engineer_user, fake_chat, mock_bot, relay_fsm_context
    ):
        """Проверяет ретрансляцию сообщения инженера клиенту."""
        db, ticket_id = db_with_active_ticket
        await relay_fsm_context.update_data(active_ticket_id=ticket_id)

        # Создаём чат для инженера
        eng_chat = Chat(id=fake_engineer_user.id, type="private")
        message = make_message(fake_engineer_user, eng_chat, text="Выеду завтра в 10:00")

        await relay_messages(message, mock_bot, db, is_engineer=True, state=relay_fsm_context)

        # Сообщение должно быть отправлено клиенту
        mock_bot.send_message.assert_called()

        # Сообщение должно быть сохранено в историю
        messages = await db.get_messages_for_ticket(ticket_id)
        assert any(m["sender_role"] == "engineer" for m in messages)

    async def test_engineer_without_tickets(
        self, db, fake_engineer_user, mock_bot, relay_fsm_context
    ):
        """Проверяет сообщение инженера без активных заявок."""
        await db.add_engineer(fake_engineer_user.id, "Инженер")

        eng_chat = Chat(id=fake_engineer_user.id, type="private")
        message = make_message(fake_engineer_user, eng_chat, text="Тест")

        await relay_messages(message, mock_bot, db, is_engineer=True, state=relay_fsm_context)

        # Ничего не должно быть отправлено
        mock_bot.send_message.assert_not_called()

    async def test_engineer_multiple_tickets_selection_required(
        self, db, fake_user, fake_engineer_user, mock_bot, relay_fsm_context
    ):
        """Проверяет запрос выбора заявки при нескольких активных."""
        # Создаём две заявки
        ticket1 = await db.create_ticket(
            client_id=fake_user.id,
            client_name="Клиент 1",
            company="", equipment_type="", brand="", cnc_model="",
            problem="Проблема 1", media_id=None, city="", inn_contract="", contact="1",
        )
        ticket2 = await db.create_ticket(
            client_id=222222,
            client_name="Клиент 2",
            company="", equipment_type="", brand="", cnc_model="",
            problem="Проблема 2", media_id=None, city="", inn_contract="", contact="2",
        )

        await db.add_engineer(fake_engineer_user.id, "Инженер")
        await db.take_ticket(ticket1, fake_engineer_user.id)
        await db.take_ticket(ticket2, fake_engineer_user.id)

        eng_chat = Chat(id=fake_engineer_user.id, type="private")
        message = make_message(fake_engineer_user, eng_chat, text="Ответ")

        await relay_messages(message, mock_bot, db, is_engineer=True, state=relay_fsm_context)

        # Должен быть запрос выбора заявки
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "Выберите, кому ответить" in call_args[0][0]


# ===================== Тесты выбора заявки =====================

class TestSelectTicket:
    """Тесты для выбора активной заявки."""

    async def test_select_ticket_success(
        self, db_with_active_ticket, fake_engineer_user, relay_fsm_context
    ):
        """Проверяет успешный выбор заявки."""
        db, ticket_id = db_with_active_ticket

        callback_data = TicketCallback(action="select", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.message = AsyncMock()
        callback.message.edit_reply_markup = AsyncMock()
        callback.message.answer = AsyncMock()
        callback.answer = AsyncMock()

        await select_ticket_for_reply(callback, callback_data, relay_fsm_context, is_engineer=True)

        # Заявка должна быть установлена как активная
        data = await relay_fsm_context.get_data()
        assert data.get("active_ticket_id") == ticket_id

    async def test_select_ticket_not_engineer(self, fake_user, relay_fsm_context):
        """Проверяет выбор заявки не инженером."""
        callback_data = TicketCallback(action="select", ticket_id=1)
        callback = AsyncMock()
        callback.from_user = fake_user
        callback.answer = AsyncMock()

        await select_ticket_for_reply(callback, callback_data, relay_fsm_context, is_engineer=False)
        callback.answer.assert_called_with("У вас нет прав инженера.", show_alert=True)


# ===================== Тесты списка заявок =====================

class TestTicketLists:
    """Тесты для списков заявок."""

    async def test_list_my_tickets_empty(
        self, db, fake_engineer_user, fake_chat, relay_fsm_context
    ):
        """Проверяет список моих заявок (пустой)."""
        await db.add_engineer(fake_engineer_user.id, "Инженер")

        message = make_message(fake_engineer_user, fake_chat, text="📋 Мои заявки в работе")
        await list_my_tickets(message, db, is_engineer=True, state=relay_fsm_context)

        message.answer.assert_called()
        call_args = message.answer.call_args_list[0]
        assert "нет активных заявок" in call_args[0][0].lower()

    async def test_list_my_tickets_with_data(
        self, db_with_active_ticket, fake_engineer_user, fake_chat, relay_fsm_context
    ):
        """Проверяет список моих заявок с данными."""
        db, ticket_id = db_with_active_ticket

        message = make_message(fake_engineer_user, fake_chat, text="📋 Мои заявки в работе")
        await list_my_tickets(message, db, is_engineer=True, state=relay_fsm_context)

        # Должны быть сохранены данные навигации
        data = await relay_fsm_context.get_data()
        assert data.get("ticket_view_mode") == "mine"
        assert ticket_id in data.get("ticket_view_ids", [])

    async def test_list_open_tickets_empty(
        self, db, fake_engineer_user, fake_chat, relay_fsm_context
    ):
        """Проверяет список нераспределённых заявок (пустой)."""
        await db.add_engineer(fake_engineer_user.id, "Инженер")

        message = make_message(fake_engineer_user, fake_chat, text="📥 Нераспределенные заявки")
        await list_open_tickets(message, db, is_engineer=True, state=relay_fsm_context)

        message.answer.assert_called()
        call_args = message.answer.call_args_list[0]
        assert "Нет нераспределенных заявок" in call_args[0][0]

    async def test_list_open_tickets_with_data(
        self, db, fake_user, fake_engineer_user, fake_chat, relay_fsm_context
    ):
        """Проверяет список нераспределённых заявок с данными."""
        await db.create_ticket(
            client_id=fake_user.id,
            client_name="Клиент",
            company="", equipment_type="", brand="", cnc_model="",
            problem="Проблема", media_id=None, city="", inn_contract="", contact="1",
        )
        await db.add_engineer(fake_engineer_user.id, "Инженер")

        message = make_message(fake_engineer_user, fake_chat, text="📥 Нераспределенные заявки")
        await list_open_tickets(message, db, is_engineer=True, state=relay_fsm_context)

        data = await relay_fsm_context.get_data()
        assert data.get("ticket_view_mode") == "open"

    async def test_list_tickets_not_engineer(self, fake_user, fake_chat, db, relay_fsm_context):
        """Проверяет список заявок для не-инженера."""
        message = make_message(fake_user, fake_chat, text="📋 Мои заявки в работе")
        await list_my_tickets(message, db, is_engineer=False, state=relay_fsm_context)
        message.answer.assert_not_called()


# ===================== Тесты просмотра заявки =====================

class TestViewTicket:
    """Тесты для просмотра деталей заявки."""

    async def test_view_ticket_success(
        self, db_with_active_ticket, fake_engineer_user, relay_fsm_context
    ):
        """Проверяет успешный просмотр заявки."""
        db, ticket_id = db_with_active_ticket
        await relay_fsm_context.update_data(
            ticket_view_mode="mine",
            ticket_view_ids=[ticket_id],
            ticket_view_index=0,
        )

        callback_data = TicketCallback(action="view", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        await view_ticket(callback, callback_data, db, relay_fsm_context, is_engineer=True)

        callback.message.edit_text.assert_called_once()
        call_args = callback.message.edit_text.call_args
        assert f"Заявка #{ticket_id}" in call_args[0][0]

    async def test_view_ticket_not_found(
        self, db, fake_engineer_user, relay_fsm_context
    ):
        """Проверяет просмотр несуществующей заявки."""
        await relay_fsm_context.update_data(
            ticket_view_mode="mine",
            ticket_view_ids=[99999],
        )

        callback_data = TicketCallback(action="view", ticket_id=99999)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.answer = AsyncMock()

        await view_ticket(callback, callback_data, db, relay_fsm_context, is_engineer=True)
        callback.answer.assert_called_with("Заявка не найдена.", show_alert=True)


# ===================== Тесты навигации =====================

class TestNavigateTicket:
    """Тесты для навигации по списку заявок."""

    async def test_navigate_next(
        self, db, fake_user, fake_engineer_user, relay_fsm_context
    ):
        """Проверяет навигацию вперёд."""
        # Создаём две заявки
        t1 = await db.create_ticket(
            client_id=fake_user.id, client_name="К1",
            company="", equipment_type="", brand="", cnc_model="",
            problem="П1", media_id=None, city="", inn_contract="", contact="1",
        )
        t2 = await db.create_ticket(
            client_id=fake_user.id, client_name="К2",
            company="", equipment_type="", brand="", cnc_model="",
            problem="П2", media_id=None, city="", inn_contract="", contact="2",
        )
        await db.add_engineer(fake_engineer_user.id, "Инженер")
        await db.take_ticket(t1, fake_engineer_user.id)
        await db.take_ticket(t2, fake_engineer_user.id)

        await relay_fsm_context.update_data(
            ticket_view_mode="mine",
            ticket_view_ids=[t1, t2],
            ticket_view_index=0,
        )

        callback_data = TicketCallback(action="next", ticket_id=t1)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        await navigate_ticket(callback, callback_data, db, relay_fsm_context, is_engineer=True)

        data = await relay_fsm_context.get_data()
        assert data.get("ticket_view_index") == 1

    async def test_navigate_prev_at_start(
        self, db, fake_user, fake_engineer_user, relay_fsm_context
    ):
        """Проверяет навигацию назад на первой заявке (не уходит ниже 0)."""
        t1 = await db.create_ticket(
            client_id=fake_user.id, client_name="К1",
            company="", equipment_type="", brand="", cnc_model="",
            problem="П1", media_id=None, city="", inn_contract="", contact="1",
        )
        await db.add_engineer(fake_engineer_user.id, "Инженер")
        await db.take_ticket(t1, fake_engineer_user.id)

        await relay_fsm_context.update_data(
            ticket_view_mode="mine",
            ticket_view_ids=[t1],
            ticket_view_index=0,
        )

        callback_data = TicketCallback(action="prev", ticket_id=t1)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        await navigate_ticket(callback, callback_data, db, relay_fsm_context, is_engineer=True)

        data = await relay_fsm_context.get_data()
        assert data.get("ticket_view_index") == 0  # Не ушёл ниже 0

    async def test_navigate_empty_list(self, db, fake_engineer_user, relay_fsm_context):
        """Проверяет навигацию по пустому списку."""
        await relay_fsm_context.update_data(ticket_view_ids=[])

        callback_data = TicketCallback(action="next", ticket_id=1)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.answer = AsyncMock()

        await navigate_ticket(callback, callback_data, db, relay_fsm_context, is_engineer=True)
        callback.answer.assert_called_with("Список заявок устарел. Откройте его заново.", show_alert=True)


# ===================== Тесты истории переписки =====================

class TestTicketHistory:
    """Тесты для истории переписки."""

    async def test_history_empty(
        self, db_with_active_ticket, fake_engineer_user, relay_fsm_context
    ):
        """Проверяет пустую историю переписки."""
        db, ticket_id = db_with_active_ticket

        callback_data = TicketCallback(action="history", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        await show_ticket_history(callback, callback_data, db, relay_fsm_context, is_engineer=True)

        callback.message.edit_text.assert_called_once()
        call_args = callback.message.edit_text.call_args
        assert "пока нет сообщений" in call_args[0][0].lower()

    async def test_history_with_messages(
        self, db_with_active_ticket, fake_engineer_user, relay_fsm_context
    ):
        """Проверяет историю с сообщениями."""
        db, ticket_id = db_with_active_ticket
        await db.save_message(ticket_id, 123, "client", "Привет")
        await db.save_message(ticket_id, fake_engineer_user.id, "engineer", "Здравствуйте")

        callback_data = TicketCallback(action="history", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        await show_ticket_history(callback, callback_data, db, relay_fsm_context, is_engineer=True)

        call_args = callback.message.edit_text.call_args
        assert "История переписки" in call_args[0][0]
        assert "Привет" in call_args[0][0]

    async def test_history_ticket_not_found(self, db, fake_engineer_user, relay_fsm_context):
        """Проверяет историю несуществующей заявки."""
        callback_data = TicketCallback(action="history", ticket_id=99999)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.answer = AsyncMock()

        await show_ticket_history(callback, callback_data, db, relay_fsm_context, is_engineer=True)
        callback.answer.assert_called_with("Заявка не найдена.", show_alert=True)


# ===================== Тесты redirect и noop =====================

class TestRedirectAndNoop:
    """Тесты для redirect и noop callbacks."""

    async def test_redirect_to_ticket(self, fake_engineer_user, relay_fsm_context):
        """Проверяет переключение на заявку."""
        callback_data = TicketCallback(action="redirect", ticket_id=42)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.message = AsyncMock()
        callback.message.answer = AsyncMock()
        callback.answer = AsyncMock()

        await redirect_to_ticket(callback, callback_data, relay_fsm_context, is_engineer=True)

        data = await relay_fsm_context.get_data()
        assert data.get("active_ticket_id") == 42

    async def test_noop(self):
        """Проверяет noop callback."""
        callback = AsyncMock()
        callback.answer = AsyncMock()
        await noop(callback)
        callback.answer.assert_called_once()


# ===================== Тесты _require_engineer =====================

class TestRequireEngineer:
    """Тесты для _require_engineer."""

    async def test_require_engineer_true(self):
        """Проверяет пропуск для инженера."""
        callback = AsyncMock()
        result = await _require_engineer(callback, is_engineer=True)
        assert result is True

    async def test_require_engineer_false(self):
        """Проверяет отказ для не-инженера."""
        callback = AsyncMock()
        callback.answer = AsyncMock()
        result = await _require_engineer(callback, is_engineer=False)
        assert result is False
        callback.answer.assert_called_with("У вас нет прав инженера.", show_alert=True)