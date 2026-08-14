# --- Стадия сборки: создание колёс зависимостей (артефакт не попадает в финальный образ) ---
FROM python:3.12-slim AS builder

WORKDIR /wheels

RUN pip install --upgrade pip

COPY requirements.txt .
# Собираем wheels, чтобы в финальной стадии не требовались компиляторы/сеть
RUN pip wheel --no-cache-dir --no-deps -w /wheels -r requirements.txt

# --- Финальная стадия: минимальный образ только с продакшен-зависимостями ---
FROM python:3.12-slim

WORKDIR /app

# Отключаем буферизацию вывода Python для мгновенного отображения логов
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Устанавливаем колёса из стадии сборки (только runtime-зависимости, без dev)
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

# Копируем код приложения
COPY bot/ ./bot/

# Непривилегированный пользователь для запуска (безопасность)
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Запускаем бота
CMD ["python", "-m", "bot.main"]