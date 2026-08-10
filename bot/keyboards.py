from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters.callback_data import CallbackData

class TicketCallback(CallbackData, prefix="ticket"):
    action: str
    ticket_id: int

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
            [KeyboardButton(text="📋 Мои заявки в работе")]
        ],
        resize_keyboard=True
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
                text=f"🎫 Заявка #{ticket['id']} ({ticket['company']})",
                callback_data=TicketCallback(action="select", ticket_id=ticket['id']).pack()
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
