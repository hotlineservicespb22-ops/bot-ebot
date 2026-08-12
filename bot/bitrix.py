"""
Интеграция с Битрикс24: создание задач (tasks.task.add) через входящий вебхук.

Документация:
- tasks.task.add: https://dev.1c-bitrix.ru/rest_help/tasks/task/tasks/task_add.php
- disk.folder.uploadfile: https://dev.1c-bitrix.ru/rest_help/disk/disk_folder_uploadfile.php
"""
import datetime
import logging
import os
from typing import List, Optional

from aiohttp import ClientSession, FormData

from bot.config import (
    BITRIX_WEBHOOK_URL,
    BITRIX_CREATED_BY,
    BITRIX_TASK_PRIORITY,
    BITRIX_TASK_DEADLINE_HOURS,
    BITRIX_DISK_FOLDER_ID,
    BITRIX_CHAT_ID,
    BITRIX_FROM_USER_ID,
)

logger = logging.getLogger(__name__)


def _make_deadline() -> Optional[str]:
    """Возвращает дедлайн в формате ISO 8601, если он задан в конфигурации."""
    if not BITRIX_TASK_DEADLINE_HOURS:
        return None
    try:
        hours = float(BITRIX_TASK_DEADLINE_HOURS)
        deadline = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)
        return deadline.isoformat()
    except ValueError:
        logger.warning(f"Некорректное значение BITRIX_TASK_DEADLINE_HOURS: {BITRIX_TASK_DEADLINE_HOURS}")
        return None


async def _get_disk_folder_id() -> Optional[int]:
    """
    Возвращает ID папки на диске Битрикс24 для загрузки файлов.

    Логика:
    1. Если BITRIX_DISK_FOLDER_ID задан — пробуем использовать его как ID папки.
    2. Если это ID хранилища (а не папки) — резолвим ROOT_OBJECT_ID хранилища.
    3. Иначе пытаемся получить общий диск (Common disk).
    """
    if not BITRIX_WEBHOOK_URL:
        return None

    if BITRIX_DISK_FOLDER_ID:
        try:
            candidate = int(BITRIX_DISK_FOLDER_ID)
        except ValueError:
            logger.warning(f"Некорректное значение BITRIX_DISK_FOLDER_ID: {BITRIX_DISK_FOLDER_ID}")
            candidate = None

        if candidate is not None:
            # Пробуем использовать как ID папки напрямую
            url = f"{BITRIX_WEBHOOK_URL.rstrip('/')}/disk.folder.get.json"
            try:
                async with ClientSession() as session:
                    async with session.post(url, json={"id": candidate}, timeout=15) as resp:
                        data = await resp.json()
                if "error" not in data:
                    logger.info(f"BITRIX_DISK_FOLDER_ID={candidate} — это ID папки.")
                    return candidate
            except Exception as e:
                logger.warning(f"Не удалось проверить BITRIX_DISK_FOLDER_ID={candidate} как папку: {e}")

            # Если это не папка — пробуем как ID хранилища (disk.storage.get)
            url = f"{BITRIX_WEBHOOK_URL.rstrip('/')}/disk.storage.get.json"
            try:
                async with ClientSession() as session:
                    async with session.post(url, json={"id": candidate}, timeout=15) as resp:
                        data = await resp.json()
                storage = data.get("result", {})
                root_id = storage.get("ROOT_OBJECT_ID")
                if root_id:
                    logger.info(
                        f"BITRIX_DISK_FOLDER_ID={candidate} — это ID хранилища "
                        f"'{storage.get('NAME', '')}', ROOT_OBJECT_ID={root_id}."
                    )
                    return int(root_id)
            except Exception as e:
                logger.warning(f"Не удалось проверить BITRIX_DISK_FOLDER_ID={candidate} как хранилище: {e}")

    # Пытаемся получить общий диск: disk.storage.getlist
    url = f"{BITRIX_WEBHOOK_URL.rstrip('/')}/disk.storage.getlist.json"
    try:
        async with ClientSession() as session:
            async with session.post(url, json={"ENTITY_TYPE": "common"}, timeout=15) as resp:
                data = await resp.json()
        storages = data.get("result", [])
        if storages:
            # ROOT_OBJECT_ID — это корневая папка хранилища (туда можно загружать файлы)
            folder_id = storages[0].get("ROOT_OBJECT_ID")
            if folder_id:
                logger.info(f"Получен ID общего диска Битрикс24: {folder_id}")
                return int(folder_id)
    except Exception as e:
        logger.error(f"Не удалось получить общий диск Битрикс24: {e}")

    return None


async def upload_file_to_bitrix(file_path: str, filename: Optional[str] = None) -> Optional[int]:
    """
    Загружает файл на диск Битрикс24 в указанную папку.

    Args:
        file_path: Локальный путь к файлу.
        filename: Имя файла в Битрикс24 (по умолчанию — имя локального файла).

    Returns:
        ID файла в Битрикс24 или None в случае ошибки.
    """
    if not BITRIX_WEBHOOK_URL:
        return None
    if not os.path.isfile(file_path):
        logger.warning(f"Файл не найден для загрузки в Битрикс24: {file_path}")
        return None

    folder_id = await _get_disk_folder_id()
    if not folder_id:
        logger.warning("Не удалось определить папку диска Битрикс24 — файл не загружен.")
        return None

    url = f"{BITRIX_WEBHOOK_URL.rstrip('/')}/disk.folder.uploadfile.json"
    fname = filename or os.path.basename(file_path)

    try:
        # Шаг 1: получаем uploadUrl от Битрикс24
        async with ClientSession() as session:
            async with session.post(url, json={"id": folder_id, "filename": fname}, timeout=15) as resp:
                data = await resp.json()

        if not resp.ok:
            logger.error(f"Ошибка HTTP {resp.status} при получении uploadUrl: {data}")
            return None
        if "error" in data:
            logger.error(f"Ошибка Битрикс24 при получении uploadUrl: {data}")
            return None

        result = data.get("result", {})
        upload_url = result.get("uploadUrl")
        # Имя поля для загрузки файла (обычно "file")
        field_name = result.get("field", "file")
        if not upload_url:
            logger.error(f"Не удалось получить uploadUrl из ответа Битрикс24: {data}")
            return None

        # Шаг 2: загружаем файл на uploadUrl
        # Передаём файловый объект напрямую (не f.read()), чтобы aiohttp
        # корректно обработал multipart/form-data (размер, потоковая передача).
        with open(file_path, "rb") as f:
            form = FormData()
            form.add_field(
                field_name,
                f,
                filename=fname,
                content_type="application/octet-stream",
            )
            async with ClientSession() as session:
                async with session.post(upload_url, data=form, timeout=30) as resp:
                    data = await resp.json()

        if not resp.ok:
            logger.error(f"Ошибка HTTP {resp.status} при загрузке файла в Битрикс24: {data}")
            return None
        if "error" in data:
            logger.error(f"Ошибка Битрикс24 при загрузке файла: {data}")
            return None

        file_id = data.get("result", {}).get("ID")
        if file_id is None:
            logger.error(f"Не удалось получить ID файла из ответа Битрикс24: {data}")
            return None

        logger.info(f"Файл {fname} загружен в Битрикс24 (file_id={file_id})")
        return file_id
    except Exception as e:
        logger.error(f"Исключение при загрузке файла в Битрикс24: {e}")
        return None


async def send_message_to_chat(text: str, chat_id: Optional[int] = None) -> bool:
    """
    Отправляет сообщение в чат Битрикс24 через метод im.message.add.

    Args:
        text: Текст сообщения.
        chat_id: ID чата Битрикс24. Если None — используется BITRIX_CHAT_ID из конфигурации.

    Returns:
        True, если сообщение отправлено, иначе False.
    """
    # Определяем DIALOG_ID:
    # - Если указан "chat{ID}" — используем как есть (групповой чат)
    # - Если указано число — для группового чата преобразуем в "chat{ID}"
    target_chat = chat_id
    if target_chat is None and BITRIX_CHAT_ID:
        raw = str(BITRIX_CHAT_ID).strip()
        if raw.lower().startswith('chat'):
            target_chat = raw  # уже в формате chat{ID}
        else:
            try:
                target_chat = f"chat{int(raw)}"
            except ValueError:
                logger.warning(f"Некорректное значение BITRIX_CHAT_ID: {BITRIX_CHAT_ID}")
                return False

    if not target_chat:
        logger.info("BITRIX_CHAT_ID не задан — сообщение в чат Битрикс24 не отправлено.")
        return False
    if not BITRIX_WEBHOOK_URL:
        logger.warning("BITRIX_WEBHOOK_URL не задан — сообщение в чат Битрикс24 не отправлено.")
        return False

    url = f"{BITRIX_WEBHOOK_URL.rstrip('/')}/im.message.add.json"
    payload = {"DIALOG_ID": target_chat, "MESSAGE": text}
    # Отправляем от имени указанного пользователя, если он задан
    if BITRIX_FROM_USER_ID:
        try:
            payload["FROM_USER_ID"] = int(BITRIX_FROM_USER_ID)
        except ValueError:
            logger.warning(f"Некорректное значение BITRIX_FROM_USER_ID: {BITRIX_FROM_USER_ID}")
    # SYSTEM=Y позволяет отправлять сообщения от имени системы (бота) без участия в чате
    payload["SYSTEM"] = "Y"

    try:
        async with ClientSession() as session:
            async with session.post(url, json=payload, timeout=15) as resp:
                data = await resp.json()

        if not resp.ok:
            logger.error(f"Ошибка HTTP {resp.status} при отправке сообщения в чат Битрикс24: {data}")
            return False
        if "error" in data:
            logger.error(f"Ошибка Битрикс24 при отправке сообщения в чат: {data}")
            return False

        logger.info(f"Сообщение отправлено в чат Битрикс24 (dialog_id={target_chat})")
        return True
    except Exception as e:
        logger.error(f"Исключение при отправке сообщения в чат Битрикс24: {e}")
        return False


async def create_task(
    title: str,
    description: str,
    responsible_id: int,
    created_by: Optional[int] = None,
    deadline: Optional[str] = None,
    priority: Optional[str] = None,
    uf_files: Optional[List[int]] = None,
) -> Optional[int]:
    """
    Создаёт задачу в Битрикс24 через метод tasks.task.add.

    Args:
        title: Заголовок задачи.
        description: Описание задачи (HTML).
        responsible_id: ID пользователя Битрикс24, на которого назначается задача.
        created_by: ID пользователя Битрикс24, от чьего имени создаётся задача.
        deadline: Дедлайн в формате ISO 8601. Если None — берётся из конфигурации.
        priority: Приоритет ('1' низкий, '2' средний, '3' высокий, '4' срочный).
        uf_files: Список ID файлов на диске Битрикс24 для прикрепления к задаче.

    Returns:
        ID созданной задачи или None в случае ошибки.
    """
    if not BITRIX_WEBHOOK_URL:
        logger.warning("BITRIX_WEBHOOK_URL не задан — задача в Битрикс24 не создана.")
        return None

    url = f"{BITRIX_WEBHOOK_URL.rstrip('/')}/tasks.task.add.json"

    # Приоритет: аргумент > конфигурация > средний
    task_priority = priority or BITRIX_TASK_PRIORITY or "2"
    # Дедлайн: аргумент > конфигурация
    task_deadline = deadline or _make_deadline()

    # Преобразуем HTML в BBCode для корректного отображения жирного текста в Битрикс24
    description = description.replace('<b>', '[B]').replace('</b>', '[/B]')

    # Постановщик задачи: аргумент > жёстко заданный ID 414 (по требованию заказчика)
    # Всегда ставим постановщиком пользователя с ID 414, если не передан явный created_by.
    task_created_by = created_by or 414

    fields = {
        "TITLE": title,
        "DESCRIPTION": description,
        "RESPONSIBLE_ID": responsible_id,
        "PRIORITY": task_priority,
        "TAGS": ["Бот-Телеграм"],   # Тег задачи (массив строк)
    }
    if task_created_by:
        fields["CREATED_BY"] = task_created_by
    if task_deadline:
        fields["DEADLINE"] = task_deadline
    if uf_files:
        # Битрикс24 ожидает ID файлов в формате "n{ID}" для UF_TASK_WEBDAV_FILES
        fields["UF_TASK_WEBDAV_FILES"] = [f"n{file_id}" for file_id in uf_files]

    payload = {"fields": fields}

    try:
        async with ClientSession() as session:
            async with session.post(url, json=payload, timeout=15) as resp:
                data = await resp.json()

        if not resp.ok:
            logger.error(
                f"Ошибка HTTP {resp.status} при создании задачи в Битрикс24: {data}"
            )
            return None

        # Проверяем ответ Битрикс24
        if "error" in data:
            logger.error(f"Ошибка Битрикс24 при создании задачи: {data}")
            return None

        task = data.get("result", {}).get("task", {})
        task_id = task.get("id")
        if task_id is None:
            logger.error(f"Не удалось получить ID задачи из ответа Битрикс24: {data}")
            return None

        logger.info(f"Задача #{task_id} создана в Битрикс24 (ответственный: {responsible_id})")
        return task_id
    except Exception as e:
        logger.error(f"Исключение при создании задачи в Битрикс24: {e}")
        return None