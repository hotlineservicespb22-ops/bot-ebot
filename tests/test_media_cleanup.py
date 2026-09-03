"""Тесты для очистки локальных медиафайлов давно закрытых заявок (MEDIA_RETENTION_DAYS)."""
import datetime

import pytest

import bot.main as bot_main
from bot.database import Database

pytestmark = pytest.mark.asyncio


async def _closed_ticket(db: Database, client_id: int, status: str, days_ago: int) -> int:
    tid = await db.create_ticket(
        client_id=client_id, client_name="Клиент", company="", equipment_type="",
        brand="", cnc_model="", problem="Проблема", media_id=None, city="",
        inn_contract="", contact="12345",
    )
    await db.close_ticket(tid, status)
    closed_at = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)
    ).isoformat()
    await db.conn.execute("UPDATE tickets SET closed_at = ? WHERE id = ?", (closed_at, tid))
    await db.conn.commit()
    return tid


class TestGetTicketIdsForMediaCleanup:
    async def test_returns_old_closed_tickets(self, db: Database):
        old = await _closed_ticket(db, 1, "completed", days_ago=40)
        ids = await db.get_ticket_ids_for_media_cleanup(retention_days=30)
        assert old in ids

    async def test_excludes_recently_closed_tickets(self, db: Database):
        recent = await _closed_ticket(db, 1, "completed", days_ago=5)
        ids = await db.get_ticket_ids_for_media_cleanup(retention_days=30)
        assert recent not in ids

    async def test_excludes_open_and_in_progress_tickets(self, db: Database):
        open_tid = await db.create_ticket(
            client_id=1, client_name="Клиент", company="", equipment_type="",
            brand="", cnc_model="", problem="Проблема", media_id=None, city="",
            inn_contract="", contact="12345",
        )
        ids = await db.get_ticket_ids_for_media_cleanup(retention_days=0)
        assert open_tid not in ids

    async def test_includes_canceled_tickets(self, db: Database):
        canceled = await _closed_ticket(db, 1, "canceled", days_ago=40)
        ids = await db.get_ticket_ids_for_media_cleanup(retention_days=30)
        assert canceled in ids


class TestRunMediaCleanup:
    async def test_disabled_by_default_does_nothing(self, db: Database, tmp_path, monkeypatch):
        """MEDIA_RETENTION_DAYS=0 (значение по умолчанию) — очистка не запускается."""
        tid = await _closed_ticket(db, 1, "completed", days_ago=100)
        ticket_dir = tmp_path / f"ticket_{tid}"
        ticket_dir.mkdir()
        (ticket_dir / "photo.jpg").write_bytes(b"fake")

        monkeypatch.setattr(bot_main, "MEDIA_RETENTION_DAYS", 0)
        monkeypatch.setattr(bot_main, "MEDIA_DIR", str(tmp_path))

        await bot_main.run_media_cleanup(db)

        assert ticket_dir.exists()

    async def test_deletes_media_for_old_closed_ticket(self, db: Database, tmp_path, monkeypatch):
        tid = await _closed_ticket(db, 1, "completed", days_ago=100)
        ticket_dir = tmp_path / f"ticket_{tid}"
        ticket_dir.mkdir()
        (ticket_dir / "photo.jpg").write_bytes(b"fake")

        monkeypatch.setattr(bot_main, "MEDIA_RETENTION_DAYS", 30)
        monkeypatch.setattr(bot_main, "MEDIA_DIR", str(tmp_path))

        await bot_main.run_media_cleanup(db)

        assert not ticket_dir.exists()

    async def test_keeps_media_for_recently_closed_ticket(self, db: Database, tmp_path, monkeypatch):
        tid = await _closed_ticket(db, 1, "completed", days_ago=2)
        ticket_dir = tmp_path / f"ticket_{tid}"
        ticket_dir.mkdir()
        (ticket_dir / "photo.jpg").write_bytes(b"fake")

        monkeypatch.setattr(bot_main, "MEDIA_RETENTION_DAYS", 30)
        monkeypatch.setattr(bot_main, "MEDIA_DIR", str(tmp_path))

        await bot_main.run_media_cleanup(db)

        assert ticket_dir.exists()

    async def test_missing_directory_does_not_crash(self, db: Database, tmp_path, monkeypatch):
        """Заявка без медиа (каталог никогда не создавался) — не должно быть исключения."""
        await _closed_ticket(db, 1, "completed", days_ago=100)

        monkeypatch.setattr(bot_main, "MEDIA_RETENTION_DAYS", 30)
        monkeypatch.setattr(bot_main, "MEDIA_DIR", str(tmp_path))

        await bot_main.run_media_cleanup(db)  # не должно бросить исключение

    async def test_deletion_failure_logged_not_raised(self, db: Database, tmp_path, monkeypatch, caplog):
        """Ошибка удаления (например, нет прав) логируется, но не прерывает обработку остальных заявок."""
        t1 = await _closed_ticket(db, 1, "completed", days_ago=100)
        t2 = await _closed_ticket(db, 2, "completed", days_ago=100)
        for tid in (t1, t2):
            d = tmp_path / f"ticket_{tid}"
            d.mkdir()
            (d / "f.jpg").write_bytes(b"fake")

        monkeypatch.setattr(bot_main, "MEDIA_RETENTION_DAYS", 30)
        monkeypatch.setattr(bot_main, "MEDIA_DIR", str(tmp_path))

        original_rmtree = bot_main.shutil.rmtree

        def _boom(path):
            if str(path).endswith(f"ticket_{t1}"):
                raise OSError("permission denied")
            original_rmtree(path)

        monkeypatch.setattr(bot_main.shutil, "rmtree", _boom)

        with caplog.at_level("ERROR"):
            await bot_main.run_media_cleanup(db)

        assert (tmp_path / f"ticket_{t1}").exists()  # не удалён из-за ошибки
        assert not (tmp_path / f"ticket_{t2}").exists()  # второй всё же удалён
        assert any(str(t1) in r.message for r in caplog.records)
