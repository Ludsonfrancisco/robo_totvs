# robo-totvs

Robô em Python + Playwright que automatiza no TOTVS Protheus WebApp o download em XLSX do relatório **"Material em Estoque por Técnico"** para uma lista de técnicos definida em JSON.

Spec completa: ver [`PRD.md`](./PRD.md). Roteiro de execução: ver [`TASKS.md`](./TASKS.md).

## Requisitos

- Python 3.11+
- Linux/macOS/Windows com display gráfico (ou rodar `HEADLESS=true`)
- Tesseract OCR instalado no sistema (apenas a partir da Sprint 4 — opcional)

## Setup

```bash
# 1. Criar e ativar venv
python3 -m venv venv
source venv/bin/activate              # Windows: venv\Scripts\activate

# 2. Instalar dependências
pip install -r requirements.txt

# 3. Instalar browser do Playwright
playwright install chromium

# 4. Configurar .env
cp .env.example .env
#   editar .env com PROTHEUS_URL, PROTHEUS_USER, PROTHEUS_PASS reais
```

## Uso

```bash
# smoke test (Sprint 1) — abre Protheus, espera 5s, screenshot
python main.py
```

Saída: screenshot em `logs/<timestamp>_boot.png` e log em `logs/run-AAAA-MM-DD-HHMMSS.log`.

## Estrutura

```
core/        # navegador, config, log, ações, visão (cresce a cada sprint)
flows/       # orquestração (Sprint 5+)
referencias/ # ground truth visual do Protheus (18 PNGs)
data/        # inputs auxiliares (technicians.json fica na raiz)
downloads/   # XLSX baixados (gitignored)
logs/        # logs e evidências (gitignored)
state/       # checkpoint.json (gitignored)
```

## Variáveis de ambiente

Ver [`.env.example`](./.env.example). Detalhamento em [PRD §9.1](./PRD.md#91-variáveis-de-ambiente-env).
