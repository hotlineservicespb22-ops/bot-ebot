import asyncio
import logging
from aiohttp import ClientSession
from bot.config import BITRIX_WEBHOOK_URL

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

async def call(method: str, payload: dict = None):
    """Вызывает метод REST API Битрикс24."""
    if not BITRIX_WEBHOOK_URL:
        print("❌ BITRIX_WEBHOOK_URL не задан")
        return None
    url = f"{BITRIX_WEBHOOK_URL.rstrip('/')}/{method}.json"
    async with ClientSession() as session:
        async with session.post(url, json=payload or {}, timeout=15) as resp:
            return await resp.json()

async def main():
    print("=== Диагностика вебхука Битрикс24 ===\n")

    # 1. Кто текущий пользователь вебхука
    print("1. Текущий пользователь вебхука (user.current):")
    data = await call("user.current")
    if "error" in data:
        print(f"   ❌ Ошибка: {data}")
    else:
        user = data.get("result", {})
        print(f"   ID: {user.get('ID')}, Имя: {user.get('NAME')} {user.get('LAST_NAME')}")

    # 2. Информация о приложении/вебхуке
    print("\n2. Информация о приложении (app.info):")
    data = await call("app.info")
    if "error" in data:
        print(f"   ❌ Ошибка: {data}")
    else:
        app = data.get("result", {})
        print(f"   ID: {app.get('ID')}, Код: {app.get('CODE')}, Установлено: {app.get('INSTALLED')}")

    # 3. Попытка отправить сообщение в личный диалог пользователя 78
    print("\n3. Отправка в личный диалог пользователя 78:")
    data = await call("im.message.add", {"DIALOG_ID": 78, "MESSAGE": "Тест из бота (личный диалог)"})
    if "error" in data:
        print(f"   ❌ Ошибка: {data}")
    else:
        print(f"   ✅ Сообщение отправлено: {data.get('result')}")

    # 4. Попытка отправить в групповой чат chat10476
    print("\n4. Отправка в групповой чат chat10476:")
    data = await call("im.message.add", {"DIALOG_ID": "chat10476", "MESSAGE": "Тест из бота (групповой чат)"})
    if "error" in data:
        print(f"   ❌ Ошибка: {data}")
    else:
        print(f"   ✅ Сообщение отправлено: {data.get('result')}")

    # 5. Участники чата chat10476
    print("\n5. Участники чата chat10476 (im.chat.get):")
    data = await call("im.chat.get", {"CHAT_ID": 10476})
    if "error" in data:
        print(f"   ❌ Ошибка: {data}")
    else:
        chat = data.get("result", {})
        print(f"   Название: {chat.get('name')}")
        users = chat.get('users', [])
        print(f"   Участников: {len(users)}")
        for u in users:
            print(f"   - ID: {u.get('id')}, Имя: {u.get('name')} {u.get('last_name')}")

asyncio.run(main())