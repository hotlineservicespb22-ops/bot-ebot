import os
import logging
from dotenv import load_dotenv

load_dotenv()

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
TICKET_TIMEOUT = int(os.getenv("TICKET_TIMEOUT", "300")) # 5 minutes

def escape_md(text: str) -> str:
    """Escapes markdown special characters."""
    if not text:
        return ""
    special_chars = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special_chars else c for c in text)
