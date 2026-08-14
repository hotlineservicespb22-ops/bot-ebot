"""
Интеграция с Битрикс24: создание задач (tasks.task.add) через входящий вебхук.

Документация:
- tasks.task.add: https://dev.1c-bitrix.ru/rest_help/tasks/task/tasks/task_add.php
- disk.folder.uploadfile: https://dev.1c-bitrix.ru/rest_help/disk/disk_folder_uploadfile.php
"""
import asyncio
import datetime
import logging
import os

from aiohttp import ClientSession, FormData

from bot.config import (
    BITRIX_CHAT_ID,
    BITRIX_DISK_FOLDER_ID,
    BITRIX_FROM_USER_ID,
    BITRIX_TASK_DEADLINE_HOURS,
    BITRIX_TASK_PRIORITY,
    BITRIX_WEBHOOK_URL,
)

logger = logging.getLogger(__name__)

# Кэш ID папки диска Битрикс24 (чтобы не делать сетевые запросы при каждой загрузке файла)
_disk_folder_id_cache: int | None = None

# Параметры ретраев при сетевых ошибках
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # секунд


async def _post_with_retry(url: str, *args, retries: int = MAX_RETRIES, **kwargs):
    """
    Выполняет POST-запрос с ретраями при сетевых ошибках (aiohttp.ClientError, asyncio.TimeoutError).

    Возвращает кортеж (response, data). При исчерпании ретраев — поднимает последнее исключение.
    """
    import aiohttp
    last_exc = None
    for attempt in range(retries):
        try:
            async with ClientSession() as session:
                async with session.post(url, *args, **kwargs) as resp:
                    data = await resp.json()
                    return resp, data
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            last_exc = e
            if attempt < retries - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    f"Сетевая ошибка при POST {url} (попытка {attempt + 1}/{retries}): {e}. Повтор через {delay:.1f}с"
                )
                await asyncio.sleep(delay)
    raise last_exc


def _make_deadline() -> str | None:
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


async def _get_disk_folder_id() -> int | None:
    """
    Возвращает ID папки на диске Битрикс24 для загрузки файлов.

    Результат кэшируется, чтобы не выполнять сетевые запросы при каждой загрузке файла.

    Логика:
    1. Если BITRIX_DISK_FOLDER_ID задан — пробуем использовать его как ID папки.
    2. Если это ID хранилища (а не папки) — резолвим ROOT_OBJECT_ID хранилища.
    3. Иначе пытаемся получить общий диск (Common disk).
    """
    global _disk_folder_id_cache
    if _disk_folder_id_cache is not None:
        return _disk_folder_id_cache

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
                    _disk_folder_id_cache = candidate
                    return _disk_folder_id_cache
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
                    _disk_folder_id_cache = int(root_id)
                    return _disk_folder_id_cache
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
                _disk_folder_id_cache = int(folder_id)
                return _disk_folder_id_cache
    except Exception as e:
        logger.error(f"Не удалось получить общий диск Битрикс24: {e}")

    return None


async def upload_file_to_bitrix(file_path: str, filename: str | None = None) -> int | None:
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


async def send_message_to_chat(text: str, chat_id: int | None = None) -> bool:
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
        resp, data = await _post_with_retry(url, json=payload, timeout=15)

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
    created_by: int | None = None,
    deadline: str | None = None,
    priority: str | None = None,
    uf_files: list[int] | None = None,
) -> int | None:
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

    # Постановщик задачи: жёстко заданный ID 414 (бизнес-требование).
    # Аргумент и конфигурация BITRIX_CREATED_BY больше не влияют — постановщик
    # всегда 414, чтобы задачи создавались от нужного пользователя Битрикс24.
    task_created_by = 414

    fields = {
        "TITLE": title,
        "DESCRIPTION": description,
        "RESPONSIBLE_ID": responsible_id,
        "PRIORITY": task_priority,
        "TAGS": ["Бот-Телеграм"],   # Тег задачи (массив строк)
        "CREATED_BY": task_created_by,
    }
    if task_deadline:
        fields["DEADLINE"] = task_deadline
    if uf_files:
        # Битрикс24 ожидает ID файлов в формате "n{ID}" для UF_TASK_WEBDAV_FILES
        fields["UF_TASK_WEBDAV_FILES"] = [f"n{file_id}" for file_id in uf_files]

    payload = {"fields": fields}

    try:
        resp, data = await _post_with_retry(url, json=payload, timeout=15)

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
