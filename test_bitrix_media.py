import asyncio
import os
import time
from aiohttp import ClientSession
from bot.bitrix import create_task, upload_file_to_bitrix
from bot.config import BITRIX_WEBHOOK_URL

TEST_FILE_PATH = f"media/test_upload_{int(time.time())}.jpg"


async def create_test_file():
    """Создаёт тестовый файл для проверки загрузки."""
    os.makedirs("media", exist_ok=True)
    with open(TEST_FILE_PATH, "wb") as f:
        # Минимальный валидный JPEG (1x1 пиксель)
        f.write(bytes([
            0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
            0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
            0x00, 0xFF, 0xD9
        ]))
    print(f"Создан тестовый файл: {TEST_FILE_PATH}, размер: {os.path.getsize(TEST_FILE_PATH)} байт")


async def check_task_files(task_id: int):
    """Проверяет, что к задаче прикреплены файлы."""
    if not BITRIX_WEBHOOK_URL:
        print("❌ BITRIX_WEBHOOK_URL не задан")
        return
    url = f"{BITRIX_WEBHOOK_URL.rstrip('/')}/tasks.task.get.json"
    payload = {"taskId": task_id, "select": ["ID", "TITLE", "UF_TASK_WEBDAV_FILES"]}
    async with ClientSession() as session:
        async with session.post(url, json=payload, timeout=15) as resp:
            data = await resp.json()
    if "error" in data:
        print(f"❌ Ошибка API: {data}")
        return
    task = data.get("result", {}).get("task", {})
    print(f"ID: {task.get('id')}")
    print(f"TITLE: {task.get('title')}")
    print(f"UF_TASK_WEBDAV_FILES: {task.get('ufTaskWebdavFiles')}")


async def main():
    print("=== Тест: загрузка файла в Битрикс24 и прикрепление к задаче ===")
    await create_test_file()

    print("\n1. Загружаем файл на диск Битрикс24...")
    file_id = await upload_file_to_bitrix(TEST_FILE_PATH)
    if not file_id:
        print("❌ Не удалось загрузить файл в Битрикс24")
        return
    print(f"✅ Файл загружен, ID: {file_id}")

    print("\n2. Создаём задачу с прикреплённым файлом...")
    task_id = await create_task(
        title="Тестовая задача с медиафайлом",
        description="<b>Заявка #TEST</b>\nПроверка прикрепления медиафайла к задаче.",
        responsible_id=82,
        uf_files=[file_id],
    )
    if not task_id:
        print("❌ Не удалось создать задачу")
        return
    print(f"✅ Задача создана, ID: {task_id}")

    print("\n3. Проверяем прикреплённые файлы...")
    await check_task_files(task_id)

    # Удаляем тестовый файл
    if os.path.exists(TEST_FILE_PATH):
        os.remove(TEST_FILE_PATH)
        print(f"\nТестовый файл {TEST_FILE_PATH} удалён")


asyncio.run(main())