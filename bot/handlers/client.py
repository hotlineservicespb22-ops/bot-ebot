import logging
import html
from aiogram import Router, Bot, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from bot.database import Database
from bot.keyboards import main_menu, ticket_action_kb, cancel_kb, active_ticket_menu_kb, engineer_default_menu_kb

router = Router()

class TicketForm(StatesGroup):
    company = State()
    equipment_type = State()
    brand = State()
    cnc_model = State()
    problem = State()
    media = State()
    city = State()
    inn_contract = State()
    contact = State()

def equipment_type_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Лазерный CO2"), KeyboardButton(text="Фрезерный")],
            [KeyboardButton(text="Маркиратор"), KeyboardButton(text="Лазер по металлу")],
            [KeyboardButton(text="Листогиб"), KeyboardButton(text="Лазерная сварка")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def brand_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Wattsan"), KeyboardButton(text="KingRabbit")],
            [KeyboardButton(text="Reci"), KeyboardButton(text="GWEIKE")],
            [KeyboardButton(text="Bodor"), KeyboardButton(text="Другое")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def skip_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⏩ Пропустить")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

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

@router.message(Command("cancel"))
@router.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu())

@router.message(CommandStart())
async def cmd_start(message: Message, is_engineer: bool):
    if is_engineer:
        await message.answer(
            "Здравствуйте! Вы авторизованы как инженер. Вам доступно меню управления заявками.",
            reply_markup=engineer_default_menu_kb()
        )
    else:
        await message.answer(
            "Здравствуйте! Сервисная служба <b>Hotline Service</b>.\n"
            "Мы специализируемся на диагностике и ремонте станков с ЧПУ.\n\n"
            "Нажмите кнопку ниже, чтобы оформить заявку дежурному инженеру.",
            reply_markup=main_menu(),
            parse_mode="HTML"
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
    await message.answer(faq_text, parse_mode="HTML", disable_web_page_preview=True)

@router.message(F.text == "🛠 Оставить заявку на сервис ЧПУ")
async def start_ticket(message: Message, state: FSMContext):
    await state.set_state(TicketForm.company)
    await message.answer(
        "Начинаем оформление заявки. Введите название вашей компании / предприятия.\n\n"
        "Чтобы прервать, нажмите '❌ Отмена' внизу.", 
        reply_markup=cancel_kb(),
        parse_mode="HTML"
    )

@router.message(StateFilter(TicketForm.company))
async def ticket_company(message: Message, state: FSMContext):
    if not message.text or len(message.text.strip()) == 0:
        await message.answer("❌ Название компании не может быть пустым. Введите еще раз:")
        return
    await state.update_data(company=message.text)
    await state.set_state(TicketForm.equipment_type)
    await message.answer("Выберите тип оборудования:", reply_markup=equipment_type_kb())

@router.message(StateFilter(TicketForm.equipment_type))
async def ticket_equipment_type(message: Message, state: FSMContext):
    await state.update_data(equipment_type=message.text)
    await state.set_state(TicketForm.brand)
    await message.answer("Выберите бренд станка:", reply_markup=brand_kb())

@router.message(StateFilter(TicketForm.brand))
async def ticket_brand(message: Message, state: FSMContext):
    await state.update_data(brand=message.text)
    await state.set_state(TicketForm.cnc_model)
    await message.answer(
        "Укажите модель станка и стойку ЧПУ (например: <b>Fanuc 0i-TF, станок DMG Mori</b>):",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML"
    )

@router.message(StateFilter(TicketForm.cnc_model))
async def ticket_model(message: Message, state: FSMContext):
    if not message.text or len(message.text.strip()) < 2:
        await message.answer("❌ Введите корректную модель станка:")
        return
    await state.update_data(cnc_model=message.text)
    await state.set_state(TicketForm.problem)
    await message.answer("Опишите неисправность и укажите код ошибки (если есть):")

@router.message(StateFilter(TicketForm.problem))
async def ticket_problem(message: Message, state: FSMContext):
    if not message.text or len(message.text.strip()) < 5:
        await message.answer("❌ Опишите проблему подробнее (минимум 5 символов):")
        return
    await state.update_data(problem=message.text)
    await state.set_state(TicketForm.media)
    await message.answer(
        "Прикрепите фото или короткое видео неисправности. Это поможет инженеру быстрее понять проблему.\n\n"
        "Если фото/видео нет, нажмите 'Пропустить'.",
        reply_markup=skip_kb()
    )

@router.message(StateFilter(TicketForm.media), F.photo | F.video | F.document | (F.text == "⏩ Пропустить"))
async def ticket_media(message: Message, state: FSMContext):
    media_id = None
    media_type = None
    if message.photo:
        media_id = message.photo[-1].file_id
        media_type = 'photo'
    elif message.video:
        media_id = message.video.file_id
        media_type = 'video'
    elif message.document:
        media_id = message.document.file_id
        media_type = 'document'
    
    await state.update_data(media_id=media_id, media_type=media_type)
    await state.set_state(TicketForm.city)
    await message.answer(
        "Укажите город, где находится оборудование (для расчета командировочных).",
        reply_markup=ReplyKeyboardRemove()
    )

@router.message(StateFilter(TicketForm.city))
async def ticket_city(message: Message, state: FSMContext):
    await state.update_data(city=message.text)
    await state.set_state(TicketForm.inn_contract)
    await message.answer("Укажите ИНН организации и номер договора (если есть).")

@router.message(StateFilter(TicketForm.inn_contract))
async def ticket_inn_contract(message: Message, state: FSMContext):
    await state.update_data(inn_contract=message.text)
    await state.set_state(TicketForm.contact)
    await message.answer("И последний шаг: укажите контактный телефон и ваше имя для связи.")

@router.message(StateFilter(TicketForm.contact))
async def ticket_contact(message: Message, state: FSMContext, bot: Bot, db: Database):
    if not message.text or len(message.text.strip()) < 5:
        await message.answer("❌ Контактная информация должна содержать минимум 5 символов:")
        return
    await state.update_data(contact=message.text)
    data = await state.get_data()
    await state.clear()
    
    ticket_id = await db.create_ticket(
        client_id=message.from_user.id,
        client_name=message.from_user.full_name,
        company=data.get('company', ''),
        equipment_type=data.get('equipment_type', ''),
        brand=data.get('brand', ''),
        cnc_model=data.get('cnc_model', ''),
        problem=data.get('problem', ''),
        media_id=data.get('media_id'),
        city=data.get('city', ''),
        inn_contract=data.get('inn_contract', ''),
        contact=data.get('contact', '')
    )
    
    engineers = await db.get_engineers()
    
    ticket_text = (
        f"🚨 <b>Новая заявка #{ticket_id}</b>\n\n"
        f"🏢 <b>Компания:</b> {html.escape(data.get('company', ''))}\n"
        f"🏙 <b>Город:</b> {html.escape(data.get('city', ''))}\n"
        f"📄 <b>ИНН/Договор:</b> {html.escape(data.get('inn_contract', ''))}\n\n"
        f"<b>Тип оборудования:</b> {html.escape(data.get('equipment_type', ''))}\n"
        f"<b>Бренд:</b> {html.escape(data.get('brand', ''))}\n"
        f"<b>Модель/ЧПУ:</b> {html.escape(data.get('cnc_model', ''))}\n\n"
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
                    reply_markup=ticket_action_kb(ticket_id),
                    parse_mode="HTML"
                )
            elif media_id and media_type == 'video':
                await bot.send_video(
                    row['user_id'],
                    video=media_id,
                    caption=ticket_text,
                    reply_markup=ticket_action_kb(ticket_id),
                    parse_mode="HTML"
                )
            elif media_id and media_type == 'document':
                await bot.send_document(
                    row['user_id'],
                    document=media_id,
                    caption=ticket_text,
                    reply_markup=ticket_action_kb(ticket_id),
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(
                    row['user_id'], 
                    ticket_text, 
                    reply_markup=ticket_action_kb(ticket_id), 
                    parse_mode="HTML"
                )
        except Exception as e:
            logging.error(f"Не удалось отправить инженеру {row['user_id']}: {e}")
            
    await message.answer(
        f"✅ <b>Заявка #{ticket_id} принята!</b>\n"
        "Дежурный инженер подключится к диалогу в ближайшее время. "
        "Все дальнейшие сообщения, отправленные сюда, будут переданы специалисту. Вы можете отменить заявку через меню ниже.",
        reply_markup=active_ticket_menu_kb(),
        parse_mode="HTML"
    )
