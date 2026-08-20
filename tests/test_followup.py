"""Тесты для фичи «повторный опрос клиента» (follow-up)."""
import datetime
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot.main as bot_main
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


@pytest.mark.asyncio
async def test_followup_ok_branch(db):
    """Ветка «ok»: followup_sent_at не меняется, клиент видит подтверждение, связанная заявка не создаётся."""
    tid = await _completed_ticket(db, 1, days_ago=0)
    await db.mark_followup_sent(tid)
    before = (await db.get_ticket(tid))["followup_sent_at"]
    assert before is not None

    callback = AsyncMock()
    callback.message = MagicMock()
    callback.message.text = "опрос"
    callback.message.edit_text = AsyncMock()

    await followup_callback_handler(
        callback, FollowupCallback(action="ok", ticket_id=tid), db, AsyncMock()
    )

    # Клиент видит toast-подтверждение, а сообщение дополняется.
    callback.answer.assert_awaited_once_with("Спасибо за ответ! Рады, что всё в порядке. ✅")
    callback.message.edit_text.assert_awaited_once()

    # followup_sent_at остался прежним (его проставляет watcher, а не ветка «ok»).
    assert (await db.get_ticket(tid))["followup_sent_at"] == before

    # Новая (связанная) заявка не создавалась.
    related = [t for t in await db.get_all_tickets() if t["related_ticket_id"] == tid]
    assert related == []


@pytest.mark.asyncio
async def test_followup_problem_notifies_all_engineers_even_if_one_fails(db, caplog):
    """В ветке «problem» рассылка идёт всем инженерам; падение одного не прерывает
    остальных и не проглатывается молча — логируется ERROR с engineer_id и ticket_id."""
    tid = await _completed_ticket(db, 1, days_ago=0)
    await db.add_engineer(101, "Инженер 1")
    await db.add_engineer(102, "Инженер 2")  # этот «заблокировал бота»
    await db.add_engineer(103, "Инженер 3")

    sent_to = set()

    def _send(chat_id, *args, **kwargs):
        if chat_id == 102:
            raise Exception("user blocked the bot")
        sent_to.add(chat_id)
        return MagicMock(message_id=chat_id)

    bot = AsyncMock()
    bot.send_message = AsyncMock(side_effect=_send)

    callback = AsyncMock()
    callback.message = MagicMock()
    callback.message.text = "опрос"
    callback.message.edit_text = AsyncMock()

    with caplog.at_level("ERROR"):
        await followup_callback_handler(
            callback, FollowupCallback(action="problem", ticket_id=tid), db, bot
        )

    # Попытка отправки была всем трём инженерам.
    assert bot.send_message.await_count == 3
    # Падение инженера 102 не помешало уведомить 101 и 103.
    assert sent_to == {101, 103}

    # Связанная заявка создана; её номер должен фигурировать в логе ошибки.
    related = [t for t in await db.get_all_tickets() if t["related_ticket_id"] == tid]
    assert len(related) == 1
    new_id = related[0]["id"]

    errors = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(
        "Не удалось уведомить инженера 102" in m and f"#{new_id}" in m
        for m in errors
    )


@pytest.mark.asyncio
async def test_followup_watcher_survives_iteration_exception(monkeypatch, caplog):
    """Необработанное исключение внутри итерации цикла не убивает таск:
    итерация N+1 всё равно выполняется, а ошибка логируется на ERROR."""
    calls = {"n": 0}

    async def flaky_run(bot, db):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("БД временно недоступна")

    monkeypatch.setattr(bot_main, "run_followup_check", flaky_run)

    class _BreakLoop(Exception):
        pass

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise _BreakLoop()

    monkeypatch.setattr(bot_main.asyncio, "sleep", fake_sleep)

    with caplog.at_level("ERROR"):
        with pytest.raises(_BreakLoop):
            await bot_main.followup_watcher(AsyncMock(), AsyncMock())

    # Итерация 1 упала (RuntimeError), итерация 2 всё равно выполнилась.
    assert calls["n"] >= 2
    # Сбой 1-й итерации залогирован на ERROR (не проглочен молча).
    assert any("Ошибка в фоновой задаче followup-опроса" in r.message for r in caplog.records)