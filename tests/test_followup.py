"""Тесты для фичи «повторный опрос клиента» (follow-up)."""
import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.database import Database
from bot.handlers.client import followup_callback_handler
from bot.keyboards import FollowupCallback
from bot.main import run_followup_check


async def _completed_ticket(db: Database, client_id: int, days_ago: int | None = None) -> int:
    tid = await db.create_ticket(
        client_id=client_id, client_name="Клиент", company="", equipment_type="",
        brand="", cnc_model="", problem="Проблема", media_id=None, city="",
        inn_contract="", contact="12345",
    )
    await db.close_ticket(tid, "completed")
    if days_ago is not None:
        ts = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=days_ago)
        ).isoformat()
        await db.conn.execute("UPDATE tickets SET closed_at = ? WHERE id = ?", (ts, tid))
        await db.conn.commit()
    return tid


@pytest.mark.asyncio
async def test_followup_returns_only_old_completed(db):
    old = await _completed_ticket(db, 1, days_ago=10)
    recent = await _completed_ticket(db, 2, days_ago=0)
    ids = [t["id"] for t in await db.get_tickets_for_followup(7)]
    assert old in ids
    assert recent not in ids


@pytest.mark.asyncio
async def test_mark_followup_sent_excludes(db):
    tid = await _completed_ticket(db, 1, days_ago=10)
    await db.mark_followup_sent(tid)
    assert tid not in [t["id"] for t in await db.get_tickets_for_followup(7)]


@pytest.mark.asyncio
async def test_related_ticket_not_requeried(db):
    """Дочерняя заявка (related_ticket_id) не должна повторно опрашиваться."""
    parent = await _completed_ticket(db, 1, days_ago=10)
    child = await db.create_ticket(
        client_id=1, client_name="Клиент", company="", equipment_type="",
        brand="", cnc_model="", problem="Проблема", media_id=None, city="",
        inn_contract="", contact="12345", related_ticket_id=parent,
    )
    await db.close_ticket(child, "completed")
    ts = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)
    ).isoformat()
    await db.conn.execute("UPDATE tickets SET closed_at = ? WHERE id = ?", (ts, child))
    await db.conn.commit()
    assert child not in [t["id"] for t in await db.get_tickets_for_followup(7)]


@pytest.mark.asyncio
async def test_two_tickets_same_day_both_returned(db):
    a = await _completed_ticket(db, 1, days_ago=10)
    b = await _completed_ticket(db, 1, days_ago=10)
    ids = [t["id"] for t in await db.get_tickets_for_followup(7)]
    assert a in ids and b in ids


@pytest.mark.asyncio
async def test_followup_send_failure_still_marks_and_logs(db, caplog):
    """При сбое отправки факт фиксируется ДО send_message (нет вечного retry),
    а ошибка видимо логируется на ERROR."""
    tid = await _completed_ticket(db, 1, days_ago=10)
    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=Exception("user blocked the bot"))
    with caplog.at_level("ERROR"):
        await run_followup_check(bot, db)
    ticket = await db.get_ticket(tid)
    assert ticket["followup_sent_at"] is not None
    assert any("Не удалось отправить повторный опрос" in r.message for r in caplog.records)

    # Повторный проход не дублирует (идемпотентность при перезапуске).
    await run_followup_check(bot, db)
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_followup_problem_creates_related_ticket(db):
    tid = await _completed_ticket(db, 1, days_ago=0)
    callback = AsyncMock()
    callback.message = MagicMock()
    callback.message.text = "опрос"
    callback.message.edit_text = AsyncMock()
    callback_data = FollowupCallback(action="problem", ticket_id=tid)
    await followup_callback_handler(callback, callback_data, db, AsyncMock())
    tickets = await db.get_all_tickets()
    related = [t for t in tickets if t["related_ticket_id"] == tid]
    assert len(related) == 1
    assert related[0]["client_id"] == 1