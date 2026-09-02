import logging
import os
from urllib.parse import urlsplit

from dotenv import load_dotenv

# Пытаемся загрузить как стандартный .env, так и .env.txt (файл может называться по-разному)
load_dotenv()
# .env.txt может перезаписать значения из .env (например, добавление новых администраторов)
load_dotenv(".env.txt", override=True)
# Логирование инициализируется в bot/main.py через setup_logging() (см. bot/logging_config.py).
# Модульный логгер используем без настройки корневого — это безопасно до вызова setup_logging().
logger = logging.getLogger("bot.config")
logger.info(f"Загрузка конфигурации. BOT_TOKEN задан: {bool(os.getenv('BOT_TOKEN'))}")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN variable is not set in environment or .env file")

raw_admin_ids = os.getenv("ADMIN_IDS", "")
# Очищаем строку от любых лишних символов: скобок, кавычек и пробелов
cleaned_str = str(raw_admin_ids).translate(str.maketrans("", "", "[]'\""))

ADMIN_IDS = []
for x in cleaned_str.split(","):
    x = x.strip()
    if x.isdigit():
        ADMIN_IDS.append(int(x))

logging.getLogger("bot.config").info(f"ИТОГОВЫЙ СПИСОК АДМИНОВ: {ADMIN_IDS}")

raw_manager_ids = os.getenv("MANAGER_IDS", "")
cleaned_mgr = str(raw_manager_ids).translate(str.maketrans("", "", "[]'\"\""))

MANAGER_IDS = []
for x in cleaned_mgr.split(","):
    x = x.strip()
    if x.isdigit():
        MANAGER_IDS.append(int(x))

logging.getLogger("bot.config").info(f"ИТОГОВЫЙ СПИСОК РУКОВОДИТЕЛЕЙ: {MANAGER_IDS}")

DB_PATH = os.getenv("DB_PATH", "hotline_service.db")
# Каталог для сохранения фото/видео материалов заявок (подпапки создаются по номеру заявки)
MEDIA_DIR = os.getenv("MEDIA_DIR", "media")
# Таймаут просрочки заявки (в секундах). Валидируем значение, чтобы не уронить бота.
try:
    TICKET_TIMEOUT = int(os.getenv("TICKET_TIMEOUT", "300"))
except ValueError:
    logging.getLogger("bot.config").warning("Некорректное значение TICKET_TIMEOUT, используется значение по умолчанию 300")
    TICKET_TIMEOUT = 300
# SLA по времени первого ответа (в минутах): ожидаемое время от создания заявки
# до взятия её инженером в работу. Используется в метрике
# «% заявок, взятых в SLA» на дашборде.
try:
    SLA_MINUTES = int(os.getenv("SLA_MINUTES", "15"))
except ValueError:
    logging.getLogger("bot.config").warning("Некорректное значение SLA_MINUTES, используется значение по умолчанию 15")
    SLA_MINUTES = 15
# Через сколько дней после завершения заявки клиенту отправляется повторный опрос
# «Всё ли в порядке / Проблема повторилась» (фоновая задача followup_watcher).
try:
    FOLLOWUP_DELAY_DAYS = int(os.getenv("FOLLOWUP_DELAY_DAYS", "7"))
except ValueError:
    logging.getLogger("bot.config").warning("Некорректное значение FOLLOWUP_DELAY_DAYS, используется значение по умолчанию 7")
    FOLLOWUP_DELAY_DAYS = 7
# Таймаут незавершённой воронки оформления заявки (FSM), в секундах. По умолчанию 30 минут.
try:
    FSM_TIMEOUT = int(os.getenv("FSM_TIMEOUT", "1800"))
except ValueError:
    logging.getLogger("bot.config").warning("Некорректное значение FSM_TIMEOUT, используется значение по умолчанию 1800")
    FSM_TIMEOUT = 1800
REDIS_URL = os.getenv("REDIS_URL", "")  # Если задан — используется RedisStorage для FSM
# Кулдаун между созданием заявок одним клиентом (в секундах) — защита от спама воронкой.
try:
    TICKET_CREATE_COOLDOWN = int(os.getenv("TICKET_CREATE_COOLDOWN", "60"))
except ValueError:
    logging.getLogger("bot.config").warning("Некорректное значение TICKET_CREATE_COOLDOWN, используется 60")
    TICKET_CREATE_COOLDOWN = 60
# Webhook-режим (если задан WEBHOOK_URL — бот работает через webhook вместо поллинга)
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
try:
    WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))
except ValueError:
    logging.getLogger("bot.config").warning("Некорректное значение WEBHOOK_PORT, используется значение по умолчанию 8080")
    WEBHOOK_PORT = 8080

# Логирование
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = os.getenv("LOG_FILE", "bot.log")

# Интеграция с Битрикс24 (создание задач)
# URL входящего вебхука, например: https://crm.example.ru/rest/14/ТОКЕН/
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL", "")
# Базовый URL портала Битрикс24 (без /rest/...) для формирования ссылок на задачи.
# Пример: https://crm.example.ru
BITRIX_PORTAL_URL = os.getenv("BITRIX_PORTAL_URL", "").rstrip("/")
# Если BITRIX_PORTAL_URL не задан, но задан BITRIX_WEBHOOK_URL — выводим портал
# из вебхука (https://crm.example.ru/rest/14/ТОКЕН/ → https://crm.example.ru),
# чтобы в уведомлениях в чат Битрикс24 всегда была ссылка на созданную задачу.
if not BITRIX_PORTAL_URL and BITRIX_WEBHOOK_URL:
    _split = urlsplit(BITRIX_WEBHOOK_URL)
    if _split.scheme and _split.netloc:
        BITRIX_PORTAL_URL = f"{_split.scheme}://{_split.netloc}"
        logging.getLogger("bot.config").info(
            f"BITRIX_PORTAL_URL не задан — определён из BITRIX_WEBHOOK_URL: {BITRIX_PORTAL_URL}"
        )
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
# ID пользователя Битрикс24, от имени которого отправляются сообщения в чат.
# ВНИМАНИЕ: im.message.add (webhook) не поддерживает отправку от другого пользователя —
# сообщения всегда уходят от владельца вебхука. Переменная оставлена для совместимости
# и в текущей реализации не используется.
BITRIX_FROM_USER_ID = os.getenv("BITRIX_FROM_USER_ID", "")

# Ключ для доступа к веб-дашборду руководителя.
# Передаётся в заголовке X-Dashboard-Key (query-параметр ?key= оставлен для
# обратной совместимости и навигации по ссылкам). Сравнение — hmac.compare_digest.
MANAGER_DASHBOARD_KEY = os.getenv("MANAGER_DASHBOARD_KEY", "")
# Адрес, на котором веб-дашборд слушает входящие запросы.
# По умолчанию — только localhost; наружу порт не публикуется.
MANAGER_DASHBOARD_HOST = os.getenv("MANAGER_DASHBOARD_HOST", "127.0.0.1")
# Порт веб-дашборда руководителя (по умолчанию 8081, чтобы не конфликтовать с webhook на 8080)
try:
    MANAGER_DASHBOARD_PORT = int(os.getenv("MANAGER_DASHBOARD_PORT", "8081"))
except ValueError:
    MANAGER_DASHBOARD_PORT = 8081
# Публичный URL веб-дашборда (используется в ссылках из Telegram-уведомлений)
MANAGER_DASHBOARD_URL = os.getenv("MANAGER_DASHBOARD_URL", f"http://localhost:{MANAGER_DASHBOARD_PORT}")

# Fail fast: если дашборд слушает на не-localhost интерфейсе (0.0.0.0 и т.п.),
# ключ доступа обязателен. Без ключа любой, кто достучится до порта, получит
# доступ к чувствительным данным (заявки, переписка, контакты).
_LOCALHOST_HOSTS = {"127.0.0.1", "localhost", "::1"}
if not MANAGER_DASHBOARD_KEY and MANAGER_DASHBOARD_HOST not in _LOCALHOST_HOSTS:
    raise ValueError(
        "MANAGER_DASHBOARD_KEY не задан, а MANAGER_DASHBOARD_HOST="
        f"'{MANAGER_DASHBOARD_HOST}' не является localhost. Веб-дашборд "
        "руководителя содержит чувствительные данные и не должен быть доступен "
        "без ключа извне. Задайте MANAGER_DASHBOARD_KEY или ограничьте "
        "MANAGER_DASHBOARD_HOST адресом 127.0.0.1."
    )

# Секретный токен для проверки подлинности входящих webhook-запросов.
# Задаётся через переменную окружения WEBHOOK_SECRET_TOKEN и сверяется
# с заголовком X-Telegram-Bot-Api-Secret-Token. Если не задан — проверка отключена.
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "")