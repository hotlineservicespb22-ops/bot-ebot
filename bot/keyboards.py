import html

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


class TicketCallback(CallbackData, prefix="ticket"):
    action: str
    ticket_id: int

class RatingCallback(CallbackData, prefix="rating"):
    ticket_id: int
    value: int

def main_menu():
    """Reply-клавиатура для клиента в главном меню."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛠 Оставить заявку на сервис ЧПУ")],
            [KeyboardButton(text="❓ Частые вопросы")]
        ],
        resize_keyboard=True
    )

def cancel_kb():
    """Reply-клавиатура с кнопкой отмены для FSM-состояний."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def active_ticket_menu_kb():
    """Reply-клавиатура для клиента, когда у него есть активная заявка."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="❌ Отменить / Закрыть заявку")],
            [KeyboardButton(text="❓ Частые вопросы")]
        ],
        resize_keyboard=True
    )

def engineer_default_menu_kb():
    """Reply-клавиатура для инженера, когда нет активно выбранной заявки."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мои заявки в работе")],
            [KeyboardButton(text="📥 Нераспределенные заявки")]
        ],
        resize_keyboard=True
    )

# Алиас для единообразия с задачей (engineer_main_menu)
def engineer_main_menu():
    """Главное меню инженера с кнопками управления заявками."""
    return engineer_default_menu_kb()

def contact_kb():
    """Reply-клавиатура с кнопкой запроса контакта для последнего шага FSM."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📞 Поделиться контактом", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def engineer_active_ticket_kb():
    """Reply-клавиатура для инженера, когда есть активно выбранная заявка."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мои заявки в работе")], # Всегда должна быть
            [
                KeyboardButton(text="✅ Завершить текущую"),
                KeyboardButton(text="🚫 Отменить текущую")
            ]
        ],
        resize_keyboard=True
    )

def ticket_action_kb(ticket_id: int):
    """Inline-клавиатура для инженера под новой заявкой (кнопка 'Взять в работу')."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Взять в работу", 
                callback_data=TicketCallback(action="take", ticket_id=ticket_id).pack()
            )
        ]]
    )

def engineer_ticket_control_kb(ticket_id: int):
    """Клавиатура для инженера для управления активной заявкой."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Завершить (Завершена)",
                    callback_data=TicketCallback(action="complete", ticket_id=ticket_id).pack()
                )
            ],
            [
            InlineKeyboardButton(
                text="🚫 Отменить заявку",
                callback_data=TicketCallback(action="eng_cancel", ticket_id=ticket_id).pack()
            )
            ]
        ]
    )

def engineer_select_client_kb(tickets):
    """Inline keyboard for engineers to select a client to reply to."""
    keyboard = []
    for ticket in tickets:
        keyboard.append([
            InlineKeyboardButton(
                text=f"🎫 Заявка #{ticket['id']} ({ticket['company_city'] or '—'})",
                callback_data=TicketCallback(action="select", ticket_id=ticket['id']).pack()
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def engineer_redirect_kb(ticket_id: int):
    """Inline-кнопка под сообщением клиента для переключения инженера на эту заявку."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=f"🔁 Ответить на заявку #{ticket_id}",
                callback_data=TicketCallback(action="redirect", ticket_id=ticket_id).pack()
            )
        ]]
    )

def engineer_list_kb(tickets, mode: str):
    """Inline keyboard to list tickets with 'view' action for details/navigation."""
    keyboard = []
    for ticket in tickets:
        keyboard.append([
            InlineKeyboardButton(
                text=f"🎫 Заявка #{ticket['id']} ({ticket['company_city'] or '—'})",
                callback_data=TicketCallback(action="view", ticket_id=ticket['id']).pack()
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def engineer_detail_kb(ticket_id: int, mode: str, index: int, total: int):
    """Клавиатура деталей заявки с навигацией ◀️/▶️ и управлением."""
    buttons = []
    # Строка навигации (если заявок больше одной)
    if total > 1:
        nav_buttons = []
        if index > 0:
            nav_buttons.append(
                InlineKeyboardButton(text="◀️", callback_data=TicketCallback(action="prev", ticket_id=ticket_id).pack())
            )
        nav_buttons.append(
            InlineKeyboardButton(text=f"{index+1}/{total}", callback_data="ticket:noop")
        )
        if index < total - 1:
            nav_buttons.append(
                InlineKeyboardButton(text="▶️", callback_data=TicketCallback(action="next", ticket_id=ticket_id).pack())
            )
        buttons.append(nav_buttons)

    # Кнопка истории переписки (доступна в любом режиме)
    buttons.append([
        InlineKeyboardButton(
            text="💬 История переписки",
            callback_data=TicketCallback(action="history", ticket_id=ticket_id).pack()
        )
    ])

    # Кнопки управления в зависимости от режима
    if mode == "mine":
        buttons.append([
            InlineKeyboardButton(
                text="✅ Выбрать для ответов",
                callback_data=TicketCallback(action="select", ticket_id=ticket_id).pack()
            )
        ])
        buttons.append([
            InlineKeyboardButton(
                text="✅ Завершить",
                callback_data=TicketCallback(action="complete", ticket_id=ticket_id).pack()
            ),
            InlineKeyboardButton(
                text="🚫 Отменить",
                callback_data=TicketCallback(action="eng_cancel", ticket_id=ticket_id).pack()
            )
        ])
    elif mode == "open":
        buttons.append([
            InlineKeyboardButton(
                text="✅ Взять в работу",
                callback_data=TicketCallback(action="take", ticket_id=ticket_id).pack()
            )
        ])

    # Возврат к списку
    buttons.append([
        InlineKeyboardButton(
            text="📋 К списку",
            callback_data=TicketCallback(action="back_to_list", ticket_id=ticket_id).pack()
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def rating_kb(ticket_id: int):
    """Клавиатура оценки заявки (1-5 звёзд)."""
    row = [
        InlineKeyboardButton(text=f"{i}⭐", callback_data=RatingCallback(ticket_id=ticket_id, value=i).pack())
        for i in range(1, 6)
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row])

class AdminCallback(CallbackData, prefix="admin"):
    action: str
    engineer_id: int = 0

def admin_menu_kb():
    """Inline-клавиатура админ-панели."""
    buttons = [
        [InlineKeyboardButton(text="📊 Статистика", callback_data=AdminCallback(action="stats").pack())],
        [InlineKeyboardButton(text="📥 Экспорт CSV", callback_data=AdminCallback(action="export").pack())],
        [InlineKeyboardButton(text="👥 Список инженеров", callback_data=AdminCallback(action="list_eng").pack())],
        [InlineKeyboardButton(text="🔄 Управление дежурными", callback_data=AdminCallback(action="duty_manage").pack())],
        [InlineKeyboardButton(text="👑 Список админов", callback_data=AdminCallback(action="list_admin").pack())],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def engineer_duty_kb(engineers):
    """Inline-клавиатура для управления дежурными инженерами на сегодня."""
    keyboard = []
    for eng in engineers:
        status = "🟢" if eng['is_active'] else "⚪"
        action = "duty_off" if eng['is_active'] else "duty_on"
        keyboard.append([
            InlineKeyboardButton(
                text=f"{status} {html.escape(eng['name'])} ({eng['user_id']})",
                callback_data=AdminCallback(action=action, engineer_id=eng['user_id']).pack()
            )
        ])
    keyboard.append([
        InlineKeyboardButton(
            text="🔙 Назад",
            callback_data=AdminCallback(action="back").pack()
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def back_to_admin_kb():
    """Inline-клавиатура с одной кнопкой 'Назад' в админ-панель."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="🔙 Назад в админ-панель",
                callback_data=AdminCallback(action="back").pack()
            )
        ]]
    )
