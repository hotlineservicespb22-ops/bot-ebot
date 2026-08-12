import html
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.config import ADMIN_IDS
from bot.database import Database
from bot.keyboards import (
    RatingCallback,
    active_ticket_menu_kb,
    cancel_kb,
    contact_kb,
    engineer_default_menu_kb,
    engineer_main_menu,
    main_menu,
    ticket_action_kb,
)
from bot.media import save_media_file

router = Router()

class TicketForm(StatesGroup):
    """Оптимизированная короткая воронка FSM: 4 шага."""
    problem_media = State()   # Шаг 1: описание проблемы + фото/видео
    machine_info = State()    # Шаг 2: фото шильдика или бренд/модель
    company_city = State()    # Шаг 3: город + ИНН/название компании
    contact = State()         # Шаг 4: контакт через request_contact
    rating_comment = State()  # Комментарий после оценки заявки

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
    await callback.answer()  # Быстрый ответ Telegram для снятия спиннера на кнопке
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
    # Устанавливаем FSM-состояние для приёма комментария
    await state.set_state(TicketForm.rating_comment)
    await state.update_data(rating_ticket_id=callback_data.ticket_id)

@router.message(StateFilter(TicketForm.rating_comment))
async def save_rating_comment(message: Message, state: FSMContext, db: Database):
    """Сохраняет комментарий клиента после оценки заявки."""
    data = await state.get_data()
    ticket_id = data.get('rating_ticket_id')
    if not ticket_id:
        await state.clear()
        await message.answer("Комментарий не сохранён. Спасибо за обращение!", reply_markup=main_menu())
        return

    comment = message.text or message.caption or ''
    if comment.strip():
        await db.update_rating_comment(ticket_id=ticket_id, comment=comment.strip())

    await state.clear()
    await message.answer("✅ Спасибо за ваш комментарий!", reply_markup=main_menu())

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
async def my_requests(message: Message, db: Database, is_engineer: bool):
    """Показывает историю заявок клиента, а для инженера — его активные заявки."""
    # Если пользователь — инженер, показываем его активные заявки (как инженера),
    # а не клиентские. Иначе инженер, нажимая "Мои заявки", видит пустой список
    # ("У вас пока нет заявок"), т.к. у него нет заявок как у клиента.
    if is_engineer:
        tickets = await db.get_active_tickets_for_engineer(message.from_user.id)
        if not tickets:
            await message.answer(
                "У вас нет активных заявок в работе.",
                reply_markup=engineer_default_menu_kb()
            )
            return

        status_map = {
            'open': '🟡 Ожидает инженера',
            'in_progress': '🔵 В работе',
            'completed': '✅ Завершена',
            'canceled': '🚫 Отменена'
        }
        lines = ["<b>📋 Ваши активные заявки:</b>\n"]
        for t in tickets:
            status = status_map.get(t['status'], t['status'])
            lines.append(
                f"🎫 <b>#{t['id']}</b> — {status}\n"
                f"   {html.escape(str(t['machine_info'] or '—'))}: {html.escape(str(t['problem'] or '—'))[:50]}"
            )
        await message.answer("\n".join(lines), reply_markup=engineer_default_menu_kb())
        return

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
        "🕐 <b>Режим работы:</b> заявки принимаем круглосуточно, 24/7. Реакция на обращение — в течение 15 минут.\n"
        "📞 Единый телефон: <b>8 800 777-38-56</b>\n"
        "✉️ E-mail: info@hotline-service.ru\n"
        "🌐 Сайт: <a href=\"https://hotline-service.ru\">hotline-service.ru</a>\n\n"
        "─── <b>Оборудование и чиллер</b> ───\n"
        "<b>1. Почему чиллер не в комплекте?</b>\n"
        "Мощность трубки клиент выбирает сам, поэтому чиллер не навязываем. Подбор: до 60 Вт — CW-3000; 80–100 Вт — CW-5000; 100–130 Вт — CW-5200; 130 Вт и выше — CW-5300 и мощнее.\n\n"
        "<b>2. Какую воду заливать в чиллер?</b>\n"
        "Только дистиллированную, рабочая температура 15–25 °C. Для трубки допустим пропиленгликоль до 30%. Антифриз и этиленгликоль нельзя — они разъедают трубку.\n\n"
        "<b>3. Как часто менять воду в чиллере?</b>\n"
        "Не реже одного раза в 5–6 месяцев при работе 8 часов в день, 5 дней в неделю.\n\n"
        "<b>4. Какой компрессор нужен?</b>\n"
        "Для гравировки — мембранный (40–70 л/мин). Для резки толстых материалов и работы 24/7 — поршневой безмасляный (70–150 л/мин, 2–6 бар). Для металлореза — до 20 бар.\n\n"
        "─── <b>Лазерная трубка</b> ───\n"
        "<b>5. Какой ресурс у трубки?</b>\n"
        "От 1500 до 10000 часов — зависит от режима работы и качества охлаждения.\n\n"
        "<b>6. Какая гарантия на трубку?</b>\n"
        "6 месяцев, если ПНР делали специалисты Hotline-Service, и 3 месяца при самостоятельной установке. Гарантия покрывает искажение луча, прогорание внутренних зеркал и отсутствие луча.\n\n"
        "<b>7. Нужен ли новый БВН при замене трубки?</b>\n"
        "Часто да: мощность БВН должна соответствовать новой трубке. Важно не эксплуатировать БВН выше 85% мощности.\n\n"
        "─── <b>Возможности станков</b> ───\n"
        "<b>8. Можно ли резать металл на CO2-станке?</b>\n"
        "Обычным CO2-станком — нет. Металл режут специальной версией NC (с подачей кислорода), фрезером или волоконным лазерным металлорезом.\n\n"
        "<b>9. Можно ли работать с ПВХ?</b>\n"
        "Резать ПВХ на CO2 не рекомендуется: выделяются агрессивные вещества, разрушающие узлы станка. Маркировать пластики можно, но ПВХ — осторожно и с вытяжкой.\n\n"
        "─── <b>Обслуживание</b> ───\n"
        "<b>10. Как часто чистить оптику на CO2?</b>\n"
        "Защитное стекло и линзу осматривать каждый день, три зеркала чистить по мере необходимости. Грязное выходное зеркало трубки ведёт к её перегреву и гибели.\n\n"
        "<b>11. Что такое юстировка и как часто её делать?</b>\n"
        "Это настройка луча по трём зеркалам и его центровка на сопло. На рамных станках после перевозки повторная юстировка обычно не нужна. Проверять стоит при двоении, косом резе или падении мощности.\n\n"
        "<b>12. Какой стабилизатор нужен?</b>\n"
        "Только сервоприводный (например, Ресанта АСН/1-ЭМ): точность 2%, вход 140–260 В, чистая синусоида. Дешёвые релейные модели использовать нельзя — они портят технику.\n\n"
        "<b>13. Как заземлять станок?</b>\n"
        "Нужен отдельный контур заземления с сопротивлением менее 4 Ом. Зануление и заземление на трубу отопления недопустимы.\n\n"
        "─── <b>Конструкция станков</b> ───\n"
        "<b>14. Чем отличается стол ST от LT?</b>\n"
        "ST — статичный (неподвижный), ход по вертикали 40 мм регулируется соплом. LT — моторизованный подъёмный, опускается на 160 мм на цепи, удобен для толстых заготовок.\n\n"
        "<b>15. Что даёт автофокус?</b>\n"
        "Автоматически держит фокус луча на поверхности материала по данным лазерного дальномера. Удобно для неровных и толстых материалов.\n\n"
        "<b>16. Зачем нужен Duos?</b>\n"
        "Это версия с двумя головами и двумя трубками: удобна для тиража одинаковых изделий, обе головы работают сразу. Рекомендуется два чиллера.\n\n"
        "<b>17. Сколько мощности трубки нужно на толщину фанеры?</b>\n"
        "Ориентир — примерно 10 Вт на каждый 1 мм толщины, держа трубку на 80% мощности. Например, для фанеры 6 мм нужна трубка 100–120 Вт.\n\n"
        "─── <b>Гарантия и ремонт</b> ───\n"
        "<b>18. Почему ремонт платный, станок же недавно куплен?</b>\n"
        "Гарантия покрывает только заводские дефекты. Расходники, перегрев, нарушение правил установки и эксплуатации, механические повреждения — вне гарантии.\n\n"
        "🔧 Мы выполняем диагностику, пусконаладочные работы (ПНР), ремонт и техническое обслуживание станков с ЧПУ и лазерного оборудования.\n\n"
        "Подробную информацию об услугах и ценах вы можете найти на нашем сайте:\n"
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
        company='',
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
            sent_msg = None
            if media_id and media_type == 'photo':
                sent_msg = await bot.send_photo(
                    row['user_id'],
                    photo=media_id,
                    caption=ticket_text,
                    reply_markup=ticket_action_kb(ticket_id)
                )
            elif media_id and media_type == 'video':
                sent_msg = await bot.send_video(
                    row['user_id'],
                    video=media_id,
                    caption=ticket_text,
                    reply_markup=ticket_action_kb(ticket_id)
                )
            else:
                sent_msg = await bot.send_message(
                    row['user_id'],
                    ticket_text,
                    reply_markup=ticket_action_kb(ticket_id)
                )

            # Сохраняем message_id уведомления, чтобы удалить его, когда заявку возьмут
            if sent_msg:
                await db.save_ticket_notification(ticket_id, row['user_id'], sent_msg.message_id)

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