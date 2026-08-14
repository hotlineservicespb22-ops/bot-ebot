"""
Единая конфигурация логирования.

Логирование настраивается один раз в точке входа (bot/main.py) через setup_logging().
Уровень и файл лога берутся из переменных окружения LOG_LEVEL и LOG_FILE
(см. bot/config.py). Функция идемпотентна — повторные вызовы не дублируют обработчики.
"""
import logging
import logging.handlers

from bot.config import LOG_FILE, LOG_LEVEL

# Храним признак, что логирование уже настроено
_logging_configured = False

# Допустимые уровни логирования
_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


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