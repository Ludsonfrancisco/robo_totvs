"""Ações de alto nível sobre o Protheus.

Sprint 2: `fazer_login()` — passos 01–07 das referências.

Convenções:
- Toda etapa tira screenshot de evidência em caso de falha.
- Senha NUNCA é logada em texto claro (apenas a digitação via teclado, sem registro).
- Retry via tenacity (3 tentativas, backoff exponencial) para falhas
  transientes; credencial inválida não é re-tentada (exceção dedicada).
"""

from __future__ import annotations

from playwright.sync_api import Frame, Locator, Page
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.config import settings
from core.log import log
from core.navegador import tirar_screenshot
from core.visao import aguardar_imagem, clicar_imagem


class LoginError(Exception):
    """Falha transiente de login — elegível para retry."""


class CredenciaisInvalidasError(Exception):
    """Credenciais rejeitadas pelo Protheus — não retentar."""


def _clicar_ok_programa_inicial(page: Page) -> bool:
    """Passo 02 — diálogo 'Programa Inicial / Ambiente / OK'.

    Tenta DOM primeiro (PRD §3.3: DOM tem prioridade quando o elemento é HTML
    acessível — caso da tela inicial). Fallback: template matching pela seta.
    """
    # Estratégia 1 — DOM (Playwright locator).
    try:
        botao = page.get_by_role("button", name="OK").first
        botao.wait_for(state="visible", timeout=5000)
        botao.click()
        log.bind(etapa="login").info("OK do programa inicial clicado via DOM")
        return True
    except Exception as e:
        log.bind(etapa="login").debug(f"DOM locator falhou ({e!s}), caindo para matching")

    # Estratégia 2 — template matching com a seta de `02_clicar_em_ok.png`.
    centro = aguardar_imagem(page, "02_clicar_em_ok.png", timeout=15)
    if centro is None:
        return False
    page.mouse.click(*centro)
    log.bind(etapa="login").info(f"OK do programa inicial clicado via matching em {centro}")
    return True


def _localizar_em_frames(
    page: Page, seletores: list[str], timeout_ms: int = 10_000
) -> Locator | None:
    """Polling rápido em main + iframes — o Protheus carrega a tela de login
    dentro de um iframe que aparece tardiamente.
    """
    import time as _time
    deadline = _time.monotonic() + timeout_ms / 1000.0
    while _time.monotonic() < deadline:
        contextos: list[Frame | Page] = [page, *page.frames]
        for ctx in contextos:
            for sel in seletores:
                try:
                    loc = ctx.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=300):
                        return loc
                except Exception:
                    continue
        _time.sleep(0.3)
    return None


def _frame_login(page: Page, timeout_ms: int = 60_000) -> Frame | None:
    """Localiza o frame que hospeda o formulário de login.

    Estratégia: busca o frame (main ou iframe) que contém `input[name="login"]`.
    O Protheus carrega o form num iframe `WA-WEBVIEW` que aparece tardiamente;
    detectar pelo input é mais robusto do que casar pela URL do iframe (que
    embute identificador de ambiente — `protheuslib-tface_env_<id>_prod/login`).
    """
    import time as _time
    deadline = _time.monotonic() + timeout_ms / 1000.0
    urls_vistas: set[str] = set()
    while _time.monotonic() < deadline:
        for f in page.frames:
            if f.url and f.url not in urls_vistas:
                urls_vistas.add(f.url)
                log.bind(etapa="login").debug(f"frame visto: {f.url!r}")
            try:
                campo = f.locator('input[name="login"]').first
                if campo.count() > 0 and campo.is_visible(timeout=200):
                    log.bind(etapa="login").debug(
                        f"frame de login localizado: {f.url!r}"
                    )
                    return f
            except Exception:
                continue
        _time.sleep(0.4)
    log.bind(etapa="login").warning(
        f"frame de login não encontrado em {timeout_ms}ms — frames vistos: {urls_vistas}"
    )
    return None


def _detectar_credencial_invalida(page: Page) -> bool:
    """Heurística: home não chegou e ainda vemos a tela de usuário/senha."""
    try:
        return page.locator('input[type="password"]').first.is_visible(timeout=2000)
    except Exception:
        return aguardar_imagem(page, "03_insira_usuario.png", timeout=3) is not None


def _preencher_usuario(page: Page) -> bool:
    """Passo 03 — preencher usuário. DOM-first via iframe de login."""
    frame = _frame_login(page, timeout_ms=60_000)
    if frame is not None:
        try:
            campo = frame.locator('input[name="login"]').first
            campo.wait_for(state="visible", timeout=10_000)
            campo.click()
            campo.fill("")
            campo.type(settings.PROTHEUS_USER, delay=20)
            log.bind(etapa="login").info(f"usuário preenchido via DOM ({settings.PROTHEUS_USER})")
            return True
        except Exception as e:
            log.bind(etapa="login").debug(f"DOM usuário falhou ({e!s})")

    log.bind(etapa="login").debug("caindo para matching no campo usuário")
    if not clicar_imagem(page, "03_insira_usuario.png", timeout=15, threshold=0.35):
        return False
    page.keyboard.type(settings.PROTHEUS_USER, delay=30)
    log.bind(etapa="login").info(f"usuário preenchido via matching ({settings.PROTHEUS_USER})")
    return True


def _preencher_senha(page: Page) -> bool:
    """Passo 04 — preencher senha (NUNCA logar valor)."""
    frame = _frame_login(page, timeout_ms=15_000)
    if frame is not None:
        try:
            campo = frame.locator('input[name="password"]').first
            campo.wait_for(state="visible", timeout=10_000)
            campo.click()
            campo.fill("")
            campo.type(settings.PROTHEUS_PASS, delay=20)
            log.bind(etapa="login").info("senha preenchida via DOM (mascarada)")
            return True
        except Exception as e:
            log.bind(etapa="login").debug(f"DOM senha falhou ({e!s})")

    log.bind(etapa="login").debug("caindo para matching no campo senha")
    if not clicar_imagem(page, "04_insira_senha.png", timeout=15, threshold=0.35):
        return False
    page.keyboard.type(settings.PROTHEUS_PASS, delay=30)
    log.bind(etapa="login").info("senha preenchida via matching (mascarada)")
    return True


def _clicar_entrar(page: Page) -> bool:
    """Passo 05 — botão Entrar. DOM-first via iframe de login."""
    frame = _frame_login(page, timeout_ms=15_000)
    if frame is not None:
        seletores = [
            'button:has-text("Entrar"):not([disabled])',
            'button:has-text("Entrar")',
            'input[type="submit"]',
        ]
        for sel in seletores:
            try:
                btn = frame.locator(sel).first
                btn.wait_for(state="visible", timeout=3000)
                btn.click()
                log.bind(etapa="login").info(f"Entrar clicado via DOM ({sel})")
                return True
            except Exception:
                continue
        # Fallback: pressionar Enter no campo de senha (formulário submete).
        try:
            frame.locator('input[name="password"]').press("Enter")
            log.bind(etapa="login").info("Entrar acionado via Enter no campo senha")
            return True
        except Exception as e:
            log.bind(etapa="login").debug(f"DOM Entrar (Enter) falhou ({e!s})")

    log.bind(etapa="login").debug("caindo para matching no botão Entrar")
    return clicar_imagem(page, "05_clicar_entrar.png", timeout=15, threshold=0.35)


def _passo_06_confirmacao_opcional(page: Page, timeout_s: int = 6) -> bool:
    """Passo 06 (opcional) — segunda confirmação 'Entrar' se aparecer.

    Per fluxo_totvs.md §[06]: 'Apenas se houver segunda confirmação'.
    Probe curto: se aparecer, clica; senão segue. Não falha o login.
    """
    import time as _time
    deadline = _time.monotonic() + timeout_s

    # DOM-first approach for second 'Entrar' across all frames
    seletores = [
        'button:has-text("Entrar"):not([disabled])',
        'button:has-text("Entrar")'
    ]
    while _time.monotonic() < deadline:
        contextos = [page, *page.frames]
        for ctx in contextos:
            for sel in seletores:
                try:
                    btn = ctx.locator(sel).last
                    if btn.is_visible(timeout=500):
                        btn.click()
                        log.bind(etapa="login").info(f"passo 06: confirmação clicada via DOM ({sel})")
                        return True
                except Exception:
                    continue
        _time.sleep(0.5)

    # Fallback to matching
    centro = aguardar_imagem(page, "06_clicar_entrar.png", timeout=1)
    if centro is not None:
        page.mouse.click(*centro)
        log.bind(etapa="login").info(f"passo 06: confirmação clicada em {centro}")
        return True

    log.bind(etapa="login").debug("passo 06 não apareceu via DOM ou match — seguindo")
    return False


def _aguardar_home(page: Page, timeout_s: int = 60) -> bool:
    """Valida home pós-login: tenta DOM (Favoritos/menu) e cai para matching."""
    import time as _time
    deadline = _time.monotonic() + timeout_s
    seletores_home = [
        'text="Favoritos"',
        '[aria-label="Favoritos"]',
        'po-menu',
        'wa-menu',
    ]
    while _time.monotonic() < deadline:
        for ctx in [page, *page.frames]:
            for sel in seletores_home:
                try:
                    loc = ctx.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=200):
                        log.bind(etapa="login").info(f"home detectada via DOM ({sel})")
                        return True
                except Exception:
                    continue
        # Fallback paralelo: matching com a referência da home.
        if aguardar_imagem(page, "07_pagina_home_clicar_favoritos.png", timeout=2) is not None:
            log.bind(etapa="login").info("home detectada via matching")
            return True
        _time.sleep(0.5)
    return False


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(LoginError),
    reraise=True,
)
def _executar_login(page: Page) -> None:
    log.bind(etapa="login").info(f"abrindo {settings.PROTHEUS_URL}")
    page.goto(settings.PROTHEUS_URL, wait_until="domcontentloaded")
    # Passo 01 — barra de URL fica fora da viewport do screenshot, então
    # não dá para validar via template matching. Logamos a URL ativa.
    log.bind(etapa="login").info(f"url ativa: {page.url}")

    # Passo 02 — diálogo 'Programa Inicial / Ambiente / OK'.
    if not _clicar_ok_programa_inicial(page):
        tirar_screenshot(page, etapa="login_02_programa_inicial", evidencia=True)
        raise LoginError("passo 02: diálogo 'Programa Inicial' não localizado")

    # Passo 03 — campo usuário (DOM-first com fallback matching).
    if not _preencher_usuario(page):
        tirar_screenshot(page, etapa="login_03_usuario", evidencia=True)
        raise LoginError("passo 03: campo usuário não localizado")

    # Passo 04 — campo senha.
    if not _preencher_senha(page):
        tirar_screenshot(page, etapa="login_04_senha", evidencia=True)
        raise LoginError("passo 04: campo senha não localizado")

    # Passo 05 — botão Entrar.
    if not _clicar_entrar(page):
        tirar_screenshot(page, etapa="login_05_entrar", evidencia=True)
        raise LoginError("passo 05: botão Entrar não localizado")

    # Passo 06 — segunda confirmação (opcional, só se aparecer).
    _passo_06_confirmacao_opcional(page, timeout_s=6)

    # Passo 07 — validar home (favoritos visível).
    if not _aguardar_home(page, timeout_s=60):
        if _detectar_credencial_invalida(page):
            tirar_screenshot(page, etapa="login_credencial_invalida", evidencia=True)
            raise CredenciaisInvalidasError(
                f"credenciais rejeitadas para usuário {settings.PROTHEUS_USER}"
            )
        tirar_screenshot(page, etapa="login_07_home", evidencia=True)
        raise LoginError("passo 07: home (favoritos) não detectada após login")

    log.bind(etapa="login").success(
        f"login concluído (usuário={settings.PROTHEUS_USER})"
    )


def fazer_login(page: Page) -> None:
    """Executa login com retry (3x, backoff exponencial).

    Lança:
      - CredenciaisInvalidasError: credenciais rejeitadas (exit code 2 no main).
      - LoginError: falha transiente após esgotar retries.
    """
    try:
        _executar_login(page)
    except CredenciaisInvalidasError:
        log.bind(etapa="login").error("login abortado — credenciais inválidas")
        raise
    except RetryError as e:
        # tenacity com reraise=True normalmente re-lança a exceção original;
        # mantemos esta cláusula como rede de segurança.
        log.bind(etapa="login").error(f"login falhou após retries: {e}")
        raise LoginError(str(e)) from e
    except LoginError as e:
        log.bind(etapa="login").error(f"login falhou após retries: {e}")
        raise


class NavegacaoError(Exception):
    """Falha transiente na navegação até a rotina."""

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(NavegacaoError),
    reraise=True,
)
def _executar_navegacao_rotina(page: Page) -> None:
    import time as _time
    
    # Passo 07: Abrir favoritos
    log.bind(etapa="navegacao").info("Aguardando e clicando em Favoritos (07)...")
    clicou_fav = False
    seletores_fav = [
        '[title="Favoritos"]',
        'text="Favoritos"',
        '[aria-label="Favoritos"]',
        '.wa-menu-item:has-text("Favoritos")',
        'po-menu-item:has-text("Favoritos")',
        'a:has-text("Favoritos")'
    ]
    for ctx in [page, *page.frames]:
        for sel in seletores_fav:
            try:
                loc = ctx.locator(sel).first
                if loc.is_visible(timeout=500):
                    loc.click()
                    log.bind(etapa="navegacao").info(f"Clicou em Favoritos via DOM ({sel})")
                    clicou_fav = True
                    break
            except Exception:
                continue
        if clicou_fav:
            break
            
    if not clicou_fav:
        clicou_fav = clicar_imagem(page, "07_pagina_home_clicar_favoritos.png", timeout=15, threshold=0.65)
        
    if not clicou_fav:
        tirar_screenshot(page, etapa="falha_07_favoritos", evidencia=True)
        raise NavegacaoError("Passo 07: Falha ao clicar em Favoritos")
        
    _time.sleep(2) # Aguarda animação do menu
    
    # Passo 08: Abrir relatório
    log.bind(etapa="navegacao").info("Aguardando e clicando em Mat Estoque Por Tecnico (08)...")
    clicou_rel = False
    for ctx in [page, *page.frames]:
        try:
            loc = ctx.locator('text="Mat Estoque Por Tecnico"').first
            if loc.is_visible(timeout=1000):
                loc.click()
                log.bind(etapa="navegacao").info("Clicou no relatório via DOM")
                clicou_rel = True
                break
        except Exception:
            pass
            
    if not clicou_rel:
        clicou_rel = clicar_imagem(page, "08_clicar_Mat_Estoque_Por_Tecnico.png", timeout=15, threshold=0.65)
        
    if not clicou_rel:
        tirar_screenshot(page, etapa="falha_08_relatorio", evidencia=True)
        raise NavegacaoError("Passo 08: Falha ao clicar no relatório")

    _time.sleep(3)
    
    # Passo 09: Confirmar tela
    log.bind(etapa="navegacao").info("Aguardando e clicando em Confirmar (09)...")
    clicou_conf = False
    seletores_conf = [
        'button:has-text("Confirmar"):not([disabled])',
        'button:has-text("Confirmar")',
        '[title="Confirmar"]'
    ]
    for ctx in [page, *page.frames]:
        for sel in seletores_conf:
            try:
                loc = ctx.locator(sel).first
                if loc.is_visible(timeout=500):
                    loc.click()
                    log.bind(etapa="navegacao").info(f"Clicou em Confirmar via DOM ({sel})")
                    clicou_conf = True
                    break
            except Exception:
                continue
        if clicou_conf:
            break
            
    if not clicou_conf:
        clicou_conf = clicar_imagem(page, "09_clicar_confirmar.png", timeout=15, threshold=0.65)
        
    if not clicou_conf:
        tirar_screenshot(page, etapa="falha_09_confirmar", evidencia=True)
        raise NavegacaoError("Passo 09: Falha ao clicar em Confirmar")

    _time.sleep(3)

    # Passo 10: Popup opcional "Não exibir nos próximos 7 dias"
    log.bind(etapa="navegacao").info("Verificando popup 7 dias (10)...")
    clicou_7d = False
    seletores_7d = [
        'text="Não exibir nos próximos 7 dias"',
        'label:has-text("Não exibir nos próximos 7 dias")',
        'span:has-text("Não exibir nos próximos 7 dias")'
    ]
    deadline_7d = _time.monotonic() + 4
    while _time.monotonic() < deadline_7d and not clicou_7d:
        for ctx in [page, *page.frames]:
            for sel in seletores_7d:
                try:
                    loc = ctx.locator(sel).first
                    if loc.is_visible(timeout=200):
                        loc.click()
                        log.bind(etapa="navegacao").info(f"Clicou popup 7 dias via DOM ({sel})")
                        clicou_7d = True
                        break
                except Exception:
                    continue
            if clicou_7d:
                break
        _time.sleep(0.5)

    if not clicou_7d:
        centro = aguardar_imagem(page, "10_caso-aparececa_clicar_Nao_exibir_proximos_7_dias.png", timeout=1, threshold=0.65)
        if centro is not None:
            page.mouse.click(*centro)
            log.bind(etapa="navegacao").info(f"Clicou popup 7 dias via matching em {centro}")
            clicou_7d = True
            
    if not clicou_7d:
        log.bind(etapa="navegacao").debug("Popup 7 dias não apareceu — seguindo")

    # Passo 11: Validar que a tela de filtro de técnico carregou (campo código)
    log.bind(etapa="navegacao").info("Aguardando campo de código do técnico (11)...")
    campo_codigo = aguardar_imagem(page, "11_colocar_o_codigo_tecnico.png", timeout=20, threshold=0.65)
    if campo_codigo is None:
        # Tenta DOM
        encontrou_dom = False
        seletores_arm = [
            'input[name="Armazem"]',
            'input[aria-label="Armazém"]',
            'label:has-text("Armazém") + input',
            'input.po-input' # fallback
        ]
        for ctx in [page, *page.frames]:
            for sel in seletores_arm:
                try:
                    if ctx.locator(sel).first.is_visible(timeout=500):
                        encontrou_dom = True
                        break
                except Exception:
                    continue
            if encontrou_dom:
                break
        
        if not encontrou_dom:
            tirar_screenshot(page, etapa="falha_11_campo_codigo", evidencia=True)
            raise NavegacaoError("Passo 11: Campo de código (Armazém) não carregou")
        
    log.bind(etapa="navegacao").success("Rotina alcançada com sucesso (passo 11)")

def navegar_ate_rotina(page: Page) -> None:
    """Navega da home até a tela de filtro do relatório com retry (3x)."""
    try:
        _executar_navegacao_rotina(page)
    except RetryError as e:
        log.bind(etapa="navegacao").error(f"Navegação falhou após retries: {e}")
        raise NavegacaoError(str(e)) from e
    except NavegacaoError as e:
        log.bind(etapa="navegacao").error(f"Navegação falhou: {e}")
        raise


class DownloadError(Exception):
    """Falha transiente no fluxo de preenchimento e download do XLSX."""

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(DownloadError),
    reraise=True,
)
def _executar_download(page: Page, code: str, name: str) -> dict:
    import time as _time
    import os
    import re
    import unicodedata
    import hashlib
    import zipfile
    from datetime import datetime
    from core.visao import validar_texto_ocr
    
    log.bind(etapa="download", tecnico=code).info(f"Iniciando download para o técnico {code}...")
    
    # Passo 11: Inserir código
    if not clicar_imagem(page, "11_colocar_o_codigo_tecnico.png", timeout=15, threshold=0.65):
        tirar_screenshot(page, etapa="falha_11_inserir_codigo", evidencia=True)
        raise DownloadError("Falha ao clicar no campo de código do técnico (11)")
        
    page.keyboard.type(code, delay=30)
    _time.sleep(1) # Aguarda input registrar
    
    # Passo 12: Confirmar código (OK)
    clicou_ok_12 = False
    seletores_ok_12 = [
        'button:has-text("OK"):not([disabled])',
        'button:has-text("OK")',
        '[title="OK"]'
    ]
    for ctx in [page, *page.frames]:
        for sel in seletores_ok_12:
            try:
                loc = ctx.locator(sel).last
                if loc.is_visible(timeout=500):
                    loc.click()
                    log.bind(etapa="download", tecnico=code).info(f"Clicou OK via DOM ({sel})")
                    clicou_ok_12 = True
                    break
            except Exception:
                continue
        if clicou_ok_12:
            break
            
    if not clicou_ok_12:
        clicou_ok_12 = clicar_imagem(page, "12_clicar_OK.png", timeout=15, threshold=0.65)
        
    if not clicou_ok_12:
        tirar_screenshot(page, etapa="falha_12_confirmar_codigo", evidencia=True)
        raise DownloadError("Falha ao clicar em OK para confirmar o código (12)")
        
    _time.sleep(3) # Tempo para a tela atualizar e (opcionalmente) carregar nome
    
    # Passo F3.3 (Opcional): Validação OCR
    if name:
        log.bind(etapa="download", tecnico=code).info("Validando nome via OCR...")
        validar_texto_ocr(page, name) # Apenas loga warning em caso de mismatch (não bloqueia)
    
    # Passo 13: Selecionar Planilha
    clicou_planilha = False
    seletores_planilha = [
        'text="Planilha"',
        'span:has-text("Planilha")',
        'a:has-text("Planilha")'
    ]
    for ctx in [page, *page.frames]:
        for sel in seletores_planilha:
            try:
                loc = ctx.locator(sel).first
                if loc.is_visible(timeout=500):
                    loc.click()
                    log.bind(etapa="download", tecnico=code).info(f"Clicou em Planilha via DOM ({sel})")
                    clicou_planilha = True
                    break
            except Exception:
                continue
        if clicou_planilha:
            break
            
    if not clicou_planilha:
        clicou_planilha = clicar_imagem(page, "13_clicar_planilha.png", timeout=15, threshold=0.65)
        
    if not clicou_planilha:
        tirar_screenshot(page, etapa="falha_13_planilha", evidencia=True)
        raise DownloadError("Falha ao clicar na aba Planilha (13)")
        
    _time.sleep(1)
    
    # Passo 14: Selecionar tipo de planilha
    clicou_tipo = False
    for ctx in [page, *page.frames]:
        try:
            # Look for the combo box representing "Tipo de planilha" or just click the combo icon near it.
            # In TOTVS the combobox is often adjacent to the label.
            # Or just rely on template match as it's a very specific combobox
            pass
        except Exception:
            continue
            
    if not clicou_tipo:
        clicou_tipo = clicar_imagem(page, "14_clicar_tipo_de_planilha.png", timeout=15, threshold=0.65)
        
    if not clicou_tipo:
        tirar_screenshot(page, etapa="falha_14_tipo_planilha", evidencia=True)
        raise DownloadError("Falha ao abrir dropdown Tipo de Planilha (14)")
        
    _time.sleep(1)
    
    # Passo 15: Definir formato XLSX
    clicou_formato = False
    seletores_formato = [
        'text="Formato de Tabela (.XLSX)"',
        'text="Formato de Tabela (.xlsx)"',
        'text="Formato de Tabela XLSX"',
        'li:has-text("XLSX")',
        'span:has-text("XLSX")'
    ]
    for ctx in [page, *page.frames]:
        for sel in seletores_formato:
            try:
                loc = ctx.locator(sel).first
                if loc.is_visible(timeout=500):
                    loc.click()
                    log.bind(etapa="download", tecnico=code).info(f"Clicou em formato XLSX via DOM ({sel})")
                    clicou_formato = True
                    break
            except Exception:
                continue
        if clicou_formato:
            break
            
    if not clicou_formato:
        clicou_formato = clicar_imagem(page, "15_clicar_Formato_de_Tabela_xlsx.png", timeout=15, threshold=0.65)
        
    if not clicou_formato:
        tirar_screenshot(page, etapa="falha_15_formato_xlsx", evidencia=True)
        raise DownloadError("Falha ao selecionar formato XLSX (15)")
        
    _time.sleep(1)
    
    # Passo 16: Gerar relatório (Imprimir)
    clicou_imprimir = False
    seletores_imprimir = [
        'button:has-text("Imprimir"):not([disabled])',
        'button:has-text("Imprimir")',
        '[title="Imprimir"]'
    ]
    for ctx in [page, *page.frames]:
        for sel in seletores_imprimir:
            try:
                loc = ctx.locator(sel).last
                if loc.is_visible(timeout=500):
                    loc.click()
                    log.bind(etapa="download", tecnico=code).info(f"Clicou em Imprimir via DOM ({sel})")
                    clicou_imprimir = True
                    break
            except Exception:
                continue
        if clicou_imprimir:
            break
            
    if not clicou_imprimir:
        clicou_imprimir = clicar_imagem(page, "16_clicar_Imprimir.png", timeout=15, threshold=0.65)
        
    if not clicou_imprimir:
        tirar_screenshot(page, etapa="falha_16_imprimir", evidencia=True)
        raise DownloadError("Falha ao clicar em Imprimir (16)")
        
    _time.sleep(2)
    
    # Passo 17: Confirmar download (Sim) e capturar arquivo
    log.bind(etapa="download", tecnico=code).info("Aguardando evento de download (timeout 60s)...")
    try:
        with page.expect_download(timeout=settings.DOWNLOAD_TIMEOUT_S * 1000) as download_info:
            clicou_sim = False
            seletores_sim = [
                'button:has-text("Sim"):not([disabled])',
                'button:has-text("Sim")'
            ]
            for ctx in [page, *page.frames]:
                for sel in seletores_sim:
                    try:
                        loc = ctx.locator(sel).last
                        if loc.is_visible(timeout=500):
                            loc.click()
                            log.bind(etapa="download", tecnico=code).info(f"Clicou em Sim via DOM ({sel})")
                            clicou_sim = True
                            break
                    except Exception:
                        continue
                if clicou_sim:
                    break
                    
            if not clicou_sim:
                clicou_sim = clicar_imagem(page, "17_clique_Sim.png", timeout=15, threshold=0.65)
                
            if not clicou_sim:
                tirar_screenshot(page, etapa="falha_17_sim", evidencia=True)
                raise DownloadError("Falha ao clicar em Sim (17)")
        download = download_info.value
    except Exception as e:
        tirar_screenshot(page, etapa="falha_download_timeout", evidencia=True)
        raise DownloadError(f"Timeout ou falha ao aguardar download: {e}")
        
    # Salvar e renomear o arquivo
    hoje_str = datetime.now().strftime("%Y-%m-%d")
    downloads_dia_dir = settings.downloads_dir / hoje_str
    downloads_dia_dir.mkdir(parents=True, exist_ok=True)
    
    # Normalizar nome (remover acentos, espaços -> underscores, tudo maiúsculo)
    nome_norm = name if name else code
    nome_norm = unicodedata.normalize('NFKD', nome_norm).encode('ASCII', 'ignore').decode('utf-8')
    nome_norm = re.sub(r'[^A-Za-z0-9_]', '_', nome_norm).upper()
    nome_norm = re.sub(r'_+', '_', nome_norm).strip('_')
    
    arquivo_nome = f"{code}_{nome_norm}.xlsx"
    arquivo_path = downloads_dia_dir / arquivo_nome
    
    download.save_as(arquivo_path)
    tamanho_bytes = arquivo_path.stat().st_size
    log.bind(etapa="download", tecnico=code).info(f"Download concluído: {arquivo_path} ({tamanho_bytes} bytes)")
    
    if tamanho_bytes == 0:
        tirar_screenshot(page, etapa="falha_arquivo_vazio", evidencia=True)
        raise DownloadError("Arquivo baixado tem 0 bytes")
        
    if not zipfile.is_zipfile(arquivo_path):
        tirar_screenshot(page, etapa="falha_arquivo_corrompido", evidencia=True)
        raise DownloadError("Arquivo baixado não é um XLSX/ZIP válido")
        
    # Calcular SHA-256
    with open(arquivo_path, "rb") as f:
        file_hash = hashlib.sha256(f.read()).hexdigest()
        
    # Passo 18: Retorno automático à home
    log.bind(etapa="download", tecnico=code).info("Aguardando retorno automático à rotina (18)...")
    _time.sleep(7)
    
    # Verifica se já retornou (checando tela de filtro 11 ou botões do Protheus)
    # Se o menu esquerdo (favoritos) está visível, é provável que retornou ou fechou a tela de relatório
    retornou = False
    if page.locator('text="Favoritos"').first.is_visible(timeout=5000):
        retornou = True
    else:
        retornou = aguardar_imagem(page, "11_colocar_o_codigo_tecnico.png", timeout=5, threshold=0.65) is not None
        
    if not retornou:
        log.bind(etapa="download", tecnico=code).warning("Não retornou à home em 15s. Forçando Esc.")
        page.keyboard.press("Escape")
        _time.sleep(2)
        page.keyboard.press("Escape")
        
    return {
        "status": "sucesso",
        "arquivo": str(arquivo_path),
        "tamanho_bytes": tamanho_bytes,
        "hash_sha256": file_hash
    }

def baixar_xlsx_tecnico(page: Page, code: str, name: str = "") -> dict:
    """Orquestra o download para um técnico. Recupera com reload em caso de falha."""
    try:
        return _executar_download(page, code, name)
    except RetryError as e:
        log.bind(etapa="download", tecnico=code).error(f"Download falhou após retries: {e}")
        # Retorna para a home p/ estado limpo para o próximo técnico
        page.goto(settings.PROTHEUS_URL, wait_until="domcontentloaded")
        raise DownloadError(str(e)) from e
    except DownloadError as e:
        log.bind(etapa="download", tecnico=code).error(f"Download falhou: {e}")
        page.goto(settings.PROTHEUS_URL, wait_until="domcontentloaded")
        raise
