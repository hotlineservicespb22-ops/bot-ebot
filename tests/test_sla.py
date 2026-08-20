"""Тесты для фичи SLA (время первого ответа, % заявок, взятых в срок)."""
import datetime

import pytest

from bot.database import Database

_BASE = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)


async def _make_taken(db: Database, minutes: int) -> int:
    """Создаёт заявку и явно задаёт created_at / session_started_at (взята в работу)."""
    tid = await db.create_ticket(
        client_id=1, client_name="Клиент", company="", equipment_type="",
        brand="", cnc_model="", problem="Проблема", media_id=None, city="",
        inn_contract="", contact="12345",
    )
    created = _BASE.isoformat()
    session = (_BASE + datetime.timedelta(minutes=minutes)).isoformat()
    await db.conn.execute(
        "UPDATE tickets SET created_at = ?, first_taken_at = ?, status = 'in_progress', engineer_id = 999 WHERE id = ?",
        (created, session, tid),
    )
    await db.conn.commit()
    return tid


@pytest.mark.asyncio
async def test_sla_boundary_inclusive(db):
    """Взята ровно на границе SLA_MINUTES — засчитывается «в SLA» (включительно)."""
    await db.add_engineer(999, "Инженер")
    await _make_taken(db, minutes=15)
    s = await db.get_sla_stats(15)
    assert s["taken_total"] == 1
    assert s["taken_in_sla"] == 1


@pytest.mark.asyncio
async def test_sla_over_boundary_not_in(db):
    await db.add_engineer(999, "Инженер")
    await _make_taken(db, minutes=16)
    s = await db.get_sla_stats(15)
    assert s["taken_total"] == 1
    assert s["taken_in_sla"] == 0


@pytest.mark.asyncio
async def test_never_taken_excluded_from_denominator(db):
    """Заявка, которую так и не взяли, исключается из знаменателя — не ломает процент."""
    await db.create_ticket(
        client_id=1, client_name="Клиент", company="", equipment_type="",
        brand="", cnc_model="", problem="Проблема", media_id=None, city="",
        inn_contract="", contact="12345",
    )
    s = await db.get_sla_stats(15)
    assert s["taken_total"] == 0
    assert s["in_sla_pct"] == 0


@pytest.mark.asyncio
async def test_sla_percent_calculation(db):
    await db.add_engineer(999, "Инженер")
    await _make_taken(db, minutes=10)  # в SLA
    await _make_taken(db, minutes=20)  # вне SLA
    s = await db.get_sla_stats(15)
    assert s["taken_total"] == 2
    assert s["taken_in_sla"] == 1
    assert s["in_sla_pct"] == 50


@pytest.mark.asyncio
async def test_dashboard_data_contains_new_metrics(db):
    """get_dashboard_data отдаёт новые виджеты: SLA, время сессии, follow-up."""
    await db.add_engineer(999, "Инженер")
    tid = await _make_taken(db, minutes=10)
    # Завершаем, чтобы появились session_seconds
    await db.close_ticket(tid, "completed")

    data = await db.get_dashboard_data(sla_minutes=15)
    assert data["sla_stats"]["taken_total"] == 1
    assert "in_sla_pct" in data["sla_stats"]
    assert data["session_stats"]["total_seconds"] >= 0
    assert data["followup_stats"]["sent_week"] == 0
    assert isinstance(data["engineer_sessions"], list)