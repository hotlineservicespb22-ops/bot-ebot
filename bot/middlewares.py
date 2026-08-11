import time
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, CallbackQuery
from bot.database import Database

class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, db: Database):
        super().__init__()
        self.db = db

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        data["db"] = self.db
        return await handler(event, data)

class RoleMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        db: Database = data["db"]
        data["is_admin"] = await db.is_admin(user.id)
        data["is_engineer"] = await db.is_engineer(user.id)
        
        return await handler(event, data)


class ThrottlingMiddleware(BaseMiddleware):
    """
    Ограничивает частоту callback-запросов (защита от двойных нажатий кнопок).
    Текстовые сообщения НЕ троттлятся, чтобы не терять переписку клиент↔инженер.
    """
    def __init__(self, interval: float = 0.5):
        super().__init__()
        self.interval = interval
        self.last_time: Dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        # Троттлим только callback-запросы (кнопки), чтобы не потерять сообщения диалога
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        user_id = user.id
        now = time.monotonic()
        last = self.last_time.get(user_id, 0.0)
        if now - last < self.interval:
            # Слишком часто — игнорируем повторное нажатие кнопки
            return
        self.last_time[user_id] = now
        return await handler(event, data)
