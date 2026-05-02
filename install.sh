#!/bin/bash
set -e

echo "=== Iniciando instalação do robo-totvs ==="

# 1. Verifica Python
if ! command -v python3 &> /dev/null; then
    echo "Erro: Python 3 não encontrado. Instale-o primeiro."
    exit 1
fi

# 2. Cria venv se não existir
if [ ! -d "venv" ]; then
    echo "Criando ambiente virtual (venv)..."
    python3 -m venv venv
fi

# 3. Ativa venv e instala dependências
echo "Instalando dependências do Python..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. Instala browser do Playwright
echo "Instalando browser Playwright (Chromium)..."
playwright install chromium

# 5. Prepara .env se não existir
if [ ! -f ".env" ]; then
    echo "Criando .env a partir do exemplo..."
    cp .env.example .env
    echo "AVISO: Edite o arquivo .env com suas credenciais Protheus."
fi

# 6. Verifica Tesseract (opcional)
if ! command -v tesseract &> /dev/null; then
    echo "Aviso: Tesseract OCR não encontrado. Funcionalidades de OCR estarão desabilitadas."
else
    echo "Tesseract OCR detectado."
fi

echo "=== Instalação concluída com sucesso! ==="
echo "Para rodar: source venv/bin/activate && python main.py"
