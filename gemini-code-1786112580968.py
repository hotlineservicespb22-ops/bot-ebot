import asyncio
import logging
import traceback
import aiosqlite
import re
import os
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, 
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import html
from aiogram.filters.callback_data import CallbackData
from aiogram.exceptions import TelegramAPIError, TelegramConflictError
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# ==================== НАСТРОЙКИ ====================
# Рекомендуется вынести в переменные окружения (например, в .env файл)
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Необходимо установить переменную окружения BOT_TOKEN")

raw_admin_ids = os.getenv("ADMIN_IDS", "7039700975")
# Очищаем строку от любых лишних символов: скобок, кавычек и пробелов
cleaned_str = str(raw_admin_ids).translate(str.maketrans("", "", "[]'\""))

ADMIN_IDS = []
for x in cleaned_str.split(","):
    x = x.strip()
    if x.isdigit():
        ADMIN_IDS.append(int(x))

logging.info(DEBUG_MSG := f"ИТОГОВЫЙ СПИСОК АДМИНОВ: {ADMIN_IDS} (тип элементов: {[type(i) for i in ADMIN_IDS]})")

# Храним базу данных в той же директории, что и сам скрипт
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(SCRIPT_DIR, "hotline_service.db")

logging.basicConfig(level=logging.INFO)
router = Router()

# ==================== CALLBACK DATA ====================
class TicketCallback(CallbackData, prefix="ticket"):
    action: str
    ticket_id: int
# ==================== FSM (СОСТОЯНИЯ) ====================
class TicketForm(StatesGroup):
    company = State()
    cnc_model = State()
    problem = State()
    contact = State()

# ==================== БАЗА ДАННЫХ ====================
db_connection = None

async def get_db():
    global db_connection
    if db_connection is None:
        db_connection = await aiosqlite.connect(DB_NAME)
        db_connection.row_factory = aiosqlite.Row
    return db_connection

async def init_db():
    db = await get_db()
    async with db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS engineers (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                is_active INTEGER DEFAULT 1
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,
                client_name TEXT,
                company TEXT,
                cnc_model TEXT,
                problem TEXT,
                contact TEXT,
                status TEXT DEFAULT 'open',
                engineer_id INTEGER NULL
            )
        """)
        await db.execute("""
            INSERT OR IGNORE INTO engineers (user_id, name, is_active)
            VALUES (7039700975, 'Лазарев Вадим', 1)
        """)
        await db.commit()

async def is_engineer(user_id: int) -> bool:
    db = await get_db()
    async with db.execute("SELECT 1 FROM engineers WHERE user_id = ? AND is_active = 1", (user_id,)) as cursor:
        return await cursor.fetchone() is not None

# ==================== КЛАВИАТУРЫ ====================
def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛠 Оставить заявку на сервис ЧПУ")],
            [KeyboardButton(text="❓ Частые вопросы")]
        ],
        resize_keyboard=True
    )

def ticket_action_kb(ticket_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Взять в работу", callback_data=TicketCallback(action="take", ticket_id=ticket_id).pack())
        ]]
    )

def close_ticket_kb(ticket_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="🏁 Завершить заявку", callback_data=TicketCallback(action="close", ticket_id=ticket_id).pack())
        ]]
    )

def cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

# ==================== УПРАВЛЕНИЕ ИНЖЕНЕРАМИ (АДМИН) ====================
@router.message(Command("add_eng"))
async def cmd_add_engineer(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Используйте: <code>/add_eng &lt;Telegram_ID&gt; &lt;Имя&gt;</code>")
        return
    
    try:
        eng_id = int(args[1])
    except ValueError:
        await message.answer("❌ ID инженера должен быть числом (Telegram ID).")
        return
        
    eng_name = " ".join(args[2:])
    db = await get_db()
    async with db:
        await db.execute(
            "INSERT OR REPLACE INTO engineers (user_id, name, is_active) VALUES (?, ?, 1)",
            (eng_id, eng_name)
        )
        await db.commit()
    await message.answer(f"✅ Инженер <b>{html.escape(eng_name)}</b> (ID: <code>{eng_id}</code>) добавлен в дежурную службу.</code>")

@router.message(Command("del_eng"))
async def cmd_del_engineer(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Используйте: <code>/del_eng &lt;Telegram_ID&gt;</code>")
        return
    
    try:
        eng_id = int(args[1])
    except ValueError:
        await message.answer("❌ ID инженера должен быть числом (Telegram ID).")
        return
        
    db = await get_db()
    async with db:
        await db.execute("DELETE FROM engineers WHERE user_id = ?", (eng_id,))
        await db.commit()
    await message.answer(f"🗑 Инженер с ID <code>{eng_id}</code> удален.")

@router.message(Command("list_eng"))
async def cmd_list_engineers(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    db = await get_db()
    async with db.execute("SELECT user_id, name FROM engineers WHERE is_active = 1") as cursor:
        rows = await cursor.fetchall()
    
    if not rows:
        await message.answer("Список инженеров пуст.")
        return
    
    text = "<b>Дежурные инженеры:</b>\n" + "\n".join([f"• {html.escape(name)} (<code>{uid}</code>)" for uid, name in rows])
    await message.answer(text)

@router.message(Command("export"))
async def cmd_export_tickets(message: Message, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    import pandas as pd
    from aiogram.types import FSInputFile
    
    try:
        db = await get_db()
        async with db.execute("SELECT * FROM tickets") as cursor:
            columns = [column[0] for column in cursor.description]
            rows = await cursor.fetchall()
        
        if not rows:
            await message.answer("В базе данных еще нет заявок для экспорта.")
            return
            
        df = pd.DataFrame(rows, columns=columns)
        
        export_path = os.path.join(SCRIPT_DIR, "tickets_report.xlsx")
        df.to_excel(export_path, index=False)
        
        input_file = FSInputFile(export_path, filename="tickets_report.xlsx")
        await bot.send_document(message.chat.id, input_file, caption="📊 Отчет по всем заявкам из базы данных.")
        
        if os.path.exists(export_path):
            os.remove(export_path)
            
    except Exception as e:
        logging.error(f"Ошибка при экспорте заявок: {e}")
        await message.answer(f"❌ Произошла ошибка при экспорте заявок: {e}")

# ==================== МОСТ ОБМЕНА СООБЩЕНИЯМИ ====================
@router.message()
async def relay_messages(message: Message, bot: Bot):
    user_id = message.from_user.id
    db = await get_db()
    
    # Проверяем, пишет ли инженер
    is_eng = await is_engineer(user_id)
    
    if not is_eng:
        # Пользователь - клиент
        # Проверяем, пишет ли клиент с активной заявкой
        cursor = await db.execute(
            "SELECT id, engineer_id FROM tickets WHERE client_id = ? AND status = 'in_progress' ORDER BY id DESC LIMIT 1",
            (user_id,)
        )
        ticket = await cursor.fetchone()
        if ticket:
            ticket_id, eng_id = ticket
            prefix = f"💬 [Клиент | Заявка #{ticket_id}]"
            await forward_message_to(bot, eng_id, message, prefix)
            return
    else:
        # Пользователь - инженер
        cursor = await db.execute(
            "SELECT id, client_id FROM tickets WHERE engineer_id = ? AND status = 'in_progress' ORDER BY id DESC",
            (user_id,)
        )
        # У инженера может быть несколько активных заявок.
        # Для простоты, отвечаем в последнюю взятую.
        # В более сложной версии нужен выбор, кому отвечать.
        ticket_eng = await cursor.fetchone()
        if ticket_eng:
            ticket_id, client_id = ticket_eng
            prefix = f"👨‍🔧 [Инженер | Заявка #{ticket_id}]"
            await forward_message_to(bot, client_id, message, prefix)
            return
        else:
            await message.answer("У вас нет активных заявок в работе. Вы не можете отправлять сообщения.")

# ==================== СОЗДАНИЕ ЗАЯВКИ (КЛИЕНТ) ====================
@router.message(CommandStart())
async def cmd_start(message: Message):
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

@router.message(Command("cancel"))
@router.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu())

@router.message(F.text == "🛠 Оставить заявку на сервис ЧПУ")
async def start_ticket(message: Message, state: FSMContext):
    await state.set_state(TicketForm.company)
    await message.answer("Введите название вашей компании / предприятия:", reply_markup=cancel_kb())

@router.message(StateFilter(TicketForm.company))
async def ticket_company(message: Message, state: FSMContext):
    if not message.text or len(message.text.strip()) == 0:
        await message.answer("❌ Название компании не может быть пустым. Введите еще раз:")
        return
    await state.update_data(company=message.text)
    await state.set_state(TicketForm.cnc_model)
    await message.answer("Укажите модель станка и стойку ЧПУ (например: <b>Fanuc 0i-TF, станок DMG Mori</b>):")

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
    await state.set_state(TicketForm.contact)
    await message.answer("Укажите контактный телефон и имя для связи:")

@router.message(StateFilter(TicketForm.contact))
async def ticket_contact(message: Message, state: FSMContext, bot: Bot):
    if not message.text or len(message.text.strip()) < 5:
        await message.answer("❌ Контактная информация должна содержать минимум 5 символов:")
        return
    await state.update_data(contact=message.text)
    data = await state.get_data()
    await state.clear()
    
    db = await get_db()
    async with db:
        cursor = await db.execute(
            """INSERT INTO tickets (client_id, client_name, company, cnc_model, problem, contact)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (message.from_user.id, message.from_user.full_name, data['company'], data['cnc_model'], data['problem'], data['contact'])
        )
        ticket_id = cursor.lastrowid
        await db.commit()
        
        # Уведомление всем активным инженерам
        cursor = await db.execute("SELECT user_id FROM engineers WHERE is_active = 1")
        engineers = await cursor.fetchall()
        
    ticket_text = (
        f"🚨 <b>Новая заявка #{ticket_id}</b>\n\n"
        f"<b>Компания:</b> {html.escape(data['company'])}\n"
        f"<b>Оборудование / ЧПУ:</b> {html.escape(data['cnc_model'])}\n"
        f"<b>Неисправность:</b> {html.escape(data['problem'])}\n"
        f"<b>Контакты:</b> {html.escape(data['contact'])}\n"
        f"<b>Отправитель:</b> {html.escape(message.from_user.full_name)}"
    )
    
    for (eng_id,) in engineers:
        try:
            await bot.send_message(eng_id, ticket_text, reply_markup=ticket_action_kb(ticket_id))
        except Exception as e:
            logging.error(f"Не удалось отправить инженеру {eng_id}: {e}")
            
    await message.answer(
        f"✅ <b>Заявка #{ticket_id} принята!</b>\n"
        "Дежурный инженер подключится к диалогу в ближайшее время. "
        "Все дальнейшие сообщения, отправленные сюда, будут переданы специалисту.",
        reply_markup=main_menu()
    )

# ==================== РАБОТА С ЗАЯВКОЙ (ИНЖЕНЕР) ====================
@router.callback_query(TicketCallback.filter(F.action == "take"))
async def take_ticket(callback: CallbackQuery, callback_data: TicketCallback, bot: Bot):
    ticket_id = callback_data.ticket_id
    eng_id = callback.from_user.id
    
    if not await is_engineer(eng_id):
        await callback.answer("У вас нет прав инженера.", show_alert=True)
        return
        
    db = await get_db()
    async with db:
        cursor = await db.execute("SELECT client_id, status FROM tickets WHERE id = ?", (ticket_id,))
        row = await cursor.fetchone()
        if not row:
            await callback.answer("Заявка не найдена.")
            return
        client_id, status = row
        if status != "open":
            await callback.answer("Заявку уже взял другой специалист или она закрыта.", show_alert=True)
            return
            
        await db.execute("UPDATE tickets SET status = 'in_progress', engineer_id = ? WHERE id = ?", (eng_id, ticket_id))
        await db.commit()
        
    await callback.message.edit_text(
        callback.message.text + f"\n\n👨‍🔧 <b>Взято в работу инженером:</b> {html.escape(callback.from_user.full_name)}",
        reply_markup=close_ticket_kb(ticket_id)
    )
    
    await bot.send_message(
        client_id,
        f"👨‍🔧 К вашей заявке #{ticket_id} подключился дежурный инженер.\n"
        "Вы можете писать уточнения и присылать фотографии прямо в этот чат."
    )

@router.callback_query(TicketCallback.filter(F.action == "close"))
async def close_ticket(callback: CallbackQuery, callback_data: TicketCallback, bot: Bot):
    ticket_id = callback_data.ticket_id
    db = await get_db()
    async with db:
        cursor = await db.execute("SELECT client_id FROM tickets WHERE id = ?", (ticket_id,))
        row = await cursor.fetchone()
        if not row:
            return
        client_id = row[0]
        await db.execute("UPDATE tickets SET status = 'closed' WHERE id = ?", (ticket_id,))
        await db.commit()
        
    await callback.message.edit_text(callback.message.text + "\n\n🏁 <b>Заявка завершена</b>")
    await bot.send_message(client_id, f"🏁 Заявка #{ticket_id} закрыта сервисной службой. Спасибо за обращение!", reply_markup=main_menu())

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
async def forward_message_to(bot: Bot, recipient_id: int, message: Message, prefix: str):
    """Пересылает сообщение с форматированием."""
    try:
        if message.text:
            await bot.send_message(recipient_id, f"<b>{prefix}</b>\n{html.escape(message.text)}")
        elif message.photo:
            await bot.send_photo(recipient_id, message.photo[-1].file_id, caption=f"<b>{prefix}</b> (Фото): {html.escape(message.caption or '')}")
        elif message.video:
            await bot.send_video(recipient_id, message.video.file_id, caption=f"<b>{prefix}</b> (Видео): {html.escape(message.caption or '')}")
        elif message.document:
            await bot.send_document(recipient_id, message.document.file_id, caption=f"<b>{prefix}</b> (Файл): {html.escape(message.caption or '')}")
        elif message.voice:
            await bot.send_voice(recipient_id, message.voice.file_id, caption=f"<b>{prefix}</b> (Голосовое)")
        else:
            await bot.send_message(recipient_id, f"{prefix} отправил неподдерживаемый формат сообщения.")
    except TelegramAPIError as e:
        logging.error(f"Не удалось отправить сообщение получателю {recipient_id}: {e}")
        # Можно уведомить отправителя об ошибке
        await message.answer("❌ Не удалось доставить ваше сообщение. Получатель мог заблокировать бота.")

# ==================== ЗАПУСК ====================
async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()    
    
    @dp.error()
    async def error_handler(event, exception):
        logging.error(f"Критическая ошибка: {exception}", exc_info=True)
        if ADMIN_IDS:
            try:
                error_message = f"❌ Критическая ошибка: {escape_md(str(exception))}\n\n```\n{traceback.format_exc()}\n```"
                for admin_id in ADMIN_IDS:
                    await bot.send_message(admin_id, error_message, parse_mode="MarkdownV2")
            except Exception as e:
                logging.error(f"Не удалось уведомить администраторов: {e}")

    dp.include_router(router)
    
    try:
        logging.info("Бот сервисной службы успешно запущен...")
        await dp.start_polling(bot, skip_updates=True)
    except TelegramConflictError:
        logging.critical("ОШИБКА: Обнаружена запущенная копия бота. Завершение работы.")
        logging.critical("Пожалуйста, остановите другой процесс и перезапустите бота.")
    finally:
        if db_connection:
            await db_connection.close()
        await bot.session.close()
        logging.info("Бот остановлен.")

if __name__ == "__main__":
    asyncio.run(main())