"""
Unit-тесты для модуля keyboards.py.

Проверяет:
- Генерацию Reply-клавиатур
- Генерацию Inline-клавиатур
- CallbackData классы (TicketCallback, RatingCallback, AdminCallback)
- Корректность callback_data
"""
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

from bot.keyboards import (
    AdminCallback,
    RatingCallback,
    TicketCallback,
    active_ticket_menu_kb,
    admin_menu_kb,
    back_to_admin_kb,
    cancel_kb,
    contact_kb,
    engineer_active_ticket_kb,
    engineer_default_menu_kb,
    engineer_detail_kb,
    engineer_duty_kb,
    engineer_list_kb,
    engineer_main_menu,
    engineer_redirect_kb,
    engineer_select_client_kb,
    engineer_ticket_control_kb,
    main_menu,
    rating_kb,
    ticket_action_kb,
)


class TestCallbackData:
    """Тесты для CallbackData классов."""

    def test_ticket_callback_pack(self):
        """Проверяет упаковку TicketCallback."""
        cb = TicketCallback(action="take", ticket_id=42)
        packed = cb.pack()
        assert packed == "ticket:take:42"

    def test_ticket_callback_unpack(self):
        """Проверяет распаковку TicketCallback."""
        cb = TicketCallback.unpack("ticket:complete:123")
        assert cb.action == "complete"
        assert cb.ticket_id == 123

    def test_rating_callback_pack(self):
        """Проверяет упаковку RatingCallback."""
        cb = RatingCallback(ticket_id=10, value=5)
        packed = cb.pack()
        assert packed == "rating:10:5"

    def test_rating_callback_unpack(self):
        """Проверяет распаковку RatingCallback."""
        cb = RatingCallback.unpack("rating:7:3")
        assert cb.ticket_id == 7
        assert cb.value == 3

    def test_admin_callback_pack(self):
        """Проверяет упаковку AdminCallback."""
        cb = AdminCallback(action="stats")
        packed = cb.pack()
        assert packed == "admin:stats:0"

    def test_admin_callback_with_engineer_id(self):
        """Проверяет упаковку AdminCallback с engineer_id."""
        cb = AdminCallback(action="duty_on", engineer_id=999)
        packed = cb.pack()
        assert "duty_on" in packed
        assert "999" in packed


class TestReplyKeyboards:
    """Тесты для Reply-клавиатур."""

    def test_main_menu_structure(self):
        """Проверяет структуру главного меню."""
        kb = main_menu()
        assert isinstance(kb, ReplyKeyboardMarkup)
        assert len(kb.keyboard) == 2
        assert kb.keyboard[0][0].text == "🛠 Оставить заявку на сервис ЧПУ"
        assert kb.keyboard[1][0].text == "❓ Частые вопросы"
        assert kb.resize_keyboard is True

    def test_cancel_kb_structure(self):
        """Проверяет структуру клавиатуры отмены."""
        kb = cancel_kb()
        assert isinstance(kb, ReplyKeyboardMarkup)
        assert len(kb.keyboard) == 1
        assert kb.keyboard[0][0].text == "❌ Отмена"
        assert kb.one_time_keyboard is True

    def test_active_ticket_menu_kb_structure(self):
        """Проверяет структуру меню активной заявки."""
        kb = active_ticket_menu_kb()
        assert isinstance(kb, ReplyKeyboardMarkup)
        assert len(kb.keyboard) == 2
        assert kb.keyboard[0][0].text == "❌ Отменить / Закрыть заявку"

    def test_engineer_default_menu_kb_structure(self):
        """Проверяет структуру меню инженера по умолчанию."""
        kb = engineer_default_menu_kb()
        assert isinstance(kb, ReplyKeyboardMarkup)
        assert len(kb.keyboard) == 2
        assert kb.keyboard[0][0].text == "📋 Мои заявки в работе"
        assert kb.keyboard[1][0].text == "📥 Нераспределенные заявки"

    def test_engineer_main_menu_alias(self):
        """Проверяет, что engineer_main_menu возвращает то же, что engineer_default_menu_kb."""
        kb1 = engineer_main_menu()
        kb2 = engineer_default_menu_kb()
        assert kb1.keyboard == kb2.keyboard

    def test_contact_kb_structure(self):
        """Проверяет структуру клавиатуры запроса контакта."""
        kb = contact_kb()
        assert isinstance(kb, ReplyKeyboardMarkup)
        assert len(kb.keyboard) == 1
        button = kb.keyboard[0][0]
        assert button.text == "📞 Поделиться контактом"
        assert button.request_contact is True

    def test_engineer_active_ticket_kb_structure(self):
        """Проверяет структуру меню инженера с активной заявкой."""
        kb = engineer_active_ticket_kb()
        assert isinstance(kb, ReplyKeyboardMarkup)
        assert len(kb.keyboard) == 2
        assert kb.keyboard[0][0].text == "📋 Мои заявки в работе"
        assert kb.keyboard[1][0].text == "✅ Завершить текущую"
        assert kb.keyboard[1][1].text == "🚫 Отменить текущую"


class TestInlineKeyboards:
    """Тесты для Inline-клавиатур."""

    def test_ticket_action_kb(self):
        """Проверяет клавиатуру действия с заявкой."""
        kb = ticket_action_kb(42)
        assert isinstance(kb, InlineKeyboardMarkup)
        assert len(kb.inline_keyboard) == 1
        button = kb.inline_keyboard[0][0]
        assert button.text == "✅ Взять в работу"
        assert "ticket:take:42" in button.callback_data

    def test_engineer_ticket_control_kb(self):
        """Проверяет клавиатуру управления заявкой инженера."""
        kb = engineer_ticket_control_kb(10)
        assert isinstance(kb, InlineKeyboardMarkup)
        assert len(kb.inline_keyboard) == 2
        # Первая строка — завершить
        assert kb.inline_keyboard[0][0].text == "✅ Завершить (Завершена)"
        # Вторая строка — отменить
        assert kb.inline_keyboard[1][0].text == "🚫 Отменить заявку"

    def test_engineer_select_client_kb_empty(self):
        """Проверяет клавиатуру выбора клиента с пустым списком."""
        kb = engineer_select_client_kb([])
        assert isinstance(kb, InlineKeyboardMarkup)
        assert len(kb.inline_keyboard) == 0

    def test_engineer_select_client_kb_with_tickets(self):
        """Проверяет клавиатуру выбора клиента с заявками."""
        tickets = [
            {"id": 1, "company_city": "Москва"},
            {"id": 2, "company_city": None},
        ]
        kb = engineer_select_client_kb(tickets)
        assert len(kb.inline_keyboard) == 2
        assert "Заявка #1" in kb.inline_keyboard[0][0].text
        assert "Москва" in kb.inline_keyboard[0][0].text
        # Для None должно быть "—"
        assert "—" in kb.inline_keyboard[1][0].text

    def test_engineer_redirect_kb(self):
        """Проверяет клавиатуру перенаправления на заявку."""
        kb = engineer_redirect_kb(55)
        assert isinstance(kb, InlineKeyboardMarkup)
        button = kb.inline_keyboard[0][0]
        assert "Ответить на заявку #55" in button.text
        assert "ticket:redirect:55" in button.callback_data

    def test_engineer_list_kb(self):
        """Проверяет клавиатуру списка заявок."""
        tickets = [
            {"id": 1, "company_city": "Москва"},
            {"id": 2, "company_city": "Казань"},
        ]
        kb = engineer_list_kb(tickets, mode="mine")
        assert len(kb.inline_keyboard) == 2
        for row in kb.inline_keyboard:
            assert "ticket:view:" in row[0].callback_data

    def test_engineer_detail_kb_single_ticket(self):
        """Проверяет клавиатуру деталей одной заявки (без навигации)."""
        kb = engineer_detail_kb(ticket_id=1, mode="mine", index=0, total=1)
        assert isinstance(kb, InlineKeyboardMarkup)
        # Не должно быть кнопок навигации ◀️/▶️
        all_texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "◀️" not in all_texts
        assert "▶️" not in all_texts
        # Должна быть кнопка истории
        assert "💬 История переписки" in all_texts

    def test_engineer_detail_kb_multiple_tickets_navigation(self):
        """Проверяет клавиатуру деталей с навигацией (несколько заявок)."""
        # Первая заявка (index=0) — только ▶️
        kb = engineer_detail_kb(ticket_id=1, mode="mine", index=0, total=3)
        all_texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "◀️" not in all_texts
        assert "▶️" in all_texts
        assert "1/3" in all_texts

        # Средняя заявка (index=1) — обе кнопки
        kb = engineer_detail_kb(ticket_id=2, mode="mine", index=1, total=3)
        all_texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "◀️" in all_texts
        assert "▶️" in all_texts
        assert "2/3" in all_texts

        # Последняя заявка (index=2) — только ◀️
        kb = engineer_detail_kb(ticket_id=3, mode="mine", index=2, total=3)
        all_texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "◀️" in all_texts
        assert "▶️" not in all_texts
        assert "3/3" in all_texts

    def test_engineer_detail_kb_mode_mine(self):
        """Проверяет кнопки управления в режиме 'mine'."""
        kb = engineer_detail_kb(ticket_id=1, mode="mine", index=0, total=1)
        all_texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "✅ Выбрать для ответов" in all_texts
        assert "✅ Завершить" in all_texts
        assert "🚫 Отменить" in all_texts
        assert "📋 К списку" in all_texts

    def test_engineer_detail_kb_mode_open(self):
        """Проверяет кнопки управления в режиме 'open'."""
        kb = engineer_detail_kb(ticket_id=1, mode="open", index=0, total=1)
        all_texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "✅ Взять в работу" in all_texts
        assert "✅ Выбрать для ответов" not in all_texts

    def test_rating_kb(self):
        """Проверяет клавиатуру оценки заявки."""
        kb = rating_kb(42)
        assert isinstance(kb, InlineKeyboardMarkup)
        assert len(kb.inline_keyboard) == 1
        row = kb.inline_keyboard[0]
        assert len(row) == 5
        for i, button in enumerate(row, start=1):
            assert button.text == f"{i}⭐"
            assert f"rating:42:{i}" in button.callback_data


class TestAdminKeyboards:
    """Тесты для админских клавиатур."""

    def test_admin_menu_kb(self):
        """Проверяет структуру админ-меню."""
        kb = admin_menu_kb()
        assert isinstance(kb, InlineKeyboardMarkup)
        assert len(kb.inline_keyboard) == 5
        all_texts = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "📊 Статистика" in all_texts
        assert "📥 Экспорт CSV" in all_texts
        assert "👥 Список инженеров" in all_texts
        assert "🔄 Управление дежурными" in all_texts
        assert "👑 Список админов" in all_texts

    def test_engineer_duty_kb_empty(self):
        """Проверяет клавиатуру дежурных с пустым списком."""
        kb = engineer_duty_kb([])
        assert isinstance(kb, InlineKeyboardMarkup)
        # Только кнопка "Назад"
        assert len(kb.inline_keyboard) == 1
        assert kb.inline_keyboard[0][0].text == "🔙 Назад"

    def test_engineer_duty_kb_with_engineers(self):
        """Проверяет клавиатуру дежурных с инженерами."""
        engineers = [
            {"user_id": 111, "name": "Иван", "is_active": 1},
            {"user_id": 222, "name": "Пётр", "is_active": 0},
        ]
        kb = engineer_duty_kb(engineers)
        assert len(kb.inline_keyboard) == 3  # 2 инженера + Назад

        # Активный инженер — зелёный кружок, действие duty_off
        active_btn = kb.inline_keyboard[0][0]
        assert "🟢" in active_btn.text
        assert "duty_off" in active_btn.callback_data

        # Неактивный инженер — белый кружок, действие duty_on
        inactive_btn = kb.inline_keyboard[1][0]
        assert "⚪" in inactive_btn.text
        assert "duty_on" in inactive_btn.callback_data

    def test_engineer_duty_kb_html_escaping(self):
        """Проверяет экранирование HTML в именах инженеров."""
        engineers = [
            {"user_id": 111, "name": "<script>alert('xss')</script>", "is_active": 1},
        ]
        kb = engineer_duty_kb(engineers)
        button_text = kb.inline_keyboard[0][0].text
        # HTML должен быть экранирован: "<script>" заменяется на "<script>"
        assert "<script>" not in button_text
        assert "script" in button_text  # Текст присутствует, но экранирован

    def test_back_to_admin_kb(self):
        """Проверяет клавиатуру возврата в админ-панель."""
        kb = back_to_admin_kb()
        assert isinstance(kb, InlineKeyboardMarkup)
        assert len(kb.inline_keyboard) == 1
        button = kb.inline_keyboard[0][0]
        assert button.text == "🔙 Назад в админ-панель"
        assert "admin:back" in button.callback_data