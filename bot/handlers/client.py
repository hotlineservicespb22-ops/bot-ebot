import datetime
import html
import logging
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.config import ADMIN_IDS, FSM_TIMEOUT, TICKET_CREATE_COOLDOWN
from bot.database import Database
from bot.faq import FAQ_ANSWERS, FAQ_SECTIONS
from bot.keyboards import (
    FaqCallback,
    MyRequestsCallback,
    RatingCallback,
    active_ticket_menu_kb,
    cancel_kb,
    contact_kb,
    engineer_default_menu_kb,
    engineer_main_menu,
    faq_answer_kb,
    faq_main_kb,
    faq_section_kb,
    main_menu,
    my_requests_pagination_kb,
    ticket_action_kb,
)
from bot.media import save_media_file

router = Router()

# Модульный словарь для антиспама: user_id -> время последнего старта воронки.
# Защищает от массового создания заявок одним клиентом.
_last_ticket_start: dict[int, float] = {}
# Максимальный размер словаря антиспама (защита от утечки памяти).
_ANTISPAM_MAX_SIZE = 1000
# TTL записей антиспама (секунды): записи старше этого значения удаляются.
_ANTISPAM_TTL = max(TICKET_CREATE_COOLDOWN * 10, 600)
# Максимальный размер файла при создании заявки (20 МБ — лимит Telegram Bot API)
_MAX_MEDIA_SIZE = 20 * 1024 * 1024


def _cleanup_antispam_dict(now: float) -> None:
    """Удаляет устаревшие записи из словаря антиспама (защита от утечки памяти)."""
    if len(_last_ticket_start) > _ANTISPAM_MAX_SIZE:
        stale = [uid for uid, ts in _last_ticket_start.items() if now - ts > _ANTISPAM_TTL]
        for uid in stale:
            _last_ticket_start.pop(uid, None)

class TicketForm(StatesGroup):
    """Оптимизированная короткая воронка FSM: 4 шага."""
    problem_media = State()   # Шаг 1: описание проблемы + фото/видео
    machine_info = State()    # Шаг 2: фото шильдика или бренд/модель
    company_city = State()    # Шаг 3: город + ИНН/название компании
    contact = State()         # Шаг 4: контакт через request_contact
    rating_comment = State()  # Комментарий после оценки заявки

async def check_fsm_expired(message: Message, state: FSMContext) -> bool:
    """
    Проверяет, не истёк ли таймаут незавершённой воронки оформления заявки.

    Если состояние висит дольше FSM_TIMEOUT — сбрасывает его и уведомляет клиента.
    Возвращает True, если состояние было сброшено (обработку шага нужно прервать).
    """
    data = await state.get_data()
    started_raw = data.get('fsm_started_at')
    if not started_raw:
        return False
    try:
        started = datetime.datetime.fromisoformat(started_raw)
    except (ValueError, TypeError):
        return False

    elapsed = datetime.datetime.now(datetime.timezone.utc) - started
    if elapsed.total_seconds() > FSM_TIMEOUT:
        await state.clear()
        await message.answer(
            "⏰ Время оформления заявки истекло. Пожалуйста, начните заново, нажав «🛠 Оставить заявку на сервис ЧПУ».",
            reply_markup=main_menu()
        )
        return True
    return False

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
    await _show_my_requests(message, db, is_engineer, page=0)


async def _show_my_requests(
    target: Message,
    db: Database,
    is_engineer: bool,
    page: int = 0,
    edit: bool = False,
) -> None:
    """Внутренняя логика показа истории заявок с пагинацией (клиент) или активных заявок (инженер)."""
    # Если пользователь — инженер, показываем его активные заявки (как инженера)
    if is_engineer:
        tickets = await db.get_active_tickets_for_engineer(target.from_user.id)
        if not tickets:
            await target.answer(
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
        if edit:
            await target.edit_text("\n".join(lines), reply_markup=engineer_default_menu_kb())
        else:
            await target.answer("\n".join(lines), reply_markup=engineer_default_menu_kb())
        return

    # Для клиента: история заявок с пагинацией (10 на странице)
    per_page = 10
    tickets = await db.get_client_tickets(target.from_user.id, limit=per_page, offset=page * per_page)
    if not tickets and page == 0:
        await target.answer("📋 У вас пока нет заявок.", reply_markup=main_menu())
        return

    # Определяем общее число страниц (грубая оценка: если набрано per_page — есть следующая)
    has_more = len(tickets) == per_page
    total_pages = page + 2 if has_more else page + 1
    if not tickets and page > 0:
        # Страница вышла за пределы — показываем последнюю известную
        page = max(0, page - 1)
        tickets = await db.get_client_tickets(target.from_user.id, limit=per_page, offset=page * per_page)
        total_pages = page + 1

    status_map = {
        'open': '🟡 Ожидает инженера',
        'in_progress': '🔵 В работе',
        'completed': '✅ Завершена',
        'canceled': '🚫 Отменена'
    }
    lines = [f"<b>📋 Ваши заявки:</b> (стр. {page + 1})\n"]
    for t in tickets:
        status = status_map.get(t['status'], t['status'])
        lines.append(
            f"🎫 <b>#{t['id']}</b> — {status}\n"
            f"   {html.escape(str(t['machine_info'] or '—'))}: {html.escape(str(t['problem'] or '—'))[:50]}"
        )
    kb = my_requests_pagination_kb(page, total_pages) if len(tickets) > 0 else None
    if edit:
        await target.edit_text("\n".join(lines), reply_markup=kb or main_menu())
    else:
        await target.answer("\n".join(lines), reply_markup=kb or main_menu())


@router.callback_query(MyRequestsCallback.filter())
async def my_requests_page_callback(callback: CallbackQuery, callback_data: MyRequestsCallback, db: Database, is_engineer: bool):
    """Переключает страницу истории заявок клиента."""
    await callback.answer()
    await _show_my_requests(callback.message, db, is_engineer, page=callback_data.page, edit=True)


@router.callback_query(F.data == "myreq:noop")
async def my_requests_noop(callback: CallbackQuery):
    """Обрабатывает нажатие на кнопку-счётчик страницы (ничего не делает)."""
    await callback.answer()

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
async def show_faq(message: Message, state: FSMContext):
    """Показывает главное меню FAQ с кнопками-разделами."""
    await state.clear()  # Сбрасываем FSM, чтобы FAQ не мешал оформлению заявки
    faq_text = (
        "❓ <b>Часто задаваемые вопросы (FAQ)</b>\n\n"
        "🕐 <b>Режим работы:</b> заявки принимаем круглосуточно, 24/7. Реакция на обращение — в течение 15 минут.\n"
        "📞 Единый телефон: <b>8 800 777-38-56</b>\n"
        "✉️ E-mail: info@hotline-service.ru\n"
        "🌐 Сайт: <a href=\"https://hotline-service.ru\">hotline-service.ru</a>\n\n"
        "🔧 Мы выполняем диагностику, пусконаладочные работы (ПНР), ремонт и техническое обслуживание станков с ЧПУ "
        "и лазерного оборудования.\n\n"
        "Выберите интересующий раздел:"
    )
    await message.answer(faq_text, reply_markup=faq_main_kb(), disable_web_page_preview=True)

@router.callback_query(FaqCallback.filter())
async def faq_callback_handler(callback: CallbackQuery, callback_data: FaqCallback):
    """Обрабатывает нажатия кнопок в FAQ: разделы, вопросы, ответы."""
    # callback.answer() может упасть, если query устарел (пользователь нажал кнопку давно).
    # Перехватываем исключение, чтобы продолжить обработку.
    try:
        await callback.answer()
    except Exception:
        logging.warning("Не удалось ответить на callback (query устарел): %s", callback_data)

    if callback_data.action == "main":
        # Возврат к списку разделов
        text = "❓ <b>Часто задаваемые вопросы (FAQ)</b>\n\nВыберите раздел:"
        try:
            await callback.message.edit_text(text, reply_markup=faq_main_kb())
        except Exception:
            await callback.message.answer(text, reply_markup=faq_main_kb())
        return

    if callback_data.action == "section":
        # Показываем список вопросов выбранного раздела
        section = next((s for s in FAQ_SECTIONS if s["id"] == callback_data.section_id), None)
        if not section:
            try:
                await callback.answer("Раздел не найден.", show_alert=True)
            except Exception:
                pass
            return
        text = f"{section['title']}\n\nВыберите вопрос:"
        try:
            await callback.message.edit_text(text, reply_markup=faq_section_kb(section["id"]))
        except Exception:
            await callback.message.answer(text, reply_markup=faq_section_kb(section["id"]))
        return

    if callback_data.action == "answer":
        # Показываем развёрнутый ответ на выбранный вопрос
        answer = FAQ_ANSWERS.get(callback_data.question_id)
        if not answer:
            try:
                await callback.answer("Вопрос не найден.", show_alert=True)
            except Exception:
                pass
            return
        text = answer
        try:
            await callback.message.edit_text(
                text,
                reply_markup=faq_answer_kb(callback_data.section_id, callback_data.question_id)
            )
        except Exception:
            await callback.message.answer(
                text,
                reply_markup=faq_answer_kb(callback_data.section_id, callback_data.question_id)
            )
        return

@router.message(F.text == "🛠 Оставить заявку на сервис ЧПУ")
async def start_ticket(message: Message, state: FSMContext, bot: Bot, db: Database):
    # Антиспам: ограничиваем частоту запуска воронки одним клиентом
    now = time.monotonic()
    # Периодическая очистка устаревших записей (защита от утечки памяти)
    _cleanup_antispam_dict(now)
    last = _last_ticket_start.get(message.from_user.id, 0.0)
    if now - last < TICKET_CREATE_COOLDOWN:
        remaining = int(TICKET_CREATE_COOLDOWN - (now - last))
        await message.answer(
            f"⏳ Не так быстро! Подождите {remaining} сек. перед созданием новой заявки.",
            reply_markup=main_menu()
        )
        return
    _last_ticket_start[message.from_user.id] = now

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
    # Сохраняем ID сообщения клиента (кнопку), чтобы удалить его на следующем шаге,
    # а также метку времени начала воронки (для таймаута).
    await state.update_data(
        last_bot_msg_id=msg.message_id,
        last_client_msg_id=message.message_id,
        fsm_started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )

@router.message(StateFilter(TicketForm.problem_media))
async def ticket_problem_media(message: Message, state: FSMContext, bot: Bot):
    # Если воронка «зависла» дольше таймаута — сбрасываем состояние
    if await check_fsm_expired(message, state):
        return
    # UX-очистка: удаляем предыдущее сообщение бота и клиента
    await delete_last_bot_message(bot, message, state)
    await delete_last_client_message(bot, message, state)

    # Извлекаем текст и медиа из одного сообщения
    problem_text = message.text or message.caption or ''
    media_id = None
    media_type = None
    if message.photo:
        # Проверяем размер файла до сохранения
        if message.photo[-1].file_size and message.photo[-1].file_size > _MAX_MEDIA_SIZE:
            await message.answer("❌ Файл слишком большой (лимит 20 МБ). Пожалуйста, пришлите файл меньшего размера.")
            return
        media_id = message.photo[-1].file_id
        media_type = 'photo'
    elif message.video:
        if message.video.file_size and message.video.file_size > _MAX_MEDIA_SIZE:
            await message.answer("❌ Файл слишком большой (лимит 20 МБ). Пожалуйста, пришлите файл меньшего размера.")
            return
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
    # Если воронка «зависла» дольше таймаута — сбрасываем состояние
    if await check_fsm_expired(message, state):
        return
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
        if message.photo[-1].file_size and message.photo[-1].file_size > _MAX_MEDIA_SIZE:
            await message.answer("❌ Файл слишком большой (лимит 20 МБ). Пожалуйста, пришлите файл меньшего размера.")
            return
        machine_media_id = message.photo[-1].file_id
        machine_media_type = 'photo'
    elif message.video:
        if message.video.file_size and message.video.file_size > _MAX_MEDIA_SIZE:
            await message.answer("❌ Файл слишком большой (лимит 20 МБ). Пожалуйста, пришлите файл меньшего размера.")
            return
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
    # Если воронка «зависла» дольше таймаута — сбрасываем состояние
    if await check_fsm_expired(message, state):
        return
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
    # Если воронка «зависла» дольше таймаута — сбрасываем состояние
    if await check_fsm_expired(message, state):
        return
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

    # Уведомляем администраторов о новой заявке (из .env и из БД)
    admin_notify = (
        f"🆕 <b>Новая заявка #{ticket_id}</b>\n\n"
        f"🏢 <b>Компания/Город:</b> {html.escape(data.get('company_city', ''))}\n"
        f"🔧 <b>Станок:</b> {html.escape(data.get('machine_info', ''))}\n\n"
        f"<b>Проблема:</b> {html.escape(data.get('problem', ''))}\n\n"
        f"<b>Контакты:</b> {html.escape(data.get('contact', ''))}\n"
        f"<b>Отправитель:</b> {html.escape(message.from_user.full_name)}"
    )
    # Собираем админов из .env и из БД
    admin_ids = set(ADMIN_IDS)
    try:
        db_admins = await db.get_admins()
        admin_ids.update(row['user_id'] for row in db_admins)
    except Exception as e:
        logging.warning(f"Не удалось получить админов из БД для уведомлений: {e}")
    for admin_id in admin_ids:
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