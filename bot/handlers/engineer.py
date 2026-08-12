import html
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.bitrix import create_task, send_message_to_chat, upload_file_to_bitrix
from bot.config import BITRIX_ATTACH_FILES
from bot.database import Database
from bot.keyboards import (
    TicketCallback,
    active_ticket_menu_kb,
    engineer_active_ticket_kb,
    engineer_default_menu_kb,
    engineer_select_client_kb,
    engineer_ticket_control_kb,
    main_menu,
    rating_kb,
)

logger = logging.getLogger(__name__)


router = Router()

@router.callback_query(TicketCallback.filter(F.action == "take"))
async def take_ticket(callback: CallbackQuery, callback_data: TicketCallback, bot: Bot, db: Database, is_engineer: bool, state: FSMContext):
    if not is_engineer:
        await callback.answer("У вас нет прав инженера.", show_alert=True)
        return
    await callback.answer()  # Быстрый ответ Telegram для снятия спиннера на кнопке

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
            # Обновляем сообщение (учитываем медиа-сообщения: фото/видео)
            try:
                already_taken_suffix = "\n\n👨‍🔧 <b>Заявка уже в работе</b>"
                if callback.message.caption is not None:
                    await callback.message.edit_caption(
                        caption=(callback.message.caption or '') + already_taken_suffix
                    )
                else:
                    await callback.message.edit_text(
                        callback.message.html_text + already_taken_suffix
                    )
            except Exception as e:
                logger.warning(f"Не удалось обновить сообщение о занятой заявке #{ticket_id}: {e}")
        return

    # Удаляем уведомления об этой заявке у других инженеров
    await _delete_ticket_notifications(bot, db, ticket_id, except_engineer_id=eng_id)

    # Устанавливаем эту заявку как активную для инженера
    # Сбрасываем данные навигации по списку, т.к. состав списков изменился.
    # Не очищаем ticket_view_ids полностью — это ломает навигацию по списку заявок
    # (инженер "теряет" свои заявки после переключения). Список пересоздаётся при возврате.
    await state.update_data(active_ticket_id=ticket_id, ticket_view_index=0)

    ticket = await db.get_ticket(ticket_id)
    # Преобразуем sqlite3.Row в dict для безопасного доступа к полям
    ticket_dict = dict(ticket) if ticket else {}

    # Создаём задачу в Битрикс24 на инженера, взявшего заявку.
    # Оборачиваем в try/except, чтобы сбой Битрикс24 не блокировал взятие заявки.
    try:
        task_id = await _create_bitrix_task_for_ticket(db, ticket)
    except Exception as e:
        logger.error(f"Ошибка при создании задачи Битрикс24 для заявки #{ticket_id}: {e}")
        task_id = None

    # Отправляем уведомление в чат Битрикс24 о новой заявке
    if task_id:
        try:
            engineer_name = callback.from_user.full_name
            # Получаем ID пользователя Битрикс24 для упоминания инженера в чате
            bitrix_engineer_id = await db.get_bitrix_user_id(eng_id)
            # Формируем упоминание инженера: [USER=ID]Имя[/USER]
            if bitrix_engineer_id:
                engineer_mention = f"[USER={bitrix_engineer_id}]{engineer_name}[/USER]"
            else:
                engineer_mention = html.escape(engineer_name)
            # Ссылка на задачу в Битрикс24
            task_url = f"https://crm.wattsan.ru/company/personal/user/{bitrix_engineer_id or ''}/tasks/task/view/{task_id}/"
            chat_msg = (
                f"🚨 [B]{engineer_mention}[/B] взял заявку [B]#{ticket_id}[/B] в работу.\n\n"
                f"🏢 [B]Компания/Город:[/B] {html.escape(str(ticket_dict.get('company_city') or '—'))}\n"
                f"🔧 [B]Станок:[/B] {html.escape(str(ticket_dict.get('machine_info') or '—'))}\n"
                f"📝 [B]Проблема:[/B] {html.escape(str(ticket_dict.get('problem') or '—'))}\n\n"
                f"📌 [B]Задача:[/B] [URL={task_url}]Заявка #{task_id}[/URL]"
            )
            await send_message_to_chat(chat_msg)
        except Exception as e:
            logger.warning(f"Не удалось отправить уведомление в чат Битрикс24 о заявке #{ticket_id}: {e}")

    # Обновляем сообщение о взятии заявки.
    # Если сообщение — медиа (фото/видео), используем edit_caption, иначе edit_text.
    try:
        taken_suffix = f"\n\n👨‍🔧 <b>Взято в работу инженером:</b> {html.escape(callback.from_user.full_name)}"
        if callback.message.caption is not None:
            # Медиа-сообщение: редактируем подпись
            new_caption = (callback.message.caption or '') + taken_suffix
            await callback.message.edit_caption(
                caption=new_caption,
                reply_markup=engineer_ticket_control_kb(ticket_id)
            )
        else:
            # Текстовое сообщение: редактируем текст
            await callback.message.edit_text(
                callback.message.html_text + taken_suffix,
                reply_markup=engineer_ticket_control_kb(ticket_id)
            )
    except Exception as e:
        logger.warning(f"Не удалось обновить сообщение о взятии заявки #{ticket_id}: {e}")

    if ticket and ticket['client_id']:
        try:
            await bot.send_message(
                ticket['client_id'],
                (
                    f"👨‍🔧 К вашей заявке #{ticket_id} подключился дежурный инженер.\n"
                    "Вы можете писать уточнения и присылать фотографии прямо в этот чат."
                ),
                reply_markup=active_ticket_menu_kb()
            )
        except Exception as e:
            logger.warning(f"Не удалось уведомить клиента {ticket['client_id']} о взятии заявки #{ticket_id}: {e}")
    else:
        logger.warning(f"Заявка #{ticket_id} не имеет client_id — клиент не уведомлён о взятии заявки.")

    try:
        await callback.message.answer(
            f"✅ Заявка #{ticket_id} взята в работу и установлена как активный чат. Ваши сообщения будут направлены этому клиенту. Используйте /my_tickets для переключения.",
            reply_markup=engineer_active_ticket_kb()
        )
    except Exception as e:
        logger.warning(f"Не удалось отправить подтверждение инженеру о взятии заявки #{ticket_id}: {e}")

    # Отправляем фото шильдика станка, если оно было прикреплено клиентом
    machine_media_id = ticket['machine_media_id'] if ticket else None
    if machine_media_id:
        try:
            await bot.send_photo(
                callback.from_user.id,
                photo=machine_media_id,
                caption="📷 Фото шильдика станка"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить фото шильдика инженеру {callback.from_user.id} для заявки #{ticket_id}: {e}")

@router.callback_query(TicketCallback.filter(F.action == "complete"))
async def complete_ticket_by_engineer(callback: CallbackQuery, callback_data: TicketCallback, bot: Bot, db: Database, state: FSMContext, is_engineer: bool):
    if not is_engineer:
        await callback.answer("У вас нет прав инженера.", show_alert=True)
        return
    await callback.answer()  # Быстрый ответ Telegram для снятия спиннера на кнопке

    ticket_id = callback_data.ticket_id
    ticket = await db.get_ticket(ticket_id)
    if not ticket or ticket['engineer_id'] != callback.from_user.id:
        await callback.answer("Это не ваша заявка или она уже закрыта.", show_alert=True)
        return

    await db.close_ticket(ticket_id, status='completed', comment='Работы успешно завершены инженером')

    # Обновляем сообщение о завершении заявки (учитываем медиа-сообщения)
    try:
        completed_suffix = "\n\n✅ <b>Статус: Заявка успешно завершена.</b>"
        if callback.message.caption is not None:
            await callback.message.edit_caption(
                caption=(callback.message.caption or '') + completed_suffix
            )
        else:
            await callback.message.edit_text(callback.message.html_text + completed_suffix)
    except Exception as e:
        logger.warning(f"Не удалось обновить сообщение о завершении заявки #{ticket_id}: {e}")

    try:
        await bot.send_message(
            ticket['client_id'],
            f"✅ Заявка #{ticket_id} успешно завершена специалистом. Спасибо за обращение!\n\nОцените, пожалуйста, качество обслуживания:",
            reply_markup=rating_kb(ticket_id)
        )
    except Exception as e:
        logger.warning(f"Не удалось уведомить клиента {ticket['client_id']} о завершении заявки #{ticket_id}: {e}")

    current_state_data = await state.get_data()
    if current_state_data.get("active_ticket_id") == ticket_id:
        await state.update_data(active_ticket_id=None)

    remaining_tickets = await db.get_active_tickets_for_engineer(callback.from_user.id)
    if remaining_tickets:
        try:
            await callback.message.answer("У вас остались активные заявки. Выберите следующую для работы:", reply_markup=engineer_select_client_kb(remaining_tickets)) # Inline keyboard
            await callback.message.answer("Ваше меню обновлено.", reply_markup=engineer_default_menu_kb()) # Revert to default ReplyKeyboardMarkup
        except Exception as e:
            logger.warning(f"Не удалось отправить список оставшихся заявок инженеру {callback.from_user.id}: {e}")
    else:
        try:
            await callback.message.answer("У вас больше нет активных заявок в работе.", reply_markup=engineer_default_menu_kb())
        except Exception as e:
            logger.warning(f"Не удалось отправить сообщение об отсутствии заявок инженеру {callback.from_user.id}: {e}")


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
        f"✅ Заявка #{active_ticket_id} успешно завершена специалистом. Спасибо за обращение!\n\nОцените, пожалуйста, качество обслуживания:",
        reply_markup=rating_kb(active_ticket_id) # Return client to main menu
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
    await callback.answer()  # Быстрый ответ Telegram для снятия спиннера на кнопке

    ticket_id = callback_data.ticket_id
    ticket = await db.get_ticket(ticket_id)
    if not ticket or ticket['engineer_id'] != callback.from_user.id:
        await callback.answer("Это не ваша заявка или она уже закрыта.", show_alert=True)
        return

    await db.close_ticket(ticket_id, status='canceled', comment='Отменена инженером')

    # Обновляем сообщение об отмене заявки (учитываем медиа-сообщения)
    try:
        canceled_suffix = "\n\n🚫 <b>Статус: Заявка отменена.</b>"
        if callback.message.caption is not None:
            await callback.message.edit_caption(
                caption=(callback.message.caption or '') + canceled_suffix
            )
        else:
            await callback.message.edit_text(callback.message.html_text + canceled_suffix)
    except Exception as e:
        logger.warning(f"Не удалось обновить сообщение об отмене заявки #{ticket_id}: {e}")

    try:
        await bot.send_message(
            ticket['client_id'],
            f"🚫 Заявка #{ticket_id} была отменена инженером.",
            reply_markup=main_menu()
        )
    except Exception as e:
        logger.warning(f"Не удалось уведомить клиента {ticket['client_id']} об отмене заявки #{ticket_id}: {e}")

    current_state_data = await state.get_data()
    if current_state_data.get("active_ticket_id") == ticket_id:
        await state.update_data(active_ticket_id=None)

    remaining_tickets = await db.get_active_tickets_for_engineer(callback.from_user.id)
    if remaining_tickets:
        try:
            await callback.message.answer("У вас остались активные заявки. Выберите следующую для работы:", reply_markup=engineer_select_client_kb(remaining_tickets))
        except Exception as e:
            logger.warning(f"Не удалось отправить список оставшихся заявок инженеру {callback.from_user.id}: {e}")
    else:
        try:
            await callback.message.answer("У вас больше нет активных заявок в работе.", reply_markup=engineer_default_menu_kb())
        except Exception as e:
            logger.warning(f"Не удалось отправить сообщение об отсутствии заявок инженеру {callback.from_user.id}: {e}")


async def _delete_ticket_notifications(bot: Bot, db: Database, ticket_id: int, except_engineer_id: int = None):
    """
    Удаляет уведомления о заявке у всех инженеров, кроме указанного.

    Args:
        bot: Экземпляр бота.
        db: Экземпляр БД.
        ticket_id: ID заявки.
        except_engineer_id: ID инженера, у которого НЕ удалять уведомление (тот, кто взял заявку).
    """
    try:
        notifications = await db.get_ticket_notifications(ticket_id)
        for notif in notifications:
            engineer_id = notif['engineer_id']
            message_id = notif['message_id']
            # Не удаляем уведомление у инженера, который взял заявку
            if except_engineer_id is not None and engineer_id == except_engineer_id:
                continue
            try:
                await bot.delete_message(chat_id=engineer_id, message_id=message_id)
                logger.info(f"Удалено уведомление о заявке #{ticket_id} у инженера {engineer_id} (msg_id={message_id})")
            except Exception as e:
                logger.warning(f"Не удалось удалить уведомление о заявке #{ticket_id} у инженера {engineer_id}: {e}")
        # Очищаем записи об уведомлениях (все, включая того, кто взял — его сообщение уже отредактировано)
        await db.delete_ticket_notifications(ticket_id)
    except Exception as e:
        logger.error(f"Ошибка при удалении уведомлений о заявке #{ticket_id}: {e}")


async def _create_bitrix_task_for_ticket(db: Database, ticket):
    """
    Создаёт задачу в Битрикс24 для заявки, назначенной на инженера.

    Назначается на пользователя Битрикс24, соответствующего инженеру (bitrix_user_id).
    Если соответствие не задано или Битрикс24 не сконфигурирован — пропускает (логирует).
    """
    if not ticket:
        return

    engineer_id = ticket['engineer_id']
    if not engineer_id:
        logger.warning(f"Заявка #{ticket['id']} не имеет инженера — задача в Битрикс24 не создана.")
        return

    # Получаем ID пользователя Битрикс24 для инженера
    bitrix_user_id = await db.get_bitrix_user_id(engineer_id)
    if not bitrix_user_id:
        logger.warning(
            f"Для инженера {engineer_id} не задан bitrix_user_id "
            f"(команда /set_bitrix) — задача в Битрикс24 не создана."
        )
        return

    # Формируем заголовок и описание задачи
    problem = str(ticket['problem'] or '—')
    title = f"Заявка #{ticket['id']}: {problem[:80]}"

    description_parts = [
        f"<b>Заявка #{ticket['id']}</b>",
        f"<b>Компания/Город:</b> {html.escape(str(ticket['company_city'] or '—'))}",
        f"<b>Станок:</b> {html.escape(str(ticket['machine_info'] or '—'))}",
        f"<b>Проблема:</b> {html.escape(problem)}",
        f"<b>Контакты:</b> {html.escape(str(ticket['contact'] or '—'))}",
        f"<b>Клиент:</b> {html.escape(str(ticket['client_name'] or '—'))}",
    ]
    description = "\n".join(description_parts)

    # Загружаем файлы заявки в Битрикс24 и прикрепляем к задаче (если включено)
    uf_files = []
    if BITRIX_ATTACH_FILES == "1":
        media_rows = await db.get_media_for_ticket(ticket['id'])
        for media in media_rows:
            file_path = media['file_path']
            if not file_path:
                continue
            # file_path хранится как относительный путь (например, media/ticket_1/001_photo.jpg)
            file_id = await upload_file_to_bitrix(file_path)
            if file_id:
                uf_files.append(file_id)

    task_id = await create_task(
        title=title,
        description=description,
        responsible_id=bitrix_user_id,
        uf_files=uf_files or None,
    )
    if task_id:
        logger.info(f"Для заявки #{ticket['id']} создана задача Битрикс24 #{task_id} (файлов: {len(uf_files)})")
    else:
        logger.error(f"Не удалось создать задачу Битрикс24 для заявки #{ticket['id']}")
    return task_id