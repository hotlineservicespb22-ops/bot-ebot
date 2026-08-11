import asyncio
import logging
from bot.bitrix import send_message_to_chat

# Настраиваем логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger('bot.bitrix').setLevel(logging.DEBUG)

async def main():
    print("=== Тест отправки сообщения в чат Битрикс24 ===\n")
    test_msg = (
        "🚨 [B]Тестовое уведомление[/B]\n\n"
        "Это проверка отправки сообщений в чат Битрикс24 из бота.\n"
        "Если вы видите это сообщение — интеграция работает!"
    )
    ok = await send_message_to_chat(test_msg)
    if ok:
        print("\n✅ Сообщение успешно отправлено в чат!")
    else:
        print("\n❌ Не удалось отправить сообщение. Проверьте логи выше.")

asyncio.run(main())