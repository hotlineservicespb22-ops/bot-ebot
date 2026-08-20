"""
Сервисный слой для работы с заявками.

Содержит единую бизнес-логику завершения и отмены заявок, чтобы избежать
дублирования кода между inline-обработчиками и меню инженера, и гарантировать
согласованное поведение (уведомление клиента, обновление состояния FSM).
"""
import logging

from aiogram import Bot
from aiogram.fsm.context import FSMContext

from bot.database import Database
from bot.keyboards import (
    engineer_default_menu_kb,
    engineer_select_client_kb,
    main_menu,
    rating_kb,
)

logger = logging.getLogger(__name__)


def _status_text(status: str) -> tuple[str, str]:
    """Возвращает (эмодзи-статус, текст-уведомление) для статусов завершения/отмены."""
    if status == "completed":
        return "✅", (
            "Заявка успешно завершена специалистом. Спасибо за обращение!\n\n"
            "Оцените, пожалуйста, качество обслуживания:"
        )
    return "🚫", "Заявка была отменена инженером."


async def _notify_client(bot: Bot, ticket, status: str) -> None:
    """Уведомляет клиента о завершении/отмене заявки."""
    icon, text = _status_text(status)
    kb = rating_kb(ticket['id']) if status == "completed" else main_menu()
    session_note = ""
    if status == "completed":
        session_seconds = ticket['session_seconds'] or 0
        session_note = f"\n\n⏱ Время работы специалиста: {session_seconds // 60}м"
    try:
        await bot.send_message(
            ticket['client_id'],
            f"{icon} Заявка #{ticket['id']} {text}{session_note}",
            reply_markup=kb,
        )
    # Сбой отправки в Telegram — внешняя ошибка связи, не связанная с состоянием БД
    # и не требующая отката. Но она не должна пропадать молча: логируем на ERROR,
    # чтобы клиент, не получивший уведомление, был заметен в логах.
    except Exception as e:
        logger.error(f"Не удалось уведомить клиента {ticket['client_id']} о заявке #{ticket['id']}: {e}")


async def _clear_active_state(state: FSMContext, ticket_id: int) -> None:
    """Сбрасывает активную заявку в FSM-состоянии инженера, если она совпадает."""
    try:
        state_data = await state.get_data()
        if state_data.get("active_ticket_id") == ticket_id:
            await state.update_data(active_ticket_id=None)
    except Exception as e:
        logger.warning(f"Не удалось сбросить активную заявку в состоянии: {e}")


async def _suggest_next_tickets(
    who: object,
    db: Database,
    engineer_id: int,
    empty_message: str,
) -> None:
    """Предлагает инженеру следующую активную заявку (или уведомляет об отсутствии)."""
    remaining = await db.tickets.get_for_engineer(engineer_id)
    try:
        if remaining:
            await who.answer(
                "У вас остались активные заявки. Выберите следующую для работы:",
                reply_markup=engineer_select_client_kb(remaining),
            )
            await who.answer("Ваше меню обновлено.", reply_markup=engineer_default_menu_kb())
        else:
            await who.answer(empty_message, reply_markup=engineer_default_menu_kb())
    except Exception as e:
        logger.warning(f"Не удалось показать инженеру {engineer_id} список оставшихся заявок: {e}")


async def complete_ticket(
    bot: Bot,
    who: object,
    db: Database,
    state: FSMContext,
    engineer_id: int,
    ticket_id: int,
    comment: str,
) -> bool:
    """
    Завершает заявку: обновляет статус, уведомляет клиента, предлагает следующие.

    Возвращает True, если заявка успешно завершена.
    """
    ticket = await db.tickets.get(ticket_id)
    if not ticket or ticket['engineer_id'] != engineer_id or ticket['status'] != 'in_progress':
        return False

    await db.tickets.close(ticket_id, status='completed', comment=comment)
    # Перечитываем заявку: close_ticket начислил session_seconds, которые нужны
    # в уведомлении клиенту («Время работы специалиста: Xм»).
    ticket = await db.tickets.get(ticket_id)
    await _notify_client(bot, ticket, 'completed')
    await _clear_active_state(state, ticket_id)
    await _suggest_next_tickets(
        who, db, engineer_id,
        "У вас больше нет активных заявок в работе.",
    )
    return True


async def cancel_ticket(
    bot: Bot,
    who: object,
    db: Database,
    state: FSMContext,
    engineer_id: int,
    ticket_id: int,
    comment: str,
) -> bool:
    """
    Отменяет заявку: обновляет статус, уведомляет клиента, предлагает следующие.

    Возвращает True, если заявка успешно отменена.
    """
    ticket = await db.tickets.get(ticket_id)
    if not ticket or ticket['engineer_id'] != engineer_id or ticket['status'] != 'in_progress':
        return False

    await db.tickets.close(ticket_id, status='canceled', comment=comment)
    ticket = await db.tickets.get(ticket_id)
    await _notify_client(bot, ticket, 'canceled')
    await _clear_active_state(state, ticket_id)
    await _suggest_next_tickets(
        who, db, engineer_id,
        "У вас больше нет активных заявок в работе.",
    )
    return True