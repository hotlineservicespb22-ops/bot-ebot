"""
Единая конфигурация логирования.

Логирование настраивается один раз в точке входа (bot/main.py) через setup_logging().
Уровень и файл лога берутся из переменных окружения LOG_LEVEL и LOG_FILE
(см. bot/config.py). Функция идемпотентна — повторные вызовы не дублируют обработчики.

Безопасность: SanitizingFilter автоматически маскирует чувствительные данные
(BOT_TOKEN, BITRIX_WEBHOOK_URL, REDIS_URL, REDIS_PASSWORD) во всех лог-записях,
предотвращая утечку секретов через файлы логов или вывод в консоль.
"""
import logging
import logging.handlers
import os
import re

from bot.config import LOG_FILE, LOG_LEVEL

# Храним признак, что логирование уже настроено
_logging_configured = False

# Допустимые уровни логирования
_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# Чувствительные переменные окружения, которые нужно маскировать в логах
_SENSITIVE_ENV_VARS = [
    "BOT_TOKEN",
    "BITRIX_WEBHOOK_URL",
    "REDIS_URL",
    "REDIS_PASSWORD",
]


class SanitizingFilter(logging.Filter):
    """
    Фильтр логирования, маскирующий чувствительные данные.

    Заменяет значения чувствительных переменных окружения (токены, пароли, URL вебхуков)
    на строку вида ``***<первые 4 символа>***`` во всех лог-записях, чтобы предотвратить
    случайную утечку секретов через файлы логов или вывод в консоль.
    """

    _patterns: list | None = None
    _initialized: bool = False

    @classmethod
    def _ensure_patterns(cls) -> None:
        """Ленивая инициализация паттернов замены (один раз)."""
        if cls._initialized:
            return
        cls._initialized = True
        cls._patterns = []
        for var_name in _SENSITIVE_ENV_VARS:
            value = os.getenv(var_name, "")
            if not value:
                continue
            safe = f"***{value[:4]}***" if len(value) > 4 else "***"
            escaped = re.escape(value)
            cls._patterns.append((re.compile(escaped), safe))

    def filter(self, record: logging.LogRecord) -> bool:
        self._ensure_patterns()
        if not self._patterns:
            return True
        msg = record.getMessage()
        for pattern, replacement in self._patterns:
            msg = pattern.sub(replacement, msg)
        record.msg = msg
        record.args = None
        return True


def setup_logging() -> None:
    """
    Настраивает корневой логгер: консоль + файл с ротацией.

    Вызывать один раз при старте бота (до создания бота и диспетчера).
    """
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True

    level_name = LOG_LEVEL.upper() if LOG_LEVEL in _VALID_LEVELS else "INFO"
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Фильтр санитизации чувствительных данных (один раз на корневой логгер)
    if not any(isinstance(f, SanitizingFilter) for f in root.filters):
        root.addFilter(SanitizingFilter())

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # Консольный обработчик
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
               for h in root.handlers):
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    # Файловый обработчик с ротацией
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                LOG_FILE,
                maxBytes=5 * 1024 * 1024,  # 5 MB
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError as e:
            logging.getLogger(__name__).warning(f"Не удалось настроить файловый логгер ({LOG_FILE}): {e}")

    logging.getLogger(__name__).info(
        "Логирование настроено: уровень=%s, файл=%s", level_name, LOG_FILE
    )
