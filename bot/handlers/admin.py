import asyncio
import contextlib
import csv
import datetime
import html
import io
import json
import logging

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.config import ADMIN_IDS  # Import global ADMIN_IDS
from bot.database import Database
from bot.keyboards import (
    AdminCallback,
    admin_menu_kb,
    back_to_admin_kb,
    engineer_duty_kb,
)

router = Router()
logger = logging.getLogger(__name__)


async def _send_dashboard(target_msg, db: Database, bot: Bot, status_msg=None):
    """Формирует и отправляет HTML-дашборд."""
    import json
    data = await db.get_dashboard_data()
    from bot.dashboard import generate_dashboard
    html = generate_dashboard(json.dumps(data, ensure_ascii=False))
    from aiogram.types import BufferedInputFile
    doc = BufferedInputFile(html.encode('utf-8'), filename='dashboard.html')
    await bot.send_document(
        chat_id=target_msg.chat.id,
        document=doc,
        caption="📊 <b>Дашборд Hotline Service</b>\n\nОткройте файл в браузере для просмотра графиков."
    )
    if status_msg:
        with contextlib.suppress(Exception):
            await status_msg.delete()

async def _safe_callback_answer(callback: CallbackQuery, *args, **kwargs):
    """
    Безопасный вызов callback.answer(). Ошибка «query is too old / invalid»
    (TelegramBadRequest) случается, если кнопку нажали давно или бот долго
    обрабатывал запрос — она не должна ронять обработчик.
    """
    try:
        await callback.answer(*args, **kwargs)
    except Exception as e:
        logger.warning(f"Не удалось ответить на callback (query устарел): {e}")

# ===================== Админ-панель =====================

@router.message(Command("admin"))
async def cmd_admin_panel(message: Message, db: Database):
    """Показывает админ-панель с inline-кнопками. Доступ только для администраторов."""
    # Явная проверка прав через БД — не полагаемся только на middleware
    is_admin = await db.is_admin(message.from_user.id)
    if not is_admin:
        await message.answer("🚫 У вас нет прав администратора.")
        return
    await message.answer("🛠 <b>Админ-панель</b>\n\nВыберите действие:", reply_markup=admin_menu_kb())

@router.callback_query(AdminCallback.filter())
async def admin_panel_callback(callback: CallbackQuery, callback_data: AdminCallback, db: Database, bot: Bot):
    """Обрабатывает нажатия кнопок админ-панели. Доступ только для администраторов."""
    # Явная проверка прав через БД — даже если middleware не передал is_admin
    is_admin = await db.is_admin(callback.from_user.id)
    if not is_admin:
        await _safe_callback_answer(callback, "🚫 Нет доступа.", show_alert=True)
        return

    await _safe_callback_answer(callback)  # Быстрый ответ Telegram для снятия спиннера на кнопке

    action = callback_data.action
    if action == "back":
        await callback.message.edit_text(
            "🛠 <b>Админ-панель</b>\n\nВыберите действие:",
            reply_markup=admin_menu_kb()
        )
    elif action == "stats":
        await cmd_stats(callback.message, db, is_admin, edit=True)
    elif action == "export":
        await cmd_export_tickets(callback.message, db, bot, is_admin, edit=True)
    elif action == "list_eng":
        await cmd_list_engineers(callback.message, db, is_admin, edit=True)
    elif action == "list_admin":
        await cmd_list_admin(callback.message, db, is_admin, edit=True)
    elif action == "duty_manage":
        await show_duty_management(callback.message, db, edit=True)
    elif action == "duty_on":
        await db.set_engineer_active(callback_data.engineer_id, 1)
        await _safe_callback_answer(callback, "✅ Инженер включён в дежурные.")
        await show_duty_management(callback.message, db, edit=True)
    elif action == "duty_off":
        await db.set_engineer_active(callback_data.engineer_id, 0)
        await _safe_callback_answer(callback, "⚪ Инженер исключён из дежурных.")
        await show_duty_management(callback.message, db, edit=True)
    elif action == "dashboard":
        await _safe_callback_answer(callback, "📈 Формирую дашборд...")
        status_msg = await callback.message.answer("⏳ Загрузка данных...")
        await _send_dashboard(callback.message, db, bot, status_msg)

@router.message(Command("add_admin"))
async def cmd_add_admin(message: Message, db: Database):
    """Добавляет пользователя в список администраторов (через БД). Доступно только администраторам."""
    # Явная проверка прав через БД — не полагаемся только на middleware
    is_admin = await db.is_admin(message.from_user.id)
    if not is_admin:
        await message.answer("🚫 У вас нет прав администратора.")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("Используйте: <code>/add_admin ID_пользователя</code>")
        return

    try:
        admin_id = int(args[1])
    except ValueError:
        await message.answer("❌ ID пользователя должен быть числом (Telegram ID).")
        return

    await db.add_admin(admin_id)
    await message.answer(f"✅ Пользователь <code>{admin_id}</code> добавлен в администраторы.")

@router.message(Command("del_admin"))
async def cmd_del_admin(message: Message, db: Database):
    """Удаляет пользователя из списка администраторов (через БД). Доступно только администраторам."""
    # Явная проверка прав через БД — не полагаемся только на middleware
    is_admin = await db.is_admin(message.from_user.id)
    if not is_admin:
        await message.answer("🚫 У вас нет прав администратора.")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("Используйте: <code>/del_admin ID_пользователя</code>")
        return

    try:
        admin_id = int(args[1])
    except ValueError:
        await message.answer("❌ ID пользователя должен быть числом.")
        return

    if admin_id in ADMIN_IDS:
        await message.answer("❌ Этот администратор задан в .env (ADMIN_IDS). Его нельзя удалить через бота.")
        return

    await db.delete_admin(admin_id)
    await message.answer(f"🗑 Пользователь <code>{admin_id}</code> удален из администраторов.")

@router.message(Command("list_admin"))
async def cmd_list_admin(message: Message, db: Database, is_admin: bool, edit: bool = False):
    """Показывает список всех администраторов (из .env и БД)."""
    # В edit-режиме message — это сообщение бота (панель), поэтому message.from_user —
    # сам бот, а не кликнувший админ. Права берём из аргумента is_admin.
    if not is_admin:
        if not edit:
            await message.answer("🚫 У вас нет прав администратора.")
        return

    text = "<b>Список администраторов:</b>\n\n"
    text += "<b>Из .env (ADMIN_IDS):</b>\n"
    for aid in ADMIN_IDS:
        text += f"  • <code>{aid}</code>\n"

    db_admins = await db.get_admins()
    text += "\n<b>Из базы данных:</b>\n"
    if db_admins:
        for row in db_admins:
            text += f"  • <code>{row['user_id']}</code>\n"
    else:
        text += "  • (пусто)\n"

    if edit:
        await message.edit_text(text, reply_markup=back_to_admin_kb())
    else:
        await message.answer(text, reply_markup=back_to_admin_kb())

# ===================== Управление инженерами =====================

@router.message(Command("add_eng"))
async def cmd_add_engineer(message: Message, db: Database, is_admin: bool):
    logger.info(f"Команда /add_eng вызвана пользователем: {message.from_user.id} (is_admin={is_admin})")

    # Явная проверка прав через БД — не полагаемся только на middleware
    is_admin = await db.is_admin(message.from_user.id)
    if not is_admin:
        logger.warning(f"Доступ к /add_eng запрещен для пользователя {message.from_user.id} (не администратор).")
        await message.answer("🚫 У вас нет прав администратора.")
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("Используйте: <code>/add_eng ID_инженера Имя</code>")
        return
    
    try:
        eng_id = int(args[1])
    except ValueError:
        await message.answer("❌ ID инженера должен быть числом (Telegram ID).")
        return
    
    # Check if the engineer ID is already an admin ID
    if eng_id in ADMIN_IDS:
        await message.answer("❌ Этот пользователь уже является администратором. Нет необходимости добавлять его как инженера отдельно, если он уже имеет все права.")
        return

    eng_name = " ".join(args[2:])
    await db.add_engineer(eng_id, eng_name)
    await message.answer(f"✅ Инженер <b>{html.escape(eng_name)}</b> (ID: <code>{eng_id}</code>) добавлен.")

@router.message(Command("del_eng"))
async def cmd_del_engineer(message: Message, db: Database, is_admin: bool):
    # Явная проверка прав через БД — не полагаемся только на middleware
    is_admin = await db.is_admin(message.from_user.id)
    if not is_admin:
        await message.answer("🚫 У вас нет прав администратора.")
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Используйте: <code>/del_eng ID_инженера</code>")
        return
    
    try:
        eng_id = int(args[1])
    except ValueError:
        await message.answer("❌ ID инженера должен быть числом.")
        return
        
    await db.delete_engineer(eng_id)
    await message.answer(f"🗑 Инженер с ID <code>{eng_id}</code> удален.")

@router.message(Command("list_eng"))
async def cmd_list_engineers(message: Message, db: Database, is_admin: bool, edit: bool = False):
    if not is_admin:
        if not edit:
            await message.answer("🚫 У вас нет прав администратора.")
        return
    rows = await db.get_all_engineers()
    if not rows:
        text = "Список инженеров пуст."
    else:
        text = "<b>Дежурные инженеры:</b>\n" + "\n".join([f"• {html.escape(row['name'])} (<code>{row['user_id']}</code>)" for row in rows])

    if edit:
        await message.edit_text(text, reply_markup=back_to_admin_kb())
    else:
        await message.answer(text, reply_markup=back_to_admin_kb())

@router.message(Command("set_bitrix"))
async def cmd_set_bitrix(message: Message, db: Database, is_admin: bool):
    """Устанавливает соответствие инженера бота пользователю Битрикс24."""
    # Явная проверка прав через БД — не полагаемся только на middleware
    is_admin = await db.is_admin(message.from_user.id)
    if not is_admin:
        await message.answer("🚫 У вас нет прав администратора.")
        return
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Используйте: <code>/set_bitrix ID_инженера ID_пользователя_Битрикс24</code>")
        return

    try:
        eng_id = int(args[1])
        bitrix_id = int(args[2])
    except ValueError:
        await message.answer("❌ ID инженера и ID пользователя Битрикс24 должны быть числами.")
        return

    # Проверяем, что инженер существует (включая неактивных)
    engineers = await db.get_all_engineers()
    if not any(e['user_id'] == eng_id for e in engineers):
        await message.answer(f"❌ Инженер с ID <code>{eng_id}</code> не найден. Сначала добавьте его через /add_eng.")
        return

    await db.set_bitrix_user_id(eng_id, bitrix_id)
    await message.answer(f"✅ Для инженера <code>{eng_id}</code> установлен ID пользователя Битрикс24: <code>{bitrix_id}</code>.")

@router.message(Command("bulk_add_eng"))
async def cmd_bulk_add_engineers(message: Message, db: Database, is_admin: bool):
    """Массовое добавление инженеров.

    Формат: /bulk_add_eng ID_телеграм:Имя,ID_телеграм:Имя
    Или построчно:
    /bulk_add_eng
    123456:Иван Иванов
    789012:Петр Петров
    """
    # Явная проверка прав через БД — не полагаемся только на middleware
    is_admin = await db.is_admin(message.from_user.id)
    if not is_admin:
        await message.answer("🚫 У вас нет прав администратора.")
        return

    # Извлекаем текст после команды
    text = message.text
    # Убираем саму команду
    if text.startswith('/bulk_add_eng'):
        text = text[len('/bulk_add_eng'):].strip()

    if not text:
        await message.answer(
            "Используйте: <code>/bulk_add_eng ID_телеграм:Имя,ID_телеграм:Имя</code>\n\n"
            "Или построчно:\n"
            "<code>/bulk_add_eng</code>\n"
            "<code>123456:Иван Иванов</code>\n"
            "<code>789012:Петр Петров</code>"
        )
        return

    # Разделяем по новой строке, точке с запятой, или по запятой, за которой следует цифра (ID).
    # Это позволяет именам содержать запятые (например, "Иванов, Иван").
    import re
    entries = re.split(r'[;\n]+|,\s*(?=\d)', text)
    added = []
    errors = []

    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        # Формат: ID:Имя
        if ':' in entry:
            id_part, name_part = entry.split(':', 1)
        elif ' ' in entry:
            # Формат: ID Имя
            id_part, name_part = entry.split(' ', 1)
        else:
            errors.append(f"❌ Неверный формат: <code>{html.escape(entry)}</code> (ожидается ID:Имя)")
            continue

        try:
            eng_id = int(id_part.strip())
        except ValueError:
            errors.append(f"❌ ID должен быть числом: <code>{html.escape(id_part.strip())}</code>")
            continue

        eng_name = name_part.strip()
        if not eng_name:
            errors.append(f"❌ Имя не указано для ID <code>{eng_id}</code>")
            continue

        if eng_id in ADMIN_IDS:
            errors.append(f"❌ ID <code>{eng_id}</code> является администратором — пропущен.")
            continue

        await db.add_engineer(eng_id, eng_name)
        added.append(f"✅ <b>{html.escape(eng_name)}</b> (<code>{eng_id}</code>)")

    result_parts = []
    if added:
        result_parts.append("✅ <b>Добавлены инженеры:</b>\n" + "\n".join(added))
    if errors:
        result_parts.append("⚠️ <b>Ошибки:</b>\n" + "\n".join(errors))
    if not result_parts:
        result_parts.append("❌ Не удалось добавить ни одного инженера.")

    await message.answer("\n\n".join(result_parts))

async def show_duty_management(message: Message, db: Database, edit: bool = False):
    """Показывает панель управления дежурными инженерами на сегодня."""
    engineers = await db.get_all_engineers()
    if not engineers:
        text = "Список инженеров пуст. Добавьте инженеров через /add_eng или /bulk_add_eng."
        if edit:
            await message.edit_text(text, reply_markup=back_to_admin_kb())
        else:
            await message.answer(text, reply_markup=back_to_admin_kb())
        return

    active_count = sum(1 for e in engineers if e['is_active'])
    text = (
        f"🔄 <b>Управление дежурными на сегодня</b>\n\n"
        f"Всего инженеров: <b>{len(engineers)}</b>\n"
        f"Дежурят сегодня: <b>{active_count}</b>\n\n"
        f"Нажмите на инженера, чтобы включить/выключить его дежурство:"
    )
    if edit:
        await message.edit_text(text, reply_markup=engineer_duty_kb(engineers))
    else:
        await message.answer(text, reply_markup=engineer_duty_kb(engineers))

# ===================== Отчеты и статистика =====================

def generate_csv(tickets):
    """Формирует CSV с полной информацией по заявкам (включая доп. поля)."""
    output = io.StringIO()
    writer = csv.writer(output)
    # Колонки с уточнёнными и доп. полями для более полного экспорта
    writer.writerow([
        'ID', 'Client ID', 'Client Name', 'Company', 'Equipment Type', 'Brand',
        'CNC Model', 'Machine Info', 'Company City', 'Problem', 'Media ID',
        'City', 'INN/Contract', 'Contact', 'Status', 'Engineer ID',
        'Close Comment', 'Created At', 'Closed At'
    ])
    for t in tickets:
        writer.writerow([
            t['id'], t['client_id'], t['client_name'], t['company'], t['equipment_type'],
            t['brand'], t['cnc_model'], t['machine_info'], t['company_city'], t['problem'],
            t['media_id'], t['city'], t['inn_contract'], t['contact'], t['status'],
            t['engineer_id'], t['close_comment'], t['created_at'], t['closed_at']
        ])
    return output.getvalue().encode('utf-8-sig')

@router.message(Command("export"))
async def cmd_export_tickets(message: Message, db: Database, bot: Bot, is_admin: bool, edit: bool = False):
    # В edit-режиме message — это сообщение бота (панель), поэтому права берём из is_admin.
    if not is_admin:
        if not edit:
            await message.answer("🚫 У вас нет прав администратора.")
        return
    
    # В edit-режиме редактируем текущее меню, в обычном — отправляем статус
    if edit:
        await message.edit_text("📥 <b>Экспорт CSV</b>\n\nГотовлю отчет...", reply_markup=back_to_admin_kb())
        status_msg = None
    else:
        status_msg = await message.answer("Готовлю отчет...")
    
    # Поддержка периода: /export ГГГГ-ММ-ДД ГГГГ-ММ-ДД
    # В edit-режиме (callback) message.text может быть None — тогда экспортируем все заявки
    args = (message.text or '').split()
    tickets = None
    filename = "tickets_report.csv"
    if len(args) == 3:
        try:
            start_date = datetime.date.fromisoformat(args[1])
            end_date = datetime.date.fromisoformat(args[2])
            # Добавляем время для корректного диапазона
            start_iso = datetime.datetime.combine(start_date, datetime.time.min, tzinfo=datetime.timezone.utc).isoformat()
            end_iso = datetime.datetime.combine(end_date, datetime.time.max, tzinfo=datetime.timezone.utc).isoformat()
            tickets = await db.get_tickets_by_period(start_iso, end_iso)
            filename = f"tickets_{args[1]}_{args[2]}.csv"
        except ValueError:
            if edit:
                await message.edit_text("❌ Неверный формат дат. Используйте: <code>/export ГГГГ-ММ-ДД ГГГГ-ММ-ДД</code>", reply_markup=back_to_admin_kb())
            else:
                await status_msg.edit_text("❌ Неверный формат дат. Используйте: <code>/export ГГГГ-ММ-ДД ГГГГ-ММ-ДД</code>")
            return
    else:
        tickets = await db.get_all_tickets()

    if not tickets:
        if edit:
            await message.edit_text("В базе данных нет заявок за указанный период.", reply_markup=back_to_admin_kb())
        else:
            await status_msg.edit_text("В базе данных нет заявок за указанный период.")
        return

    # Run in a thread to avoid blocking the event loop
    csv_data = await asyncio.to_thread(generate_csv, tickets)
    
    document = BufferedInputFile(csv_data, filename=filename)
    
    period_text = " за указанный период" if len(args) == 3 else ""
    await bot.send_document(
        chat_id=message.chat.id,
        document=document,
        caption=f"📊 Отчет по заявкам{period_text} (CSV)."
    )
    if status_msg:
        await status_msg.delete()
    else:
        await message.edit_text("📥 <b>Экспорт CSV</b>\n\n✅ Отчет сформирован и отправлен.", reply_markup=back_to_admin_kb())


@router.message(Command("dashboard"))
async def cmd_dashboard(message: Message, db: Database, is_admin: bool):
    """Отправляет HTML-дашборд как WebApp или файл."""
    if not is_admin:
        await message.answer("🚫 У вас нет прав администратора.")
        return
    
    data = await db.get_dashboard_data()
    from bot.dashboard import generate_dashboard
    html = generate_dashboard(json.dumps(data, ensure_ascii=False))
    
    # Отправляем как HTML-файл (Telegram WebApp можно открыть позже)
    from aiogram.types import BufferedInputFile
    doc = BufferedInputFile(html.encode('utf-8'), filename='dashboard.html')
    await message.answer_document(
        doc,
        caption="📊 <b>Дашборд Hotline Service</b>\n\n"
                "Откройте файл в браузере для просмотра графиков."
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message, db: Database, is_admin: bool, edit: bool = False):
    """Выводит статистику по заявкам (с агрегацией в SQL)."""
    # В edit-режиме message — это сообщение бота (панель), поэтому права берём из is_admin.
    if not is_admin:
        if not edit:
            await message.answer("🚫 У вас нет прав администратора.")
        return

    # Оптимизация: считаем заявки одним SQL-запросом, без загрузки всех строк
    counts = await db.get_ticket_status_counts()

    if counts['total'] == 0:
        text = "Заявок пока нет."
        if edit:
            await message.edit_text(text, reply_markup=back_to_admin_kb())
        else:
            await message.answer(text, reply_markup=back_to_admin_kb())
        return

    total_count = counts['total']
    open_count = counts['open']
    in_progress_count = counts['in_progress']
    closed_count = counts['completed'] + counts['canceled']

    text = (
        f"📊 <b>Общая статистика по заявкам:</b>\n\n"
        f"Всего заявок: <b>{total_count}</b>\n"
        f"Открытых (ожидают инженера): <b>{open_count}</b>\n"
        f"В работе: <b>{in_progress_count}</b>\n"
        f"Закрытых: <b>{closed_count}</b>"
    )

    # Добавляем статистику по инженерам
    engineer_stats = await db.get_engineer_stats()
    if engineer_stats:
        stats_lines = []
        for eng_stat in engineer_stats:
            # Пропускаем инженеров без активности для чистоты отчета
            if eng_stat['active_count'] == 0 and eng_stat['closed_count'] == 0:
                continue
            stats_lines.append(
                f"  • <b>{html.escape(eng_stat['name'])}</b>: "
                f"в работе - <b>{eng_stat['active_count']}</b>, "
                f"закрыто - <b>{eng_stat['closed_count']}</b>"
            )
        
        if stats_lines:
            text += "\n\n📈 <b>Статистика по инженерам:</b>\n"
            text += "\n".join(stats_lines)

    # Среднее время решения
    avg_time = await db.get_avg_resolution_time()
    if avg_time is not None:
        text += f"\n\n⏱ <b>Среднее время решения:</b> {avg_time:.1f} ч."

    # Средняя оценка клиентов
    avg_rating = await db.get_avg_rating()
    if avg_rating is not None:
        text += f"\n⭐ <b>Средняя оценка клиентов:</b> {avg_rating:.1f}/5"

    # Статистика по городам
    city_stats = await db.get_tickets_by_city()
    if city_stats:
        city_lines = []
        for cs in city_stats:
            city_lines.append(f"  • <b>{html.escape(str(cs['company_city']))}</b>: {cs['cnt']}")
        text += "\n\n🏙 <b>Статистика по городам:</b>\n" + "\n".join(city_lines)

    if edit:
        await message.edit_text(text, reply_markup=back_to_admin_kb())
    else:
        await message.answer(text, reply_markup=back_to_admin_kb())
