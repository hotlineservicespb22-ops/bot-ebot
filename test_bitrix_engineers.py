import asyncio
import logging
from aiohttp import ClientSession
from bot.database import Database
from bot.config import DB_PATH, BITRIX_WEBHOOK_URL
from bot.handlers.engineer import _create_bitrix_task_for_ticket

# Настраиваем логирование в консоль, чтобы видеть ошибки от Битрикс24
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger('bot.bitrix').setLevel(logging.DEBUG)
logging.getLogger('bot.handlers.engineer').setLevel(logging.DEBUG)

# Инженеры для тестирования: Telegram ID -> Bitrix ID
ENGINEERS = [
    (1882774889, 174, "Вадим Пономарев"),
    (6808951412, 138, "Андрей Пушкин"),
    (984308298, 258, "Алексей Чечко"),
    (7903716863, 172, "Михаил Глебов"),
]


async def check_task(task_id: int, expected_responsible: int):
    """Проверяет задачу в Битрикс24: ответственный и тег."""
    if not BITRIX_WEBHOOK_URL:
        print("❌ BITRIX_WEBHOOK_URL не задан")
        return False
    url = f"{BITRIX_WEBHOOK_URL.rstrip('/')}/tasks.task.get.json"
    payload = {"taskId": task_id, "select": ["ID", "TITLE", "RESPONSIBLE_ID", "TAGS", "CREATED_BY"]}
    async with ClientSession() as session:
        async with session.post(url, json=payload, timeout=15) as resp:
            data = await resp.json()
    if "error" in data:
        print(f"❌ Ошибка API: {data}")
        return False
    task = data.get("result", {}).get("task", {})
    # Битрикс24 может возвращать ID как строки — приводим к int
    responsible = int(task.get("responsibleId") or 0)
    created_by = int(task.get("createdBy") or 0)
    tags = task.get("tags", {})
    tag_titles = [t.get('title') for t in tags.values()] if isinstance(tags, dict) else []

    ok = True
    if responsible != expected_responsible:
        print(f"  ❌ Ответственный: {responsible} (ожидалось {expected_responsible})")
        ok = False
    else:
        print(f"  ✅ Ответственный: {responsible}")

    if created_by != 414:
        print(f"  ❌ Постановщик: {created_by} (ожидалось 414)")
        ok = False
    else:
        print(f"  ✅ Постановщик: {created_by}")

    if "Бот-Телеграм" not in tag_titles:
        print(f"  ❌ Тег 'Бот-Телеграм' не найден. Теги: {tag_titles}")
        ok = False
    else:
        print(f"  ✅ Тег 'Бот-Телеграм' присутствует")

    return ok


async def main():
    db = Database(DB_PATH)
    await db.connect()
    await db.init_db()
    await db.migrate()

    print("=== Тест создания задач в Битрикс24 для каждого инженера ===\n")

    all_ok = True
    for tg_id, bitrix_id, name in ENGINEERS:
        print(f"--- {name} (TG: {tg_id}, Bitrix: {bitrix_id}) ---")

        # Проверяем, что инженер существует и привязан к Битриксу
        bitrix_user_id = await db.get_bitrix_user_id(tg_id)
        if not bitrix_user_id:
            print(f"  ❌ Инженер {name} не привязан к Битрикс24 в БД")
            all_ok = False
            continue
        if bitrix_user_id != bitrix_id:
            print(f"  ❌ Bitrix ID в БД ({bitrix_user_id}) не совпадает с ожидаемым ({bitrix_id})")
            all_ok = False
            continue
        print(f"  ✅ Привязка в БД корректна (Bitrix: {bitrix_user_id})")

        # Создаём тестовую заявку, назначенную на этого инженера
        ticket_id = await db.create_ticket(
            client_id=999999,
            client_name="Тест Клиент",
            company="Тестовая компания",
            equipment_type="",
            brand="",
            cnc_model="",
            problem="Тестовая заявка для проверки интеграции с Битрикс24",
            media_id=None,
            city="",
            inn_contract="",
            contact="+79990000000",
            machine_info="Тестовый станок",
            company_city="Москва",
        )
        # Назначаем инженера
        await db.take_ticket(ticket_id, tg_id)
        ticket = await db.get_ticket(ticket_id)

        # Создаём задачу в Битрикс24
        task_id = await _create_bitrix_task_for_ticket(db, ticket)
        if not task_id:
            print(f"  ❌ Не удалось создать задачу для инженера {name}")
            all_ok = False
            continue
        print(f"  ✅ Задача создана: #{task_id}")

        # Проверяем задачу
        ok = await check_task(task_id, bitrix_id)
        if not ok:
            all_ok = False

        # Закрываем тестовую заявку
        await db.close_ticket(ticket_id, status='completed', comment='Тестовая заявка')
        print()

    print("=== Итог ===")
    if all_ok:
        print("✅ Все задачи созданы и проверены успешно!")
    else:
        print("❌ Есть ошибки. Проверьте логи выше.")

    await db.close()


asyncio.run(main())