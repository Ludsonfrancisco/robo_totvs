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

def _find_frame(page: Page, url_fragment: str):
    """Encontra o primeiro frame cuja URL contém o fragmento."""
    for f in page.frames:
        if url_fragment in f.url:
            return f
    return None


def baixar_backlog_routerbox(
    page: Page,
    instance: RouterBoxInstance,
    destino: Path,
    usuario: str,
    senha: str,
    timeout_s: int = 180,
) -> Path:
    """Baixa XLSX de backlog de uma instância RouterBox via Playwright.

    Fluxo validado (13/07/2026):
      1. Login via page.fill() — ScriptCase ignora page.evaluate(JS)
      2. Menu hamburger -> "Atendimentos/Execução" (texto exato)
      3. Frame cons_atendimentos -> #pesq_top (abre form de filtro)
      4. #sel_recup_filters_bot -> seleciona filtro salvo
      5. #sc_b_pesq_bot (pesquisar rodapé)
      6. #sc_GerarXLS_top (botão Excel)
      7. Link "Baixar" -> download

    Retorna o Path do arquivo salvo.
    """
    name = instance.name.lower()
    url = instance.url
    filter_label = instance.filter_label

    log.info(f"== {name}: abrindo login {url} ==")

    # ===== STEP 1: LOGIN via page.fill() =====
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    # page.fill() dispara eventos DOM corretos (keydown/keyup/input/change).
    # page.evaluate(JS) não dispora esses eventos e o ScriptCase ignora o valor.
    page.fill('input[name="usuario"]', usuario, timeout=5000)
    page.fill('input[name="senha"]', senha, timeout=5000)
    log.info(f"OK {name}: login preenchido via page.fill()")

    # Entrar: o botão <a id="sub_form_b"> tem onclick="scBtnFn_sys_format_ok()"
    try:
        page.click('#sub_form_b', timeout=5000)
    except Exception:
        # Fallbacks
        for sel in ['a:has-text("Entrar")', 'input[value*="Entrar"]', 'text=Entrar']:
            try:
                page.click(sel, timeout=3000)
                break
            except Exception:
                continue
        else:
            page.keyboard.press("Enter")

    page.wait_for_load_state("domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)

    # Verificar se login funcionou
    still_login = page.evaluate(
        "() => document.body ? document.body.innerText.includes('Esqueci minha senha') : false"
    )
    if still_login:
        raise RuntimeError(
            f"{name}: login rejeitado pelo servidor — verificar credenciais"
        )
    log.info(f"OK {name}: login aceito -> {page.url[:60]}")

    _fechar_modal_novidades(page)

    # ===== STEP 2: MENU hamburger -> Atendimentos/Execução =====
    log.info(f"{name}: menu hamburger")
    try:
        page.click(
            'xpath=//*[@id="idMenuHeader"]/td/header/div/div[1]/div/div[1]',
            timeout=5000,
        )
    except Exception:
        page.evaluate("""() => {
            for (const el of document.querySelectorAll('#idMenuHeader, [class*="toggle"], [class*="menu"]')) {
                el.click();
            }
        }""")
    page.wait_for_timeout(1500)

    # Clicar "Atendimentos/Execução" (texto exato, não "Atendimentos" + "Execução" separados)
    menu_result = page.evaluate("""() => {
        for (const el of document.querySelectorAll('a, [id^="item_"], li, td, div, span')) {
            const t = (el.textContent || '').trim();
            if (t === 'Atendimentos/Execução' || t === 'Atendimentos/Execucao') {
                el.click();
                el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                return t;
            }
        }
        // Fuzzy: "Execu" + "Atend" e texto curto
        for (const el of document.querySelectorAll('a, [id^="item_"], li')) {
            const t = (el.textContent || '').trim();
            if (t.includes('Execu') && t.includes('Atend') && t.length < 60) {
                el.click();
                return 'fuzzy: ' + t;
            }
        }
        return 'NAO ACHOU';
    }""")
    if "NAO ACHOU" in menu_result:
        raise RuntimeError(f"{name}: item de menu 'Atendimentos/Execução' não encontrado")
    log.info(f"OK {name}: menu '{menu_result}' clicado")
    page.wait_for_timeout(6000)

    # ===== STEP 3: ACHAR FRAME cons_atendimentos =====
    target = _find_frame(page, "cons_atendimentos")
    if not target:
        # Fallback: qualquer frame com "atendimentos" que não seja menu/novidades
        for f in page.frames:
            if "atendimentos" in f.url and "app_menu" not in f.url and "novidades" not in f.url:
                target = f
                break
    if not target:
        raise RuntimeError(f"{name}: frame cons_atendimentos não encontrado após navegação")
    log.info(f"OK {name}: frame alvo = {target.url[:80]}")

    # ===== STEP 4: PESQUISAR (abre form de filtro) =====
    try:
        target.locator('#pesq_top').click(timeout=10000)
        log.info(f"OK {name}: #pesq_top clicado")
    except Exception:
        target.evaluate("() => { const el = document.querySelector('#pesq_top'); if (el) el.click(); }")
        log.info(f"OK {name}: #pesq_top clicado via JS")
    page.wait_for_timeout(4000)

    # ===== STEP 5: SELECIONAR FILTRO =====
    # O frame pode ter recarregado após pesq_top — re-encontrar
    target = _find_frame(page, "cons_atendimentos") or target

    filtro_sel = target.locator('select#sel_recup_filters_bot').first
    if not filtro_sel.count():
        # Buscar em todos os frames
        for f in page.frames:
            loc = f.locator('select#sel_recup_filters_bot').first
            if loc.count():
                filtro_sel = loc
                target = f
                break

    if not filtro_sel.count():
        raise RuntimeError(f"{name}: select #sel_recup_filters_bot não encontrado")

    try:
        filtro_sel.select_option(label=filter_label, timeout=10000)
        log.info(f"OK {name}: filtro selecionado (label exato)")
    except Exception:
        # Fuzzy: buscar option que contém DMAIS ou BACKLOG ou FIELD
        value = target.evaluate("""() => {
            const sel = document.querySelector('#sel_recup_filters_bot');
            if (!sel) return null;
            for (const o of sel.options) {
                const t = (o.textContent || '').trim().toUpperCase();
                if (t.includes('DMAIS') || t.includes('BACKLOG') || t.includes('FIELD'))
                    return o.value;
            }
            return null;
        }""")
        if value:
            filtro_sel.select_option(value=value)
            log.info(f"OK {name}: filtro selecionado (fuzzy value={value})")
        else:
            all_opts = target.evaluate("""() => {
                const sel = document.querySelector('#sel_recup_filters_bot');
                return sel ? Array.from(sel.options).map(o => (o.textContent||'').trim()) : [];
            }""")
            raise RuntimeError(
                f"{name}: filtro '{filter_label[:40]}' não encontrado. "
                f"Options: {all_opts[:10]}"
            )
    page.wait_for_timeout(8000)

    # ===== STEP 6: PESQUISAR RODAPÉ =====
    pesq_ok = False
    for f in page.frames:
        try:
            loc = f.locator('#sc_b_pesq_bot').first
            if loc.count():
                loc.click(timeout=10000)
                log.info(f"OK {name}: #sc_b_pesq_bot clicado")
                pesq_ok = True
                break
        except Exception:
            continue
    if not pesq_ok:
        raise RuntimeError(f"{name}: botão Pesquisar rodapé (#sc_b_pesq_bot) não encontrado")
    page.wait_for_timeout(10000)

    # ===== STEP 7: EXCEL =====
    target = _find_frame(page, "cons_atendimentos") or target
    excel_ok = False

    # Tentativa 1: botão direto #sc_GerarXLS_top
    try:
        target.locator('#sc_GerarXLS_top').first.click(timeout=10000)
        excel_ok = True
        log.info(f"OK {name}: #sc_GerarXLS_top clicado")
    except Exception:
        pass

    # Tentativa 2: grupo de botões -> "Excel"
    if not excel_ok:
        try:
            target.locator('#sc_btgp_btn_group_1_top').click(timeout=5000)
            page.wait_for_timeout(1000)
            target.evaluate("""() => {
                for (const el of document.querySelectorAll('a, button, li')) {
                    if ((el.textContent || '').includes('Excel')) { el.click(); return; }
                }
            }""")
            excel_ok = True
            log.info(f"OK {name}: Excel via grupo de botões")
        except Exception:
            pass

    # Tentativa 3: procurar "Excel" em todos os frames
    if not excel_ok:
        for f in page.frames:
            try:
                found = f.evaluate("""() => {
                    for (const el of document.querySelectorAll('a, button, li')) {
                        if ((el.textContent || '').includes('Excel')) { el.click(); return true; }
                    }
                    return false;
                }""")
                if found:
                    excel_ok = True
                    log.info(f"OK {name}: Excel via busca cross-frame")
                    break
            except Exception:
                continue

    if not excel_ok:
        raise RuntimeError(f"{name}: botão Excel não encontrado")

    # ===== STEP 8: AGUARDAR LINK BAIXAR =====
    log.info(f"{name}: aguardando link Baixar (timeout {timeout_s}s)")
    baixar_loc = None
    for i in range(timeout_s // 5):
        page.wait_for_timeout(5000)
        for f in page.frames:
            try:
                loc = f.locator('a:has-text("Baixar")').first
                if loc.count() and loc.is_visible(timeout=500):
                    baixar_loc = loc
                    break
            except Exception:
                pass
        if baixar_loc:
            log.info(f"OK {name}: link Baixar apareceu após {(i + 1) * 5}s")
            break

    # Fallback: link .xlsx direto
    if not baixar_loc:
        for f in page.frames:
            try:
                loc = f.locator('a[href*=".xlsx"]').first
                if loc.count() and loc.is_visible(timeout=1000):
                    baixar_loc = loc
                    break
            except Exception:
                pass

    if not baixar_loc:
        raise RuntimeError(f"{name}: link Baixar/.xlsx não encontrado após {timeout_s}s")

    # ===== STEP 9: DOWNLOAD =====
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