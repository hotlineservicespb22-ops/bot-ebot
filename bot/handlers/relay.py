import datetime
import html
import logging
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.database import Database
from bot.keyboards import (
    TicketCallback,
    engineer_active_ticket_kb,
    engineer_default_menu_kb,
    engineer_detail_kb,
    engineer_list_kb,
    engineer_redirect_kb,
    engineer_select_client_kb,
    ticket_action_kb,
)
from bot.media import save_media_file

router = Router()

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB

async def safe_send(bot: Bot, chat_id: int, method_name: str, **kwargs):
    """
    Безопасная отправка сообщений через Telegram API.
    parse_mode больше не передаётся локально — используется глобальный HTML из DefaultBotProperties.
    """
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
    if message.animation:
        return "send_animation", message.animation.file_id, message.animation.file_size
    if message.sticker:
        return "send_sticker", message.sticker.file_id, message.sticker.file_size
    if message.location:
        return "send_location", message.location, None
    if message.contact:
        return "send_contact", message.contact, None
    return None, None, None

def format_ticket_detail(ticket) -> str:
    """Форматирует детальную информацию о заявке для просмотра инженером."""
    status_map = {
        'open': '🟡 Нераспределена',
        'in_progress': '🔵 В работе',
        'completed': '✅ Завершена',
        'canceled': '🚫 Отменена'
    }
    status = status_map.get(ticket['status'], ticket['status'])
    text = (
        f"{_status_badge(ticket)} <b>Заявка #{ticket['id']}</b>\n"
        f"Статус: <b>{status}</b>\n\n"
        f"🏢 <b>Компания/Город:</b> {html.escape(str(ticket['company_city'] or '—'))}\n"
        f"🔧 <b>Станок:</b> {html.escape(str(ticket['machine_info'] or '—'))}\n"
        f"📝 <b>Проблема:</b> {html.escape(str(ticket['problem'] or '—'))}\n"
        f"📞 <b>Контакты:</b> {html.escape(str(ticket['contact'] or '—'))}\n"
        f"👤 <b>Клиент:</b> {html.escape(str(ticket['client_name'] or '—'))}"
    )
    return text


def format_client_history(history) -> str:
    """Компактный блок истории оборудования клиента (закрытые заявки).

    Пустая строка, если истории нет — блок не показывается вовсе.
    """
    if not history:
        return ""
    lines = ["\n🗂 <b>История оборудования клиента:</b>"]
    for h in history:
        machine = str(h['machine_info'] or '—')
        problem = str(h['problem'] or '—')
        lines.append(f"  • #{h['id']}: {html.escape(machine[:40])} — {html.escape(problem[:60])}")
    return "\n".join(lines)


async def build_ticket_detail(db: Database, ticket) -> str:
    """Детальная информация о заявке + история оборудования клиента (если есть)."""
    detail = format_ticket_detail(ticket)
    client_id = ticket['client_id']
    if client_id:
        history = await db.get_client_ticket_history(
            client_id, exclude_ticket_id=ticket['id'], limit=3
        )
        detail += format_client_history(history)
    return detail

def _status_badge(ticket) -> str:
    """Цветовой тег: 🔴 просрочка, 🟡 ждёт, 🟢 в работе, ⚪ остальное."""
    status = ticket['status'] if 'status' in ticket else ''
    created = ticket['created_at'] if 'created_at' in ticket else ''
    if status == 'open' and created:
        try:
            from bot.config import TICKET_TIMEOUT
            dt = datetime.datetime.fromisoformat(created)
            elapsed = (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds()
            if elapsed > TICKET_TIMEOUT:
                return '🔴'
        except Exception:
            pass
        return '🟡'
    if status == 'in_progress':
        return '🟢'
    if status in ('completed', 'canceled'):
        return '⚪'
    return '⚪'

def build_list_text(tickets, mode: str) -> str:
    """Строит текстовое описание списка заявок."""
    header = "<b>📋 Ваши активные заявки:</b>" if mode == 'mine' else "<b>📥 Нераспределенные заявки:</b>"
    lines = [header, ""]
    for t in tickets:
        company = str(t['company_city'] or '—')
        problem = str(t['problem'] or '—')
        badge = _status_badge(t)
        lines.append(f"{badge} <b>Заявка #{t['id']}</b> — {html.escape(company)}: {html.escape(problem)}")
    lines.append("")
    lines.append("Выберите заявку для просмотра:")
    return "\n".join(lines)

def format_ticket_history(messages) -> str:
    """Форматирует историю переписки по заявке для просмотра инженером."""
    if not messages:
        return "В этой заявке пока нет сообщений."

    lines = ["<b>💬 История переписки</b>\n"]
    for m in messages:
        role_icon = "👤 Клиент" if m['sender_role'] == 'client' else "👨‍🔧 Инженер"
        time_str = m['created_at'] or ''
        # Форматируем время (обрезаем ISO до даты и времени)
        try:
            dt = datetime.datetime.fromisoformat(time_str)
            time_str = dt.strftime("%d.%m %H:%M")
        except Exception:
            pass

        text = m['text'] or ''
        if m['media_type']:
            media_label = f"📎 {m['media_type']}"
            text = f"{text} [{media_label}]" if text else f"[{media_label}]"

        lines.append(f"<b>{role_icon}</b> ({time_str}):\n{html.escape(text)}\n")
    return "\n".join(lines)

async def show_ticket_list(msg, tickets, mode: str, db: Database, edit: bool = False, page: int = 0):
    """Показывает (или редактирует) сообщение со списком заявок (с пагинацией)."""
    text = build_list_text(tickets, mode)
    kb = engineer_list_kb(tickets, mode, page=page)
    if edit:
        await msg.edit_text(text, reply_markup=kb)
    else:
        await msg.answer(text, reply_markup=kb)

async def get_ticket_list_data(db: Database, user_id: int, mode: str):
    """Возвращает список заявок в зависимости от режима."""
    if mode == 'mine':
        return await db.get_active_tickets_for_engineer(user_id)
    return await db.get_open_tickets()

async def _require_engineer(callback: CallbackQuery, is_engineer: bool) -> bool:
    """Проверяет права инженера для callback-запросов."""
    if not is_engineer:
        await callback.answer("У вас нет прав инженера.", show_alert=True)
        return False
    return True

@router.callback_query(TicketCallback.filter(F.action == "peek"))
async def view_ticket_info(callback: CallbackQuery, callback_data: TicketCallback, db: Database, is_engineer: bool):
    """Показывает детальную информацию о заявке без взятия в работу (кнопка «👁 Посмотреть»)."""
    if not await _require_engineer(callback, is_engineer):
        return
    await callback.answer()
    ticket = await db.get_ticket(callback_data.ticket_id)
    if not ticket:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return
    detail = await build_ticket_detail(db, ticket)

    # Показываем правильные кнопки в зависимости от статуса
    if ticket['status'] == 'open':
        kb = ticket_action_kb(callback_data.ticket_id)
    elif ticket['engineer_id'] == callback.from_user.id:
        kb = engineer_redirect_kb(callback_data.ticket_id)
    else:
        kb = None

    await callback.message.answer(detail, reply_markup=kb)


@router.callback_query(TicketCallback.filter(F.action == "select"))
async def select_ticket_for_reply(callback: CallbackQuery, callback_data: TicketCallback, db: Database, state: FSMContext, is_engineer: bool):
    if not await _require_engineer(callback, is_engineer):
        return
    await callback.answer()  # Быстрый ответ Telegram для снятия спиннера на кнопке
    # Возобновляем сессию заявки (session_started_at), если инженер переключается
    # на другую свою активную заявку — иначе время простоя между заявками не считается.
    ticket = await db.get_ticket(callback_data.ticket_id)
    if ticket and ticket['status'] == 'in_progress':
        await db.resume_session(callback_data.ticket_id)
    # Устанавливаем активную заявку, НЕ очищая state целиком.
    # state.clear() стирает навигацию по списку заявок (ticket_view_ids/index/mode),
    # из-за чего после переключения список заявок инженера "пропадает".
    await state.update_data(active_ticket_id=callback_data.ticket_id)
    await callback.message.edit_reply_markup(reply_markup=None)  # Remove inline keyboard from previous message
    await callback.message.answer(
        f"✅ Выбран чат по заявке #{callback_data.ticket_id}. Ваши следующие сообщения будут отправлены этому клиенту.",
        reply_markup=engineer_active_ticket_kb()
    )

@router.message(Command("my_tickets"))
@router.message(F.text == "📋 Мои заявки в работе")
async def list_my_tickets(message: Message, db: Database, is_engineer: bool, state: FSMContext):
    if not is_engineer:
        return
    user_id = message.from_user.id
    active_tickets = await db.get_active_tickets_for_engineer(user_id)

    if not active_tickets:
        await message.answer("У вас нет активных заявок в работе.", reply_markup=engineer_default_menu_kb())
        return

    # Сохраняем данные для навигации по списку
    ids = [t['id'] for t in active_tickets]
    await state.update_data(ticket_view_mode='mine', ticket_view_ids=ids, ticket_view_index=0, ticket_view_page=0)

    await show_ticket_list(message, active_tickets, 'mine', db, page=0)
    await message.answer("Ваше меню обновлено.", reply_markup=engineer_default_menu_kb())

@router.message(F.text == "📥 Нераспределенные заявки")
async def list_open_tickets(message: Message, db: Database, is_engineer: bool, state: FSMContext):
    if not is_engineer:
        return

    open_tickets = await db.get_open_tickets()

    if not open_tickets:
        await message.answer("Нет нераспределенных заявок.", reply_markup=engineer_default_menu_kb())
        return

    # Сохраняем данные для навигации по списку
    ids = [t['id'] for t in open_tickets]
    await state.update_data(ticket_view_mode='open', ticket_view_ids=ids, ticket_view_index=0, ticket_view_page=0)

    await show_ticket_list(message, open_tickets, 'open', db, page=0)
    await message.answer("Ваше меню обновлено.", reply_markup=engineer_default_menu_kb())

@router.callback_query(TicketCallback.filter(F.action == "view"))
async def view_ticket(callback: CallbackQuery, callback_data: TicketCallback, db: Database, state: FSMContext, is_engineer: bool):
    if not await _require_engineer(callback, is_engineer):
        return
    await callback.answer()  # Быстрый ответ Telegram для снятия спиннера на кнопке
    data = await state.get_data()
    mode = data.get('ticket_view_mode', 'mine')
    ids = data.get('ticket_view_ids') or []

    # Если список устарел, пересоздаём его
    if callback_data.ticket_id not in ids:
        tickets = await get_ticket_list_data(db, callback.from_user.id, mode)
        ids = [t['id'] for t in tickets]
        await state.update_data(ticket_view_ids=ids)

    if not ids:
        await callback.answer("Список заявок пуст.", show_alert=True)
        return

    index = ids.index(callback_data.ticket_id)
    await state.update_data(ticket_view_index=index)

    ticket = await db.get_ticket(callback_data.ticket_id)
    if not ticket:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    await callback.message.edit_text(
        await build_ticket_detail(db, ticket),
        reply_markup=engineer_detail_kb(callback_data.ticket_id, mode, index, len(ids))
    )

@router.callback_query(TicketCallback.filter(F.action == "prev"))
@router.callback_query(TicketCallback.filter(F.action == "next"))
async def navigate_ticket(callback: CallbackQuery, callback_data: TicketCallback, db: Database, state: FSMContext, is_engineer: bool):
    if not await _require_engineer(callback, is_engineer):
        return
    await callback.answer()  # Быстрый ответ Telegram для снятия спиннера на кнопке
    data = await state.get_data()
    mode = data.get('ticket_view_mode', 'mine')
    ids = data.get('ticket_view_ids') or []
    index = data.get('ticket_view_index') or 0

    if not ids:
        await callback.answer("Список заявок устарел. Откройте его заново.", show_alert=True)
        return

    if callback_data.action == "prev":
        index = max(0, index - 1)
    else:
        index = min(len(ids) - 1, index + 1)

    await state.update_data(ticket_view_index=index)

    ticket_id = ids[index]
    ticket = await db.get_ticket(ticket_id)
    if not ticket:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    await callback.message.edit_text(
        await build_ticket_detail(db, ticket),
        reply_markup=engineer_detail_kb(ticket_id, mode, index, len(ids))
    )

@router.callback_query(TicketCallback.filter(F.action == "page"))
async def change_ticket_page(callback: CallbackQuery, callback_data: TicketCallback, db: Database, state: FSMContext, is_engineer: bool):
    """Переключает страницу списка заявок инженера."""
    if not await _require_engineer(callback, is_engineer):
        return
    await callback.answer()  # Быстрый ответ Telegram для снятия спиннера на кнопке
    data = await state.get_data()
    mode = data.get('ticket_view_mode', 'mine')
    page = max(0, callback_data.page)

    await state.update_data(ticket_view_page=page)

    tickets = await get_ticket_list_data(db, callback.from_user.id, mode)
    if not tickets:
        await callback.message.edit_text("Список пуст.")
        return

    ids = [t['id'] for t in tickets]
    await state.update_data(ticket_view_ids=ids)

    await show_ticket_list(callback.message, tickets, mode, db, edit=True, page=page)

@router.callback_query(TicketCallback.filter(F.action == "back_to_list"))
async def back_to_list(callback: CallbackQuery, callback_data: TicketCallback, db: Database, state: FSMContext, is_engineer: bool):
    if not await _require_engineer(callback, is_engineer):
        return
    await callback.answer()  # Быстрый ответ Telegram для снятия спиннера на кнопке
    data = await state.get_data()
    mode = data.get('ticket_view_mode', 'mine')
    page = data.get('ticket_view_page', 0) or 0

    tickets = await get_ticket_list_data(db, callback.from_user.id, mode)
    if not tickets:
        await callback.message.edit_text("Список пуст.")
        return

    # Обновляем сохранённый список (мог измениться)
    ids = [t['id'] for t in tickets]
    await state.update_data(ticket_view_ids=ids)

    await show_ticket_list(callback.message, tickets, mode, db, edit=True, page=page)

@router.callback_query(TicketCallback.filter(F.action == "redirect"))
async def redirect_to_ticket(callback: CallbackQuery, callback_data: TicketCallback, state: FSMContext, is_engineer: bool):
    """Переключает инженера на заявку, из которой пришло сообщение, для ответа."""
    if not await _require_engineer(callback, is_engineer):
        return
    await callback.answer()  # Быстрый ответ Telegram для снятия спиннера на кнопке
    await state.update_data(active_ticket_id=callback_data.ticket_id)
    await callback.message.answer(
        f"🔁 Вы переключены на заявку #{callback_data.ticket_id}. "
        f"Следующие сообщения будут отправлены клиенту этой заявки.",
        reply_markup=engineer_active_ticket_kb()
    )

@router.callback_query(TicketCallback.filter(F.action == "noop"))
async def noop(callback: CallbackQuery):
    await callback.answer()

@router.callback_query(TicketCallback.filter(F.action == "history"))
async def show_ticket_history(callback: CallbackQuery, callback_data: TicketCallback, db: Database, state: FSMContext, is_engineer: bool):
    if not await _require_engineer(callback, is_engineer):
        return
    await callback.answer()  # Быстрый ответ Telegram для снятия спиннера на кнопке

    ticket_id = callback_data.ticket_id
    ticket = await db.get_ticket(ticket_id)
    if not ticket:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    messages = await db.get_messages_for_ticket(ticket_id)
    history_text = format_ticket_history(messages)

    # Возвращаемся к деталям заявки после просмотра истории
    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="🔙 К заявке",
                callback_data=TicketCallback(action="view", ticket_id=ticket_id).pack()
            )
        ]]
    )

    await callback.message.edit_text(
        history_text,
        reply_markup=back_kb
    )

async def _send_relayed_message(
    bot: Bot,
    target_id: int,
    message: Message,
    method: str,
    file_id,
    prefix: str,
    reply_markup=None,
):
    """Отправляет пересылаемое сообщение получателю (медиа/гео/контакт/текст)."""
    if method == 'send_location':
        # Сначала отправляем префикс с именем отправителя, затем геолокацию
        if prefix:
            await safe_send(bot, target_id, "send_message", text=f"<b>{prefix}</b>")
        await safe_send(bot, target_id, method, latitude=file_id.latitude, longitude=file_id.longitude, reply_markup=reply_markup)
        return
    if method == 'send_contact':
        # Сначала отправляем префикс с именем отправителя, затем контакт
        if prefix:
            await safe_send(bot, target_id, "send_message", text=f"<b>{prefix}</b>")
        await safe_send(bot, target_id, method, phone_number=file_id.phone_number, first_name=file_id.first_name or '', reply_markup=reply_markup)
        return
    if file_id:
        caption = f"<b>{prefix}</b>{html.escape(message.caption or '')}"
        await safe_send(bot, target_id, method, **{method.replace('send_', ''): file_id, 'caption': caption, 'reply_markup': reply_markup})
    elif message.text:
        await safe_send(bot, target_id, "send_message", text=f"<b>{prefix}</b>{html.escape(message.text)}", reply_markup=reply_markup)


async def _persist_relayed_message(
    db: Database,
    ticket_id: int,
    sender_id: int,
    sender_role: str,
    message: Message,
    method: str,
    file_id,
    bot: Bot,
) -> None:
    """
    Сохраняет сообщение переписки и, при наличии, медиафайл заявки.
    Общая логика для сообщений клиента и инженера (устраняет дублирование).
    """
    # Сохраняем в историю переписки
    await db.save_message(
        ticket_id=ticket_id,
        sender_id=sender_id,
        sender_role=sender_role,
        text=message.text or message.caption or '',
        media_type=file_id and method.replace('send_', '') or None
    )

    # Сохраняем медиафайл в папку заявки
    if file_id and method not in ('send_location', 'send_contact'):
        media_type = method.replace('send_', '')
        file_path = await save_media_file(
            bot=bot,
            file_id=str(file_id),
            ticket_id=ticket_id,
            media_type=media_type,
            file_name=getattr(message, media_type, None) and getattr(getattr(message, media_type), 'file_name', None)
        )
        if file_path:
            await db.save_media(
                ticket_id=ticket_id,
                file_id=str(file_id),
                file_type=media_type,
                file_path=file_path,
                sender_id=sender_id,
                sender_role=sender_role
            )


@router.message()
async def relay_messages(message: Message, bot: Bot, db: Database, is_engineer: bool, state: FSMContext):
    user_id = message.from_user.id

    # 1. Logic for Client -> Engineer
    ticket = await db.get_active_ticket_for_client(user_id)
    if ticket:
        eng_id = ticket['engineer_id']
        if not eng_id:  # Заявка ещё не взята инженером (status = 'open')
            # Сохраняем сообщение клиента (текст + медиа) в историю, чтобы оно
            # не потерялось и было передано инженеру после взятия заявки.
            method, file_id, file_size = get_file_id_and_size(message)
            await _persist_relayed_message(
                db, ticket['id'], user_id, 'client', message, method, file_id, bot
            )
            await message.answer(
                "⏳ Ваша заявка ещё ожидает назначения дежурного инженера. "
                "Как только инженер подключится, ваши сообщения будут ему переданы."
            )
            return

        method, file_id, file_size = get_file_id_and_size(message)

        # Проверяем размер файла ДО сохранения в историю, чтобы не фиксировать недоставленное
        if file_size and file_size > MAX_FILE_SIZE:
            await message.answer("❌ Файл слишком большой (лимит 20 МБ).")
            return

        # Отображаем имя клиента, если есть; иначе название компании; иначе "Клиент"
        display_name = (ticket['client_name'] or '').strip()
        if not display_name:
            display_name = (ticket['company_city'] or '').strip()
        if not display_name:
            display_name = "Клиент"
        prefix = f"💬 [{html.escape(display_name)} | Заявка #{ticket['id']}]:\n"

        # Сохраняем в историю переписки и медиафайл (общая логика)
        await _persist_relayed_message(db, ticket['id'], user_id, 'client', message, method, file_id, bot)

        # Кнопка для быстрого переключения инженера на эту заявку для ответа
        redirect_kb = engineer_redirect_kb(ticket['id'])
        await _send_relayed_message(bot, eng_id, message, method, file_id, prefix, reply_markup=redirect_kb)
        return

    # 2. Logic for Engineer -> Client
    if is_engineer:
        active_tickets = await db.get_active_tickets_for_engineer(user_id)
        if not active_tickets:
            return  # Engineer is not working on any ticket

        # Приоритет: if engineer replies (reply) to a message, resolve the ticket
        # from the "Заявка #ID" marker in the forwarded message's text/caption.
        # This prevents messages from going to the wrong client when the engineer
        # has multiple active tickets but has not switched the active selection.
        reply_ticket_id = None
        if message.reply_to_message:
            reply_text = (message.reply_to_message.text or message.reply_to_message.caption or '')
            m = re.search(r"Заявка #(\d+)", reply_text)
            if m:
                reply_ticket_id = int(m.group(1))

        target_ticket = None
        if len(active_tickets) == 1:
            target_ticket = active_tickets[0]
        elif reply_ticket_id is not None:
            # Нашли заявку по reply — используем её, если она принадлежит инженеру
            for t in active_tickets:
                if t['id'] == reply_ticket_id:
                    target_ticket = t
                    break
        if target_ticket is None and len(active_tickets) > 1:
            # Check if engineer selected a ticket in state
            state_data = await state.get_data()
            selected_id = state_data.get("active_ticket_id")
            if selected_id:
                for t in active_tickets:
                    if t['id'] == selected_id:
                        target_ticket = t
                        break
                # Если выбранная заявка устарела (завершена/отменена/не принадлежит
                # инженеру) — сбрасываем её, чтобы не блокировать доставку сообщений.
                if target_ticket is None:
                    await state.update_data(active_ticket_id=None)

            if not target_ticket:
                await message.answer(
                    "У вас несколько активных заявок. Выберите, кому ответить:",
                    reply_markup=engineer_select_client_kb(active_tickets)
                )
                return

        client_id = target_ticket['client_id']
        method, file_id, file_size = get_file_id_and_size(message)

        # Проверяем размер файла ДО сохранения в историю
        if file_size and file_size > MAX_FILE_SIZE:
            await message.answer("❌ Файл слишком большой (лимит 20 МБ).")
            return

        # Отображаем имя инженера из БД, если есть; иначе "Инженер"
        engineer_name = await db.get_engineer_name(user_id)
        if not engineer_name:
            engineer_name = "Инженер"
        prefix = f"👨‍🔧 [{html.escape(engineer_name)}]:\n"

        # Сохраняем в историю переписки и медиафайл (общая логика)
        await _persist_relayed_message(db, target_ticket['id'], user_id, 'engineer', message, method, file_id, bot)

        await _send_relayed_message(bot, client_id, message, method, file_id, prefix)
        return

    # 3. Пользователь не имеет активной заявки и не является инженером
    await message.answer(
        "Я не понял вас. Используйте меню ниже, чтобы оформить заявку или задать вопрос.",
        reply_markup=None
    )