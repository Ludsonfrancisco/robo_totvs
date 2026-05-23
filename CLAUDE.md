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

## EasyPanel — operations & gotchas (production runbook)

### Force a real rebuild — the "Stop + Continue" trick

EasyPanel's "Deploy" / "Force Rebuild" button frequently *doesn't* recreate the container — it returns success silently while the old container keeps running. **Reliable rebuild = Stop the service, wait for it to actually stop, then Continue/Start.** That kills the old container and Swarm boots a new one with the latest image, env vars, and code.

Symptoms of a fake deploy:
- `docker ps` shows the same container with `Up X hours` (not seconds)
- env changes don't reflect inside the container (`docker exec <c> env`)
- recent code commits aren't in the running image

Manual force from the host (last resort, ~15s downtime):
`docker rm -f <container_name>` → Swarm auto-restarts a fresh one.

### Chrome SingletonLock orphans

If a container is killed mid-run (deploy during a download, OOM, manual `docker kill`), Chrome leaves three orphan symlinks in the persistent profile that block the next launch:

```
/app/.browser-profile/protheus/SingletonLock      → <dead_hostname>-<pid>
/app/.browser-profile/protheus/SingletonCookie    → <random>
/app/.browser-profile/protheus/SingletonSocket    → /tmp/com.google.Chrome.xxx/SingletonSocket
```

Next boot fails with `TargetClosedError: Target page, context or browser has been closed`, and Chrome stderr shows `The profile appears to be in use by another Google Chrome process`.

Fix from the new container's Console: `rm -f /app/.browser-profile/protheus/Singleton*` then retry. Always do this after any forced kill/restart.

### Two containers running in parallel during deploys

Swarm's rolling update sometimes leaves the old container alongside the new one for several seconds (longer if the service has no exposed port for healthchecks — our case). You'll see two `apps_robo_totvs.1.<task_id>` entries in `docker ps` with the same name root. While that lasts, both workers race for the same Protheus session and the same `run.log` / `run.signal` files in the shared volume — guaranteed chaos (limit-of-connections modal, corrupted checkpoint, duplicate downloads).

Always check `docker ps --filter name=robo` after a deploy. If two are alive, wait 30s — usually Swarm converges. If not: `docker rm -f <old_task_id>`.

### Timezone — must have `tzdata` AND env `TZ`

The base Playwright image does **not** include `tzdata`. Without it, env `TZ=America/Sao_Paulo` is silently ignored and the container runs in UTC. Consequences:
- `ROBOT_SCHEDULE_HOUR=6` fires at **03:00 BRT** (not 06:00)
- Output folder `entrada/AAAA-MM-DD/` uses UTC date (off-by-one in evenings)
- Logs timestamps in UTC

Dockerfile must install `tzdata` AND symlink `/etc/localtime`:
```dockerfile
RUN apt-get install -y tzdata \
    && ln -sf /usr/share/zoneinfo/America/Sao_Paulo /etc/localtime \
    && echo "America/Sao_Paulo" > /etc/timezone
```

Validate after deploy: `docker exec <container> date` — must end in `-03 2026`.

### Host vs container paths

| Resource | Container path | Host path |
|---|---|---|
| Pipeline data (XLSX) | `/app/data_pipeline/` | `/srv/dmais/data_pipeline/` |
| Chrome profile | `/app/.browser-profile/` | (named volume `robo_profile`, not on host fs) |
| Logs & checkpoint | `/app/logs/`, `/app/state/` | (ephemeral — inside container, lost on rebuild) |

**Nota sobre integração com dmais_portal:** O portal consome os `.xlsx` do volume `/app/data_pipeline/`. A partir de v1.7.0 do dmais_portal, a consolidação usa fallback hierárquico (hoje → ontem → mais recente) e propaga a data do diretório encontrado como `snapshot_date` (BRT local, não UTC). O robô não precisa mudar nada — a compatibilidade é total com a estrutura `entrada/<YYYY-MM-DD>/*.xlsx`.

**CRÍTICO — XLSX duplicados:** O robô salva cada download com nome UUID (`*.xlsx`). Se o robô rodar mais de uma vez no mesmo dia (manual + retry + scheduled), os arquivos se acumulam no diretório `entrada/<DATA>/`. O portal consolida **todos** os `.xlsx` presentes — resultando em estoque duplicado/triplicado no snapshot. **Antes de consolidar**, verifique se não há duplicados:

```bash
ls /srv/dmais/data_pipeline/entrada/$(date +%Y-%m-%d)/ | wc -l  # deve ser ~34 (um por técnico)
```

Se > 40 arquivos: limpe duplicados antes de consolidar. Veja "Clean XLSX duplicados" no runbook abaixo.

Important: `/app/state/checkpoint_<DATA>.json` is **not** in a volume. Rebuilds reset the day's checkpoint — fine for the morning scheduled run (no prior state), but a rebuild *mid-run* makes the next run re-process technicians already done.

### Sanity-test before full runs

After any code change or revert, validate from the container Console with:
```
python main.py --limite 3 --reset
```
That runs only 3 technicians in ~3 minutes. Confirm each XLSX has a different armazém (see "verifying XLSX content" below) **before** letting the scheduler loop over all 33.

### Verifying XLSX content from the host

```bash
for f in /srv/dmais/data_pipeline/entrada/<DATA>/*.xlsx; do
  echo "=== $(basename $f) ==="
  unzip -p "$f" xl/sharedStrings.xml \
    | grep -oE '<t[^>]*>[^<]+' | sed 's/<t[^>]*>//' \
    | grep -A1 "Armazém?" | head -2
done
```

Each file should show a different armazém after `Armazém?` (HK, HJ, 2S, etc.). If all show the same code → robot isn't typing into the Armazém field correctly. **Don't trust file size alone** — XLSX timestamps make sizes differ by 1 byte even when content is identical.

### History incidents (so we don't repeat)

- **2026-05-17**: commit `9a8cf63` added popup-Moedas handling but regressed Passo 11, making every technician download armazém F8. Fix was `git revert 9a8cf63` — runbook in dmais_portal CLAUDE.md covers banco/XLSX cleanup.
- **2026-05-17 (same day)**: deploy *during* a running batch left an orphan Chrome SingletonLock. Resolution: `rm -f Singleton*` in new container.
- **2026-05-17 (same day)**: forgot to set `tzdata` in image — `TZ` env had no effect; container kept UTC. Fixed in commit `802bfd4`.
- **2026-05-22**: robo rodou 4x no mesmo dia (scheduled + manual + retries) → 87 XLSX no diretorio (34 tecnicos × 2-7 copias). Portal consolidou todos → estoque triplicado (3358 items duplicados). Fix: script Python para identificar armazem por `sharedStrings.xml`, manter só o mais recente por tecnico, deletar snapshot duplicado do DB, re-consolidar. See "Clean XLSX duplicados" abaixo.

### Clean XLSX duplicados (runbook)

Se o diretorio `entrada/<DATA>/` tem mais arquivos que tecnicos (~34), limpe antes de consolidar:

```bash
CONTAINER=$(docker ps --filter "name=robo_totvs" --format '{{.Names}}' | head -1)
docker exec "$CONTAINER" python3 -c "
import os, zipfile, re
dir_path = '/app/data_pipeline/entrada/$(date +%Y-%m-%d)'
files_by_armazem = {}
for fname in os.listdir(dir_path):
    if not fname.endswith('.xlsx'): continue
    fpath = os.path.join(dir_path, fname)
    mtime = os.path.getmtime(fpath)
    try:
        with zipfile.ZipFile(fpath) as zf:
            if 'xl/sharedStrings.xml' in zf.namelist():
                xml = zf.read('xl/sharedStrings.xml').decode('utf-8', errors='ignore')
                texts = re.findall(r'<t[^>]*>([^<]+)', xml)
                for i, t in enumerate(texts):
                    if 'Armazem' in t or 'Armaz' in t or 'armaz' in t.lower():
                        if i+1 < len(texts):
                            arm = texts[i+1].strip()[:10]
                            files_by_armazem.setdefault(arm, []).append((mtime, fname))
                        break
    except: pass
deleted = 0
for arm, files in files_by_armazem.items():
    files.sort(key=lambda x: x[0], reverse=True)
    for _, fname in files[1:]:
        os.remove(os.path.join(dir_path, fname))
        deleted += 1
print(f'Removidos {deleted} duplicados. Restantes: {len(os.listdir(dir_path))}')
"
```

Depois, no dmais_portal: deletar snapshot do dia + re-consolidar:
```bash
docker exec apps_dmais python manage.py shell -c "
from estoque.models import StockSnapshot, StockItem, ConsolidationLog
import datetime
today = datetime.date.today()
StockSnapshot.objects.filter(snapshot_date=today).delete()
StockItem.objects.filter(snapshot__snapshot_date=today).delete()
ConsolidationLog.objects.filter(created_at__gte=datetime.datetime.combine(today, datetime.time.min)).delete()
print('Snapshots de hoje removidos')
"
docker exec apps_dmais python manage.py executar_consolidacao --force
```
