"""
Обработчики панели руководителя (manager).

Руководитель видит:
- Список заявок с оценкой ≤ 3 (проблемные)
- Детальную информацию по заявке
- Переписку клиент↔инженер без медиафайлов
- Ссылку на веб-дашборд
"""

import html
import logging

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.config import MANAGER_DASHBOARD_KEY, MANAGER_DASHBOARD_URL
from bot.database import Database
from bot.keyboards import (
    ManagerCallback,
    manager_menu_kb,
    manager_ticket_detail_kb,
    manager_ticket_list_kb,
)

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("manager"))
async def cmd_manager_panel(message: Message, db: Database, is_manager: bool):
    """Показывает панель руководителя. Доступ только для managers."""
    if not is_manager:
        await message.answer("🚫 У вас нет прав руководителя.")
        return
    await message.answer(
        "👔 <b>Панель руководителя</b>\n\n"
        "Здесь вы можете просмотреть заявки с низкими оценками (≤ 3) "
        "и изучить переписку по каждой из них.",
        reply_markup=manager_menu_kb(),
    )


# ═════════════════════════════════════════════════════════════════
# Callback-обработчик всех действий руководителя
# ═════════════════════════════════════════════════════════════════


@router.callback_query(ManagerCallback.filter())
async def manager_callback_handler(
    callback: CallbackQuery,
    callback_data: ManagerCallback,
    db: Database,
    bot: Bot,
    is_manager: bool,
):
    if not is_manager:
        await callback.answer("🚫 Нет доступа.", show_alert=True)
        return

    await callback.answer()
    action = callback_data.action

    if action == "menu":
        await callback.message.edit_text(
            "👔 <b>Панель руководителя</b>\n\nВыберите действие:",
            reply_markup=manager_menu_kb(),
        )
    elif action == "list":
        await _show_ticket_list(callback, db, callback_data.page)
    elif action == "detail":
        await _show_ticket_detail(callback, db, callback_data.ticket_id)
    elif action == "chat":
        await _show_ticket_chat(callback, db, bot, callback_data.ticket_id)




# ═════════════════════════════════════════════════════════════════
# Показать список проблемных заявок
# ═════════════════════════════════════════════════════════════════


async def _show_ticket_list(callback: CallbackQuery, db: Database, page: int):
    tickets = await db.low_rated.get_list(limit=100, offset=0)
    if not tickets:
        await callback.message.edit_text(
            "✅ <b>Проблемных заявок нет!</b>\n\n"
            "Все заявки имеют оценку выше 3 — отличная работа команды.",
            reply_markup=manager_menu_kb(),
        )
        return

    total = await db.low_rated.count()
    avg_rating = await db.low_rated.avg_rating()
    this_week = await db.low_rated.this_week()

    header = (
        f"📋 <b>Проблемные заявки (оценка ≤ 3)</b>\n"
        f"Всего: <b>{total}</b> | За неделю: <b>{this_week}</b> | "
        f"Средняя оценка: <b>{avg_rating:.1f if avg_rating else '—'}</b>\n\n"
        "<i>Выберите заявку для просмотра:</i>"
    )
    await callback.message.edit_text(
        header,
        reply_markup=manager_ticket_list_kb(tickets, page),
    )


# ═════════════════════════════════════════════════════════════════
# Детальный просмотр заявки
# ═════════════════════════════════════════════════════════════════


def _status_text(status: str) -> str:
    status_map = {
        "open": "🟡 Нераспределена",
        "in_progress": "🔵 В работе",
        "completed": "✅ Завершена",
        "canceled": "🚫 Отменена",
    }
    return status_map.get(status, status)


async def _show_ticket_detail(
    callback: CallbackQuery, db: Database, ticket_id: int
):
    ticket = await db.tickets.get(ticket_id)
    if not ticket:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    rating = await db.ratings.get_for_ticket(ticket_id)
    msg_count = len(await db.messages.get_for_ticket(ticket_id))

    stars = "⭐" * (rating["rating"] if rating else 0)
    rating_val = f'{rating["rating"]}/5' if rating else "—"
    comment = (
        html.escape(rating["comment"])
        if rating and rating["comment"]
        else "—"
    )

    dash_url = MANAGER_DASHBOARD_URL
    if MANAGER_DASHBOARD_KEY:
        dash_url += f"?key={MANAGER_DASHBOARD_KEY}"
    web_link = f"{dash_url}/ticket/{ticket_id}"

    text = (
        f"📋 <b>Заявка #{ticket_id}</b>\n"
        f"Статус: <b>{_status_text(ticket['status'])}</b> | "
        f"Оценка: {stars} <b>({rating_val})</b>\n\n"
        f"👤 <b>Клиент:</b> {html.escape(str(ticket['client_name'] or '—'))}\n"
        f"🏢 <b>Компания/Город:</b> {html.escape(str(ticket['company_city'] or '—'))}\n"
        f"🔧 <b>Станок:</b> {html.escape(str(ticket['machine_info'] or '—'))}\n"
        f"📝 <b>Проблема:</b> {html.escape(str(ticket['problem'] or '—'))}\n"
        f"📞 <b>Контакты:</b> {html.escape(str(ticket['contact'] or '—'))}\n"
        f"💬 <b>Комментарий клиента:</b> {comment}\n"
        f"📨 <b>Сообщений в переписке:</b> {msg_count}\n"
        f"\n🔗 <a href=\"{web_link}\">Открыть в веб-дашборде</a>"
    )
    await callback.message.edit_text(
        text,
        reply_markup=manager_ticket_detail_kb(ticket_id),
        disable_web_page_preview=False,

    )


# ═════════════════════════════════════════════════════════════════
# Показать переписку без медиа
# ═════════════════════════════════════════════════════════════════


async def _show_ticket_chat(
    callback: CallbackQuery, db: Database, bot: Bot, ticket_id: int
):
    """Отправляет историю переписки по заявке (только текст, без медиа)."""
    ticket = await db.tickets.get(ticket_id)
    if not ticket:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    messages = await db.low_rated.chat_text_only(ticket_id)

    if not messages:
        await callback.answer(
            "Переписка пуста или содержит только медиа.", show_alert=True
        )
        return

    engineer_name = (
        await db.engineers.get_name(ticket["engineer_id"])
        if ticket["engineer_id"]
        else None
    )
    engineer_display = html.escape(engineer_name or "Инженер")
    client_display = html.escape(str(ticket["client_name"] or "Клиент"))

    header = (
        f"📜 <b>Переписка по заявке #{ticket_id}</b>\n"
        f"👤 {client_display} ↔ 👨‍🔧 {engineer_display}\n"
        f"<i>Текстовые сообщения (без медиа): {len(messages)}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

    lines = [header]
    chars = len(header)
    max_body = 3900

    for msg in messages:
        role_icon = "👤" if msg["sender_role"] == "client" else "👨‍🔧"
        sender_label = (
            client_display
            if msg["sender_role"] == "client"
            else engineer_display
        )
        try:
            ts = msg["created_at"][:16].replace("T", " ")
        except (TypeError, IndexError):
            ts = "—"
        msg_text = msg["text"] or ""
        if len(msg_text) > 200:
            msg_text = msg_text[:197] + "..."

        line = (
            f"\n{ts} {role_icon} <b>{html.escape(sender_label[:20])}:</b>\n"
            f"{html.escape(msg_text)}"
        )
        if chars + len(line) > max_body:
            remaining = len(messages) - list(messages).index(msg)
            lines.append(
                f"\n... и ещё {remaining} сообщений. "
                f"Откройте веб-дашборд для полной переписки."
            )
            break
        lines.append(line)
        chars += len(line)

    await callback.message.edit_text(
        "".join(lines),
        reply_markup=manager_ticket_detail_kb(ticket_id),
    )