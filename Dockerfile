# Robô TOTVS — Playwright + Chrome real (Cloud TOTVS exige Chrome, não Chromium).
# Base oficial do Playwright já vem com libs do sistema necessárias.
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# Tesseract OCR (validação defensiva) + tzdata (timezone Brasil — sem isso
# o container roda em UTC, scheduler 06:00 dispara às 03:00 BRT e o nome
# da pasta de saída vira a data UTC, divergindo do horário Brasil).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-por tzdata \
    && ln -sf /usr/share/zoneinfo/America/Sao_Paulo /etc/localtime \
    && echo "America/Sao_Paulo" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala deps Python primeiro (cache de layer)
COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# Instala Chrome real (TOTVS Cloud exige) — chromium do base não basta
RUN playwright install chrome \
    && playwright install-deps chrome

# Copia código do robô
COPY . .

# Cria estrutura do volume compartilhado (igual ao Dockerfile do Portal D+)
RUN mkdir -p /app/data_pipeline/entrada /app/data_pipeline/processos \
    && chmod -R 775 /app/data_pipeline

# Volumes a serem montados em runtime:
#   /app/data_pipeline      → volume "dados_pipeline" (compartilhado com dmais)
#   /app/.browser-profile   → volume "robo_profile"   (perfil Chrome persistente)
VOLUME ["/app/data_pipeline", "/app/.browser-profile"]

# Loop de worker — bloqueante, sem porta exposta.
CMD ["python", "worker.py"]
