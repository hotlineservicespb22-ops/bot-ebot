"""
Тесты для конфигурации логирования (bot/logging_config.py).
"""
import contextlib
import logging
import sys

import pytest

import bot.logging_config as lc


@pytest.fixture(autouse=True)
def _reset_logging():
    """Сбрасываем флаг конфигурации и удаляем наши обработчики до/после теста."""
    old = lc._logging_configured
    lc._logging_configured = False
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    yield
    lc._logging_configured = old
    # Убираем обработчики, добавленные setup_logging (RotatingFile на тестовый лог)
    root = logging.getLogger()
    for h in list(root.handlers):
        if h not in handlers_before:
            root.removeHandler(h)
            with contextlib.suppress(Exception):
                h.close()


class TestSetupLogging:
    def test_sets_root_level(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(lc, "LOG_LEVEL", "WARNING")
            mp.setattr(lc, "LOG_FILE", "test_bot.log")
            lc.setup_logging()
            assert logging.getLogger().level == logging.WARNING

    def test_adds_handlers(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(lc, "LOG_LEVEL", "DEBUG")
            mp.setattr(lc, "LOG_FILE", "test_bot.log")
            root = logging.getLogger()
            initial_count = len(root.handlers)
            lc.setup_logging()
            assert len(root.handlers) > initial_count
            # Должен быть и консольный, и файловый (RotatingFile)
            from logging.handlers import RotatingFileHandler
            assert any(isinstance(h, RotatingFileHandler) for h in root.handlers)
            assert any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
                       for h in root.handlers)

    def test_idempotent(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(lc, "LOG_LEVEL", "INFO")
            mp.setattr(lc, "LOG_FILE", "test_bot.log")
            root = logging.getLogger()
            lc.setup_logging()
            count_after_first = len(root.handlers)
            lc.setup_logging()
            assert len(root.handlers) == count_after_first

    def test_invalid_level_defaults_to_info(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(lc, "LOG_LEVEL", "BOGUS")
            mp.setattr(lc, "LOG_FILE", "test_bot.log")
            lc.setup_logging()
            assert logging.getLogger().level == logging.INFO


class TestSanitizingFilter:
    """Tests for SanitizingFilter — masks sensitive data in logs."""

    def test_filter_added_to_root_logger(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(lc, "LOG_LEVEL", "INFO")
            mp.setattr(lc, "LOG_FILE", "test_bot.log")
            lc.setup_logging()
            root = logging.getLogger()
            assert any(isinstance(f, lc.SanitizingFilter) for f in root.filters)

    def test_masks_bot_token(self, monkeypatch):
        monkeypatch.setenv("BOT_TOKEN", "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz")
        # Reset class-level cache to pick up new env
        lc.SanitizingFilter._initialized = False
        lc.SanitizingFilter._patterns = None
        sf = lc.SanitizingFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Token: 1234567890:ABCdefGHIjklMNOpqrsTUVwxyz used",
            args=(), exc_info=None
        )
        sf.filter(record)
        assert "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz" not in record.msg
        assert "***1234***" in record.msg

    def test_masks_bitrix_webhook_url(self, monkeypatch):
        monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://crm.example.ru/rest/14/SECRET_TOKEN/")
        lc.SanitizingFilter._initialized = False
        lc.SanitizingFilter._patterns = None
        sf = lc.SanitizingFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Webhook: https://crm.example.ru/rest/14/SECRET_TOKEN/ called",
            args=(), exc_info=None
        )
        sf.filter(record)
        assert "SECRET_TOKEN" not in record.msg
        assert "***http***" in record.msg

    def test_masks_redis_url(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://:super_secret_pass@redis:6379/0")
        lc.SanitizingFilter._initialized = False
        lc.SanitizingFilter._patterns = None
        sf = lc.SanitizingFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Redis: redis://:super_secret_pass@redis:6379/0 connected",
            args=(), exc_info=None
        )
        sf.filter(record)
        assert "super_secret_pass" not in record.msg
        assert "***redi***" in record.msg

    def test_masks_multiple_secrets_in_one_message(self, monkeypatch):
        monkeypatch.setenv("BOT_TOKEN", "11111:tok1")
        monkeypatch.setenv("REDIS_URL", "redis://pass2@host")
        lc.SanitizingFilter._initialized = False
        lc.SanitizingFilter._patterns = None
        sf = lc.SanitizingFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Token 11111:tok1 and redis redis://pass2@host",
            args=(), exc_info=None
        )
        sf.filter(record)
        assert "11111:tok1" not in record.msg
        assert "pass2" not in record.msg
        assert "***1111***" in record.msg
        assert "***redi***" in record.msg

    def test_no_masking_when_env_empty(self, monkeypatch):
        monkeypatch.setenv("BOT_TOKEN", "")
        monkeypatch.setenv("BITRIX_WEBHOOK_URL", "")
        monkeypatch.setenv("REDIS_URL", "")
        monkeypatch.setenv("REDIS_PASSWORD", "")
        lc.SanitizingFilter._initialized = False
        lc.SanitizingFilter._patterns = None
        sf = lc.SanitizingFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Safe message without secrets",
            args=(), exc_info=None
        )
        sf.filter(record)
        assert record.msg == "Safe message without secrets"

    def test_short_token_masked(self, monkeypatch):
        monkeypatch.setenv("BOT_TOKEN", "abc")
        lc.SanitizingFilter._initialized = False
        lc.SanitizingFilter._patterns = None
        sf = lc.SanitizingFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Token abc used",
            args=(), exc_info=None
        )
        sf.filter(record)
        assert "abc" not in record.msg
        assert "***" in record.msg

    def test_idempotent_initialization(self):
        lc.SanitizingFilter._initialized = False
        lc.SanitizingFilter._patterns = None
        sf = lc.SanitizingFilter()
        sf._ensure_patterns()
        patterns_count = len(sf._patterns or [])
        # Second call should not change anything
        sf._ensure_patterns()
        assert len(sf._patterns or []) == patterns_count

    def test_masks_secret_in_traceback(self, monkeypatch):
        """Секреты маскируются не только в record.msg, но и в тексте traceback."""
        monkeypatch.setenv("REDIS_URL", "redis://:traceback_secret_pass@host")
        lc.SanitizingFilter._initialized = False
        lc.SanitizingFilter._patterns = None
        sf = lc.SanitizingFilter()
        record = None
        try:
            raise ValueError("failure with redis://:traceback_secret_pass@host")
        except ValueError:
            record = logging.LogRecord(
                name="test", level=logging.ERROR, pathname="", lineno=0,
                msg="Something went wrong", args=(), exc_info=sys.exc_info(),
            )
            sf.filter(record)
        assert record is not None
        assert record.exc_info is None
        assert record.exc_text is not None
        assert "traceback_secret_pass" not in record.exc_text