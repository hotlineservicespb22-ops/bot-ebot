"""
Тесты для веб-дашборда руководителя (bot/web_dashboard.py).

Покрывает аутентификацию (_check_auth): заголовок X-Dashboard-Key как основной
способ, fallback на query-параметр ?key= и constant-time сравнение через hmac.
А также JSON-эндпоинт /api/dashboard-data (фича 5).
"""
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

import bot.web_dashboard as wd


def _request(headers=None, query=None):
    """Создаёт минимальный мок aiohttp-запроса с plain-dict headers/query."""
    req = MagicMock()
    req.headers = headers or {}
    req.query = query or {}
    return req


class TestCheckAuth:
    def test_open_when_no_key_configured(self, monkeypatch):
        monkeypatch.setattr(wd, "MANAGER_DASHBOARD_KEY", "")
        assert wd._check_auth(_request()) is True

    def test_accepts_valid_header(self, monkeypatch):
        monkeypatch.setattr(wd, "MANAGER_DASHBOARD_KEY", "secret-key")
        req = _request(headers={"X-Dashboard-Key": "secret-key"})
        assert wd._check_auth(req) is True

    def test_accepts_valid_query_fallback(self, monkeypatch):
        monkeypatch.setattr(wd, "MANAGER_DASHBOARD_KEY", "secret-key")
        req = _request(query={"key": "secret-key"})
        assert wd._check_auth(req) is True

    def test_header_takes_precedence_over_bad_query(self, monkeypatch):
        monkeypatch.setattr(wd, "MANAGER_DASHBOARD_KEY", "secret-key")
        req = _request(
            headers={"X-Dashboard-Key": "secret-key"},
            query={"key": "wrong"},
        )
        assert wd._check_auth(req) is True

    def test_rejects_wrong_key(self, monkeypatch):
        monkeypatch.setattr(wd, "MANAGER_DASHBOARD_KEY", "secret-key")
        req = _request(headers={"X-Dashboard-Key": "wrong"})
        assert wd._check_auth(req) is False

    def test_rejects_missing_key(self, monkeypatch):
        monkeypatch.setattr(wd, "MANAGER_DASHBOARD_KEY", "secret-key")
        assert wd._check_auth(_request()) is False


class TestDashboardDataEndpoint:
    """Тесты JSON-эндпоинта /api/dashboard-data (фича 5: живой дашборд)."""

    @pytest.mark.asyncio
    async def test_403_without_key(self, db, monkeypatch):
        monkeypatch.setattr(wd, "MANAGER_DASHBOARD_KEY", "secret-key")
        app = wd.create_app(db)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/dashboard-data")
            assert resp.status == 403
            # В теле ответа нет утечки данных
            assert await resp.text() == "Forbidden"

    @pytest.mark.asyncio
    async def test_403_with_wrong_key(self, db, monkeypatch):
        monkeypatch.setattr(wd, "MANAGER_DASHBOARD_KEY", "secret-key")
        app = wd.create_app(db)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/dashboard-data?key=wrong")
            assert resp.status == 403

    @pytest.mark.asyncio
    async def test_200_with_valid_key(self, db, monkeypatch):
        monkeypatch.setattr(wd, "MANAGER_DASHBOARD_KEY", "secret-key")
        app = wd.create_app(db)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/dashboard-data?key=secret-key")
            assert resp.status == 200
            data = await resp.json()
            assert "sla_stats" in data
            assert "session_stats" in data
            assert "followup_stats" in data