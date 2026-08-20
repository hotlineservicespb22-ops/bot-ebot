"""
Интеграционные тесты для хендлеров администратора (bot/handlers/admin.py).

Проверяет:
- Админ-панель (/admin)
- Управление администраторами (/add_admin, /del_admin, /list_admin)
- Управление инженерами (/add_eng, /del_eng, /list_eng, /bulk_add_eng)
- Привязку к Битрикс24 (/set_bitrix)
- Статистику (/stats)
- Экспорт CSV (/export)
- Callback-обработчики админ-панели
"""
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import Chat
from conftest import make_message

from bot.handlers.admin import (
    add_eng_step_bitrix,
    add_eng_step_name,
    add_eng_step_tg_id,
    admin_panel_callback,
    cmd_add_admin,
    cmd_add_engineer,
    cmd_admin_panel,
    cmd_bulk_add_engineers,
    cmd_del_admin,
    cmd_del_engineer,
    cmd_export_tickets,
    cmd_list_admin,
    cmd_list_engineers,
    cmd_set_bitrix,
    cmd_stats,
    generate_csv,
    show_duty_management,
)
from bot.keyboards import AdminCallback

# ===================== Вспомогательные фикстуры =====================

@pytest.fixture
async def db_with_admin(db, fake_admin_user):
    """База данных с администратором."""
    await db.add_admin(fake_admin_user.id)
    return db


@pytest.fixture
def admin_chat(fake_admin_user):
    """Чат администратора."""
    return Chat(
        id=fake_admin_user.id,
        type="private",
        first_name=fake_admin_user.first_name,
    )


# ===================== Тесты админ-панели =====================

class TestAdminPanel:
    """Тесты для админ-панели."""

    async def test_admin_panel_for_admin(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет доступ к админ-панели для администратора."""
        message = make_message(fake_admin_user, admin_chat, text="/admin")
        await cmd_admin_panel(message, db_with_admin)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "Админ-панель" in call_args[0][0]

    async def test_admin_panel_for_non_admin(self, fake_user, fake_chat, db):
        """Проверяет отказ в доступе к админ-панели для не-администратора."""
        message = make_message(fake_user, fake_chat, text="/admin")
        await cmd_admin_panel(message, db)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "нет прав администратора" in call_args[0][0].lower()


# ===================== Тесты управления администраторами =====================

class TestAdminManagement:
    """Тесты для управления администраторами."""

    async def test_add_admin_success(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет успешное добавление администратора."""
        message = make_message(fake_admin_user, admin_chat, text="/add_admin 111222333")
        await cmd_add_admin(message, db_with_admin)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "добавлен" in call_args[0][0].lower()

        # Проверяем, что админ добавлен в БД
        admins = await db_with_admin.get_admins()
        admin_ids = [a["user_id"] for a in admins]
        assert 111222333 in admin_ids

    async def test_add_admin_no_args(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет добавление администратора без аргументов."""
        message = make_message(fake_admin_user, admin_chat, text="/add_admin")
        await cmd_add_admin(message, db_with_admin)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "Используйте" in call_args[0][0]

    async def test_add_admin_invalid_id(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет добавление администратора с некорректным ID."""
        message = make_message(fake_admin_user, admin_chat, text="/add_admin abc")
        await cmd_add_admin(message, db_with_admin)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "числом" in call_args[0][0].lower()

    async def test_add_admin_for_non_admin(self, fake_user, fake_chat, db):
        """Проверяет отказ в добавлении администратора для не-администратора."""
        message = make_message(fake_user, fake_chat, text="/add_admin 111222333")
        await cmd_add_admin(message, db)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "нет прав администратора" in call_args[0][0].lower()

    async def test_del_admin_success(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет успешное удаление администратора."""
        await db_with_admin.add_admin(111222333)
        message = make_message(fake_admin_user, admin_chat, text="/del_admin 111222333")
        await cmd_del_admin(message, db_with_admin)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "удален" in call_args[0][0].lower()

    async def test_del_admin_from_env(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет попытку удаления администратора из .env."""
        with patch("bot.handlers.admin.ADMIN_IDS", [111222333]):
            message = make_message(fake_admin_user, admin_chat, text="/del_admin 111222333")
            await cmd_del_admin(message, db_with_admin)
            message.answer.assert_called_once()
            call_args = message.answer.call_args
            assert "нельзя удалить" in call_args[0][0].lower()

    async def test_list_admin(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет список администраторов."""
        await db_with_admin.add_admin(111222333)
        message = make_message(fake_admin_user, admin_chat, text="/list_admin")
        with patch("bot.handlers.admin.ADMIN_IDS", [fake_admin_user.id]):
            await cmd_list_admin(message, db_with_admin, is_admin=True)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "администраторов" in call_args[0][0].lower()


# ===================== Тесты управления инженерами =====================

class TestEngineerManagement:
    """Тесты для управления инженерами."""

    async def test_add_engineer_success(self, fake_admin_user, admin_chat, db_with_admin, fsm_context):
        """Проверяет успешное добавление инженера (пошаговый FSM-диалог)."""
        # Шаг 0: /add_eng запускает диалог
        message = make_message(fake_admin_user, admin_chat, text="/add_eng")
        await cmd_add_engineer(message, db_with_admin, is_admin=True, state=fsm_context)
        message.answer.assert_called_once()
        assert "Шаг 1/3" in message.answer.call_args[0][0]

        # Шаг 1: Telegram ID инженера
        msg1 = make_message(fake_admin_user, admin_chat, text="999888777")
        await add_eng_step_tg_id(msg1, fsm_context, db_with_admin)
        assert "Шаг 2/3" in msg1.answer.call_args[0][0]

        # Шаг 2: имя инженера
        msg2 = make_message(fake_admin_user, admin_chat, text="Иван Петров")
        await add_eng_step_name(msg2, fsm_context, db_with_admin)
        assert "Шаг 3/3" in msg2.answer.call_args[0][0]

        # Шаг 3: ID в Битрикс24 (0 — пропустить привязку)
        msg3 = make_message(fake_admin_user, admin_chat, text="0")
        await add_eng_step_bitrix(msg3, fsm_context, db_with_admin)

        # Проверяем, что инженер добавлен в БД
        engineers = await db_with_admin.get_engineers()
        eng_ids = [e["user_id"] for e in engineers]
        assert 999888777 in eng_ids

    async def test_add_engineer_starts_flow(self, fake_admin_user, admin_chat, db_with_admin, fsm_context):
        """Проверяет, что /add_eng запускает диалог (аргументы в команде не обязательны)."""
        message = make_message(fake_admin_user, admin_chat, text="/add_eng")
        await cmd_add_engineer(message, db_with_admin, is_admin=True, state=fsm_context)
        message.answer.assert_called_once()
        assert "Шаг 1/3" in message.answer.call_args[0][0]

    async def test_add_engineer_invalid_id(self, fake_admin_user, admin_chat, db_with_admin, fsm_context):
        """Проверяет ввод некорректного Telegram ID на шаге 1."""
        message = make_message(fake_admin_user, admin_chat, text="/add_eng")
        await cmd_add_engineer(message, db_with_admin, is_admin=True, state=fsm_context)

        msg = make_message(fake_admin_user, admin_chat, text="abc")
        await add_eng_step_tg_id(msg, fsm_context, db_with_admin)
        msg.answer.assert_called_once()
        call_args = msg.answer.call_args
        assert "числом" in call_args[0][0].lower()

    async def test_add_engineer_already_admin(self, fake_admin_user, admin_chat, db_with_admin, fsm_context):
        """Проверяет ввод ID администратора на шаге 1."""
        with patch("bot.handlers.admin.ADMIN_IDS", [999888777]):
            message = make_message(fake_admin_user, admin_chat, text="/add_eng")
            await cmd_add_engineer(message, db_with_admin, is_admin=True, state=fsm_context)

            msg = make_message(fake_admin_user, admin_chat, text="999888777")
            await add_eng_step_tg_id(msg, fsm_context, db_with_admin)
            msg.answer.assert_called_once()
            call_args = msg.answer.call_args
            assert "администратор" in call_args[0][0].lower()

    async def test_del_engineer_success(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет успешное удаление инженера."""
        await db_with_admin.add_engineer(999888777, "Иван")
        message = make_message(fake_admin_user, admin_chat, text="/del_eng 999888777")
        await cmd_del_engineer(message, db_with_admin, is_admin=True)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "удален" in call_args[0][0].lower()

    async def test_list_engineers_empty(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет список инженеров (пустой)."""
        message = make_message(fake_admin_user, admin_chat, text="/list_eng")
        await cmd_list_engineers(message, db_with_admin, is_admin=True)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "пуст" in call_args[0][0].lower()

    async def test_list_engineers_with_data(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет список инженеров с данными."""
        await db_with_admin.add_engineer(111, "Иван")
        await db_with_admin.add_engineer(222, "Пётр")
        message = make_message(fake_admin_user, admin_chat, text="/list_eng")
        await cmd_list_engineers(message, db_with_admin, is_admin=True)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "Иван" in call_args[0][0]
        assert "Пётр" in call_args[0][0]


class TestBulkAddEngineers:
    """Тесты для массового добавления инженеров."""

    async def test_bulk_add_success(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет успешное массовое добавление."""
        message = make_message(
            fake_admin_user, admin_chat,
            text="/bulk_add_eng 111:Иван Иванов,222:Пётр Петров"
        )
        await cmd_bulk_add_engineers(message, db_with_admin, is_admin=True)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "Добавлены" in call_args[0][0]

        engineers = await db_with_admin.get_engineers()
        assert len(engineers) == 2

    async def test_bulk_add_multiline(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет массовое добавление построчно."""
        message = make_message(
            fake_admin_user, admin_chat,
            text="/bulk_add_eng\n111:Иван Иванов\n222:Пётр Петров"
        )
        await cmd_bulk_add_engineers(message, db_with_admin, is_admin=True)

        engineers = await db_with_admin.get_engineers()
        assert len(engineers) == 2

    async def test_bulk_add_no_data(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет массовое добавление без данных."""
        message = make_message(fake_admin_user, admin_chat, text="/bulk_add_eng")
        await cmd_bulk_add_engineers(message, db_with_admin, is_admin=True)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "Используйте" in call_args[0][0]

    async def test_bulk_add_invalid_format(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет массовое добавление с некорректным форматом."""
        message = make_message(fake_admin_user, admin_chat, text="/bulk_add_eng invalid_data")
        await cmd_bulk_add_engineers(message, db_with_admin, is_admin=True)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "Неверный формат" in call_args[0][0] or "Ошибки" in call_args[0][0]

    async def test_bulk_add_invalid_id(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет массовое добавление с некорректным ID."""
        message = make_message(fake_admin_user, admin_chat, text="/bulk_add_eng abc:Иван")
        await cmd_bulk_add_engineers(message, db_with_admin, is_admin=True)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "числом" in call_args[0][0].lower()

    async def test_bulk_add_skips_admins(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет, что массовое добавление пропускает администраторов."""
        with patch("bot.handlers.admin.ADMIN_IDS", [111]):
            message = make_message(fake_admin_user, admin_chat, text="/bulk_add_eng 111:Иван,222:Пётр")
            await cmd_bulk_add_engineers(message, db_with_admin, is_admin=True)

        engineers = await db_with_admin.get_engineers()
        eng_ids = [e["user_id"] for e in engineers]
        assert 111 not in eng_ids
        assert 222 in eng_ids


# ===================== Тесты привязки к Битрикс24 =====================

class TestSetBitrix:
    """Тесты для /set_bitrix."""

    async def test_set_bitrix_success(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет успешную привязку к Битрикс24."""
        await db_with_admin.add_engineer(999, "Иван")
        message = make_message(fake_admin_user, admin_chat, text="/set_bitrix 999 42")
        await cmd_set_bitrix(message, db_with_admin, is_admin=True)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "установлен" in call_args[0][0].lower()

        bitrix_id = await db_with_admin.get_bitrix_user_id(999)
        assert bitrix_id == 42

    async def test_set_bitrix_engineer_not_found(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет привязку для несуществующего инженера."""
        message = make_message(fake_admin_user, admin_chat, text="/set_bitrix 999 42")
        await cmd_set_bitrix(message, db_with_admin, is_admin=True)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "не найден" in call_args[0][0].lower()

    async def test_set_bitrix_invalid_ids(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет привязку с некорректными ID."""
        message = make_message(fake_admin_user, admin_chat, text="/set_bitrix abc def")
        await cmd_set_bitrix(message, db_with_admin, is_admin=True)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "числами" in call_args[0][0].lower()


# ===================== Тесты статистики =====================

class TestStats:
    """Тесты для /stats."""

    async def test_stats_empty(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет статистику без заявок."""
        message = make_message(fake_admin_user, admin_chat, text="/stats")
        await cmd_stats(message, db_with_admin, is_admin=True)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "Заявок пока нет" in call_args[0][0]

    async def test_stats_with_tickets(self, fake_admin_user, admin_chat, db_with_admin, fake_user):
        """Проверяет статистику с заявками."""
        await db_with_admin.create_ticket(
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
        message = make_message(fake_admin_user, admin_chat, text="/stats")
        await cmd_stats(message, db_with_admin, is_admin=True)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "Всего заявок" in call_args[0][0]

    async def test_stats_not_admin(self, fake_user, fake_chat, db):
        """Проверяет отказ в статистике для не-администратора (явная проверка через БД)."""
        message = make_message(fake_user, fake_chat, text="/stats")
        await cmd_stats(message, db, is_admin=False)
        # Теперь функция явно проверяет права через БД и отправляет сообщение об отказе
        message.answer.assert_called_once()
        assert "нет прав" in message.answer.call_args[0][0].lower()


# ===================== Тесты экспорта CSV =====================

class TestExportCsv:
    """Тесты для /export."""

    async def test_export_empty(self, fake_admin_user, admin_chat, db_with_admin, mock_bot):
        """Проверяет экспорт без заявок."""
        message = make_message(fake_admin_user, admin_chat, text="/export")
        await cmd_export_tickets(message, db_with_admin, mock_bot, is_admin=True)
        # Должно быть сообщение о пустой базе
        message.answer.assert_called()

    async def test_export_with_tickets(self, fake_admin_user, admin_chat, db_with_admin, fake_user, mock_bot):
        """Проверяет экспорт с заявками."""
        await db_with_admin.create_ticket(
            client_id=fake_user.id,
            client_name="Клиент",
            company="ООО Тест",
            equipment_type="",
            brand="",
            cnc_model="Wattsan",
            problem="Проблема",
            media_id=None,
            city="Москва",
            inn_contract="",
            contact="12345",
        )
        message = make_message(fake_admin_user, admin_chat, text="/export")
        await cmd_export_tickets(message, db_with_admin, mock_bot, is_admin=True)
        # Должен быть отправлен документ
        mock_bot.send_document.assert_called_once()

    async def test_export_invalid_dates(self, fake_admin_user, admin_chat, db_with_admin, mock_bot):
        """Проверяет экспорт с некорректными датами."""
        message = make_message(fake_admin_user, admin_chat, text="/export invalid dates")
        await cmd_export_tickets(message, db_with_admin, mock_bot, is_admin=True)
        # При edit=False обработчик сначала отправляет "Готовлю отчет...",
        # затем редактирует это сообщение (status_msg) с ошибкой
        message.answer.assert_called()
        status_msg = message.answer.return_value
        status_msg.edit_text.assert_called_once()
        error_text = status_msg.edit_text.call_args[0][0]
        assert "Неверный формат дат" in error_text or "ГГГГ-ММ-ДД" in error_text


class TestGenerateCsv:
    """Тесты для generate_csv."""

    def test_generate_csv_empty(self):
        """Проверяет генерацию CSV без заявок."""
        result = generate_csv([])
        assert isinstance(result, bytes)
        assert b"ID" in result  # Заголовок

    def test_generate_csv_with_data(self):
        """Проверяет генерацию CSV с заявками (все поля)."""
        tickets = [
            {
                "id": 1,
                "client_id": 123,
                "client_name": "Клиент",
                "company": "ООО Тест",
                "equipment_type": "лазер",
                "brand": "Wattsan",
                "cnc_model": "1610",
                "machine_info": "Wattsan 1610",
                "company_city": "Москва",
                "problem": "Проблема",
                "media_id": None,
                "city": "Москва",
                "inn_contract": "123",
                "contact": "12345",
                "status": "open",
                "engineer_id": None,
                "close_comment": None,
                "created_at": "2026-01-01T10:00:00+00:00",
                "closed_at": None,
            }
        ]
        result = generate_csv(tickets)
        assert isinstance(result, bytes)
        assert b"123" in result
        assert b"Wattsan" in result


# ===================== Тесты управления дежурными =====================

class TestDutyManagement:
    """Тесты для управления дежурными инженерами."""

    async def test_show_duty_empty(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет панель дежурных без инженеров."""
        message = make_message(fake_admin_user, admin_chat, text="duty")
        await show_duty_management(message, db_with_admin)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "пуст" in call_args[0][0].lower()

    async def test_show_duty_with_engineers(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет панель дежурных с инженерами."""
        await db_with_admin.add_engineer(111, "Иван")
        await db_with_admin.add_engineer(222, "Пётр")
        message = make_message(fake_admin_user, admin_chat, text="duty")
        await show_duty_management(message, db_with_admin)
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        assert "Управление дежурными" in call_args[0][0]

    async def test_duty_on_callback(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет включение инженера в дежурные через callback."""
        await db_with_admin.add_engineer(111, "Иван")
        await db_with_admin.set_engineer_active(111, 0)

        callback_data = AdminCallback(action="duty_on", engineer_id=111)
        callback = AsyncMock()
        callback.from_user = fake_admin_user
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        await admin_panel_callback(callback, callback_data, db_with_admin, AsyncMock())

        # Инженер должен быть включён
        assert await db_with_admin.is_engineer(111) is True

    async def test_duty_off_callback(self, fake_admin_user, admin_chat, db_with_admin):
        """Проверяет исключение инженера из дежурных через callback."""
        await db_with_admin.add_engineer(111, "Иван")

        callback_data = AdminCallback(action="duty_off", engineer_id=111)
        callback = AsyncMock()
        callback.from_user = fake_admin_user
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        await admin_panel_callback(callback, callback_data, db_with_admin, AsyncMock())

        # Инженер должен быть выключен
        assert await db_with_admin.is_engineer(111) is False


# ===================== Тесты callback-обработчиков =====================

class TestAdminCallbacks:
    """Тесты для callback-обработчиков админ-панели."""

    async def test_callback_not_admin(self, fake_user, db):
        """Проверяет отказ в callback для не-администратора."""
        callback_data = AdminCallback(action="stats")
        callback = AsyncMock()
        callback.from_user = fake_user
        callback.answer = AsyncMock()

        await admin_panel_callback(callback, callback_data, db, AsyncMock())
        callback.answer.assert_called_with("🚫 Нет доступа.", show_alert=True)

    async def test_callback_back(self, fake_admin_user, db_with_admin):
        """Проверяет возврат в админ-панель."""
        callback_data = AdminCallback(action="back")
        callback = AsyncMock()
        callback.from_user = fake_admin_user
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        await admin_panel_callback(callback, callback_data, db_with_admin, AsyncMock())
        callback.message.edit_text.assert_called_once()
        call_args = callback.message.edit_text.call_args
        assert "Админ-панель" in call_args[0][0]