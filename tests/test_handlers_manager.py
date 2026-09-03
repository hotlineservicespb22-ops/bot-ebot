"""Тесты для панели руководителя (bot/handlers/manager.py).

Покрывает: проверку доступа (is_manager) на команде и на всех callback-действиях,
список проблемных заявок, детальный просмотр, переписку без медиа и экранирование
пользовательских данных (HTML) в отрисованных сообщениях.
"""
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import User

from bot.database import Database
from bot.handlers.manager import cmd_manager_panel, manager_callback_handler
from bot.keyboards import ManagerCallback
from conftest import make_message

pytestmark = pytest.mark.asyncio


async def _low_rated_ticket(
    db: Database, client_id: int, rating: int = 2, comment: str | None = None,
    problem: str = "Проблема", client_name: str = "Клиент",
) -> int:
    """Создаёт заявку и ставит ей низкую оценку — попадает в low_rated_tickets."""
    tid = await db.create_ticket(
        client_id=client_id, client_name=client_name, company="",
        equipment_type="", brand="", cnc_model="", problem=problem,
        media_id=None, city="", inn_contract="", contact="+70000000000",
        machine_info="Wattsan 1610",
    )
    await db.save_rating(tid, client_id, rating, comment)
    return tid


def _cb(user: User, message: AsyncMock | None = None) -> AsyncMock:
    callback = AsyncMock()
    callback.from_user = user
    callback.message = message or AsyncMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    return callback


# ===================== Доступ =====================

class TestManagerAccess:
    async def test_panel_denied_for_non_manager(self, db, fake_user):
        message = make_message(fake_user, chat=None)
        object.__setattr__(message, "answer", AsyncMock())
        await cmd_manager_panel(message, db, is_manager=False)
        message.answer.assert_awaited_once()
        assert "нет прав руководителя" in message.answer.call_args[0][0].lower()

    async def test_panel_allowed_for_manager(self, db, fake_manager_user):
        message = make_message(fake_manager_user, chat=None)
        object.__setattr__(message, "answer", AsyncMock())
        await cmd_manager_panel(message, db, is_manager=True)
        message.answer.assert_awaited_once()
        assert "Панель руководителя" in message.answer.call_args[0][0]

    async def test_callback_denied_for_non_manager(self, db, fake_user):
        callback = _cb(fake_user)
        callback_data = ManagerCallback(action="list")
        await manager_callback_handler(callback, callback_data, db, AsyncMock(), is_manager=False)
        callback.answer.assert_called_with("🚫 Нет доступа.", show_alert=True)
        callback.message.edit_text.assert_not_called()

    @pytest.mark.parametrize("action", ["menu", "list", "detail", "chat"])
    async def test_every_action_denied_for_non_manager(self, db, fake_user, action):
        """Ни одно действие панели руководителя не должно быть доступно без is_manager."""
        callback = _cb(fake_user)
        callback_data = ManagerCallback(action=action, ticket_id=1)
        await manager_callback_handler(callback, callback_data, db, AsyncMock(), is_manager=False)
        callback.answer.assert_called_with("🚫 Нет доступа.", show_alert=True)
        callback.message.edit_text.assert_not_called()


# ===================== Меню =====================

class TestManagerMenu:
    async def test_menu_action_renders_menu(self, db, fake_manager_user):
        callback = _cb(fake_manager_user)
        callback_data = ManagerCallback(action="menu")
        await manager_callback_handler(callback, callback_data, db, AsyncMock(), is_manager=True)
        callback.message.edit_text.assert_awaited_once()
        assert "Панель руководителя" in callback.message.edit_text.call_args[0][0]


# ===================== Список проблемных заявок =====================

class TestManagerTicketList:
    async def test_list_empty(self, db, fake_manager_user):
        callback = _cb(fake_manager_user)
        callback_data = ManagerCallback(action="list")
        await manager_callback_handler(callback, callback_data, db, AsyncMock(), is_manager=True)
        callback.message.edit_text.assert_awaited_once()
        assert "Проблемных заявок нет" in callback.message.edit_text.call_args[0][0]

    async def test_list_with_tickets(self, db, fake_manager_user):
        await _low_rated_ticket(db, client_id=1, rating=2)
        await _low_rated_ticket(db, client_id=2, rating=1)

        callback = _cb(fake_manager_user)
        callback_data = ManagerCallback(action="list")
        await manager_callback_handler(callback, callback_data, db, AsyncMock(), is_manager=True)

        callback.message.edit_text.assert_awaited_once()
        text = callback.message.edit_text.call_args[0][0]
        assert "Всего: <b>2</b>" in text

    async def test_list_excludes_normal_rated_tickets(self, db, fake_manager_user):
        """Заявка с оценкой > 3 не должна попадать в список руководителя."""
        await _low_rated_ticket(db, client_id=1, rating=5)

        callback = _cb(fake_manager_user)
        callback_data = ManagerCallback(action="list")
        await manager_callback_handler(callback, callback_data, db, AsyncMock(), is_manager=True)

        text = callback.message.edit_text.call_args[0][0]
        assert "Проблемных заявок нет" in text


# ===================== Детальный просмотр =====================

class TestManagerTicketDetail:
    async def test_detail_not_found(self, db, fake_manager_user):
        callback = _cb(fake_manager_user)
        callback_data = ManagerCallback(action="detail", ticket_id=99999)
        await manager_callback_handler(callback, callback_data, db, AsyncMock(), is_manager=True)
        callback.answer.assert_called_with("Заявка не найдена.", show_alert=True)
        callback.message.edit_text.assert_not_called()

    async def test_detail_shows_ticket_info(self, db, fake_manager_user):
        tid = await _low_rated_ticket(db, client_id=1, rating=2, comment="Долго ждали")

        callback = _cb(fake_manager_user)
        callback_data = ManagerCallback(action="detail", ticket_id=tid)
        await manager_callback_handler(callback, callback_data, db, AsyncMock(), is_manager=True)

        callback.message.edit_text.assert_awaited_once()
        text = callback.message.edit_text.call_args[0][0]
        assert f"Заявка #{tid}" in text
        assert "Долго ждали" in text
        assert "(2/5)" in text

    async def test_detail_escapes_html_in_user_fields(self, db, fake_manager_user):
        """Пользовательские поля (проблема, имя клиента) должны экранироваться."""
        tid = await _low_rated_ticket(
            db, client_id=1, rating=1,
            problem="<script>alert(1)</script>",
            client_name="<b>Клиент</b>",
        )

        callback = _cb(fake_manager_user)
        callback_data = ManagerCallback(action="detail", ticket_id=tid)
        await manager_callback_handler(callback, callback_data, db, AsyncMock(), is_manager=True)

        text = callback.message.edit_text.call_args[0][0]
        assert "<script>" not in text
        assert "&lt;script&gt;" in text
        assert "<b>Клиент</b>" not in text


# ===================== Переписка без медиа =====================

class TestManagerTicketChat:
    async def test_chat_not_found(self, db, fake_manager_user):
        callback = _cb(fake_manager_user)
        callback_data = ManagerCallback(action="chat", ticket_id=99999)
        await manager_callback_handler(callback, callback_data, db, AsyncMock(), is_manager=True)
        callback.answer.assert_called_with("Заявка не найдена.", show_alert=True)

    async def test_chat_empty_shows_alert(self, db, fake_manager_user):
        tid = await _low_rated_ticket(db, client_id=1, rating=2)

        callback = _cb(fake_manager_user)
        callback_data = ManagerCallback(action="chat", ticket_id=tid)
        await manager_callback_handler(callback, callback_data, db, AsyncMock(), is_manager=True)

        callback.answer.assert_called_with(
            "Переписка пуста или содержит только медиа.", show_alert=True
        )
        callback.message.edit_text.assert_not_called()

    async def test_chat_shows_text_messages(self, db, fake_manager_user):
        tid = await _low_rated_ticket(db, client_id=1, rating=2)
        await db.save_message(tid, 1, "client", "Станок не работает", None)
        await db.save_message(tid, 999, "engineer", "Уже разбираемся", None)

        callback = _cb(fake_manager_user)
        callback_data = ManagerCallback(action="chat", ticket_id=tid)
        await manager_callback_handler(callback, callback_data, db, AsyncMock(), is_manager=True)

        callback.message.edit_text.assert_awaited_once()
        text = callback.message.edit_text.call_args[0][0]
        assert "Станок не работает" in text
        assert "Уже разбираемся" in text
        assert f"заявке #{tid}" in text

    async def test_chat_excludes_media_only_messages(self, db, fake_manager_user):
        """Сообщения с медиа (без текста) не учитываются в текстовой переписке."""
        tid = await _low_rated_ticket(db, client_id=1, rating=2)
        await db.save_message(tid, 1, "client", "", "photo")

        callback = _cb(fake_manager_user)
        callback_data = ManagerCallback(action="chat", ticket_id=tid)
        await manager_callback_handler(callback, callback_data, db, AsyncMock(), is_manager=True)

        callback.answer.assert_called_with(
            "Переписка пуста или содержит только медиа.", show_alert=True
        )

    async def test_chat_escapes_html_in_message_text(self, db, fake_manager_user):
        tid = await _low_rated_ticket(db, client_id=1, rating=2)
        await db.save_message(tid, 1, "client", "<img src=x onerror=alert(1)>", None)

        callback = _cb(fake_manager_user)
        callback_data = ManagerCallback(action="chat", ticket_id=tid)
        await manager_callback_handler(callback, callback_data, db, AsyncMock(), is_manager=True)

        text = callback.message.edit_text.call_args[0][0]
        assert "<img src=x" not in text
        assert "&lt;img" in text


# ===================== _safe_edit: игнорирование "message is not modified" =====================

class TestSafeEdit:
    async def test_ignores_message_not_modified(self, db, fake_manager_user):
        callback = _cb(fake_manager_user)
        callback.message.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(method=AsyncMock(), message="message is not modified")
        )
        callback_data = ManagerCallback(action="menu")

        # Не должно бросить исключение наружу.
        await manager_callback_handler(callback, callback_data, db, AsyncMock(), is_manager=True)

    async def test_reraises_other_bad_request(self, db, fake_manager_user):
        callback = _cb(fake_manager_user)
        callback.message.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(method=AsyncMock(), message="chat not found")
        )
        callback_data = ManagerCallback(action="menu")

        with pytest.raises(TelegramBadRequest):
            await manager_callback_handler(callback, callback_data, db, AsyncMock(), is_manager=True)
