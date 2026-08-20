"""
Интеграционные тесты для хендлеров клиента (bot/handlers/client.py).

Проверяет:
- Команды /start, /help, /cancel, /my_requests
- FSM-воронку создания заявки (4 шага)
- Отмену заявки клиентом
- Процесс оценки заявки
- FAQ
"""
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Contact, PhotoSize
from conftest import make_message

from bot.handlers.client import (
    TicketForm,
    _last_ticket_start,
    cancel_ticket_by_client,
    check_fsm_expired,
    cmd_cancel,
    cmd_help,
    cmd_start,
    faq_callback_handler,
    my_requests,
    process_rating,
    save_rating_comment,
    show_faq,
    start_ticket,
    ticket_company_city,
    ticket_contact,
    ticket_machine_info,
    ticket_problem_media,
)
from bot.keyboards import FaqCallback, RatingCallback

# ===================== Вспомогательные фикстуры =====================

@pytest.fixture
async def client_fsm_context():
    """FSM-контекст для клиента."""
    storage = MemoryStorage()
    key = StorageKey(chat_id=123456789, user_id=123456789, bot_id=1)
    context = FSMContext(storage=storage, key=key)
    yield context
    await context.clear()


# ===================== Тесты /start =====================

class TestCmdStart:
    """Тесты для команды /start."""

    async def test_start_for_client(self, fake_user, fake_chat):
        """Проверяет /start для обычного пользователя."""
        message = make_message(fake_user, fake_chat, text="/start")
        await cmd_start(message, is_engineer=False)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "Hotline Service" in call_args[0][0]

    async def test_start_for_engineer(self, fake_engineer_user, fake_chat):
        """Проверяет /start для инженера."""
        message = make_message(fake_engineer_user, fake_chat, text="/start")
        await cmd_start(message, is_engineer=True)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "инженер" in call_args[0][0].lower()


# ===================== Тесты /help =====================

class TestCmdHelp:
    """Тесты для команды /help."""

    async def test_help_for_client(self, fake_user, fake_chat):
        """Проверяет /help для клиента."""
        message = make_message(fake_user, fake_chat, text="/help")
        await cmd_help(message, is_engineer=False)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "Помощь" in call_args[0][0]
        assert "Оставить заявку" in call_args[0][0]

    async def test_help_for_engineer(self, fake_engineer_user, fake_chat):
        """Проверяет /help для инженера."""
        message = make_message(fake_engineer_user, fake_chat, text="/help")
        await cmd_help(message, is_engineer=True)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "Меню инженера" in call_args[0][0]


# ===================== Тесты /cancel =====================

class TestCmdCancel:
    """Тесты для команды /cancel."""

    async def test_cancel_without_active_state(self, fake_user, fake_chat, client_fsm_context):
        """Проверяет /cancel без активного FSM-состояния."""
        message = make_message(fake_user, fake_chat, text="/cancel")
        await cmd_cancel(message, client_fsm_context)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "Нет активного действия" in call_args[0][0]

    async def test_cancel_with_active_state(self, fake_user, fake_chat, client_fsm_context):
        """Проверяет /cancel с активным FSM-состоянием."""
        await client_fsm_context.set_state(TicketForm.problem_media)
        message = make_message(fake_user, fake_chat, text="/cancel")
        await cmd_cancel(message, client_fsm_context)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "отменено" in call_args[0][0].lower()
        # Состояние должно быть очищено
        assert await client_fsm_context.get_state() is None

    async def test_cancel_by_button_text(self, fake_user, fake_chat, client_fsm_context):
        """Проверяет отмену по тексту кнопки '❌ Отмена'."""
        await client_fsm_context.set_state(TicketForm.machine_info)
        message = make_message(fake_user, fake_chat, text="❌ Отмена")
        await cmd_cancel(message, client_fsm_context)
        assert await client_fsm_context.get_state() is None


# ===================== Тесты FAQ =====================

class TestShowFaq:
    """Тесты для FAQ."""

    async def test_faq_content(self, fake_user, fake_chat, client_fsm_context):
        """Проверяет содержимое FAQ и наличие кнопок-разделов."""
        message = make_message(fake_user, fake_chat, text="❓ Частые вопросы")
        await show_faq(message, client_fsm_context)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        text = call_args[0][0]
        assert "Часто задаваемые вопросы" in text
        assert "8 800 777-38-56" in text
        # Должна быть клавиатура с разделами
        assert call_args[1]["reply_markup"] is not None


class TestFaqCallback:
    """Тесты для callback-обработчика FAQ."""

    def _make_callback(self):
        callback = AsyncMock()
        callback.message = AsyncMock()
        return callback

    async def test_faq_main(self, fake_user):
        """Проверяет возврат к списку разделов."""
        callback = self._make_callback()
        callback_data = FaqCallback(action="main")
        await faq_callback_handler(callback, callback_data)
        callback.answer.assert_called_once()
        callback.message.edit_text.assert_called_once()
        text = callback.message.edit_text.call_args[0][0]
        assert "Часто задаваемые вопросы" in text

    async def test_faq_section(self, fake_user):
        """Проверяет открытие списка вопросов раздела."""
        callback = self._make_callback()
        callback_data = FaqCallback(action="section", section_id="equipment")
        await faq_callback_handler(callback, callback_data)
        callback.answer.assert_called_once()
        callback.message.edit_text.assert_called_once()
        text = callback.message.edit_text.call_args[0][0]
        assert "Оборудование" in text

    async def test_faq_section_not_found(self, fake_user):
        """Проверяет открытие несуществующего раздела."""
        callback = self._make_callback()
        callback_data = FaqCallback(action="section", section_id="nonexistent")
        await faq_callback_handler(callback, callback_data)
        callback.answer.assert_called_with("Раздел не найден.", show_alert=True)

    async def test_faq_answer(self, fake_user):
        """Проверяет показ ответа на выбранный вопрос."""
        callback = self._make_callback()
        callback_data = FaqCallback(action="answer", section_id="equipment", question_id=1)
        await faq_callback_handler(callback, callback_data)
        callback.answer.assert_called_once()
        callback.message.edit_text.assert_called_once()
        text = callback.message.edit_text.call_args[0][0]
        assert "чиллер" in text.lower()

    async def test_faq_answer_not_found(self, fake_user):
        """Проверяет ответ на несуществующий вопрос."""
        callback = self._make_callback()
        callback_data = FaqCallback(action="answer", section_id="equipment", question_id=999)
        await faq_callback_handler(callback, callback_data)
        callback.answer.assert_called_with("Вопрос не найден.", show_alert=True)


# ===================== Тесты FSM-воронки создания заявки =====================

class TestTicketFormFSM:
    """Тесты FSM-воронки создания заявки."""

    async def test_start_ticket_no_active_ticket(
        self, fake_user, fake_chat, db, client_fsm_context, mock_bot
    ):
        """Проверяет начало создания заявки при отсутствии активной."""
        _last_ticket_start.clear()  # Сбрасываем антиспам между тестами
        message = make_message(fake_user, fake_chat, text="🛠 Оставить заявку на сервис ЧПУ")
        await start_ticket(message, client_fsm_context, mock_bot, db)

        # Должно быть установлено состояние problem_media
        assert await client_fsm_context.get_state() == TicketForm.problem_media.state

    async def test_start_ticket_with_active_ticket(
        self, fake_user, fake_chat, db, client_fsm_context, mock_bot
    ):
        """Проверяет попытку создать заявку при наличии активной."""
        _last_ticket_start.clear()  # Сбрасываем антиспам между тестами
        # Создаём активную заявку
        await db.create_ticket(
            client_id=fake_user.id,
            client_name="Тест",
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

        message = make_message(fake_user, fake_chat, text="🛠 Оставить заявку на сервис ЧПУ")
        await start_ticket(message, client_fsm_context, mock_bot, db)

        # Состояние не должно измениться
        assert await client_fsm_context.get_state() is None
        # Должно быть показано предупреждение
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "уже есть активная заявка" in call_args[0][0]

    async def test_start_ticket_anti_spam_cooldown(
        self, fake_user, fake_chat, db, client_fsm_context, mock_bot
    ):
        """Проверяет, что повторная попытка в течение кулдауна блокируется."""
        import time as _time
        _last_ticket_start.clear()
        _last_ticket_start[fake_user.id] = _time.monotonic()  # "только что" создал заявку
        message = make_message(fake_user, fake_chat, text="🛠 Оставить заявку на сервис ЧПУ")
        await start_ticket(message, client_fsm_context, mock_bot, db)

        # Состояние не должно измениться (антиспам сработал раньше)
        assert await client_fsm_context.get_state() is None
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "Не так быстро" in call_args[0][0]

    async def test_problem_media_valid_text(
        self, fake_user, fake_chat, client_fsm_context, mock_bot
    ):
        """Проверяет шаг 1: валидное описание проблемы."""
        await client_fsm_context.set_state(TicketForm.problem_media)
        message = make_message(
            fake_user, fake_chat,
            text="Станок не включается, ошибка AL-01 на панели управления"
        )
        await ticket_problem_media(message, client_fsm_context, mock_bot)

        # Должно быть установлено следующее состояние
        assert await client_fsm_context.get_state() == TicketForm.machine_info.state
        data = await client_fsm_context.get_data()
        assert "Станок не включается" in data["problem"]

    async def test_problem_media_too_short(
        self, fake_user, fake_chat, client_fsm_context, mock_bot
    ):
        """Проверяет шаг 1: слишком короткое описание (менее 5 символов)."""
        await client_fsm_context.set_state(TicketForm.problem_media)
        # Текст короче 5 символов должен быть отклонён
        message = make_message(fake_user, fake_chat, text="Нет")
        await ticket_problem_media(message, client_fsm_context, mock_bot)

        # Состояние не должно измениться
        assert await client_fsm_context.get_state() == TicketForm.problem_media.state
        # Должно быть показано сообщение об ошибке
        message.answer.assert_called()

    async def test_problem_media_empty(
        self, fake_user, fake_chat, client_fsm_context, mock_bot
    ):
        """Проверяет шаг 1: пустое сообщение."""
        await client_fsm_context.set_state(TicketForm.problem_media)
        message = make_message(fake_user, fake_chat, text="")
        await ticket_problem_media(message, client_fsm_context, mock_bot)

        # Состояние не должно измениться
        assert await client_fsm_context.get_state() == TicketForm.problem_media.state

    async def test_problem_media_with_photo(
        self, fake_user, fake_chat, client_fsm_context, mock_bot
    ):
        """Проверяет шаг 1: описание с фото."""
        await client_fsm_context.set_state(TicketForm.problem_media)
        photo = PhotoSize(
            file_id="test_photo_id",
            file_unique_id="unique",
            width=800,
            height=600,
        )
        message = make_message(
            fake_user, fake_chat,
            caption="Вот фото проблемы со станком",
            photo=[photo],
        )
        await ticket_problem_media(message, client_fsm_context, mock_bot)

        data = await client_fsm_context.get_data()
        assert data["media_id"] == "test_photo_id"
        assert data["media_type"] == "photo"

    async def test_machine_info_valid_text(
        self, fake_user, fake_chat, client_fsm_context, mock_bot
    ):
        """Проверяет шаг 2: валидная информация о станке."""
        await client_fsm_context.set_state(TicketForm.machine_info)
        message = make_message(fake_user, fake_chat, text="Wattsan 1610, Fanuc 0i-MF")
        await ticket_machine_info(message, client_fsm_context, mock_bot)

        assert await client_fsm_context.get_state() == TicketForm.company_city.state
        data = await client_fsm_context.get_data()
        assert "Wattsan" in data["machine_info"]

    async def test_machine_info_too_short(
        self, fake_user, fake_chat, client_fsm_context, mock_bot
    ):
        """Проверяет шаг 2: слишком короткая информация."""
        await client_fsm_context.set_state(TicketForm.machine_info)
        message = make_message(fake_user, fake_chat, text="W")
        await ticket_machine_info(message, client_fsm_context, mock_bot)

        # Состояние не должно измениться
        assert await client_fsm_context.get_state() == TicketForm.machine_info.state

    async def test_machine_info_photo_without_text(
        self, fake_user, fake_chat, client_fsm_context, mock_bot
    ):
        """Проверяет шаг 2: фото шильдика без текста."""
        await client_fsm_context.set_state(TicketForm.machine_info)
        photo = PhotoSize(
            file_id="shield_photo_id",
            file_unique_id="unique",
            width=800,
            height=600,
        )
        message = make_message(fake_user, fake_chat, photo=[photo])
        await ticket_machine_info(message, client_fsm_context, mock_bot)

        data = await client_fsm_context.get_data()
        assert data["machine_info"] == "Фото шильдика прикреплено"
        assert data["machine_media_id"] == "shield_photo_id"

    async def test_company_city_valid(
        self, fake_user, fake_chat, client_fsm_context, mock_bot
    ):
        """Проверяет шаг 3: валидный город и компания."""
        await client_fsm_context.set_state(TicketForm.company_city)
        message = make_message(fake_user, fake_chat, text="Москва, ИНН 7712345678")
        await ticket_company_city(message, client_fsm_context, mock_bot)

        assert await client_fsm_context.get_state() == TicketForm.contact.state
        data = await client_fsm_context.get_data()
        assert "Москва" in data["company_city"]

    async def test_company_city_too_short(
        self, fake_user, fake_chat, client_fsm_context, mock_bot
    ):
        """Проверяет шаг 3: слишком короткий ввод."""
        await client_fsm_context.set_state(TicketForm.company_city)
        message = make_message(fake_user, fake_chat, text="М")
        await ticket_company_city(message, client_fsm_context, mock_bot)

        # Состояние не должно измениться
        assert await client_fsm_context.get_state() == TicketForm.company_city.state

    async def test_contact_valid_text(
        self, fake_user, fake_chat, db, client_fsm_context, mock_bot, mock_media_save
    ):
        """Проверяет шаг 4: валидный контакт текстом."""
        await client_fsm_context.set_state(TicketForm.contact)
        await client_fsm_context.update_data(
            problem="Проблема",
            machine_info="Wattsan",
            company_city="Москва",
        )
        message = make_message(fake_user, fake_chat, text="+79991234567")

        with patch("bot.handlers.client.ADMIN_IDS", []):
            await ticket_contact(message, client_fsm_context, mock_bot, db)

        # Состояние должно быть очищено
        assert await client_fsm_context.get_state() is None
        # Заявка должна быть создана
        tickets = await db.get_client_tickets(fake_user.id)
        assert len(tickets) == 1

    async def test_contact_via_shared_contact(
        self, fake_user, fake_chat, db, client_fsm_context, mock_bot, mock_media_save
    ):
        """Проверяет шаг 4: контакт через кнопку request_contact."""
        await client_fsm_context.set_state(TicketForm.contact)
        await client_fsm_context.update_data(
            problem="Проблема",
            machine_info="Wattsan",
            company_city="Москва",
        )
        contact = Contact(
            phone_number="+79991234567",
            first_name="Иван",
            user_id=fake_user.id,
        )
        message = make_message(fake_user, fake_chat, contact=contact)

        with patch("bot.handlers.client.ADMIN_IDS", []):
            await ticket_contact(message, client_fsm_context, mock_bot, db)

        tickets = await db.get_client_tickets(fake_user.id)
        assert len(tickets) == 1
        assert "+79991234567" in tickets[0]["contact"]

    async def test_contact_too_short(
        self, fake_user, fake_chat, db, client_fsm_context, mock_bot
    ):
        """Проверяет шаг 4: слишком короткий контакт."""
        await client_fsm_context.set_state(TicketForm.contact)
        message = make_message(fake_user, fake_chat, text="+7")
        await ticket_contact(message, client_fsm_context, mock_bot, db)

        # Состояние не должно измениться
        assert await client_fsm_context.get_state() == TicketForm.contact.state


# ===================== Тесты таймаута FSM =====================

class TestCheckFsmExpired:
    """Тесты для автоматического сброса воронки по таймауту."""

    async def test_not_expired_returns_false(self, fake_user, fake_chat, client_fsm_context, db):
        """Проверяет, что свежая воронка не сбрасывается."""
        from datetime import datetime, timezone

        await client_fsm_context.update_data(
            fsm_started_at=datetime.now(timezone.utc).isoformat()
        )
        message = make_message(fake_user, fake_chat, text="Описание проблемы")
        result = await check_fsm_expired(message, client_fsm_context)
        assert result is False

    async def test_expired_returns_true_and_clears(self, fake_user, fake_chat, client_fsm_context, db):
        """Проверяет, что просроченная воронка сбрасывается."""
        from datetime import datetime, timedelta, timezone

        # Время старта — позади таймаута (FSM_TIMEOUT по умолчанию 1800 сек)
        old_time = datetime.now(timezone.utc) - timedelta(seconds=99999)
        await client_fsm_context.update_data(
            fsm_started_at=old_time.isoformat()
        )
        message = make_message(fake_user, fake_chat, text="Описание проблемы")
        result = await check_fsm_expired(message, client_fsm_context)
        assert result is True
        # Состояние должно быть очищено
        assert await client_fsm_context.get_state() is None
        # Клиент должен получить уведомление об истечении
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "истекло" in call_args[0][0].lower()

    async def test_no_started_at_returns_false(self, fake_user, fake_chat, client_fsm_context, db):
        """Проверяет, что отсутствие метки времени не приводит к сбросу."""
        message = make_message(fake_user, fake_chat, text="Описание проблемы")
        result = await check_fsm_expired(message, client_fsm_context)
        assert result is False


# ===================== Тесты отмены заявки клиентом =====================

class TestCancelTicketByClient:
    """Тесты для отмены заявки клиентом."""

    async def test_cancel_no_active_ticket(self, fake_user, fake_chat, db):
        """Проверяет отмену при отсутствии активной заявки."""
        message = make_message(fake_user, fake_chat, text="❌ Отменить / Закрыть заявку")
        await cancel_ticket_by_client(message, AsyncMock(), db)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "нет активных заявок" in call_args[0][0].lower()

    async def test_cancel_active_ticket(self, fake_user, fake_chat, db):
        """Проверяет успешную отмену активной заявки."""
        ticket_id = await db.create_ticket(
            client_id=fake_user.id,
            client_name="Тест",
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

        mock_bot = AsyncMock()
        message = make_message(fake_user, fake_chat, text="❌ Отменить / Закрыть заявку")
        await cancel_ticket_by_client(message, mock_bot, db)

        # Заявка должна быть закрыта
        ticket = await db.get_ticket(ticket_id)
        assert ticket["status"] == "canceled"
        assert ticket["close_comment"] == "Отменено клиентом"

    async def test_cancel_ticket_notifies_engineer(self, fake_user, fake_chat, db, fake_engineer_user):
        """Проверяет уведомление инженера об отмене заявки."""
        ticket_id = await db.create_ticket(
            client_id=fake_user.id,
            client_name="Тест",
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
        await db.add_engineer(fake_engineer_user.id, "Инженер")
        await db.take_ticket(ticket_id, fake_engineer_user.id)

        mock_bot = AsyncMock()
        message = make_message(fake_user, fake_chat, text="❌ Отменить / Закрыть заявку")
        await cancel_ticket_by_client(message, mock_bot, db)

        # Инженер должен быть уведомлён
        mock_bot.send_message.assert_called()


# ===================== Тесты оценки заявки =====================

class TestProcessRating:
    """Тесты для процесса оценки заявки."""

    async def test_rating_success(self, fake_user, fake_chat, db, client_fsm_context):
        """Проверяет успешную оценку заявки."""
        ticket_id = await db.create_ticket(
            client_id=fake_user.id,
            client_name="Тест",
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

        callback_data = RatingCallback(ticket_id=ticket_id, value=5)
        callback = AsyncMock()
        callback.from_user = fake_user
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()

        await process_rating(callback, callback_data, db, client_fsm_context, AsyncMock())

        # Оценка должна быть сохранена
        rating = await db.get_rating_for_ticket(ticket_id)
        assert rating is not None
        assert rating["rating"] == 5

        # Должно быть установлено состояние для комментария
        assert await client_fsm_context.get_state() == TicketForm.rating_comment.state

    async def test_rating_wrong_user(self, fake_user, fake_engineer_user, fake_chat, db, client_fsm_context):
        """Проверяет оценку заявки другим пользователем."""
        ticket_id = await db.create_ticket(
            client_id=fake_user.id,
            client_name="Тест",
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

        callback_data = RatingCallback(ticket_id=ticket_id, value=5)
        callback = AsyncMock()
        callback.from_user = fake_engineer_user  # Другой пользователь
        callback.answer = AsyncMock()

        await process_rating(callback, callback_data, db, client_fsm_context, AsyncMock())

        # Оценка не должна быть сохранена
        rating = await db.get_rating_for_ticket(ticket_id)
        assert rating is None
        callback.answer.assert_called_with("Вы не можете оценить эту заявку.", show_alert=True)

    async def test_rating_nonexistent_ticket(self, fake_user, db, client_fsm_context):
        """Проверяет оценку несуществующей заявки."""
        callback_data = RatingCallback(ticket_id=99999, value=5)
        callback = AsyncMock()
        callback.from_user = fake_user
        callback.answer = AsyncMock()

        await process_rating(callback, callback_data, db, client_fsm_context, AsyncMock())
        callback.answer.assert_called_with("Заявка не найдена.", show_alert=True)


class TestSaveRatingComment:
    """Тесты для сохранения комментария к оценке."""

    async def test_save_comment(self, fake_user, fake_chat, db, client_fsm_context):
        """Проверяет сохранение комментария."""
        ticket_id = await db.create_ticket(
            client_id=fake_user.id,
            client_name="Тест",
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
        await db.save_rating(ticket_id, fake_user.id, 5)

        await client_fsm_context.set_state(TicketForm.rating_comment)
        await client_fsm_context.update_data(rating_ticket_id=ticket_id)

        message = make_message(fake_user, fake_chat, text="Отличный сервис!")
        await save_rating_comment(message, client_fsm_context, db)

        rating = await db.get_rating_for_ticket(ticket_id)
        assert rating["comment"] == "Отличный сервис!"

    async def test_save_comment_without_ticket_id(self, fake_user, fake_chat, db, client_fsm_context):
        """Проверяет сохранение комментария без ticket_id в состоянии."""
        await client_fsm_context.set_state(TicketForm.rating_comment)
        # Не устанавливаем rating_ticket_id

        message = make_message(fake_user, fake_chat, text="Комментарий")
        await save_rating_comment(message, client_fsm_context, db)

        # Состояние должно быть очищено
        assert await client_fsm_context.get_state() is None


# ===================== Тесты /my_requests =====================

class TestMyRequests:
    """Тесты для команды /my_requests."""

    async def test_my_requests_empty(self, fake_user, fake_chat, db):
        """Проверяет /my_requests без заявок."""
        message = make_message(fake_user, fake_chat, text="/my_requests")
        await my_requests(message, db, is_engineer=False)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "пока нет заявок" in call_args[0][0].lower()

    async def test_my_requests_with_tickets(self, fake_user, fake_chat, db):
        """Проверяет /my_requests с заявками."""
        await db.create_ticket(
            client_id=fake_user.id,
            client_name="Тест",
            company="",
            equipment_type="",
            brand="",
            cnc_model="",
            problem="Проблема 1",
            media_id=None,
            city="",
            inn_contract="",
            contact="12345",
        )

        message = make_message(fake_user, fake_chat, text="/my_requests")
        await my_requests(message, db, is_engineer=False)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "Заявка #1" in call_args[0][0] or "#1" in call_args[0][0]

    async def test_my_requests_for_engineer(self, fake_engineer_user, fake_chat, db):
        """Проверяет /my_requests для инженера (показывает его заявки в работе)."""
        await db.add_engineer(fake_engineer_user.id, "Инженер")

        message = make_message(fake_engineer_user, fake_chat, text="/my_requests")
        await my_requests(message, db, is_engineer=True)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "нет активных заявок" in call_args[0][0].lower()