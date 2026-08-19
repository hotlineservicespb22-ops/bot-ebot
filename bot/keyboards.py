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
    page: int = 0

class RatingCallback(CallbackData, prefix="rating"):
    ticket_id: int
    value: int

class FaqCallback(CallbackData, prefix="faq"):
    action: str
    section_id: str = ""
    question_id: int = 0

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
            [KeyboardButton(text="📋 Мои заявки в работе")],
            [
                KeyboardButton(text="✅ Завершить текущую"),
                KeyboardButton(text="🚫 Отменить текущую")
            ]
        ],
        resize_keyboard=True
    )

def ticket_action_kb(ticket_id: int):
    """Inline-клавиатура для инженера под новой заявкой (кнопки 'Взять в работу' и 'Посмотреть')."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Взять в работу", 
                callback_data=TicketCallback(action="take", ticket_id=ticket_id).pack()
            ),
            InlineKeyboardButton(
                text="👁 Посмотреть",
                callback_data=TicketCallback(action="view", ticket_id=ticket_id).pack()
            ),
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

# Сколько заявок показывать на одной странице списка
TICKETS_PER_PAGE = 10

def engineer_list_kb(tickets, mode: str, page: int = 0, per_page: int = TICKETS_PER_PAGE):
    """Inline keyboard to list tickets with 'view' action, with pagination."""
    total = len(tickets)
    start = page * per_page
    end = min(total, start + per_page)
    page_tickets = tickets[start:end]

    keyboard = []
    for ticket in page_tickets:
        keyboard.append([
            InlineKeyboardButton(
                text=f"🎫 Заявка #{ticket['id']} ({ticket['company_city'] or '—'})",
                callback_data=TicketCallback(action="view", ticket_id=ticket['id']).pack()
            )
        ])

    # Кнопки пагинации (◀️/▶️ между страницами)
    max_page = max(0, (total - 1) // per_page) if total else 0
    if max_page > 0:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(
                text="⬅️",
                callback_data=TicketCallback(action="page", ticket_id=0, page=page - 1).pack()
            ))
        nav.append(InlineKeyboardButton(
            text=f"{page + 1}/{max_page + 1}",
            callback_data="ticket:noop"
        ))
        if page < max_page:
            nav.append(InlineKeyboardButton(
                text="➡️",
                callback_data=TicketCallback(action="page", ticket_id=0, page=page + 1).pack()
            ))
        keyboard.append(nav)

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

# ─── FAQ ────────────────────────────────────────────────────────────────
# Разделы FAQ и ответы на вопросы вынесены в модуль bot/faq.py,
# чтобы держать данные отдельно от клавиатур и обработчиков.
from bot.faq import FAQ_SECTIONS  # noqa: E402

def faq_main_kb():
    """Клавиатура с разделами FAQ (главное меню FAQ)."""
    keyboard = [
        [InlineKeyboardButton(
            text=section["title"],
            callback_data=FaqCallback(action="section", section_id=section["id"]).pack()
        )]
        for section in FAQ_SECTIONS
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def faq_section_kb(section_id: str):
    """Клавиатура со списком вопросов выбранного раздела + кнопка 'Назад'."""
    section = next((s for s in FAQ_SECTIONS if s["id"] == section_id), None)
    if not section:
        return faq_main_kb()
    keyboard = [
        [InlineKeyboardButton(
            text=f"{idx}. {title}",
            callback_data=FaqCallback(action="answer", section_id=section_id, question_id=qid).pack()
        )]
        for idx, (qid, title) in enumerate(section["questions"], start=1)
    ]
    keyboard.append([
        InlineKeyboardButton(
            text="🔙 Назад к разделам",
            callback_data=FaqCallback(action="main").pack()
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def faq_answer_kb(section_id: str, question_id: int):
    """Клавиатура под ответом: 'Назад к списку вопросов' и 'Назад к разделам'."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔙 Назад к списку",
                    callback_data=FaqCallback(action="section", section_id=section_id, question_id=question_id).pack()
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 К разделам FAQ",
                    callback_data=FaqCallback(action="main").pack()
                )
            ]
        ]
    )

class MyRequestsCallback(CallbackData, prefix="myreq"):
    page: int

class AdminCallback(CallbackData, prefix="admin"):
    action: str
    engineer_id: int = 0

class ManagerCallback(CallbackData, prefix="manager"):
    action: str
    ticket_id: int = 0
    page: int = 0

def admin_menu_kb():
    """Inline-клавиатура админ-панели."""
    buttons = [
        [InlineKeyboardButton(text="📊 Статистика", callback_data=AdminCallback(action="stats").pack())],
        [InlineKeyboardButton(text="📈 Дашборд", callback_data=AdminCallback(action="dashboard").pack())],
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


def my_requests_pagination_kb(page: int, total_pages: int):
    """
    Inline-клавиатура пагинации истории заявок клиента.
    Показывает страницы «◀️ N/M ▶️» (клик по текущей странице — no-op).
    """
    keyboard = []
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(
                text="⬅️",
                callback_data=MyRequestsCallback(page=page - 1).pack()
            ))
        # Кнопка-счётчик (no-op) с текущей страницей
        nav.append(InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data="myreq:noop"
        ))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(
                text="➡️",
                callback_data=MyRequestsCallback(page=page + 1).pack()
            ))
        keyboard.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ─── Клавиатуры руководителя ───────────────────────────────────

def manager_menu_kb() -> InlineKeyboardMarkup:
    """Inline-клавиатура панели руководителя."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📋 Проблемные заявки (оценка ≤ 3)",
            callback_data=ManagerCallback(action="list").pack()
        )],
    ])


def manager_ticket_list_kb(tickets: list, page: int = 0) -> InlineKeyboardMarkup:
    """Список проблемных заявок с пагинацией по 5 на страницу."""
    per_page = 5
    start = page * per_page
    chunk = tickets[start:start + per_page]
    total_pages = max((len(tickets) + per_page - 1) // per_page, 1)

    keyboard = []
    for t in chunk:
        stars = "⭐" * t['rating']
        eng = t['engineer_name'] or "—"
        label = (
            f"#{t['id']} {stars} {eng[:15]}"
        )
        keyboard.append([InlineKeyboardButton(
            text=label,
            callback_data=ManagerCallback(action="detail", ticket_id=t['id']).pack()
        )])

    # Пагинация
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(
                text="⬅️",
                callback_data=ManagerCallback(action="list", page=page - 1).pack()
            ))
        nav.append(InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data="manager:noop"
        ))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(
                text="➡️",
                callback_data=ManagerCallback(action="list", page=page + 1).pack()
            ))
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton(
        text="🔙 Назад",
        callback_data=ManagerCallback(action="menu").pack()
    )])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def manager_ticket_detail_kb(ticket_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для детального просмотра заявки руководителем."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📜 Показать переписку (без медиа)",
            callback_data=ManagerCallback(action="chat", ticket_id=ticket_id).pack()
        )],
        [InlineKeyboardButton(
            text="🔙 К списку",
            callback_data=ManagerCallback(action="list").pack()
        )],
        [InlineKeyboardButton(
            text="🏠 В меню руководителя",
            callback_data=ManagerCallback(action="menu").pack()
        )],
    ])
