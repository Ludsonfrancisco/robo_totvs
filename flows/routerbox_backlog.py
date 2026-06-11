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
    """Clica no primeiro elemento visível que encontrar."""
    loc, ctx, sel = _first_visible(page, selectors, timeout=timeout)
    loc.click(timeout=timeout)
    log.info(f"OK click {name}: {sel}")
    page.wait_for_timeout(1000)
    return loc


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

    u, _, us = _first_visible(page, user_selectors, timeout=10000)
    u.fill(usuario)
    log.info(f"OK usuário preenchido via {us}")

    p, _, ps = _first_visible(page, pass_selectors, timeout=10000)
    p.fill(senha)
    log.info(f"OK senha preenchida via {ps}")

    try:
        _click_any(page, [
            'button:has-text("Entrar")', 'input[value*="Entrar"]',
            'a:has-text("Entrar")', 'text=Entrar',
        ], 'entrar', timeout=5000)
    except Exception:
        page.keyboard.press("Enter")
        log.info("OK submit via Enter")

    page.wait_for_load_state("domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)

    _fechar_modal_novidades(page)

    # Navegação: hamburger → Atendimentos → Execução
    _click_any(page, [
        'xpath=//*[@id="idMenuHeader"]/td/header/div/div[1]/div/div[1]',
        '#idMenuHeader header div div div',
        '.menu-button, .hamburger, [class*="hamb"]',
    ], 'hamburger', timeout=10000)

    _click_any(page, [
        'text=Atendimentos', '.menu__item:has-text("Atendimentos")',
        'a:has-text("Atendimentos")',
    ], 'Atendimentos', timeout=10000)

    _click_any(page, [
        '#item_59', 'a#item_59', 'a:has-text("Execução")',
        'text=Execução',
    ], 'Execução', timeout=10000)
    page.wait_for_timeout(5000)

    # Pesquisar (topo)
    _click_any(page, [
        '#pesq_top', 'a#pesq_top', 'a:has-text("Pesquisar")',
        'text=Pesquisar',
    ], 'Pesquisar topo', timeout=15000)
    page.wait_for_timeout(3000)

    # Selecionar filtro salvo
    filtro_ok = False
    for ctx in _contexts(page):
        try:
            sel = ctx.locator('select#sel_recup_filters_bot, select[name="sel_recup_filters_bot"]').first
            if sel.count():
                sel.scroll_into_view_if_needed(timeout=5000)
                try:
                    sel.select_option(label=filter_label, timeout=10000)
                    log.info(f"OK {name}: filtro selecionado por label")
                    page.wait_for_timeout(6000)
                    filtro_ok = True
                    break
                except Exception:
                    value = sel.evaluate("""(s, wanted) => {
                        for (const o of s.options) {
                            if ((o.textContent || '').trim() === wanted) return o.value;
                        }
                        return null;
                    }""", filter_label)
                    if value:
                        sel.select_option(value=value)
                        log.info(f"OK {name}: filtro selecionado por value={value}")
                        page.wait_for_timeout(6000)
                        filtro_ok = True
                        break
        except Exception:
            continue
    if not filtro_ok:
        raise RuntimeError(f"{name}: filtro '{filter_label}' não encontrado")

    # Pesquisar (rodapé)
    _click_any(page, [
        '#sc_b_pesq_bot', 'a#sc_b_pesq_bot', 'button#sc_b_pesq_bot',
        'input#sc_b_pesq_bot', 'text=Pesquisar',
    ], 'Pesquisar rodapé', timeout=15000)
    page.wait_for_timeout(8000)

    # Grupo botões → Excel
    _click_any(page, [
        '#sc_btgp_btn_group_1_top', 'button#sc_btgp_btn_group_1_top',
        'a#sc_btgp_btn_group_1_top', '[id="sc_btgp_btn_group_1_top"]',
    ], 'grupo botoes', timeout=15000)
    page.wait_for_timeout(1000)

    _click_any(page, [
        'text=Excel', 'a:has-text("Excel")', 'button:has-text("Excel")',
        'li:has-text("Excel")',
    ], 'Excel', timeout=15000)
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

    out = Path(output_dir) if output_dir else Path(settings.ROUTERBOX_OUTPUT_DIR)
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

    today = datetime.now().strftime("%Y-%m-%d")
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
                    baixar_backlog_routerbox(
                        page=page,
                        instance=inst,
                        destino=destino,
                        usuario=settings.ROUTERBOX_USER,
                        senha=settings.ROUTERBOX_PASS,
                        timeout_s=settings.ROUTERBOX_DOWNLOAD_TIMEOUT_S,
                    )
                    downloaded[inst.name] = destino
                    fresh_downloads.add(inst.name)
                except Exception as exc:
                    log.error(f"Erro ao baixar {inst.name}: {exc}")
                    # Fallback: usar o XLSX mais recente disponivel dessa instancia
                    fallback = _find_latest(out, prefix=f"{inst.name.lower()}_backlog_", suffix=".xlsx")
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

    consolidado_path = out / f"BACKLOG-GERAL-CONSOLIDADO-{today}.xlsx"
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
        manifest_path = out / f"manifest-{today}.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(f"Manifest salvo: {manifest_path}")

        # Limpar arquivos antigos (manter apenas os ultimos 12h)
        _cleanup_old_files(out, keep_hours=12, prefix="BACKLOG-GERAL-CONSOLIDADO-")
        _cleanup_old_files(out, keep_hours=12, prefix="manifest-")
        _cleanup_old_files(out, keep_hours=12, prefix="acerta_backlog_")
        _cleanup_old_files(out, keep_hours=12, prefix="loga_backlog_")

        if fallback_downloads:
            log.warning(f"RouterBox consolidado com fallback: {sorted(fallback_downloads)}")
            return 1
        return 0

    except Exception as exc:
        log.error(f"Erro na consolidação: {exc}")
        return 1


def _find_latest(directory: Path, prefix: str, suffix: str = ".xlsx") -> Path | None:
    """Encontra o arquivo mais recente com o prefixo e sufixo dados."""
    files = sorted(
        (f for f in directory.glob(f"{prefix}*{suffix}") if f.is_file()),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def _cleanup_old_files(directory: Path, keep_hours: int, prefix: str) -> None:
    """Remove arquivos com o prefixo dado mais antigos que keep_hours."""
    import re as _re
    now = datetime.now()
    for f in directory.glob(f"{prefix}*"):
        if f.is_file():
            age_hours = (now - datetime.fromtimestamp(f.stat().st_mtime)).total_seconds() / 3600
            if age_hours >= keep_hours:
                try:
                    f.unlink()
                    log.info(f"Removido arquivo antigo: {f}")
                except OSError as exc:
                    log.warning(f"Nao conseguiu remover {f}: {exc}")