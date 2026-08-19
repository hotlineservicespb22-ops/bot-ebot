import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject

from bot.database import Database


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, db: Database):
        super().__init__()
        self.db = db

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any]
    ) -> Any:
        data["db"] = self.db
        return await handler(event, data)

class RoleMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        db: Database = data["db"]
        is_in_engineers_table = await db.is_engineer(user.id)
        has_active = await db.has_active_tickets(user.id)
        data["is_admin"] = await db.is_admin(user.id)
        # Инженер — это либо действующий инженер из таблицы engineers (is_active=1),
        # либо бывший инженер, у которого остались активные заявки в работе.
        data["is_engineer"] = is_in_engineers_table or has_active
        # Руководитель — имеет доступ к дашборду проблемных заявок
        data["is_manager"] = await db.is_manager(user.id)
        
        return await handler(event, data)


class ThrottlingMiddleware(BaseMiddleware):
    """
    Ограничивает частоту callback-запросов (защита от двойных нажатий кнопок).
    Текстовые сообщения НЕ троттлятся, чтобы не терять переписку клиент↔инженер.
    """
    def __init__(self, interval: float = 0.5, ttl: float = 3600.0):
        super().__init__()
        self.interval = interval
        # TTL (секунды): записи старше этого значения считаются устаревшими
        # и удаляются, чтобы словарь не рос бесконечно.
        self.ttl = ttl
        # user_id -> (последнее время, время вставки)
        self.last_time: dict[int, tuple[float, float]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        # Троттлим только callback-запросы (кнопки), чтобы не потерять сообщения диалога
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        user_id = user.id
        now = time.monotonic()

        # Периодическая очистка устаревших записей (когда словарь слишком разросся)
        if len(self.last_time) > 1000:
            stale = [uid for uid, (_, inserted) in self.last_time.items() if now - inserted > self.ttl]
            for uid in stale:
                self.last_time.pop(uid, None)

        last = self.last_time.get(user_id, (0.0, 0.0))[0]
        if now - last < self.interval:
            # Слишком часто — игнорируем повторное нажатие кнопки
            return
        self.last_time[user_id] = (now, now)
        return await handler(event, data)
