import logging
import html
from aiogram import Router, Bot, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from bot.config import ADMIN_IDS
from bot.database import Database
from bot.keyboards import main_menu, ticket_action_kb, cancel_kb, active_ticket_menu_kb, engineer_main_menu, contact_kb, RatingCallback, rating_kb
from bot.media import save_media_file

router = Router()

class TicketForm(StatesGroup):
    """Оптимизированная короткая воронка FSM: 4 шага."""
    problem_media = State()   # Шаг 1: описание проблемы + фото/видео
    machine_info = State()    # Шаг 2: фото шильдика или бренд/модель
    company_city = State()    # Шаг 3: город + ИНН/название компании
    contact = State()         # Шаг 4: контакт через request_contact

async def delete_last_bot_message(bot: Bot, message: Message, state: FSMContext):
    """UX-очистка: удаляет предыдущее сообщение бота (вопрос) из чата."""
    data = await state.get_data()
    last_msg_id = data.get('last_bot_msg_id')
    if last_msg_id:
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=last_msg_id)
        except Exception as e:
            logging.warning(f"Не удалось удалить сообщение бота {last_msg_id}: {e}")

async def delete_last_client_message(bot: Bot, message: Message, state: FSMContext):
    """UX-очистка: удаляет предыдущее сообщение клиента из чата (по аналогии с сообщениями бота)."""
    data = await state.get_data()
    last_msg_id = data.get('last_client_msg_id')
    if last_msg_id:
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=last_msg_id)
        except Exception as e:
            logging.warning(f"Не удалось удалить сообщение клиента {last_msg_id}: {e}")

@router.message(F.text == "❌ Отменить / Закрыть заявку")
async def cancel_ticket_by_client(message: Message, bot: Bot, db: Database):
    user_id = message.from_user.id
    # Ищем активную заявку для этого клиента
    ticket = await db.get_active_ticket_for_client(user_id)

    if not ticket:
        await message.answer(
            "У вас нет активных заявок для отмены.",
            reply_markup=main_menu()
        )
        return

    ticket_id = ticket['id']
    engineer_id = ticket['engineer_id']

    # Закрываем заявку в БД с комментарием об отмене клиентом
    await db.close_ticket(ticket_id, status='canceled', comment="Отменено клиентом")

    # Уведомляем клиента и возвращаем в главное меню
    await message.answer(
        f"✅ Заявка #{ticket_id} была отменена. Вы можете создать новую в любой момент.",
        reply_markup=main_menu()
    )

    # Уведомляем инженера, если он был назначен
    if engineer_id:
        try:
            await bot.send_message(engineer_id, f"⚠️ Клиент отменил заявку #{ticket_id}.")
        except Exception as e:
            logging.error(f"Не удалось уведомить инженера {engineer_id} об отмене заявки {ticket_id}: {e}")

@router.callback_query(RatingCallback.filter())
async def process_rating(callback: CallbackQuery, callback_data: RatingCallback, db: Database, state: FSMContext):
    """Обрабатывает выбор оценки заявки клиентом."""
    # Проверяем, что оценку ставит клиент этой заявки
    ticket = await db.get_ticket(callback_data.ticket_id)
    if not ticket:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return
    if ticket['client_id'] != callback.from_user.id:
        await callback.answer("Вы не можете оценить эту заявку.", show_alert=True)
        return

    # Сохраняем оценку, комментарий можно будет добавить следующим сообщением
    await db.save_rating(
        ticket_id=callback_data.ticket_id,
        client_id=callback.from_user.id,
        rating=callback_data.value
    )
    await callback.message.edit_text(
        f"⭐ Спасибо за оценку <b>{callback_data.value}/5</b>!\n\n"
        "Если хотите, можете оставить комментарий текстом, или нажмите /cancel чтобы завершить."
    )
    await callback.answer("Оценка сохранена!")

@router.message(Command("cancel"))
@router.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активного действия для отмены.", reply_markup=main_menu())
        return
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu())

@router.message(Command("my_requests"))
@router.message(F.text == "📋 Мои заявки")
async def my_requests(message: Message, db: Database):
    """Показывает историю заявок клиента."""
    tickets = await db.get_client_tickets(message.from_user.id, limit=10)
    if not tickets:
        await message.answer("📋 У вас пока нет заявок.", reply_markup=main_menu())
        return

    status_map = {
        'open': '🟡 Ожидает инженера',
        'in_progress': '🔵 В работе',
        'completed': '✅ Завершена',
        'canceled': '🚫 Отменена'
    }
    lines = ["<b>📋 Ваши заявки:</b>\n"]
    for t in tickets:
        status = status_map.get(t['status'], t['status'])
        lines.append(
            f"🎫 <b>#{t['id']}</b> — {status}\n"
            f"   {html.escape(str(t['machine_info'] or '—'))}: {html.escape(str(t['problem'] or '—'))[:50]}"
        )
    await message.answer("\n".join(lines), reply_markup=main_menu())

@router.message(Command("help"))
async def cmd_help(message: Message, is_engineer: bool):
    """Помощь по командам и возможностям бота."""
    if is_engineer:
        await message.answer(
            "👨‍🔧 <b>Меню инженера</b>\n\n"
            "• <b>📋 Мои заявки в работе</b> — список активных заявок\n"
            "• <b>📥 Нераспределенные заявки</b> — взять новую заявку\n"
            "• <b>✅ Завершить текущую</b> — закрыть выбранную заявку\n"
            "• <b>🚫 Отменить текущую</b> — отменить заявку\n\n"
            "Отвечайте на сообщения клиентов прямо в чат — они будут автоматически переданы клиенту."
        )
    else:
        await message.answer(
            "❓ <b>Помощь</b>\n\n"
            "• <b>🛠 Оставить заявку на сервис ЧПУ</b> — оформить новую заявку (4 шага)\n"
            "• <b>❌ Отменить / Закрыть заявку</b> — отменить активную заявку\n"
            "• <b>❓ Частые вопросы</b> — FAQ и контакты\n\n"
            "После создания заявки вы можете переписываться с инженером прямо в этом чате."
        )

@router.message(CommandStart())
async def cmd_start(message: Message, is_engineer: bool):
    if is_engineer:
        await message.answer(
            "Здравствуйте! Вы авторизованы как инженер. Вам доступно меню управления заявками.",
            reply_markup=engineer_main_menu()
        )
    else:
        await message.answer(
            "Здравствуйте! Сервисная служба <b>Hotline Service</b>.\n"
            "Мы специализируемся на диагностике и ремонте станков с ЧПУ.\n\n"
            "Нажмите кнопку ниже, чтобы оформить заявку дежурному инженеру.",
            reply_markup=main_menu()
        )

@router.message(F.text == "❓ Частые вопросы")
async def show_faq(message: Message):
    faq_text = (
        "❓ <b>Часто задаваемые вопросы (FAQ)</b>\n\n"
        "📅 <b>График работы нашей сервисной службы:</b>\n"
        "• Будни (Пн-Пт): с 09:00 до 18:00 (МСК)\n"
        "• Суббота-Воскресенье: выходной\n\n"
        "🔧 Мы выполняем диагностику, пусконаладочные работы, а также ремонт механической и электронной части станков со стойками ЧПУ (Fanuc, Siemens, Heidenhain, Mitsubishi и др.).\n\n"
        "🌐 Подробную информацию об услугах и ценах вы можете найти на нашем официальном сайте:\n"
        '👉 <a href="https://hotline-service.ru">hotline-service.ru</a>'
    )
    await message.answer(faq_text, disable_web_page_preview=True)

@router.message(F.text == "🛠 Оставить заявку на сервис ЧПУ")
async def start_ticket(message: Message, state: FSMContext, bot: Bot, db: Database):
    # Не даём клиенту создать новую заявку, пока есть активная
    active = await db.get_active_ticket_for_client(message.from_user.id)
    if active:
        await message.answer(
            f"⚠️ У вас уже есть активная заявка #{active['id']}. "
            "Сначала дождитесь её завершения или отмените её через меню.",
            reply_markup=active_ticket_menu_kb()
        )
        return
    await state.clear()
    await state.set_state(TicketForm.problem_media)
    msg = await message.answer(
        "Начинаем оформление заявки.\n\n"
        "📝 <b>Шаг 1/4.</b> Опишите проблему и прикрепите фото/видео поломки одним сообщением.\n\n"
        "Например: «Станок не включается, ошибка AL-01» + фото панели.\n\n"
        "Чтобы прервать, нажмите '❌ Отмена' внизу.",
        reply_markup=cancel_kb()
    )
    # Сохраняем ID сообщения клиента (кнопку), чтобы удалить его на следующем шаге
    await state.update_data(last_bot_msg_id=msg.message_id, last_client_msg_id=message.message_id)

@router.message(StateFilter(TicketForm.problem_media))
async def ticket_problem_media(message: Message, state: FSMContext, bot: Bot):
    # UX-очистка: удаляем предыдущее сообщение бота и клиента
    await delete_last_bot_message(bot, message, state)
    await delete_last_client_message(bot, message, state)

    # Извлекаем текст и медиа из одного сообщения
    problem_text = message.text or message.caption or ''
    media_id = None
    media_type = None
    if message.photo:
        media_id = message.photo[-1].file_id
        media_type = 'photo'
    elif message.video:
        media_id = message.video.file_id
        media_type = 'video'

    if len(problem_text.strip()) < 5:
        msg = await message.answer("❌ Опишите проблему подробнее (минимум 5 символов). Можно добавить фото/видео:")
        await state.update_data(last_bot_msg_id=msg.message_id)
        return

    await state.update_data(problem=problem_text, media_id=media_id, media_type=media_type, last_client_msg_id=message.message_id)
    await state.set_state(TicketForm.machine_info)
    msg = await message.answer(
        "📷 <b>Шаг 2/4.</b> Пришлите фото шильдика станка (табличка с моделью) "
        "или напишите бренд и модель вручную.\n\n"
        "Например: «Wattsan 1610, Fanuc 0i-MF»"
    )
    await state.update_data(last_bot_msg_id=msg.message_id)

@router.message(StateFilter(TicketForm.machine_info))
async def ticket_machine_info(message: Message, state: FSMContext, bot: Bot):
    # UX-очистка: удаляем предыдущее сообщение бота и клиента
    await delete_last_bot_message(bot, message, state)
    await delete_last_client_message(bot, message, state)

    machine_info_text = message.text or message.caption or ''
    # Если прислали фото шильдика без текста, фиксируем это
    if not machine_info_text and message.photo:
        machine_info_text = "Фото шильдика прикреплено"

    if len(machine_info_text.strip()) < 2:
        msg = await message.answer("❌ Укажите бренд/модель станка или пришлите фото шильдика:")
        await state.update_data(last_bot_msg_id=msg.message_id)
        return

    # Сохраняем фото/видео шильдика, если оно было прикреплено
    machine_media_id = None
    machine_media_type = None
    if message.photo:
        machine_media_id = message.photo[-1].file_id
        machine_media_type = 'photo'
    elif message.video:
        machine_media_id = message.video.file_id
        machine_media_type = 'video'

    await state.update_data(
        machine_info=machine_info_text,
        machine_media_id=machine_media_id,
        machine_media_type=machine_media_type,
        last_client_msg_id=message.message_id
    )
    await state.set_state(TicketForm.company_city)
    msg = await message.answer(
        "🏢 <b>Шаг 3/4.</b> Укажите город и ИНН или название компании.\n\n"
        "Например: «Москва, ИНН 7712345678» или «ООО Ромашка, Казань»"
    )
    await state.update_data(last_bot_msg_id=msg.message_id)

@router.message(StateFilter(TicketForm.company_city))
async def ticket_company_city(message: Message, state: FSMContext, bot: Bot):
    # UX-очистка: удаляем предыдущее сообщение бота и клиента
    await delete_last_bot_message(bot, message, state)
    await delete_last_client_message(bot, message, state)

    company_city_text = message.text or ''
    if len(company_city_text.strip()) < 2:
        msg = await message.answer("❌ Укажите город и ИНН/название компании:")
        await state.update_data(last_bot_msg_id=msg.message_id)
        return

    await state.update_data(company_city=company_city_text, last_client_msg_id=message.message_id)
    await state.set_state(TicketForm.contact)
    msg = await message.answer(
        "📞 <b>Шаг 4/4.</b> Поделитесь контактом для связи:",
        reply_markup=contact_kb()
    )
    await state.update_data(last_bot_msg_id=msg.message_id)

@router.message(StateFilter(TicketForm.contact))
async def ticket_contact(message: Message, state: FSMContext, bot: Bot, db: Database):
    # UX-очистка: удаляем предыдущее сообщение бота и клиента
    await delete_last_bot_message(bot, message, state)
    await delete_last_client_message(bot, message, state)

    # Контакт может быть текстом или через кнопку request_contact
    contact_text = message.text or ''
    if message.contact:
        contact_text = f"{message.contact.phone_number} ({message.contact.first_name or message.from_user.full_name})"

    if len(contact_text.strip()) < 5:
        msg = await message.answer(
            "❌ Контактная информация должна содержать минимум 5 символов. "
            "Нажмите кнопку ниже или введите контакт вручную:",
            reply_markup=contact_kb()
        )
        await state.update_data(last_bot_msg_id=msg.message_id)
        return

    await state.update_data(contact=contact_text)
    data = await state.get_data()
    await state.clear()

    ticket_id = await db.create_ticket(
        client_id=message.from_user.id,
        client_name=message.from_user.full_name,
        company=data.get('company_city', ''),
        equipment_type='',
        brand='',
        cnc_model='',
        problem=data.get('problem', ''),
        media_id=data.get('media_id'),
        city='',
        inn_contract='',
        contact=data.get('contact', ''),
        machine_info=data.get('machine_info', ''),
        company_city=data.get('company_city', ''),
        machine_media_id=data.get('machine_media_id')
    )

    # Сохраняем фото/видео проблемы в папку заявки
    media_id = data.get('media_id')
    media_type = data.get('media_type')
    if media_id:
        file_path = await save_media_file(
            bot=bot,
            file_id=media_id,
            ticket_id=ticket_id,
            media_type=media_type or 'photo'
        )
        if file_path:
            await db.save_media(
                ticket_id=ticket_id,
                file_id=media_id,
                file_type=media_type or 'photo',
                file_path=file_path,
                sender_id=message.from_user.id,
                sender_role='client'
            )

    # Сохраняем фото/видео шильдика в папку заявки
    machine_media_id = data.get('machine_media_id')
    machine_media_type = data.get('machine_media_type')
    if machine_media_id:
        machine_file_path = await save_media_file(
            bot=bot,
            file_id=machine_media_id,
            ticket_id=ticket_id,
            media_type=machine_media_type or 'photo'
        )
        if machine_file_path:
            await db.save_media(
                ticket_id=ticket_id,
                file_id=machine_media_id,
                file_type=machine_media_type or 'photo',
                file_path=machine_file_path,
                sender_id=message.from_user.id,
                sender_role='client'
            )

    # Уведомляем администраторов о новой заявке
    admin_notify = (
        f"🆕 <b>Новая заявка #{ticket_id}</b>\n\n"
        f"🏢 <b>Компания/Город:</b> {html.escape(data.get('company_city', ''))}\n"
        f"🔧 <b>Станок:</b> {html.escape(data.get('machine_info', ''))}\n\n"
        f"<b>Проблема:</b> {html.escape(data.get('problem', ''))}\n\n"
        f"<b>Контакты:</b> {html.escape(data.get('contact', ''))}\n"
        f"<b>Отправитель:</b> {html.escape(message.from_user.full_name)}"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_notify)
        except Exception as e:
            logging.error(f"Не удалось уведомить админа {admin_id} о заявке #{ticket_id}: {e}")

    engineers = await db.get_engineers()

    if not engineers:
        logging.warning(f"Заявка #{ticket_id} создана, но не найдено ни одного активного инженера для уведомления (is_active = 1)")

    ticket_text = (
        f"🚨 <b>Новая заявка #{ticket_id}</b>\n\n"
        f"🏢 <b>Компания/Город:</b> {html.escape(data.get('company_city', ''))}\n"
        f"🔧 <b>Станок:</b> {html.escape(data.get('machine_info', ''))}\n\n"
        f"<b>Проблема:</b> {html.escape(data.get('problem', ''))}\n\n"
        f"<b>Контакты:</b> {html.escape(data.get('contact', ''))}\n"
        f"<b>Отправитель:</b> {html.escape(message.from_user.full_name)}"
    )

    media_id = data.get('media_id')
    media_type = data.get('media_type')

    for row in engineers:
        try:
            if media_id and media_type == 'photo':
                await bot.send_photo(
                    row['user_id'],
                    photo=media_id,
                    caption=ticket_text,
                    reply_markup=ticket_action_kb(ticket_id)
                )
            elif media_id and media_type == 'video':
                await bot.send_video(
                    row['user_id'],
                    video=media_id,
                    caption=ticket_text,
                    reply_markup=ticket_action_kb(ticket_id)
                )
            else:
                await bot.send_message(
                    row['user_id'],
                    ticket_text,
                    reply_markup=ticket_action_kb(ticket_id)
                )

            # Отправляем фото/видео шильдика отдельным сообщением
            if machine_media_id:
                if machine_media_type == 'photo':
                    await bot.send_photo(
                        row['user_id'],
                        photo=machine_media_id,
                        caption="📷 Фото шильдика станка"
                    )
                elif machine_media_type == 'video':
                    await bot.send_video(
                        row['user_id'],
                        video=machine_media_id,
                        caption="📷 Видео шильдика станка"
                    )
        except Exception as e:
            logging.error(f"Не удалось отправить инженеру {row['user_id']}: {e}")

    await message.answer(
        f"✅ <b>Заявка #{ticket_id} принята!</b>\n"
        "Дежурный инженер подключится к диалогу в ближайшее время. "
        "Все дальнейшие сообщения, отправленные сюда, будут переданы специалисту. Вы можете отменить заявку через меню ниже.",
        reply_markup=active_ticket_menu_kb()
    )