# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`robo-totvs` is a Python RPA that automates the TOTVS Protheus WebApp to download the "Material em Estoque por Técnico" report as XLSX, one file per technician listed in `technicians.json`.

The single source of truth for product/architecture decisions is `PRD.md`. The execution roadmap (sprints, checkboxes, demos) lives in `TASKS.md`. **Never mix the two**: spec/decisions go in PRD.md, sprint progress goes in TASKS.md (this is durable user feedback — see `.claude/.../memory/MEMORY.md`).

## Setup & commands

```bash
# one-time
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env  # fill PROTHEUS_URL/USER/PASS

# run end-to-end
python main.py

# run Transferência Múltipla (Sprint 9-12)
python main.py trans-multipla [--planilha references/trans_mult.xlsx]

# headed/headless toggle
HEADLESS=true python main.py

# alt input file
TECNICOS_JSON=data/lote_b.json python main.py
```

There is no test suite, no linter config, and no Docker — by explicit product decision (PRD §13.3, §13.4). Validation is per-sprint demo against the real Protheus.

Tesseract OCR must be installed at the OS level (`pytesseract` is just the wrapper) — only required when OCR validation kicks in (Sprint 4+).

## Architecture: 3-layer hybrid (DOM → CV → OCR)

The Protheus WebApp renders most of its UI inside `<canvas>` and inside dynamically-loaded `<iframe>`s with obfuscated IDs. Pure DOM automation is fragile; pure CV is slow. Every interaction in `core/acoes.py` follows this fallback chain in order:

1. **DOM (Playwright locators)** — used first for any element that *might* be HTML-accessible (login form fields, "Favoritos" menu, "Confirmar"/"OK"/"Sim" buttons, the hidden `<select>` for spreadsheet type). Search runs across `page` AND every `page.frames` because the login form sits in a `WA-WEBVIEW` iframe whose URL embeds a per-session env id — match by `input[name="login"]`, never by frame URL.
2. **Template matching (`core/visao.py`)** — for canvas-painted controls. References live in `referencias/` (18 PNGs, one per step). The matcher detects the red arrow drawn on each reference, inpaints it out, then matches at multi-scale (0.9/1.0/1.1) with `TM_CCOEFF_NORMED`. **Threshold default is 0.70**, deliberately looser than the 0.85 in PRD §8.1 — some references contain pre-typed user/password text that doesn't match the live screen exactly. The arrow's tip becomes the click point, not the geometric center.
3. **OCR (`validar_texto_ocr`)** — last resort, used only to *validate* that the right technician's name appears on screen (fuzzy match via `rapidfuzz`). Mismatch logs a warning; it never blocks the flow (PRD F3 step 3).

**Viewport is fixed at 1366×768** (`core/navegador.py`). Changing it invalidates every reference image. Don't touch it without re-capturing all 18 PNGs.

### Module map

| Module | Role |
|---|---|
| `main.py` | CLI entrypoint. Currently runs one hard-coded technician (Sprint 4 demo); Sprint 5 will swap in `flows/processar_lista.py`. |
| `core/config.py` | `pydantic-settings` reading `.env`. Exposes `settings.tecnicos_path`, `downloads_dir`, `logs_dir`, `state_dir`, `referencias_dir` (all anchored to `PROJECT_ROOT`). |
| `core/log.py` | Loguru singleton. **Always** call `log.bind(etapa="...", tecnico="...")` — the file sink expects both extras and will key file format on them. Console output uses colorized `etapa`. |
| `core/navegador.py` | `iniciar_navegador()` context manager (Chromium, fixed viewport, `accept_downloads=True`, `ignore_https_errors=True`) and `tirar_screenshot(page, etapa, evidencia=)` (evidence shots go to `logs/evidencias/`). |
| `core/visao.py` | Template matching pipeline + `validar_texto_ocr`. Tolerates references missing `.png` extension (the original `01_link_de_acesso` was extensionless). |
| `core/acoes.py` | High-level Protheus actions: `fazer_login`, `navegar_ate_rotina`, `baixar_xlsx_tecnico`. Each is wrapped in `@retry(stop_after_attempt(3), wait_exponential)` from `tenacity`. |
| `core/estado.py` | Per-day checkpoint at `state/checkpoint_AAAA-MM-DD.json`. Uses **atomic write** (write-temp + rename) to survive crashes mid-write — do not change to direct write. |
| `core/schema.py` | Pydantic models: `Tecnico`, `LinhaTransferencia` and `CheckpointItem`. |
| `core/planilha.py` | Reading and validation of F7 XLSX spreadsheets. |
| `flows/processar_lista.py` | Orchestrator for F3-F6 (download reports). |
| `flows/transferencia_multipla.py` | Orchestrator for F7 (write transfers). |
| `referencias/` | 18 ground-truth PNGs of each Protheus step. Re-capture if Protheus UI changes. |
| `scripts/` | One-off DOM inspectors and download debuggers (`inspect_*.py`, `debug_download.py`). Not part of the runtime path. |
| `agentes/estrategias_download_protheus.py` | Reference notes on download interception strategies — exploratory, not imported. |

### Critical exceptions to "no fixed sleep"

PRD §3.3 forbids blind `sleep()`, but two exceptions are intentional:
- **`time.sleep(7)` after "Imprimir"+"Sim"** in `_executar_download` (PRD §13.8) — the Protheus auto-return window has no detectable event during those 7s.
- Short `sleep(1-3)` after menu clicks in `navegar_ate_rotina` to let canvas animations settle before matching the next reference (otherwise mid-animation screenshots produce sub-threshold scores).

### Exception types and exit codes

`acoes.py` exposes three exception classes that the orchestrator must distinguish:
- `CredenciaisInvalidasError` → never retried, propagated up to `main()` → **exit 2**.
- `LoginError` / `NavegacaoError` / `DownloadError` → transient, retried 3× via tenacity. After exhaustion, `baixar_xlsx_tecnico` falls back to `page.goto(PROTHEUS_URL)` to reset state for the next technician.

Exit codes (PRD §9.3): `0` all OK, `1` partial failures, `2` login/session abort, `3` config error.

### Input data quirk

`technicians.json` lives **at the repo root**, not at `data/`, despite older sections of PRD.md still saying `data/tecnicos.json`. The field name is `code` (e.g., `"HK"`, `"8Q"`), not `codigo`. Default for `TECNICOS_JSON` env var is `technicians.json`. Treat `code` as a free-form string — current dataset uses 2-char alphanumerics but don't assume that.

### Output layout

- XLSX: `downloads/AAAA-MM-DD/{code}_{NAME_NORMALIZED}.xlsx` — name is NFKD-stripped, non-alphanumeric → `_`, uppercased.
- Validation gate before counting as success: file > 0 bytes AND `zipfile.is_zipfile()` true (XLSX is a ZIP). SHA-256 is then stored in checkpoint for audit.
- Logs: `logs/run-AAAA-MM-DD-HHMMSS.log` with rotation 10 MB / 5 files; failure screenshots in `logs/evidencias/`.

## Conventions

- **Never log `PROTHEUS_PASS`.** It's typed via `keyboard.type` or `locator.type` and that's it. Don't add it to a log message even at DEBUG.
- **Bind `etapa` and `tecnico` on every log call** — the file formatter requires both extras. Use `log.bind(etapa="download", tecnico=code).info(...)` — bare `log.info` will render `etapa=-`.
- **Search across all frames**, not just `page`. The Protheus UI mounts in iframes that load late; `[page, *page.frames]` is the standard sweep pattern in `acoes.py`.
- **Prefer DOM `<select>` over canvas-clicking** for the spreadsheet-type dropdown (Sprint 4 finding, see `DOCS.md` §2). Search every frame for `select`, check option `value="3"`, call `select_option(value="3")`. Falling back to template matching on this dropdown is unreliable.
- **Don't add tests, Docker, CI, or LLM-driven decisions** unless the user asks — these are explicit non-goals for the MVP (PRD §1, §13).
