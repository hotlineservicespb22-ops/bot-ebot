import logging
import html
from aiogram import Router, Bot, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramAPIError
from bot.database import Database
from bot.keyboards import TicketCallback, engineer_select_client_kb, engineer_active_ticket_kb, engineer_default_menu_kb
router = Router()

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

async def safe_send(bot: Bot, chat_id: int, method_name: str, **kwargs):
    method = getattr(bot, method_name)
    try:
        await method(chat_id=chat_id, **kwargs)
    except TelegramAPIError as e:
        logging.error(f"Failed to send {method_name} to {chat_id}: {e}")

def get_file_id_and_size(message: Message):
    if message.photo:
        return "send_photo", message.photo[-1].file_id, message.photo[-1].file_size
    if message.video:
        return "send_video", message.video.file_id, message.video.file_size
    if message.document:
        return "send_document", message.document.file_id, message.document.file_size
    if message.voice:
        return "send_voice", message.voice.file_id, message.voice.file_size
    if message.audio:
        return "send_audio", message.audio.file_id, message.audio.file_size
    return None, None, None

class EngineerRelayState(StatesGroup):
    selecting_client = State()

@router.callback_query(TicketCallback.filter(F.action == "select"))
async def select_ticket_for_reply(callback: CallbackQuery, callback_data: TicketCallback, state: FSMContext):
    await state.update_data(active_ticket_id=callback_data.ticket_id)
    # Мы больше не в состоянии "выбора клиента", но сохраняем данные.
    await callback.message.edit_reply_markup(reply_markup=None) # Remove inline keyboard from previous message
    await callback.message.answer(
        f"✅ Выбран чат по заявке #{callback_data.ticket_id}. Ваши следующие сообщения будут отправлены этому клиенту.",
        reply_markup=engineer_active_ticket_kb()
    )
    await callback.answer()

@router.message(Command("my_tickets"))
@router.message(F.text == "📋 Мои заявки в работе")
async def list_my_tickets(message: Message, db: Database, is_engineer: bool, state: FSMContext):
    if not is_engineer:
        return
    user_id = message.from_user.id
    active_tickets = await db.get_active_tickets_for_engineer(user_id)

    state_data = await state.get_data()
    current_active_ticket_id = state_data.get("active_ticket_id")

    response_text = "Выберите заявку для переключения активного чата:"
    if not active_tickets:
        await message.answer("У вас нет активных заявок в работе.", reply_markup=engineer_default_menu_kb())
        return

    # Формируем и отправляем красивое HTML-сообщение со списком активных заявок
    summary_text = "<b>Ваши активные заявки:</b>\n\n"
    for ticket in active_tickets:
        summary_text += (
            f"<b>Заявка #{ticket['id']}</b>\n"
            f"  Компания: {html.escape(ticket['company'])}\n"
            f"  Оборудование: {html.escape(ticket['equipment_type'])}\n"
            f"  Проблема: {html.escape(ticket['problem'])}\n\n"
        )
    
    await message.answer(summary_text, parse_mode="HTML")

    # Далее идет существующая логика для выбора активного чата
    if current_active_ticket_id:
        response_text += f"\n\nТекущая активная заявка для ответов: <b>#{current_active_ticket_id}</b>"

    await message.answer(response_text, reply_markup=engineer_select_client_kb(active_tickets), parse_mode="HTML") # Inline keyboard
    await message.answer("Ваше меню обновлено.", reply_markup=engineer_default_menu_kb()) # Revert to default ReplyKeyboardMarkup

@router.message()
async def relay_messages(message: Message, bot: Bot, db: Database, is_engineer: bool, state: FSMContext):
    user_id = message.from_user.id
    
    # 1. Logic for Client -> Engineer
    ticket = await db.get_active_ticket_for_client(user_id)
    if ticket:
        eng_id = ticket['engineer_id']
        if not eng_id: # Should not happen if in_progress
            return

        method, file_id, file_size = get_file_id_and_size(message)
        
        prefix = f"💬 [Клиент | Заявка #{ticket['id']}]:\n"

        if file_id:
            if file_size and file_size > MAX_FILE_SIZE:
                await message.answer("❌ Файл слишком большой (лимит 20 МБ).")
                return

            caption = f"<b>{prefix}</b>{html.escape(message.caption or '')}"
            await safe_send(bot, eng_id, method, **{method.replace('send_', ''): file_id, 'caption': caption, 'parse_mode': "HTML"})
        elif message.text:
            await safe_send(bot, eng_id, "send_message", text=f"<b>{prefix}</b>{html.escape(message.text)}", parse_mode="HTML")
        return

    # 2. Logic for Engineer -> Client
    if is_engineer:
        active_tickets = await db.get_active_tickets_for_engineer(user_id)
        if not active_tickets:
            return # Engineer is not working on any ticket

        target_ticket = None
        if len(active_tickets) == 1:
            target_ticket = active_tickets[0]
        else:
            # Check if engineer selected a ticket in state
            state_data = await state.get_data()
            selected_id = state_data.get("active_ticket_id")
            if selected_id:
                for t in active_tickets:
                    if t['id'] == selected_id:
                        target_ticket = t
                        break
            
            if not target_ticket:
                await state.set_state(EngineerRelayState.selecting_client) # Set state to wait for selection
                await message.answer(
                    "У вас несколько активных заявок. Выберите, кому ответить:",
                    reply_markup=engineer_select_client_kb(active_tickets)
                )
                return

        client_id = target_ticket['client_id']
        method, file_id, file_size = get_file_id_and_size(message)
        
        prefix = "👨‍🔧 [Инженер]:\n"
        
        if file_id:
            if file_size and file_size > MAX_FILE_SIZE:
                await message.answer("❌ Файл слишком большой (лимит 20 МБ).")
                return
            caption = f"<b>{prefix}</b>{html.escape(message.caption or '')}"
            await safe_send(bot, client_id, method, **{method.replace('send_', ''): file_id, 'caption': caption, 'parse_mode': "HTML"})
        elif message.text:
            await safe_send(bot, client_id, "send_message", text=f"<b>{prefix}</b>{html.escape(message.text)}", parse_mode="HTML")
        return
