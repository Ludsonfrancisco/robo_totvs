from __future__ import annotations

import json
import logging
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

if TYPE_CHECKING:
    from playwright.sync_api import Page

DATA_SHEET = "Relatório de Atendimentos"

log = logging.getLogger("routerbox_backlog")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RouterBoxInstance:
    name: Literal["ACERTA", "LOGA"]
    url: str
    filter_label: str


# ---------------------------------------------------------------------------
# XLSX validation & consolidation (pure — no browser)
# ---------------------------------------------------------------------------

def validar_xlsx(path: str | Path) -> Path:
    """Valida se o arquivo existe e é um XLSX/ZIP legível."""
    xlsx_path = Path(path)
    if not xlsx_path.exists():
        raise FileNotFoundError(f"Arquivo XLSX não encontrado: {xlsx_path}")
    if not zipfile.is_zipfile(xlsx_path):
        raise ValueError(f"XLSX inválido: {xlsx_path}")
    return xlsx_path


def normalizar_fluxo_coluna(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza Fluxo para código curto e preserva o texto original."""
    if "Fluxo" not in df.columns:
        return df

    result = df.copy()
    if "Fluxo Original" not in result.columns:
        result["Fluxo Original"] = result["Fluxo"]

    fluxo_texto = result["Fluxo"].astype(str)
    codigos = fluxo_texto.str.extract(r"#?(\d+\.\d+)", expand=False)
    result["Fluxo"] = codigos.fillna(fluxo_texto)
    return result


def _ler_relatorio(path: Path, origem: str) -> pd.DataFrame:
    validar_xlsx(path)
    df = pd.read_excel(path, sheet_name=DATA_SHEET, dtype={
        "Numero": str,
        "Fluxo": str,
        "Tel. Cel.": str,
    })
    df["Origem RouterBox"] = origem
    return normalizar_fluxo_coluna(df)


def _calcular_ultima_data_ab(df: pd.DataFrame) -> str | None:
    if "Data AB" not in df.columns or "Hora AB" not in df.columns:
        return None

    datas = pd.to_datetime(
        df["Data AB"].astype(str) + " " + df["Hora AB"].astype(str),
        dayfirst=True,
        errors="coerce",
    ).dropna()
    if datas.empty:
        return None
    return datas.max().strftime("%Y-%m-%d %H:%M:%S")


def consolidar_backlogs(
    acerta_path: str | Path,
    loga_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Consolida os backlogs RouterBox ACERTA e LOGA em um XLSX compatível com o portal."""
    acerta = _ler_relatorio(Path(acerta_path), "ACERTA")
    loga = _ler_relatorio(Path(loga_path), "LOGA")
    consolidado = pd.concat([acerta, loga], ignore_index=True)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    ultima_data_ab = _calcular_ultima_data_ab(consolidado)
    resumo = {
        "gerado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "arquivo_acerta": str(acerta_path),
        "arquivo_loga": str(loga_path),
        "linhas_acerta": int(len(acerta)),
        "linhas_loga": int(len(loga)),
        "linhas_total": int(len(consolidado)),
        "ultima_data_ab": ultima_data_ab,
    }

    resumo_df = pd.DataFrame(
        [{"Campo": key, "Valor": value} for key, value in resumo.items()]
    )
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        resumo_df.to_excel(writer, sheet_name="Resumo", index=False)
        consolidado.to_excel(writer, sheet_name=DATA_SHEET, index=False)

    validar_xlsx(output)
    resumo["output_path"] = str(output)
    return resumo


# ---------------------------------------------------------------------------
# Playwright helpers (browser navigation)
# ---------------------------------------------------------------------------

def _contexts(page: Page) -> list:
    """Retorna page + todos os frames."""
    return [page] + list(page.frames)


def _first_visible(page: Page, selectors: list[str], timeout: int = 1500):
    """Procura o primeiro seletor visível em page e frames."""
    deadline = time.time() + timeout / 1000
    last_exc = None
    while time.time() < deadline:
        for ctx in _contexts(page):
            for sel in selectors:
                try:
                    loc = ctx.locator(sel).first
                    if loc.count() and loc.is_visible(timeout=200):
                        return loc, ctx, sel
                except Exception as exc:
                    last_exc = exc
        time.sleep(0.2)
    raise RuntimeError(f"nenhum seletor visível: {selectors}; ultimo={last_exc}")


def _click_any(page: Page, selectors: list[str], name: str, timeout: int = 8000):
    """Clica no primeiro elemento que encontrar, visível ou não (JS fallback)."""
    try:
        loc, ctx, sel = _first_visible(page, selectors, timeout=min(timeout, 3000))
        loc.click(timeout=timeout)
        log.info(f"OK click {name}: {sel}")
    except Exception:
        # Fallback: JS click em elemento mesmo oculto, busca em todos os frames
        import re
        found = False
        for sel in selectors:
            try:
                if sel.startswith('text='):
                    js_text = sel[5:]
                    code = f"""() => {{ const el = Array.from(document.querySelectorAll('a,button,span,li,div')).find(e => e.textContent.trim() === '{js_text}'); if (el) {{ el.click(); return true; }} return false; }}"""
                elif ':has-text(' in sel:
                    m = re.search(r':has-text\("(.+?)"\)', sel)
                    js_text = m.group(1) if m else ''
                    code = f"""() => {{ const el = Array.from(document.querySelectorAll('a')).find(e => e.textContent.includes('{js_text}')); if (el) {{ el.click(); return true; }} return false; }}"""
                else:
                    code = f"""() => {{ const el = document.querySelector('{sel}'); if (el) {{ el.click(); return true; }} return false; }}"""
                # Tenta em page + todos os frames
                for ctx in _contexts(page):
                    try:
                        result = ctx.evaluate(code)
                        if result:
                            log.info(f"OK click {name}: {sel} (via JS)")
                            found = True
                            break
                    except Exception:
                        continue
                if found:
                    break
            except Exception:
                continue
        if not found:
            raise RuntimeError(f"nenhum seletor clicável: {selectors}")
    page.wait_for_timeout(1000)
    return loc if 'loc' in dir() else None


def _fechar_modal_novidades(page: Page) -> None:
    """Fecha modal de novidades do RouterBox que bloqueia cliques."""
    try:
        close = page.locator('.modal_menu .closed span, .modal_menu span:has-text("x")').first
        if close.count() and close.is_visible(timeout=1000):
            close.click(timeout=3000)
            log.info("modal pós-login fechado")
            page.wait_for_timeout(1000)
    except Exception:
        log.debug("modal pós-login não encontrado ou já fechado")


# ---------------------------------------------------------------------------
# Playwright download flow
# ---------------------------------------------------------------------------

def baixar_backlog_routerbox(
    page: Page,
    instance: RouterBoxInstance,
    destino: Path,
    usuario: str,
    senha: str,
    timeout_s: int = 180,
) -> Path:
    """Baixa XLSX de backlog de uma instância RouterBox via Playwright.

    Retorna o Path do arquivo salvo.
    """
    name = instance.name.lower()
    url = instance.url
    filter_label = instance.filter_label

    log.info(f"== {name}: abrindo login {url} ==")

    # Login
    user_selectors = [
        'input[name="login"]', 'input[name="usr_login"]',
        'input[name="user"]', 'input[name="usuario"]',
        'input[type="text"]', 'input:not([type])',
    ]
    pass_selectors = [
        'input[type="password"]', 'input[name="password"]',
        'input[name="senha"]', 'input[name="pswd"]',
    ]

    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)

    # Login via JS (mais confiável que DOM — RouterBox/ScriptCase muda seletores)
    page.evaluate(f"""
        () => {{
            const u = document.querySelector('input[name="usuario"]') || document.querySelector('input[name="login"]') || document.querySelector('input[type="text"]:not([hidden])');
            if (u) {{ u.value = '{usuario}'; u.dispatchEvent(new Event('input', {{bubbles:true}})); }}
            const s = document.querySelector('input[name="senha"]') || document.querySelector('input[type="password"]');
            if (s) {{ s.value = '{senha}'; s.dispatchEvent(new Event('input', {{bubbles:true}})); }}
        }}
    """)
    log.info("OK login preenchido via JS")

    try:
        _click_any(page, [
            'button:has-text("Entrar")', 'input[value*="Entrar"]',
            'a:has-text("Entrar")', 'text=Entrar',
            '#sub_form_b', 'a#sub_form_b',
        ], 'entrar', timeout=5000)
    except Exception:
        # Fallback: JS submit + Enter
        page.evaluate("document.querySelector('a#sub_form_b')?.click() || document.querySelector('form')?.submit()")
        page.keyboard.press("Enter")
        log.info("OK submit via JS fallback")

    page.wait_for_load_state("domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)

    _fechar_modal_novidades(page)

    # Navegação: JS puro cross-frame
    log.info(f"{name}: navegando para Atendimentos/Planejamento de OS")
    page.evaluate("""
        () => {
            const search = (doc) => {
                for (const a of doc.querySelectorAll('a')) {
                    if (a.textContent.includes('Atendimentos') || a.textContent.includes('Planejamento de OS')) {
                        a.click(); return true;
                    }
                }
                for (const frame of doc.querySelectorAll('iframe, frame')) {
                    try { if (search(frame.contentDocument)) return true; } catch(e) {}
                }
                return false;
            };
            return search(document) ? 'clicou' : 'nao achou';
        }
    """)
    page.wait_for_timeout(2000)

    log.info(f"{name}: navegando para Execução")
    page.evaluate("""
        () => {
            const search = (doc) => {
                for (const a of doc.querySelectorAll('a')) {
                    if (a.textContent.includes('Execução') || a.textContent.includes('Execucao')) {
                        a.click(); return true;
                    }
                }
                for (const frame of doc.querySelectorAll('iframe, frame')) {
                    try { if (search(frame.contentDocument)) return true; } catch(e) {}
                }
                return false;
            };
            return search(document) ? 'clicou' : 'nao achou';
        }
    """)
    page.wait_for_timeout(5000)

    # Pesquisar (topo)
    log.info(f"{name}: clicando Pesquisar")
    page.evaluate("""
        () => {
            const search = (doc) => {
                let el = doc.querySelector('#pesq_top') || doc.querySelector('a#pesq_top');
                if (el) { el.click(); return true; }
                for (const a of doc.querySelectorAll('a')) {
                    if (a.textContent.includes('Pesquisar')) { a.click(); return true; }
                }
                for (const frame of doc.querySelectorAll('iframe, frame')) {
                    try { if (search(frame.contentDocument)) return true; } catch(e) {}
                }
                return false;
            };
            return search(document) ? 'clicou' : 'nao achou';
        }
    """)
    page.wait_for_timeout(3000)

    # Selecionar filtro salvo — busca cross-frame via JS
    filtro_ok = False
    value = page.evaluate("""(wanted) => {
        const search = (doc) => {
            const sel = doc.querySelector('select#sel_recup_filters_bot') || doc.querySelector('select[name="sel_recup_filters_bot"]');
            if (sel) {
                for (const o of sel.options) {
                    const t = (o.textContent || '').trim();
                    if (t === wanted || t.includes('DMAIS') || t.includes('dmais')) return o.value;
                }
            }
            for (const frame of doc.querySelectorAll('iframe, frame')) {
                try { const r = search(frame.contentDocument); if (r) return r; } catch(e) {}
            }
            return null;
        };
        return search(document);
    }""", filter_label)
    if value:
        # Seleciona o option encontrado
        for ctx in _contexts(page):
            try:
                sel = ctx.locator('select#sel_recup_filters_bot, select[name="sel_recup_filters_bot"]').first
                if sel.count():
                    sel.select_option(value=value)
                    log.info(f"OK {name}: filtro DMAIS selecionado por value={value}")
                    page.wait_for_timeout(6000)
                    filtro_ok = True
                    break
            except Exception:
                continue
    if not filtro_ok:
        raise RuntimeError(f"{name}: filtro '{filter_label}' não encontrado")

    # Pesquisar (rodapé) — JS puro
    log.info(f"{name}: clicando Pesquisar rodapé")
    page.evaluate("""
        () => {
            const ids = ['#sc_b_pesq_bot', 'a#sc_b_pesq_bot', 'button#sc_b_pesq_bot', 'input#sc_b_pesq_bot'];
            for (const id of ids) {
                const el = document.querySelector(id);
                if (el) { el.click(); return 'clicou'; }
            }
            for (const a of document.querySelectorAll('a')) {
                if (a.textContent.includes('Pesquisar')) { a.click(); return 'clicou'; }
            }
            return 'nao achou';
        }
    """)
    page.wait_for_timeout(8000)

    # Grupo botões → Excel — JS puro
    log.info(f"{name}: clicando grupo botoes")
    page.evaluate("""
        () => {
            const el = document.querySelector('#sc_btgp_btn_group_1_top') || document.querySelector('button#sc_btgp_btn_group_1_top');
            if (el) { el.click(); return 'clicou'; }
            return 'nao achou';
        }
    """)
    page.wait_for_timeout(1000)

    log.info(f"{name}: clicando Excel")
    page.evaluate("""
        () => {
            for (const el of document.querySelectorAll('a, button, li')) {
                if ((el.textContent||'').includes('Excel')) { el.click(); return 'clicou'; }
            }
            return 'nao achou';
        }
    """)
    log.info(f"{name}: aguardando geração do XLSX/link Baixar")

    # Polling para o link "Baixar"
    baixar_loc = None
    for i in range(timeout_s // 5):
        page.wait_for_timeout(5000)
        for ctx in _contexts(page):
            try:
                loc = ctx.locator('a:has-text("Baixar"), text=Baixar').first
                if loc.count() and loc.is_visible(timeout=500):
                    baixar_loc = loc
                    break
            except Exception:
                pass
        if baixar_loc:
            log.info(f"OK {name}: link Baixar apareceu após {(i+1)*5}s")
            break

    # Fallback: link .xlsx direto
    if not baixar_loc:
        for ctx in _contexts(page):
            try:
                loc = ctx.locator('a[href*=".xlsx"]').first
                if loc.count() and loc.is_visible(timeout=1000):
                    baixar_loc = loc
                    break
            except Exception:
                pass

    if not baixar_loc:
        raise RuntimeError(f"{name}: link Baixar/.xlsx não encontrado após {timeout_s}s")

    # Download
    with page.expect_download(timeout=60000) as di:
        baixar_loc.click(timeout=10000)
    download = di.value
    destino.parent.mkdir(parents=True, exist_ok=True)
    download.save_as(str(destino))
    log.info(f"OK {name}: salvo {destino} ({destino.stat().st_size} bytes)")
    return destino


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def run_routerbox_backlog(
    only: str | None = None,
    output_dir: str | None = None,
    no_consolidate: bool = False,
) -> int:
    """Ponto de entrada principal: baixa, consolida e gera manifest.

    Returns exit code: 0=sucesso, 1=falha parcial, 2=falha critica, 3=erro config.
    """
    from core.config import settings
    from playwright.sync_api import sync_playwright

    if not settings.ROUTERBOX_USER or not settings.ROUTERBOX_PASS:
        log.error("ROUTERBOX_USER e ROUTERBOX_PASS são obrigatórios.")
        return 3

    base = Path(output_dir) if output_dir else Path(settings.ROUTERBOX_OUTPUT_DIR)
    base.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    hora = now.strftime("%H-%M")
    out = base / today / hora
    out.mkdir(parents=True, exist_ok=True)

    instances: list[RouterBoxInstance] = []
    if only in (None, "acerta"):
        instances.append(RouterBoxInstance(
            name="ACERTA",
            url=settings.ROUTERBOX_ACERTA_URL,
            filter_label=settings.ROUTERBOX_FILTER_ACERTA,
        ))
    if only in (None, "loga"):
        instances.append(RouterBoxInstance(
            name="LOGA",
            url=settings.ROUTERBOX_LOGA_URL,
            filter_label=settings.ROUTERBOX_FILTER_LOGA,
        ))

    if not instances:
        log.error("Nenhuma instância RouterBox para processar (only=%s)", only)
        return 3

    downloaded: dict[str, Path] = {}
    fresh_downloads: set[str] = set()
    fallback_downloads: dict[str, str] = {}
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel=settings.BROWSER_CHANNEL,
            headless=settings.HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        try:
            for inst in instances:
                context = browser.new_context(
                    accept_downloads=True,
                    viewport={"width": settings.VIEWPORT_W, "height": settings.VIEWPORT_H},
                    ignore_https_errors=True,
                )
                context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
                page = context.new_page()
                try:
                    destino = out / f"{inst.name.lower()}_backlog_{today}.xlsx"
                    # LOGA usa senha diferente da ACERTA
                    usuario = settings.ROUTERBOX_USER
                    senha = settings.ROUTERBOX_LOGA_PASS if inst.name == "LOGA" and settings.ROUTERBOX_LOGA_PASS else settings.ROUTERBOX_PASS
                    baixar_backlog_routerbox(
                        page=page,
                        instance=inst,
                        destino=destino,
                        usuario=usuario,
                        senha=senha,
                        timeout_s=settings.ROUTERBOX_DOWNLOAD_TIMEOUT_S,
                    )
                    downloaded[inst.name] = destino
                    fresh_downloads.add(inst.name)
                except Exception as exc:
                    log.error(f"Erro ao baixar {inst.name}: {exc}")
                    # Fallback: usar o XLSX mais recente disponivel dessa instancia
                    fallback = _find_latest_recursive(base, prefix=f"{inst.name.lower()}_backlog_", suffix=".xlsx")
                    if fallback:
                        log.warning(f"Usando XLSX anterior para {inst.name}: {fallback.name}")
                        downloaded[inst.name] = fallback
                        fallback_downloads[inst.name] = str(exc)
                    else:
                        log.error(f"Sem fallback para {inst.name} — download falhou e nao ha arquivo anterior.")
                        errors.append(f"{inst.name}: {exc} (sem fallback)")
                finally:
                    context.close()
        finally:
            browser.close()

    if not downloaded:
        log.error("Nenhum download realizado com sucesso.")
        return 2

    # Consolidação
    if no_consolidate:
        log.info("--no-consolidate: pulando consolidação.")
        return 0

    if "ACERTA" not in downloaded or "LOGA" not in downloaded:
        log.warning(f"Download parcial: {list(downloaded.keys())}. Consolidação requer ACERTA + LOGA.")
        return 1

    consolidado_path = out / "BACKLOG-GERAL-CONSOLIDADO.xlsx"
    try:
        resumo = consolidar_backlogs(
            acerta_path=downloaded["ACERTA"],
            loga_path=downloaded["LOGA"],
            output_path=consolidado_path,
        )
        log.info(f"Consolidação OK: {resumo['linhas_total']} linhas → {consolidado_path}")

        # Manifest JSON para o portal consumir
        source_mtimes = {
            name.lower(): datetime.fromtimestamp(path.stat().st_mtime).isoformat()
            for name, path in downloaded.items()
        }
        source_mtime_values = sorted(source_mtimes.values())
        manifest = {
            "gerado_em": resumo["gerado_em"],
            "arquivo": consolidado_path.name,
            "linhas_total": resumo["linhas_total"],
            "linhas_acerta": resumo["linhas_acerta"],
            "linhas_loga": resumo["linhas_loga"],
            "ultima_data_ab": resumo["ultima_data_ab"],
            "fresh_downloads": sorted(fresh_downloads),
            "fallback_downloads": fallback_downloads,
            "used_fallback": bool(fallback_downloads),
            "source_mtimes": source_mtimes,
            "source_mtime_min": source_mtime_values[0] if source_mtime_values else None,
            "source_mtime_max": source_mtime_values[-1] if source_mtime_values else None,
        }
        manifest_path = out / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(f"Manifest salvo: {manifest_path}")

        # Limpar diretórios antigos (manter 61 dias)
        _cleanup_old_dirs(base, keep_days=61)

        if fallback_downloads:
            log.warning(f"RouterBox consolidado com fallback: {sorted(fallback_downloads)}")
            return 1
        return 0

    except Exception as exc:
        log.error(f"Erro na consolidação: {exc}")
        return 1


def _find_latest_recursive(base: Path, prefix: str, suffix: str = ".xlsx") -> Path | None:
    """Busca recursivamente nos subdiretórios <DATA>/<HORA>/ o arquivo mais recente."""
    files = sorted(
        (f for f in base.rglob(f"{prefix}*{suffix}") if f.is_file()),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def _cleanup_old_dirs(base: Path, keep_days: int) -> None:
    """Remove diretórios de data com mais de keep_days."""
    import shutil
    now = datetime.now()
    for d in sorted(base.glob("????-??-??")):
        if d.is_dir():
            age_days = (now - datetime.fromtimestamp(d.stat().st_mtime)).total_seconds() / 86400
            if age_days >= keep_days:
                try:
                    shutil.rmtree(d)
                    log.info(f"Removido diretório antigo: {d}")
                except OSError as exc:
                    log.warning(f"Nao conseguiu remover {d}: {exc}")