"""
Интеграционные тесты для хендлеров инженера (bot/handlers/engineer.py).

Проверяет:
- Взятие заявки в работу (callback take)
- Завершение заявки (callback complete)
- Отмена заявки инженером (callback eng_cancel)
- Завершение/отмена через меню (✅ Завершить текущую / 🚫 Отменить текущую)
- Проверку прав инженера
"""
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from conftest import make_message

from bot.handlers.engineer import (
    _create_bitrix_task_for_ticket,
    _delete_ticket_notifications,
    _send_pre_assign_client_messages,
    cancel_current_ticket_via_menu,
    cancel_ticket_by_engineer,
    complete_current_ticket_via_menu,
    complete_ticket_by_engineer,
    take_ticket,
)
from bot.keyboards import TicketCallback

# ===================== Вспомогательные фикстуры =====================

@pytest.fixture
async def engineer_fsm_context(fake_engineer_user):
    """FSM-контекст для инженера."""
    storage = MemoryStorage()
    key = StorageKey(chat_id=fake_engineer_user.id, user_id=fake_engineer_user.id, bot_id=1)
    context = FSMContext(storage=storage, key=key)
    yield context
    await context.clear()


@pytest.fixture
async def db_with_open_ticket(db, fake_user):
    """База данных с открытой заявкой."""
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
    return db, ticket_id


# ===================== Тесты взятия заявки =====================

class TestTakeTicket:
    """Тесты для взятия заявки в работу."""

    async def test_take_ticket_success(
        self, db_with_open_ticket, fake_engineer_user, engineer_fsm_context, mock_bot
    ):
        """Проверяет успешное взятие заявки."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")

        callback_data = TicketCallback(action="take", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.message = AsyncMock()
        callback.message.caption = None
        callback.message.html_text = "Тест заявки"
        callback.message.edit_text = AsyncMock()
        callback.message.answer = AsyncMock()
        callback.answer = AsyncMock()

        with patch("bot.handlers.engineer._create_bitrix_task_for_ticket", new_callable=AsyncMock) as mock_bitrix:
            mock_bitrix.return_value = None
            await take_ticket(callback, callback_data, mock_bot, db, is_engineer=True, state=engineer_fsm_context)

        # Заявка должна быть взята
        ticket = await db.get_ticket(ticket_id)
        assert ticket["status"] == "in_progress"
        assert ticket["engineer_id"] == fake_engineer_user.id

    async def test_take_ticket_not_engineer(
        self, db_with_open_ticket, fake_user, engineer_fsm_context, mock_bot
    ):
        """Проверяет взятие заявки пользователем без прав инженера."""
        db, ticket_id = db_with_open_ticket

        callback_data = TicketCallback(action="take", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_user
        callback.answer = AsyncMock()

        await take_ticket(callback, callback_data, mock_bot, db, is_engineer=False, state=engineer_fsm_context)

        # Должен быть отказ
        callback.answer.assert_called_with("У вас нет прав инженера.", show_alert=True)

        # Заявка должна остаться открытой
        ticket = await db.get_ticket(ticket_id)
        assert ticket["status"] == "open"

    async def test_take_ticket_already_taken(
        self, db_with_open_ticket, fake_engineer_user, engineer_fsm_context, mock_bot
    ):
        """Проверяет взятие уже занятой заявки."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")
        await db.add_engineer(111222333, "Другой Инженер")

        # Первый инженер берёт заявку
        await db.take_ticket(ticket_id, 111222333)

        callback_data = TicketCallback(action="take", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.message = AsyncMock()
        callback.message.caption = None
        callback.message.html_text = "Тест"
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        with patch("bot.handlers.engineer._create_bitrix_task_for_ticket", new_callable=AsyncMock):
            await take_ticket(callback, callback_data, mock_bot, db, is_engineer=True, state=engineer_fsm_context)

        # Должно быть сообщение о том, что заявка занята
        callback.answer.assert_called_with("Заявка уже занята другим специалистом или закрыта.", show_alert=True)

    async def test_take_ticket_sets_active_in_state(
        self, db_with_open_ticket, fake_engineer_user, engineer_fsm_context, mock_bot
    ):
        """Проверяет установку active_ticket_id в FSM-состоянии."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")

        callback_data = TicketCallback(action="take", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.message = AsyncMock()
        callback.message.caption = None
        callback.message.html_text = "Тест"
        callback.message.edit_text = AsyncMock()
        callback.message.answer = AsyncMock()
        callback.answer = AsyncMock()

        with patch("bot.handlers.engineer._create_bitrix_task_for_ticket", new_callable=AsyncMock) as mock_bitrix:
            mock_bitrix.return_value = None
            await take_ticket(callback, callback_data, mock_bot, db, is_engineer=True, state=engineer_fsm_context)

        data = await engineer_fsm_context.get_data()
        assert data.get("active_ticket_id") == ticket_id

    async def test_take_ticket_notifies_client(
        self, db_with_open_ticket, fake_engineer_user, fake_user, engineer_fsm_context, mock_bot
    ):
        """Проверяет уведомление клиента о взятии заявки."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")

        callback_data = TicketCallback(action="take", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.message = AsyncMock()
        callback.message.caption = None
        callback.message.html_text = "Тест"
        callback.message.edit_text = AsyncMock()
        callback.message.answer = AsyncMock()
        callback.answer = AsyncMock()

        with patch("bot.handlers.engineer._create_bitrix_task_for_ticket", new_callable=AsyncMock) as mock_bitrix:
            mock_bitrix.return_value = None
            await take_ticket(callback, callback_data, mock_bot, db, is_engineer=True, state=engineer_fsm_context)

        # Клиент должен быть уведомлён
        mock_bot.send_message.assert_called()
        call_args = mock_bot.send_message.call_args
        assert call_args[0][0] == fake_user.id or call_args[1].get("chat_id") == fake_user.id


# ===================== Тесты завершения заявки =====================

class TestCompleteTicket:
    """Тесты для завершения заявки."""

    async def test_complete_ticket_success(
        self, db_with_open_ticket, fake_engineer_user, fake_user, engineer_fsm_context, mock_bot
    ):
        """Проверяет успешное завершение заявки."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")
        await db.take_ticket(ticket_id, fake_engineer_user.id)

        callback_data = TicketCallback(action="complete", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.message = AsyncMock()
        callback.message.caption = None
        callback.message.html_text = "Тест"
        callback.message.edit_text = AsyncMock()
        callback.message.answer = AsyncMock()
        callback.answer = AsyncMock()

        await complete_ticket_by_engineer(callback, callback_data, mock_bot, db, engineer_fsm_context, is_engineer=True)

        # Заявка должна быть завершена
        ticket = await db.get_ticket(ticket_id)
        assert ticket["status"] == "completed"

    async def test_complete_ticket_not_owner(
        self, db_with_open_ticket, fake_engineer_user, engineer_fsm_context, mock_bot
    ):
        """Проверяет завершение заявки не её владельцем."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")
        await db.add_engineer(111222333, "Другой Инженер")
        await db.take_ticket(ticket_id, 111222333)  # Берёт другой инженер

        callback_data = TicketCallback(action="complete", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.answer = AsyncMock()

        await complete_ticket_by_engineer(callback, callback_data, mock_bot, db, engineer_fsm_context, is_engineer=True)

        # Должен быть отказ
        callback.answer.assert_called_with("Это не ваша заявка или она уже закрыта.", show_alert=True)

    async def test_complete_ticket_not_engineer(
        self, db_with_open_ticket, fake_user, engineer_fsm_context, mock_bot
    ):
        """Проверяет завершение заявки пользователем без прав инженера."""
        db, ticket_id = db_with_open_ticket

        callback_data = TicketCallback(action="complete", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_user
        callback.answer = AsyncMock()

        await complete_ticket_by_engineer(callback, callback_data, mock_bot, db, engineer_fsm_context, is_engineer=False)

        callback.answer.assert_called_with("У вас нет прав инженера.", show_alert=True)

    async def test_complete_ticket_sends_rating_to_client(
        self, db_with_open_ticket, fake_engineer_user, fake_user, engineer_fsm_context, mock_bot
    ):
        """Проверяет отправку запроса оценки клиенту после завершения."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")
        await db.take_ticket(ticket_id, fake_engineer_user.id)

        callback_data = TicketCallback(action="complete", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.message = AsyncMock()
        callback.message.caption = None
        callback.message.html_text = "Тест"
        callback.message.edit_text = AsyncMock()
        callback.message.answer = AsyncMock()
        callback.answer = AsyncMock()

        await complete_ticket_by_engineer(callback, callback_data, mock_bot, db, engineer_fsm_context, is_engineer=True)

        # Клиенту должен быть отправлен запрос оценки
        mock_bot.send_message.assert_called()
        call_kwargs = mock_bot.send_message.call_args
        assert "Оцените" in str(call_kwargs) or "оцените" in str(call_kwargs).lower()


# ===================== Тесты отмены заявки инженером =====================

class TestCancelTicketByEngineer:
    """Тесты для отмены заявки инженером."""

    async def test_cancel_ticket_success(
        self, db_with_open_ticket, fake_engineer_user, engineer_fsm_context, mock_bot
    ):
        """Проверяет успешную отмену заявки инженером."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")
        await db.take_ticket(ticket_id, fake_engineer_user.id)

        callback_data = TicketCallback(action="eng_cancel", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.message = AsyncMock()
        callback.message.caption = None
        callback.message.html_text = "Тест"
        callback.message.edit_text = AsyncMock()
        callback.message.answer = AsyncMock()
        callback.answer = AsyncMock()

        await cancel_ticket_by_engineer(callback, callback_data, mock_bot, db, engineer_fsm_context, is_engineer=True)

        ticket = await db.get_ticket(ticket_id)
        assert ticket["status"] == "canceled"

    async def test_cancel_ticket_not_owner(
        self, db_with_open_ticket, fake_engineer_user, engineer_fsm_context, mock_bot
    ):
        """Проверяет отмену заявки не её владельцем."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")
        await db.add_engineer(111222333, "Другой Инженер")
        await db.take_ticket(ticket_id, 111222333)

        callback_data = TicketCallback(action="eng_cancel", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.answer = AsyncMock()

        await cancel_ticket_by_engineer(callback, callback_data, mock_bot, db, engineer_fsm_context, is_engineer=True)

        callback.answer.assert_called_with("Это не ваша заявка или она уже закрыта.", show_alert=True)


# ===================== Тесты завершения/отмены через меню =====================

class TestCompleteViaMenu:
    """Тесты для завершения заявки через меню."""

    async def test_complete_via_menu_no_active_ticket(
        self, fake_engineer_user, fake_chat, db, engineer_fsm_context, mock_bot
    ):
        """Проверяет завершение без выбранной активной заявки."""
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")

        message = make_message(fake_engineer_user, fake_chat, text="✅ Завершить текущую")
        await complete_current_ticket_via_menu(message, mock_bot, db, engineer_fsm_context, is_engineer=True)

        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "не выбрана активная заявка" in call_args[0][0].lower()

    async def test_complete_via_menu_success(
        self, db_with_open_ticket, fake_engineer_user, fake_user, fake_chat, engineer_fsm_context, mock_bot
    ):
        """Проверяет успешное завершение заявки через меню."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")
        await db.take_ticket(ticket_id, fake_engineer_user.id)
        await engineer_fsm_context.update_data(active_ticket_id=ticket_id)

        message = make_message(fake_engineer_user, fake_chat, text="✅ Завершить текущую")
        await complete_current_ticket_via_menu(message, mock_bot, db, engineer_fsm_context, is_engineer=True)

        ticket = await db.get_ticket(ticket_id)
        assert ticket["status"] == "completed"

    async def test_complete_via_menu_not_engineer(
        self, fake_user, fake_chat, db, engineer_fsm_context, mock_bot
    ):
        """Проверяет завершение заявки не инженером."""
        message = make_message(fake_user, fake_chat, text="✅ Завершить текущую")
        await complete_current_ticket_via_menu(message, mock_bot, db, engineer_fsm_context, is_engineer=False)

        # Ничего не должно произойти
        message.answer.assert_not_called()


class TestCancelViaMenu:
    """Тесты для отмены заявки через меню."""

    async def test_cancel_via_menu_no_active_ticket(
        self, fake_engineer_user, fake_chat, db, engineer_fsm_context, mock_bot
    ):
        """Проверяет отмену без выбранной активной заявки."""
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")

        message = make_message(fake_engineer_user, fake_chat, text="🚫 Отменить текущую")
        await cancel_current_ticket_via_menu(message, mock_bot, db, engineer_fsm_context, is_engineer=True)

        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "не выбрана активная заявка" in call_args[0][0].lower()

    async def test_cancel_via_menu_success(
        self, db_with_open_ticket, fake_engineer_user, fake_user, fake_chat, engineer_fsm_context, mock_bot
    ):
        """Проверяет успешную отмену заявки через меню."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")
        await db.take_ticket(ticket_id, fake_engineer_user.id)
        await engineer_fsm_context.update_data(active_ticket_id=ticket_id)

        message = make_message(fake_engineer_user, fake_chat, text="🚫 Отменить текущую")
        await cancel_current_ticket_via_menu(message, mock_bot, db, engineer_fsm_context, is_engineer=True)

        ticket = await db.get_ticket(ticket_id)
        assert ticket["status"] == "canceled"


# ===================== Тесты вспомогательных функций =====================

class TestSendPreAssignClientMessages:
    """Тесты для _send_pre_assign_client_messages (передача уточнений клиента инженеру)."""

    async def test_sends_media_without_text_messages(
        self, db, fake_user, fake_engineer_user, mock_bot, tmp_path
    ):
        """Медиа передаются инженеру, даже если нет текстовых сообщений в messages.

        Воспроизводит баг: при отсутствии записей в messages функция выходила
        через `if not client_msgs: return` и не доходила до отправки медиафайлов.
        """
        # Создаём заявку с фото ошибки (machine_media_id не задан)
        ticket_id = await db.create_ticket(
            client_id=fake_user.id,
            client_name="Клиент Тест",
            company="", equipment_type="", brand="", cnc_model="",
            problem="Ошибка",
            media_id=None, city="", inn_contract="", contact="+7999",
            machine_info="Станок",
            company_city="Москва",
        )
        # Сохраняем медиафайл (фото ошибки). В messages записей НЕТ (как при создании заявки
        # через FSM). Это ключевое условие бага.
        media_photo_path = tmp_path / "photo_error.jpg"
        media_photo_path.write_bytes(b"fake_image_data")
        await db.save_media(
            ticket_id=ticket_id,
            file_id="error_photo_file_id",
            file_type="photo",
            file_path=str(media_photo_path),
            sender_id=fake_user.id,
            sender_role="client",
        )

        await _send_pre_assign_client_messages(mock_bot, db, ticket_id, fake_engineer_user.id)

        # В отсутствие текстовых сообщений функция всё равно должна отправить медиа
        assert mock_bot.send_photo.called

    async def test_skips_machine_media_duplicate(
        self, db, fake_user, fake_engineer_user, mock_bot, tmp_path
    ):
        """Медиа шильды (machine_media_id) не дублируется в пересылке.

        Фото/видео шильды уже отправляется инженеру отдельно в take_ticket,
        поэтому в _send_pre_assign_client_messages оно должно быть пропущено.
        """
        ticket_id = await db.create_ticket(
            client_id=fake_user.id,
            client_name="Клиент Тест",
            company="", equipment_type="", brand="", cnc_model="",
            problem="Ошибка",
            media_id=None, city="", inn_contract="", contact="+7999",
            machine_info="Станок",
            company_city="Москва",
            machine_media_id="shilda_file_id",
        )
        # Медиа шильды (совпадает с machine_media_id заявки)
        shilda_path = tmp_path / "shilda.jpg"
        shilda_path.write_bytes(b"shilda_data")
        await db.save_media(
            ticket_id=ticket_id,
            file_id="shilda_file_id",
            file_type="photo",
            file_path=str(shilda_path),
            sender_id=fake_user.id,
            sender_role="client",
        )
        # Медиа ошибки
        error_path = tmp_path / "error.jpg"
        error_path.write_bytes(b"error_data")
        await db.save_media(
            ticket_id=ticket_id,
            file_id="error_photo_file_id",
            file_type="photo",
            file_path=str(error_path),
            sender_id=fake_user.id,
            sender_role="client",
        )

        await _send_pre_assign_client_messages(mock_bot, db, ticket_id, fake_engineer_user.id)

        # Отправлено только 1 фото (ошибки), шильда пропущена
        assert mock_bot.send_photo.call_count == 1


class TestDeleteTicketNotifications:
    """Тесты для _delete_ticket_notifications."""

    async def test_delete_notifications(self, db_with_open_ticket, fake_engineer_user, mock_bot):
        """Проверяет удаление уведомлений о заявке."""
        db, ticket_id = db_with_open_ticket

        # Сохраняем уведомления
        await db.save_ticket_notification(ticket_id, 111, 1001)
        await db.save_ticket_notification(ticket_id, 222, 1002)

        await _delete_ticket_notifications(mock_bot, db, ticket_id)

        # Уведомления должны быть удалены из БД
        notifications = await db.get_ticket_notifications(ticket_id)
        assert len(notifications) == 0

        # Сообщения должны быть удалены
        assert mock_bot.delete_message.call_count >= 2

    async def test_delete_notifications_except_engineer(
        self, db_with_open_ticket, fake_engineer_user, mock_bot
    ):
        """Проверяет удаление уведомлений кроме указанного инженера."""
        db, ticket_id = db_with_open_ticket

        await db.save_ticket_notification(ticket_id, fake_engineer_user.id, 1001)
        await db.save_ticket_notification(ticket_id, 222, 1002)

        await _delete_ticket_notifications(mock_bot, db, ticket_id, except_engineer_id=fake_engineer_user.id)

        # Для инженера 222 сообщение должно быть удалено
        delete_calls = [call for call in mock_bot.delete_message.call_args_list]
        assert any(call[1].get("chat_id") == 222 for call in delete_calls)


class TestCreateBitrixTask:
    """Тесты для _create_bitrix_task_for_ticket."""

    async def test_create_bitrix_task_no_ticket(self, db):
        """Проверяет создание задачи без заявки."""
        result = await _create_bitrix_task_for_ticket(db, None)
        assert result is None

    async def test_create_bitrix_task_no_engineer(self, db_with_open_ticket):
        """Проверяет создание задачи для заявки без инженера."""
        db, ticket_id = db_with_open_ticket
        ticket = await db.get_ticket(ticket_id)

        result = await _create_bitrix_task_for_ticket(db, ticket)
        assert result is None

    async def test_create_bitrix_task_no_bitrix_user_id(self, db_with_open_ticket, fake_engineer_user):
        """Проверяет создание задачи для инженера без bitrix_user_id."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер")
        await db.take_ticket(ticket_id, fake_engineer_user.id)
        ticket = await db.get_ticket(ticket_id)

        result = await _create_bitrix_task_for_ticket(db, ticket)
        assert result is None

    async def test_create_bitrix_task_success(self, db_with_open_ticket, fake_engineer_user):
        """Проверяет успешное создание задачи Битрикс24."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер")
        await db.take_ticket(ticket_id, fake_engineer_user.id)
        await db.set_bitrix_user_id(fake_engineer_user.id, 100500)
        ticket = await db.get_ticket(ticket_id)

        with patch("bot.handlers.engineer.create_task", new=AsyncMock(return_value=777)), \
             patch("bot.handlers.engineer.BITRIX_ATTACH_FILES", "0"):
            result = await _create_bitrix_task_for_ticket(db, ticket)
            assert result == 777

    async def test_create_bitrix_task_with_files(self, db_with_open_ticket, fake_engineer_user):
        """Проверяет создание задачи с прикреплением файлов."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер")
        await db.take_ticket(ticket_id, fake_engineer_user.id)
        await db.set_bitrix_user_id(fake_engineer_user.id, 100500)
        await db.save_media(ticket_id, "file_id", "photo", "media/ticket_1/001.jpg", 123, "client")
        ticket = await db.get_ticket(ticket_id)

        with patch("bot.handlers.engineer.create_task", new=AsyncMock(return_value=888)) as mock_create, \
             patch("bot.handlers.engineer.upload_file_to_bitrix", new=AsyncMock(return_value=99)) as mock_upload, \
             patch("bot.handlers.engineer.BITRIX_ATTACH_FILES", "1"):
            result = await _create_bitrix_task_for_ticket(db, ticket)
            assert result == 888
            mock_upload.assert_called_once()
            mock_create.assert_called_once()
            # Проверяем, что файл передан в задачу
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["uf_files"] == [99]

    async def test_create_bitrix_task_upload_error(self, db_with_open_ticket, fake_engineer_user):
        """Проверяет создание задачи при ошибке загрузки файла."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер")
        await db.take_ticket(ticket_id, fake_engineer_user.id)
        await db.set_bitrix_user_id(fake_engineer_user.id, 100500)
        await db.save_media(ticket_id, "file_id", "photo", "media/ticket_1/001.jpg", 123, "client")
        ticket = await db.get_ticket(ticket_id)

        with patch("bot.handlers.engineer.create_task", new=AsyncMock(return_value=None)) as mock_create, \
             patch("bot.handlers.engineer.upload_file_to_bitrix", new=AsyncMock(return_value=None)), \
             patch("bot.handlers.engineer.BITRIX_ATTACH_FILES", "1"):
            result = await _create_bitrix_task_for_ticket(db, ticket)
            assert result is None
            mock_create.assert_called_once()



# ===================== Тесты сценария «бывший инженер» =====================

class TestFormerEngineer:
    """Тесты для проверки прав бывшего инженера с активными заявками."""

    async def test_former_engineer_cannot_take_new_ticket(
        self, db_with_open_ticket, fake_engineer_user, engineer_fsm_context, mock_bot
    ):
        """Бывший инженер НЕ может взять новую заявку."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")
        # Деактивируем инженера
        await db.set_engineer_active(fake_engineer_user.id, 0)

        callback_data = TicketCallback(action="take", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.answer = AsyncMock()

        # is_engineer=True (из-за middleware), но прямая проверка db.is_engineer() не пройдёт
        await take_ticket(callback, callback_data, mock_bot, db, is_engineer=True, state=engineer_fsm_context)

        callback.answer.assert_called_with("У вас нет прав инженера.", show_alert=True)
        ticket = await db.get_ticket(ticket_id)
        assert ticket["status"] == "open"

    async def test_former_engineer_completes_own_ticket(
        self, db_with_open_ticket, fake_engineer_user, fake_user, engineer_fsm_context, mock_bot
    ):
        """Бывший инженер может завершить свою заявку."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")
        await db.take_ticket(ticket_id, fake_engineer_user.id)
        # Деактивируем инженера ПОСЛЕ взятия
        await db.set_engineer_active(fake_engineer_user.id, 0)

        callback_data = TicketCallback(action="complete", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.message = AsyncMock()
        callback.message.caption = None
        callback.message.html_text = "Тест"
        callback.message.edit_text = AsyncMock()
        callback.message.answer = AsyncMock()
        callback.answer = AsyncMock()

        # is_engineer=True благодаря has_active_tickets в middleware
        await complete_ticket_by_engineer(callback, callback_data, mock_bot, db, engineer_fsm_context, is_engineer=True)

        ticket = await db.get_ticket(ticket_id)
        assert ticket["status"] == "completed"

    async def test_former_engineer_cancels_own_ticket(
        self, db_with_open_ticket, fake_engineer_user, engineer_fsm_context, mock_bot
    ):
        """Бывший инженер может отменить свою заявку."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")
        await db.take_ticket(ticket_id, fake_engineer_user.id)
        await db.set_engineer_active(fake_engineer_user.id, 0)

        callback_data = TicketCallback(action="eng_cancel", ticket_id=ticket_id)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user
        callback.message = AsyncMock()
        callback.message.caption = None
        callback.message.html_text = "Тест"
        callback.message.edit_text = AsyncMock()
        callback.message.answer = AsyncMock()
        callback.answer = AsyncMock()

        await cancel_ticket_by_engineer(callback, callback_data, mock_bot, db, engineer_fsm_context, is_engineer=True)

        ticket = await db.get_ticket(ticket_id)
        assert ticket["status"] == "canceled"

    async def test_former_engineer_completes_via_menu(
        self, db_with_open_ticket, fake_engineer_user, fake_chat, engineer_fsm_context, mock_bot
    ):
        """Бывший инженер завершает заявку через меню."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")
        await db.take_ticket(ticket_id, fake_engineer_user.id)
        await db.set_engineer_active(fake_engineer_user.id, 0)
        await engineer_fsm_context.update_data(active_ticket_id=ticket_id)

        message = make_message(fake_engineer_user, fake_chat, text="✅ Завершить текущую")
        await complete_current_ticket_via_menu(message, mock_bot, db, engineer_fsm_context, is_engineer=True)

        ticket = await db.get_ticket(ticket_id)
        assert ticket["status"] == "completed"

    async def test_former_engineer_cancels_via_menu(
        self, db_with_open_ticket, fake_engineer_user, fake_chat, engineer_fsm_context, mock_bot
    ):
        """Бывший инженер отменяет заявку через меню."""
        db, ticket_id = db_with_open_ticket
        await db.add_engineer(fake_engineer_user.id, "Инженер Тест")
        await db.take_ticket(ticket_id, fake_engineer_user.id)
        await db.set_engineer_active(fake_engineer_user.id, 0)
        await engineer_fsm_context.update_data(active_ticket_id=ticket_id)

        message = make_message(fake_engineer_user, fake_chat, text="🚫 Отменить текущую")
        await cancel_current_ticket_via_menu(message, mock_bot, db, engineer_fsm_context, is_engineer=True)

        ticket = await db.get_ticket(ticket_id)
        assert ticket["status"] == "canceled"
