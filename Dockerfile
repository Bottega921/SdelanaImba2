FROM python:3.8-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y chromium chromium-driver xdg-utils --no-install-recommends && apt-get clean && rm -rf /var/lib/apt/lists/*

# Создание виртуального окружения
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

# Копируем и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt --no-cache-dir

# Копируем остальные файлы
COPY . .

# Запуск бота
CMD ["python", "bot.py"]
