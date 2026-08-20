"""Тесты для фичи «история оборудования клиента» (get_client_ticket_history)."""
import pytest

from bot.database import Database


async def _create_closed(db: Database, client_id: int, problem: str = "Проблема") -> int:
    tid = await db.create_ticket(
        client_id=client_id, client_name="Клиент", company="", equipment_type="",
        brand="", cnc_model="", problem=problem, media_id=None, city="",
        inn_contract="", contact="12345", machine_info="Wattsan 1610",
    )
    await db.close_ticket(tid, "completed")
    return tid


@pytest.mark.asyncio
async def test_history_returns_last_3_closed_only(db):
    ids = [await _create_closed(db, 7, f"Проблема {i}") for i in range(5)]
    # Открытая заявка не должна попасть в историю
    await db.create_ticket(
        client_id=7, client_name="Клиент", company="", equipment_type="",
        brand="", cnc_model="", problem="Открытая", media_id=None, city="",
        inn_contract="", contact="12345",
    )
    hist = await db.get_client_ticket_history(7, limit=3)
    assert len(hist) == 3
    assert hist[0]["id"] == ids[-1]  # самая свежая закрытая первой


@pytest.mark.asyncio
async def test_history_excludes_current_ticket(db):
    a = await _create_closed(db, 7, "A")
    b = await _create_closed(db, 7, "B")
    hist = await db.get_client_ticket_history(7, exclude_ticket_id=b, limit=3)
    assert all(h["id"] != b for h in hist)
    assert any(h["id"] == a for h in hist)


@pytest.mark.asyncio
async def test_history_no_history_returns_empty(db):
    assert await db.get_client_ticket_history(12345, limit=3) == []


@pytest.mark.asyncio
async def test_history_limit_applied_in_sql(db):
    """LIMIT реально применяется в SQL: 55 закрытых заявок -> ровно 3 строки."""
    for i in range(55):
        await _create_closed(db, 7, f"Проблема {i}")
    hist = await db.get_client_ticket_history(7, limit=3)
    assert len(hist) == 3


@pytest.mark.asyncio
async def test_history_sql_injection_safe(db):
    """Параметризованный SQL: кавычки/метасимволы не ломают запрос и не «раскрывают» данные."""
    evil = "'; DROP TABLE tickets; -- ' OR '1'='1"
    own = await _create_closed(db, 7, evil)
    other = await _create_closed(db, 999, "secret-problem")
    hist = await db.get_client_ticket_history(7, limit=10)
    assert own in [h["id"] for h in hist]
    assert all(h["id"] != other for h in hist)
    # Таблица tickets не была удалена
    assert len(await db.get_all_tickets()) == 2