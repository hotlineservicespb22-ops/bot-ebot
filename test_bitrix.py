import asyncio
from aiohttp import ClientSession
from bot.bitrix import create_task
from bot.config import BITRIX_WEBHOOK_URL

async def check_task(task_id: int):
    """Проверяет параметры созданной задачи через API Битрикс24."""
    if not BITRIX_WEBHOOK_URL:
        print("❌ BITRIX_WEBHOOK_URL не задан")
        return
    url = f"{BITRIX_WEBHOOK_URL.rstrip('/')}/tasks.task.get.json"
    payload = {"taskId": task_id, "select": ["ID", "TITLE", "DESCRIPTION", "CREATED_BY", "TAGS", "RESPONSIBLE_ID"]}
    async with ClientSession() as session:
        async with session.post(url, json=payload, timeout=15) as resp:
            data = await resp.json()
    if "error" in data:
        print(f"❌ Ошибка API: {data}")
        return
    task = data.get("result", {}).get("task", {})
    print("=== Данные созданной задачи ===")
    print(f"ID: {task.get('id')}")
    print(f"TITLE: {task.get('title')}")
    print(f"DESCRIPTION: {task.get('description')}")
    print(f"CREATED_BY: {task.get('createdBy')}")
    print(f"TAGS: {task.get('tags')}")
    print(f"RESPONSIBLE_ID: {task.get('responsibleId')}")

async def main():
    print("Пытаемся создать тестовую задачу в Битрикс24 с новыми параметрами...")
    task_id = await create_task(
        title="Тестовая задача от бота (BBCode + TAGS + CREATED_BY)",
        description="<b>Заявка #999</b>\n<b>Компания/Город:</b> Тест\n<b>Станок:</b> Тестовый станок\n<b>Проблема:</b> Проверка форматирования",
        responsible_id=82,
    )
    if task_id:
        print(f"✅ Задача успешно создана! ID: {task_id}")
        await check_task(task_id)
    else:
        print("❌ Не удалось создать задачу. Проверьте логи выше.")

asyncio.run(main())