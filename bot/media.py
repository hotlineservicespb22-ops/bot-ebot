import asyncio
import logging
import mimetypes
import os
import re

from aiogram import Bot

from bot.config import MEDIA_DIR

logger = logging.getLogger(__name__)

# Разрешённые расширения и их соответствие типу медиа
MEDIA_EXTENSIONS = {
    'photo': '.jpg',
    'video': '.mp4',
    'document': '',
    'voice': '.ogg',
    'audio': '.mp3',
    'animation': '.gif',
    'sticker': '.webp',
}

# Whitelist безопасных расширений файлов (защита от загрузки исполняемых файлов).
# Если расширение файла не в списке — используется расширение по умолчанию для типа медиа.
ALLOWED_EXTENSIONS = {
    # Изображения
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.heic', '.heif',
    # Видео
    '.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp',
    # Аудио
    '.mp3', '.ogg', '.wav', '.m4a', '.aac', '.opus',
    # Документы
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.txt', '.csv', '.rtf', '.odt', '.ods', '.odp',
    # Архивы (могут потребоваться для логов/конфигов станков)
    '.zip', '.rar', '.7z', '.tar', '.gz',
}

# Максимальная длина имени файла (без расширения), чтобы не превышать лимиты ФС
MAX_NAME_LENGTH = 80


def get_ticket_dir(ticket_id: int) -> str:
    """Возвращает путь к папке заявки: <MEDIA_DIR>/ticket_<id>."""
    return os.path.join(MEDIA_DIR, f"ticket_{ticket_id}")


def ensure_ticket_dir(ticket_id: int) -> str:
    """Создаёт (при необходимости) папку заявки и возвращает её путь."""
    ticket_dir = get_ticket_dir(ticket_id)
    os.makedirs(ticket_dir, exist_ok=True)
    return ticket_dir


def sanitize_filename(name: str) -> str:
    """Очищает имя файла от недопустимых символов и ограничивает длину."""
    name = re.sub(r'[\\/*?:"<>|]', '_', name)
    name = name.strip() or 'file'
    # Обрезаем длину, сохраняя расширение (если есть)
    if len(name) > MAX_NAME_LENGTH:
        root, ext = os.path.splitext(name)
        name = root[:MAX_NAME_LENGTH - len(ext)] + ext
    return name


def _guess_extension(file_name: str | None, mime_type: str | None, media_type: str) -> str:
    """Определяет расширение файла по имени, MIME-типу или типу медиа.

    Безопасность: расширение проверяется по whitelist (ALLOWED_EXTENSIONS).
    Если расширение не в списке — используется расширение по умолчанию для типа медиа.
    Это защищает от сохранения исполняемых файлов (.exe, .php, .sh и т.д.).
    """
    default_ext = MEDIA_EXTENSIONS.get(media_type, '')

    if file_name:
        ext = os.path.splitext(file_name)[1].lower()
        if ext and ext in ALLOWED_EXTENSIONS:
            return ext
        # Расширение не в whitelist — используем безопасное по умолчанию
        if ext:
            logger.warning(
                f"Расширение '{ext}' не в whitelist — заменено на '{default_ext or 'без расширения'}'"
            )

    if mime_type:
        ext = mimetypes.guess_extension(mime_type)
        if ext and ext.lower() in ALLOWED_EXTENSIONS:
            return ext.lower()

    return default_ext


def next_file_number(ticket_id: int) -> int:
    """Возвращает следующий порядковый номер файла в папке заявки."""
    ticket_dir = get_ticket_dir(ticket_id)
    if not os.path.isdir(ticket_dir):
        return 1
    max_num = 0
    for name in os.listdir(ticket_dir):
        # Ищем файлы вида 001_*.*
        match = re.match(r'^(\d+)_', name)
        if match:
            try:
                max_num = max(max_num, int(match.group(1)))
            except ValueError:
                pass
    return max_num + 1


async def save_media_file(
    bot: Bot,
    file_id: str,
    ticket_id: int,
    media_type: str,
    file_name: str | None = None,
    mime_type: str | None = None,
) -> str | None:
    """
    Скачивает файл из Telegram и сохраняет его в папку заявки.

    Возвращает относительный путь к сохранённому файлу (например:
    media/ticket_12/001_photo.jpg) или None в случае ошибки.
    """
    if not file_id:
        return None

    try:
        file_info = await bot.get_file(file_id)
        if not file_info.file_path:
            logger.warning(f"Невозможно получить файл {file_id} (нет file_path).")
            return None

        # Определяем расширение
        base_name = file_info.file_path.split('/')[-1] if file_info.file_path else None
        file_name = file_name or base_name
        ext = _guess_extension(file_name, mime_type, media_type)

        # Уникальный порядковый номер файла (вычисление в отдельном потоке,
        # чтобы не блокировать event loop на операциях с ФС)
        num = await asyncio.to_thread(next_file_number, ticket_id)
        safe_name = sanitize_filename(file_name)
        if not safe_name and not ext:
            safe_name = f"file{ext}"
        elif not safe_name:
            safe_name = f"{media_type}"
        # Если имя уже содержит расширение, не дублируем его
        if ext and safe_name.lower().endswith(ext):
            safe_name = safe_name[:-len(ext)]
        # Имя: 001_исходное_имя.расширение (или 001_media_type.расширение)
        stored_name = f"{num:03d}_{safe_name}{ext}"

        ticket_dir = await asyncio.to_thread(ensure_ticket_dir, ticket_id)
        dest_path = os.path.join(ticket_dir, stored_name)

        # Скачиваем файл
        await bot.download(file_id, destination=dest_path)
        logger.info(f"Файл {file_id} сохранён как {dest_path}")

        # Возвращаем относительный путь (относительно корня проекта)
        rel_path = os.path.join(MEDIA_DIR, f"ticket_{ticket_id}", stored_name)
        return rel_path.replace('\\', '/')
    except Exception as e:
        logger.error(f"Ошибка при сохранении файла {file_id}: {e}")
        return None