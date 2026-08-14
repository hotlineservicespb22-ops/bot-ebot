FROM python:3.12-slim

WORKDIR /app

# Отключаем буферизацию вывода Python для мгновенного отображения логов
ENV PYTHONUNBUFFERED=1

# Устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY bot/ ./bot/

# Запускаем бота
CMD ["python", "-m", "bot.main"]
