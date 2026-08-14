"""
Тесты для конфигурации логирования (bot/logging_config.py).
"""
import logging

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
            try:
                h.close()
            except Exception:
                pass


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