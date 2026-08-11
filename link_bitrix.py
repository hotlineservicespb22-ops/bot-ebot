import asyncio
from bot.database import Database
from bot.config import DB_PATH

# Соответствия: Telegram ID -> Bitrix ID
MAPPING = {
    1882774889: 174,
    6808951412: 138,
    984308298: 258,
    7903716863: 172,
}

async def main():
    db = Database(DB_PATH)
    await db.connect()
    await db.init_db()
    await db.migrate()

    print("=== Связывание инженеров с Битрикс24 ===\n")

    # Получаем всех инженеров
    all_eng = await db.get_all_engineers()
    existing_ids = {e['user_id'] for e in all_eng}

    for tg_id, bitrix_id in MAPPING.items():
        if tg_id not in existing_ids:
            print(f"❌ Инженер с Telegram ID {tg_id} не найден в БД. Пропущен.")
            continue

        await db.set_bitrix_user_id(tg_id, bitrix_id)
        # Активируем инженера (is_active = 1), чтобы он получал заявки
        await db.set_engineer_active(tg_id, 1)
        # Получаем имя инженера для отчёта
        name = next((e['name'] for e in all_eng if e['user_id'] == tg_id), '?')
        print(f"✅ {name} (TG: {tg_id}) -> Bitrix ID: {bitrix_id}, активирован")

    print("\n=== Проверка ===")
    for eng in await db.get_all_engineers():
        print(f"  {eng['name']} (TG: {eng['user_id']}) -> Bitrix: {eng['bitrix_user_id']}")

    await db.close()

asyncio.run(main())