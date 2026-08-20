"""Тесты для фичи «учёт времени сессии инженера» (session_started_at / session_seconds)."""
import datetime

import pytest

from bot.database import Database, _elapsed_seconds


def test_elapsed_seconds_handles_invalid_and_empty():
    """_elapsed_seconds не падает и возвращает 0 для пустых/некорректных timestamp."""
    now = datetime.datetime.now(datetime.timezone.utc)
    assert _elapsed_seconds(None, now) == 0
    assert _elapsed_seconds("", now) == 0
    assert _elapsed_seconds("not-a-date", now) == 0
    assert _elapsed_seconds((now - datetime.timedelta(seconds=5)).isoformat(), now) == 5


async def _set_session_start(db: Database, ticket_id: int, seconds_ago: int) -> None:
    ts = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(seconds=seconds_ago)
    ).isoformat()
    await db.conn.execute(
        "UPDATE tickets SET session_started_at = ? WHERE id = ?", (ts, ticket_id)
    )
    await db.conn.commit()


async def _create(db: Database, client_id: int = 1) -> int:
    return await db.create_ticket(
        client_id=client_id, client_name="Клиент", company="", equipment_type="",
        brand="", cnc_model="", problem="Проблема", media_id=None, city="",
        inn_contract="", contact="12345",
    )


@pytest.mark.asyncio
async def test_take_sets_session_started_at(db):
    tid = await _create(db)
    await db.add_engineer(999, "Инженер")
    assert await db.take_ticket(tid, 999) is True
    ticket = await db.get_ticket(tid)
    assert ticket["session_started_at"] is not None


@pytest.mark.asyncio
async def test_close_accumulates_session_seconds(db):
    tid = await _create(db)
    await db.add_engineer(999, "Инженер")
    await db.take_ticket(tid, 999)
    await _set_session_start(db, tid, 120)
    await db.close_ticket(tid, "completed")
    ticket = await db.get_ticket(tid)
    assert 110 <= ticket["session_seconds"] <= 130


@pytest.mark.asyncio
async def test_cancel_immediately_seconds_not_negative(db):
    """Заявка отменена сразу после взятия: время близко к 0 и не отрицательное."""
    tid = await _create(db)
    await db.add_engineer(999, "Инженер")
    await db.take_ticket(tid, 999)
    await db.close_ticket(tid, "canceled")
    ticket = await db.get_ticket(tid)
    assert ticket["session_seconds"] is not None
    assert ticket["session_seconds"] >= 0
    assert ticket["session_seconds"] < 10


@pytest.mark.asyncio
async def test_switch_tickets_sums_intervals(db):
    """Взял A -> переключился на B -> вернулся к A: интервалы суммируются, не перезаписываются."""
    a_id = await _create(db, client_id=1)
    b_id = await _create(db, client_id=2)
    await db.add_engineer(999, "Инженер")
    await db.take_ticket(a_id, 999)
    await _set_session_start(db, a_id, 60)

    # Переключение на B: 60 секунд по A фиксируются
    await db.take_ticket(b_id, 999)
    a = await db.get_ticket(a_id)
    assert a["session_started_at"] is None
    assert 55 <= a["session_seconds"] <= 65

    # Возврат к A (resume) и завершение: +30 секунд суммируются
    await db.resume_session(a_id)
    await _set_session_start(db, a_id, 30)
    await db.close_ticket(a_id, "completed")
    a = await db.get_ticket(a_id)
    assert 85 <= a["session_seconds"] <= 95


@pytest.mark.asyncio
async def test_restart_resets_stale_session(db):
    """После рестарта «зависший» session_started_at не должен считаться рабочим временем."""
    tid = await _create(db)
    await db.add_engineer(999, "Инженер")
    await db.take_ticket(tid, 999)
    await _set_session_start(db, tid, 10 * 3600)  # «завис» 10 часов назад
    await db.reset_stale_sessions()
    await db.close_ticket(tid, "completed")
    ticket = await db.get_ticket(tid)
    assert ticket["session_seconds"] < 10


@pytest.mark.asyncio
async def test_concurrent_close_does_not_double_count(db):
    """Два конкурентных закрытия одной заявки не задваивают время."""
    tid = await _create(db)
    await db.add_engineer(999, "Инженер")
    await db.take_ticket(tid, 999)
    await _set_session_start(db, tid, 50)
    ok_first = await db.close_ticket(tid, "completed")
    ok_second = await db.close_ticket(tid, "completed")
    assert ok_first is True
    assert ok_second is False
    ticket = await db.get_ticket(tid)
    assert 45 <= ticket["session_seconds"] <= 55


@pytest.mark.asyncio
async def test_engineer_session_stats(db):
    await db.add_engineer(999, "Инженер")
    tid = await _create(db)
    await db.take_ticket(tid, 999)
    await _set_session_start(db, tid, 60)
    await db.close_ticket(tid, "completed")
    stats = await db.get_engineer_session_stats()
    eng = [s for s in stats if s["user_id"] == 999][0]
    assert 55 <= eng["total_seconds"] <= 65