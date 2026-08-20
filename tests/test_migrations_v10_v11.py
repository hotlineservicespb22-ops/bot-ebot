"""Тесты миграций v10/v11: идемпотентность и сохранность данных."""
import pytest

from bot.migrations import _migration_v10_followup, _migration_v11_session_time


async def _columns(db, table: str = "tickets") -> set:
    cursor = await db.conn.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    return {r["name"] for r in rows}


@pytest.mark.asyncio
async def test_v10_v11_columns_present_idempotent_and_preserve_data(db):
    # Фикстура `db` уже применила init_db + все миграции (включая v10/v11).
    # Вставляем данные, как будто они существовали до новых миграций.
    tid = await db.create_ticket(
        client_id=1, client_name="Клиент", company="", equipment_type="",
        brand="", cnc_model="", problem="Проблема", media_id=None, city="",
        inn_contract="", contact="12345",
    )
    await db.save_message(tid, 1, "client", "привет")
    await db.save_rating(tid, 1, 5)

    # Повторно применяем v10/v11 — идемпотентно (не падает, колонки не дублируются).
    await _migration_v10_followup(db.conn)
    await _migration_v11_session_time(db.conn)
    await db.conn.commit()

    cols = await _columns(db)
    assert "followup_sent_at" in cols
    assert "related_ticket_id" in cols
    assert "session_started_at" in cols
    assert "session_seconds" in cols
    assert "first_taken_at" in cols

    # Данные не потеряны
    ticket = await db.get_ticket(tid)
    assert ticket["client_id"] == 1
    assert ticket["status"] == "open"
    assert len(await db.get_messages_for_ticket(tid)) == 1
    assert (await db.get_rating_for_ticket(tid))["rating"] == 5