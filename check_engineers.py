import asyncio
from bot.database import Database
from bot.config import DB_PATH

async def main():
    db = Database(DB_PATH)
    await db.connect()
    await db.init_db()
    await db.migrate()

    print("=== Все инженеры в БД ===\n")
    for eng in await db.get_all_engineers():
        print(f"  {eng['name']} (TG: {eng['user_id']}) -> Bitrix: {eng['bitrix_user_id']}, is_active: {eng['is_active']}")

    await db.close()

asyncio.run(main())