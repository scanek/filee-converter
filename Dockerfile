FROM mcr.microsoft.com/dotnet/runtime:10.0-preview

LABEL maintainer="scanek"
LABEL description="Filee Converter — автономный веб-сервер приватной конвертации 270+ форматов файлов в Docker"

# Установка Python, внешних конвертационных движков, LibreOffice, OCR и шрифтов
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    ca-certificates \
    libicu-dev \
    ffmpeg \
    pandoc \
    ghostscript \
    libreoffice-writer \
    libreoffice-calc \
    default-jre-headless \
    fonts-liberation \
    fonts-dejavu-core \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Копирование и установка зависимостей Python
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Установка скомпилированного ядра Filee.Cli
WORKDIR /opt/filee
COPY bin/ /opt/filee/
RUN chmod +x /opt/filee/filee-cli && ln -s /opt/filee/filee-cli /usr/local/bin/filee

# Настройка веб-сервиса
WORKDIR /app
COPY app.py /app/app.py
COPY static/ /app/static/

# Переменные окружения для Filee
ENV PYTHONUNBUFFERED=1 \
    FILEE_DATA_DIR=/tmp/filee-data

# Порт веб-интерфейса и API
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
