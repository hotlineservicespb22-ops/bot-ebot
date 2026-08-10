import html
from aiogram import Router, Bot, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from bot.database import Database
from bot.keyboards import TicketCallback, main_menu, engineer_default_menu_kb, engineer_active_ticket_kb, engineer_select_client_kb, engineer_ticket_control_kb, active_ticket_menu_kb


router = Router()

@router.callback_query(TicketCallback.filter(F.action == "take"))
async def take_ticket(callback: CallbackQuery, callback_data: TicketCallback, bot: Bot, db: Database, is_engineer: bool, state: FSMContext):
    if not is_engineer:
        await callback.answer("У вас нет прав инженера.", show_alert=True)
        return
        
    ticket_id = callback_data.ticket_id
    eng_id = callback.from_user.id
    
    success = await db.take_ticket(ticket_id, eng_id)
    if not success:
        await callback.answer("Заявка уже занята другим специалистом или закрыта.", show_alert=True)
        # Update message to show current status if possible
        current_ticket = await db.get_ticket(ticket_id)
        if current_ticket and current_ticket['status'] == 'in_progress':
            # Try to find engineer name
            engineer_name = "Неизвестный инженер"
            if current_ticket['engineer_id']:
                engineers = await db.get_engineers()
                for eng in engineers:
                    if eng['user_id'] == current_ticket['engineer_id']:
                        engineer_name = eng['name']
                        break
            await callback.message.edit_text(
                callback.message.html_text + f"\n\n👨‍🔧 <b>Заявка уже в работе</b>",
                parse_mode="HTML"
            )
        return

    # Устанавливаем эту заявку как активную для инженера
    await state.update_data(active_ticket_id=ticket_id)

    ticket = await db.get_ticket(ticket_id)
    
    await callback.message.edit_text(
        callback.message.html_text + f"\n\n👨‍🔧 <b>Взято в работу инженером:</b> {html.escape(callback.from_user.full_name)}",
        reply_markup=engineer_ticket_control_kb(ticket_id),
        parse_mode="HTML"
    )
    
    await bot.send_message(
        ticket['client_id'],
        (
            f"👨‍🔧 К вашей заявке #{ticket_id} подключился дежурный инженер.\n"
            "Вы можете писать уточнения и присылать фотографии прямо в этот чат."
        ),
        reply_markup=active_ticket_menu_kb()
    )
    await callback.message.answer(
        f"✅ Заявка #{ticket_id} взята в работу и установлена как активный чат. Ваши сообщения будут направлены этому клиенту. Используйте /my_tickets для переключения.",
        reply_markup=engineer_active_ticket_kb()
    )
    await callback.answer("Заявка взята в работу.")

@router.callback_query(TicketCallback.filter(F.action == "complete"))
async def complete_ticket_by_engineer(callback: CallbackQuery, callback_data: TicketCallback, bot: Bot, db: Database, state: FSMContext, is_engineer: bool):
    if not is_engineer:
        await callback.answer("У вас нет прав инженера.", show_alert=True)
        return

    ticket_id = callback_data.ticket_id
    ticket = await db.get_ticket(ticket_id)
    if not ticket or ticket['engineer_id'] != callback.from_user.id:
        await callback.answer("Это не ваша заявка или она уже закрыта.", show_alert=True)
        return

    await db.close_ticket(ticket_id, status='completed', comment='Работы успешно завершены инженером')

    await callback.message.edit_text(callback.message.html_text + "\n\n✅ <b>Статус: Заявка успешно завершена.</b>", parse_mode="HTML")
    await callback.answer("Заявка закрыта как выполненная.")

    await bot.send_message(
        ticket['client_id'],
        f"✅ Заявка #{ticket_id} успешно завершена специалистом. Спасибо за обращение!",
        reply_markup=main_menu()
    )

    current_state_data = await state.get_data()
    if current_state_data.get("active_ticket_id") == ticket_id:
        await state.update_data(active_ticket_id=None)

    remaining_tickets = await db.get_active_tickets_for_engineer(callback.from_user.id)
    if remaining_tickets:
        await callback.message.answer("У вас остались активные заявки. Выберите следующую для работы:", reply_markup=engineer_select_client_kb(remaining_tickets)) # Inline keyboard
        await callback.message.answer("Ваше меню обновлено.", reply_markup=engineer_default_menu_kb()) # Revert to default ReplyKeyboardMarkup
    else:
        await callback.message.answer("У вас больше нет активных заявок в работе.", reply_markup=engineer_default_menu_kb())


@router.message(F.text == "✅ Завершить текущую")
async def complete_current_ticket_via_menu(message: Message, bot: Bot, db: Database, state: FSMContext, is_engineer: bool):
    if not is_engineer:
        return

    user_id = message.from_user.id
    state_data = await state.get_data()
    active_ticket_id = state_data.get("active_ticket_id")

    if not active_ticket_id:
        await message.answer("❌ У вас не выбрана активная заявка. Выберите её через меню «Мои заявки в работе».")
        return

    ticket = await db.get_ticket(active_ticket_id)
    if not ticket or ticket['engineer_id'] != user_id or ticket['status'] != 'in_progress':
        await message.answer("Эта заявка не активна, не закреплена за вами или уже закрыта.")
        # Clear state if the ticket is no longer valid for this engineer
        await state.update_data(active_ticket_id=None)
        return

    # Close the ticket
    await db.close_ticket(active_ticket_id, status='completed', comment='Работы успешно завершены инженером через меню')

    # Notify client
    await bot.send_message(
        ticket['client_id'],
        f"✅ Заявка #{active_ticket_id} успешно завершена специалистом. Спасибо за обращение!",
        reply_markup=main_menu() # Return client to main menu
    )

    # Clear active ticket from engineer's state
    await state.update_data(active_ticket_id=None)

    await message.answer(f"✅ Заявка #{active_ticket_id} успешно завершена.")
    # Optionally, suggest next steps or list remaining tickets
    remaining_tickets = await db.get_active_tickets_for_engineer(user_id)
    if remaining_tickets:
        await message.answer("У вас остались активные заявки. Выберите следующую для работы:", reply_markup=engineer_select_client_kb(remaining_tickets)) # Inline keyboard
        await message.answer("Ваше меню обновлено.", reply_markup=engineer_default_menu_kb()) # Revert to default ReplyKeyboardMarkup
    else:
        await message.answer("У вас больше нет активных заявок в работе.", reply_markup=engineer_default_menu_kb())


@router.message(F.text == "🚫 Отменить текущую")
async def cancel_current_ticket_via_menu(message: Message, bot: Bot, db: Database, state: FSMContext, is_engineer: bool):
    if not is_engineer:
        return

    user_id = message.from_user.id
    state_data = await state.get_data()
    active_ticket_id = state_data.get("active_ticket_id")

    if not active_ticket_id:
        await message.answer("❌ У вас не выбрана активная заявка. Выберите её через меню «Мои заявки в работе».")
        return

    ticket = await db.get_ticket(active_ticket_id)
    if not ticket or ticket['engineer_id'] != user_id or ticket['status'] != 'in_progress':
        await message.answer("Эта заявка не активна, не закреплена за вами или уже закрыта.")
        # Clear state if the ticket is no longer valid for this engineer
        await state.update_data(active_ticket_id=None)
        return

    # Cancel the ticket
    await db.close_ticket(active_ticket_id, status='canceled', comment='Отменено инженером через меню')

    # Notify client
    await bot.send_message(
        ticket['client_id'],
        f"🚫 Заявка #{active_ticket_id} была отменена инженером.",
        reply_markup=main_menu() # Return client to main menu
    )

    # Clear active ticket from engineer's state
    await state.update_data(active_ticket_id=None)

    await message.answer(f"🚫 Заявка #{active_ticket_id} отменена.")
    # Optionally, suggest next steps or list remaining tickets
    remaining_tickets = await db.get_active_tickets_for_engineer(user_id)
    if remaining_tickets:
        await message.answer("У вас остались активные заявки. Выберите следующую для работы:", reply_markup=engineer_select_client_kb(remaining_tickets)) # Inline keyboard
        await message.answer("Ваше меню обновлено.", reply_markup=engineer_default_menu_kb()) # Revert to default ReplyKeyboardMarkup
    else:
        await message.answer("У вас больше нет активных заявок в работе.", reply_markup=engineer_default_menu_kb())

@router.callback_query(TicketCallback.filter(F.action == "eng_cancel"))
async def cancel_ticket_by_engineer(callback: CallbackQuery, callback_data: TicketCallback, bot: Bot, db: Database, state: FSMContext, is_engineer: bool):
    if not is_engineer:
        await callback.answer("У вас нет прав инженера.", show_alert=True)
        return

    ticket_id = callback_data.ticket_id
    ticket = await db.get_ticket(ticket_id)
    if not ticket or ticket['engineer_id'] != callback.from_user.id:
        await callback.answer("Это не ваша заявка или она уже закрыта.", show_alert=True)
        return

    await db.close_ticket(ticket_id, status='canceled', comment='Отменена инженером')

    await callback.message.edit_text(callback.message.html_text + "\n\n🚫 <b>Статус: Заявка отменена.</b>", parse_mode="HTML")
    await callback.answer("Заявка переведена в статус отмененных.")

    await bot.send_message(
        ticket['client_id'],
        f"🚫 Заявка #{ticket_id} была отменена инженером.",
        reply_markup=main_menu()
    )

    current_state_data = await state.get_data()
    if current_state_data.get("active_ticket_id") == ticket_id:
        await state.update_data(active_ticket_id=None)

    remaining_tickets = await db.get_active_tickets_for_engineer(callback.from_user.id)
    if remaining_tickets:
        await callback.message.answer("У вас остались активные заявки. Выберите следующую для работы:", reply_markup=engineer_select_client_kb(remaining_tickets))
