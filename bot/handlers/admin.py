import asyncio
import csv
import io
import logging
import os
import html
from aiogram import Router, Bot, F
from aiogram.types import Message, BufferedInputFile
from aiogram.filters import Command
from bot.config import ADMIN_IDS # Import global ADMIN_IDS
from bot.database import Database

router = Router()
logger = logging.getLogger(__name__)

@router.message(Command("add_eng"))
async def cmd_add_engineer(message: Message, db: Database):
    logger.info(f"Команда /add_eng вызвана пользователем: {message.from_user.id}")

    if message.from_user.id not in ADMIN_IDS:
        logger.warning(f"Доступ к /add_eng запрещен для пользователя {message.from_user.id} (не администратор).")
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("Используйте: <code>/add_eng &lt;Telegram_ID&gt; &lt;Имя&gt;</code>", parse_mode="HTML")
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
    await message.answer(f"✅ Инженер <b>{html.escape(eng_name)}</b> (ID: <code>{eng_id}</code>) добавлен.", parse_mode="HTML")

@router.message(Command("del_eng"))
async def cmd_del_engineer(message: Message, db: Database):
    if message.from_user.id not in ADMIN_IDS:
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Используйте: <code>/del_eng &lt;Telegram_ID&gt;</code>", parse_mode="HTML")
        return
    
    try:
        eng_id = int(args[1])
    except ValueError:
        await message.answer("❌ ID инженера должен быть числом.")
        return
        
    await db.delete_engineer(eng_id)
    await message.answer(f"🗑 Инженер с ID <code>{eng_id}</code> удален.", parse_mode="HTML")

@router.message(Command("list_eng"))
async def cmd_list_engineers(message: Message, db: Database):
    if message.from_user.id not in ADMIN_IDS:
        return
    rows = await db.get_engineers()
    if not rows:
        await message.answer("Список инженеров пуст.")
        return
    
    text = "<b>Дежурные инженеры:</b>\n" + "\n".join([f"• {html.escape(row['name'])} (<code>{row['user_id']}</code>)" for row in rows])
    await message.answer(text, parse_mode="HTML")

def generate_csv(tickets):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Client ID', 'Client Name', 'Company', 'CNC Model', 'Problem', 'Contact', 'Status', 'Engineer ID', 'Comment'])
    for t in tickets:
        writer.writerow([t['id'], t['client_id'], t['client_name'], t['company'], t['cnc_model'], t['problem'], t['contact'], t['status'], t['engineer_id'], t['close_comment']])
    return output.getvalue().encode('utf-8-sig')

@router.message(Command("export"))
async def cmd_export_tickets(message: Message, db: Database, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    status_msg = await message.answer("Готовлю отчет...")
    
    tickets = await db.get_all_tickets()
    if not tickets:
        await status_msg.edit_text("В базе данных еще нет заявок для экспорта.")
        return

    # Run in executor to avoid blocking
    loop = asyncio.get_event_loop()
    csv_data = await loop.run_in_executor(None, generate_csv, tickets)
    
    document = BufferedInputFile(csv_data, filename="tickets_report.csv")
    
    await bot.send_document(
        chat_id=message.chat.id,
        document=document,
        caption="📊 Отчет по всем заявкам (CSV)."
    )
    await status_msg.delete()

@router.message(Command("stats"))
async def cmd_stats(message: Message, db: Database):
    """Выводит статистику по заявкам."""
    if message.from_user.id not in ADMIN_IDS:
        return

    tickets = await db.get_all_tickets()

    if not tickets:
        await message.answer("Заявок пока нет.")
        return

    total_count = len(tickets)
    open_count = sum(1 for t in tickets if t['status'] == 'open')
    in_progress_count = sum(1 for t in tickets if t['status'] == 'in_progress')
    # Завершенные и отмененные считаются закрытыми
    closed_count = sum(1 for t in tickets if t['status'] in ('completed', 'canceled'))

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

    await message.answer(text, parse_mode="HTML")
