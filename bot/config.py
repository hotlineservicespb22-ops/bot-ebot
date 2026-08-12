import logging
import os

from dotenv import load_dotenv

# Пытаемся загрузить как стандартный .env, так и .env.txt (файл может называться по-разному)
load_dotenv()
# .env.txt может перезаписать значения из .env (например, добавление новых администраторов)
load_dotenv(".env.txt", override=True)
logging.info(f"Загрузка конфигурации. BOT_TOKEN задан: {bool(os.getenv('BOT_TOKEN'))}")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN variable is not set in environment or .env file")

raw_admin_ids = os.getenv("ADMIN_IDS", "7039700975")
# Очищаем строку от любых лишних символов: скобок, кавычек и пробелов
cleaned_str = str(raw_admin_ids).translate(str.maketrans("", "", "[]'\""))

ADMIN_IDS = []
for x in cleaned_str.split(","):
    x = x.strip()
    if x.isdigit():
        ADMIN_IDS.append(int(x))

logging.info(f"ИТОГОВЫЙ СПИСОК АДМИНОВ: {ADMIN_IDS} (тип элементов: {[type(i) for i in ADMIN_IDS]})")

DB_PATH = os.getenv("DB_PATH", "hotline_service.db")
# Каталог для сохранения фото/видео материалов заявок (подпапки создаются по номеру заявки)
MEDIA_DIR = os.getenv("MEDIA_DIR", "media")
# Таймаут просрочки заявки (в секундах). Валидируем значение, чтобы не уронить бота.
try:
    TICKET_TIMEOUT = int(os.getenv("TICKET_TIMEOUT", "300"))
except ValueError:
    logging.warning("Некорректное значение TICKET_TIMEOUT, используется значение по умолчанию 300")
    TICKET_TIMEOUT = 300
REDIS_URL = os.getenv("REDIS_URL", "")  # Если задан — используется RedisStorage для FSM
# Webhook-режим (если задан WEBHOOK_URL — бот работает через webhook вместо поллинга)
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
try:
    WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))
except ValueError:
    logging.warning("Некорректное значение WEBHOOK_PORT, используется значение по умолчанию 8080")
    WEBHOOK_PORT = 8080

# Интеграция с Битрикс24 (создание задач)
# URL входящего вебхука, например: https://crm.wattsan.ru/rest/14/ТОКЕН/
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL", "")
# ID пользователя Битрикс24, от чьего имени создаются задачи (необязательно, по умолчанию — автор вебхука)
BITRIX_CREATED_BY = os.getenv("BITRIX_CREATED_BY", "")
# Приоритет создаваемых задач: 1 - низкий, 2 - средний, 3 - высокий, 4 - срочный
BITRIX_TASK_PRIORITY = os.getenv("BITRIX_TASK_PRIORITY", "2")
# Дедлайн задачи в часах от момента взятия (пусто — без дедлайна)
BITRIX_TASK_DEADLINE_HOURS = os.getenv("BITRIX_TASK_DEADLINE_HOURS", "")
# ID папки на диске Битрикс24, куда загружаются файлы заявки (пусто — используется общий диск)
BITRIX_DISK_FOLDER_ID = os.getenv("BITRIX_DISK_FOLDER_ID", "")
# Прикреплять ли файлы заявки к задаче (1 - да, 0 - нет)
BITRIX_ATTACH_FILES = os.getenv("BITRIX_ATTACH_FILES", "1")
# ID чата Битрикс24, куда отправляются уведомления о новых заявках (пусто — не отправлять)
BITRIX_CHAT_ID = os.getenv("BITRIX_CHAT_ID", "")
# ID пользователя Битрикс24, от имени которого отправляются сообщения в чат (пусто — автор вебхука)
BITRIX_FROM_USER_ID = os.getenv("BITRIX_FROM_USER_ID", "")
