# GEMINI.md — robo-totvs

## Project Overview
**robo-totvs** is a Python-based RPA (Robotic Process Automation) tool designed to automate the download of the "Material em Estoque por Técnico" report from the TOTVS Protheus WebApp. 

The project uses a hybrid automation strategy to handle the TOTVS SmartClient HTML interface, which relies heavily on `<canvas>` elements and ofuscated DOM:
1.  **DOM (Playwright)**: Preferred for accessible HTML elements (login forms, headers).
2.  **Computer Vision (OpenCV)**: Used for interacting with Canvas-rendered elements via template matching (using references in `referencias/`).
3.  **OCR (Tesseract)**: Last resort for reading variable text within Canvas elements.

### Tech Stack
- **Language**: Python 3.11+
- **Automation**: Playwright (sync API)
- **Vision**: OpenCV (`cv2.matchTemplate`)
- **OCR**: Pytesseract
- **Configuration**: Pydantic Settings
- **Logging**: Loguru
- **Resilience**: Tenacity (retries)

---

## Building and Running

### Prerequisites
- Python 3.11+
- Tesseract OCR installed on the system.
- Chromium browser (installed via Playwright).

### Setup
```bash
# 1. Create and activate venv
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install Playwright browser
playwright install chromium

# 4. Configure environment
cp .env.example .env
# Edit .env with PROTHEUS_URL, PROTHEUS_USER, PROTHEUS_PASS
```

### Key Commands
- **Run Automation**: `python main.py`
- **Headless Mode**: Toggle `HEADLESS` in `.env` or prefix: `HEADLESS=true python main.py`
- **Reprocess Failures**: `python main.py --retry-falhos` (Planned for later sprints)

---

## Development Conventions

### 1. Hybrid Automation Strategy
Always attempt to interact with the system in this order:
- **Playwright Locators**: Check if the element is in the DOM (e.g., `page.locator(...)`).
- **Template Matching**: Use `core/visao.py:clicar_imagem(page, "referencia.png")`. References are stored in `referencias/`.
- **OCR**: Use only if matching fails and you need to validate text.

### 2. Fixed Viewport
To ensure template matching reliability, the viewport is fixed at **1366x768**. Never change this without updating all reference images.

### 3. Resilience & Retries
- Use `@retry` from `tenacity` for transient actions (e.g., waiting for a popup, clicking a button).
- Differentiate between transient errors (e.g., `LoginError`) and permanent ones (e.g., `CredenciaisInvalidasError`).

### 4. Logging & Debugging
- Use `core.log:log` (Loguru).
- Always bind context: `log.bind(etapa="etapa_name", tecnico="codigo")`.
- Screenshots are automatically saved on failure if using standard action patterns.
- **Security**: Never log `PROTHEUS_PASS` or any sensitive data in plain text.

### 5. File Structure
- `core/`: Low-level primitives (browser, vision, config).
- `flows/`: High-level orchestration (business logic).
- `referencias/`: Ground truth images for template matching.
- `data/`: Inputs (e.g., `technicians.json`).
- `downloads/`: Output XLSX files (gitignored).
- `logs/`: Runtime logs and failure screenshots (gitignored).
- `state/`: Execution checkpoints (gitignored).

---

## Project Status
- **Sprint 1 (Complete)**: Environment setup and smoke test.
- **Sprint 2 (Current)**: Resilient login implementation.
- **Next Steps**: Navigation to Favorites and report download loop.

Refer to `PRD.md` for the full specification and `TASKS.md` for the detailed roadmap.
