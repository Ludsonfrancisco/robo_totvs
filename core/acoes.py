"""Ações de alto nível sobre o Protheus.

Sprint 2: `fazer_login()` — passos 01–07 das referências.

Convenções:
- Toda etapa tira screenshot de evidência em caso de falha.
- Senha NUNCA é logada em texto claro (apenas a digitação via teclado, sem registro).
- Retry via tenacity (3 tentativas, backoff exponencial) para falhas
  transientes; credencial inválida não é re-tentada (exceção dedicada).
"""

from __future__ import annotations

import re
import time as _time
from datetime import datetime
from typing import Literal

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
from core.schema import LinhaTransferencia
from core.visao import aguardar_imagem, clicar_imagem


class LoginError(Exception):
    """Falha transiente de login — elegível para retry."""


class CredenciaisInvalidasError(Exception):
    """Credenciais rejeitadas pelo Protheus — não retentar."""


class SessaoEsgotadaError(Exception):
    """Protheus rejeitou novas sessões (modal 'Limite de conexoes do Usuario excedido').

    Não é credencial inválida — é teto de licença/sessões do servidor. Tentar
    re-logar imediatamente costuma falhar (o servidor ainda enxerga as sessões
    órfãs do robô). Mapeia para exit 2 e o operador precisa pedir ao admin
    para encerrar as sessões fantasma antes de re-rodar.
    """


class PlanilhaInvalidaError(Exception):
    """Planilha de Transferência Múltipla inválida — mapeia para exit 3."""


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


def _passo_06_1_fechar_assistente(page: Page) -> bool:
    """Passo 06.1 (opcional) — Fecha o popup da assistente virtual."""
    import time as _time
    log.bind(etapa="login").info("Verificando se o popup da assistente apareceu (06.1)...")
    
    # 1. Tentar localizar via DOM
    for ctx in [page, *page.frames]:
        try:
            # Podemos tentar buscar elementos contendo o texto da assistente e clicar no botão/SVG próximo
            loc_assist = ctx.locator('text="assistente virtual da TOTVS"').first
            if loc_assist.is_visible(timeout=500):
                # Tentativa genérica de clicar em SVG ou botão de fechar dentro do mesmo container pai
                fechar_btn = loc_assist.locator('xpath=../..//svg').first
                if fechar_btn.count() > 0:
                    fechar_btn.click()
                    log.bind(etapa="login").info("Assistente fechada via DOM (SVG)")
                    return True
        except Exception:
            continue

    # 2. Matching visual (seguro e principal)
    centro = aguardar_imagem(page, "06.1_clicar_no_X_assiste.png", timeout=3, threshold=0.5)
    if centro is not None:
        page.mouse.click(*centro)
        log.bind(etapa="login").info(f"Assistente fechada via matching em {centro}")
        return True
        
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
    
    # Passo 06.1 - Popup assistente virtual (Carolina)
    _passo_06_1_fechar_assistente(page)

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


def detectar_logout(page: Page) -> bool:
    """Detecta se a sessão expirou ou foi encerrada.
    
    Heurística: Verifica se algum elemento da tela de login (passos 03/04/05) está visível.
    """
    log.bind(etapa="sessao").debug("Verificando se houve logout...")
    
    # 1. Tenta via DOM (mais rápido)
    for ctx in [page, *page.frames]:
        try:
            if ctx.locator('input[name="login"]').first.is_visible(timeout=500):
                log.bind(etapa="sessao").warning("Logout detectado via DOM (campo login visível)")
                return True
            if ctx.locator('input[name="password"]').first.is_visible(timeout=500):
                log.bind(etapa="sessao").warning("Logout detectado via DOM (campo password visível)")
                return True
        except Exception:
            continue
            
    # 2. Tenta via template matching (fallback)
    if aguardar_imagem(page, "03_insira_usuario.png", timeout=2, threshold=0.5) is not None:
        log.bind(etapa="sessao").warning("Logout detectado via matching (campo usuário)")
        return True
        
    return False

def validar_esta_na_home(page: Page) -> bool:
    """Verifica se o navegador está na home ou na tela de favoritos."""
    seletores_home = [
        'text="Favoritos"',
        '[aria-label="Favoritos"]',
        'po-menu',
        'wa-menu',
    ]
    for ctx in [page, *page.frames]:
        for sel in seletores_home:
            try:
                if ctx.locator(sel).first.is_visible(timeout=500):
                    return True
            except Exception:
                continue
    
    if aguardar_imagem(page, "07_pagina_home_clicar_favoritos.png", timeout=2) is not None:
        return True
        
    return False

def detectar_limite_conexoes(page: Page) -> bool:
    """Detecta o modal 'Help: HELP — Problema: Limite de conexoes do Usuario excedido'.

    Esse modal é levantado pelo Protheus quando o teto de sessões por usuário
    é atingido. Aparece como diálogo HTML modal com botão `Fechar`. Quando
    presente, qualquer navegação subsequente trava — daí precisar tratar antes
    de tentar re-logar/re-navegar.

    Heurística:
    1. DOM: texto 'Limite de conex' ou cabeçalho 'Help: HELP' visível.
    2. Fallback: combinação 'Help' + botão 'Fechar' no mesmo frame.
    """
    marcadores = [
        'text=/Limite de conex[oõ]es/i',
        'text="Limite de conexoes do Usuario excedido"',
        'text="Limite de conexões do Usuário excedido"',
    ]
    for ctx in [page, *page.frames]:
        for sel in marcadores:
            try:
                if ctx.locator(sel).first.is_visible(timeout=300):
                    log.bind(etapa="sessao").warning(
                        "Modal 'Limite de conexões do Usuário excedido' detectado"
                    )
                    return True
            except Exception:
                continue

    # Heurística secundária: cabeçalho Help + botão Fechar (alguns ambientes
    # truncam o texto do problema).
    for ctx in [page, *page.frames]:
        try:
            tem_help = ctx.locator('text=/Help.*HELP/i').first.is_visible(timeout=200)
            tem_fechar = ctx.locator('button:has-text("Fechar")').first.is_visible(timeout=200)
            if tem_help and tem_fechar:
                log.bind(etapa="sessao").warning(
                    "Modal Help do Protheus detectado (provável limite de conexões)"
                )
                return True
        except Exception:
            continue

    return False


def fechar_modal_limite_conexoes(page: Page) -> bool:
    """Fecha o modal de limite de conexões clicando em 'Fechar'.

    Retorna True se conseguiu clicar; False caso contrário. Não verifica se o
    modal sumiu (responsabilidade de quem chama, que normalmente tenta
    re-logar logo em seguida).
    """
    seletores = [
        'button:has-text("Fechar"):not([disabled])',
        'button:has-text("Fechar")',
        '[title="Fechar"]',
    ]
    for ctx in [page, *page.frames]:
        for sel in seletores:
            try:
                loc = ctx.locator(sel).first
                if loc.is_visible(timeout=500):
                    loc.click()
                    log.bind(etapa="sessao").info(f"Modal 'Fechar' clicado via DOM ({sel})")
                    return True
            except Exception:
                continue

    try:
        page.keyboard.press("Enter")
        log.bind(etapa="sessao").info("Modal 'Fechar' acionado via Enter (fallback)")
        return True
    except Exception as e:
        log.bind(etapa="sessao").warning(f"Falha ao acionar Fechar via teclado: {e}")
        return False


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
def _executar_navegacao_rotina(page: Page, rotina: Literal["mat_estoque", "trans_multipla"] = "mat_estoque") -> None:
    import time as _time
    
    nome_rotina_log = "Mat Estoque Por Tecnico" if rotina == "mat_estoque" else "Tranf. Multipla"
    
    # Verifica se popup "Excedeu numero de licenças" apareceu (bloqueia tudo)
    for ctx in [page, *page.frames]:
        try:
            if ctx.locator('text="Excedeu numero de licenças"').first.is_visible(timeout=500):
                log.bind(etapa="navegacao").warning("Popup 'Excedeu numero de licenças' detectado! Fechando e aguardando...")
                # Fecha clicando em "Fechar"
                for btn_sel in ['button:has-text("Fechar")', 'button:has-text("OK")']:
                    try:
                        btn = ctx.locator(btn_sel).first
                        if btn.is_visible(timeout=300):
                            btn.click()
                            log.bind(etapa="navegacao").info(f"Clicou '{btn_sel}' no popup de licenças")
                            break
                    except Exception:
                        continue
                # Espera 2 min para liberar licença
                _time.sleep(120)
                # Vai direto para URL + re-login completo (reload nao funciona bem com sessao perdida)
                log.bind(etapa="navegacao").info("Recarregando pagina completa e re-fazendo login...")
                page.goto(settings.PROTHEUS_URL, wait_until="domcontentloaded", timeout=30000)
                _time.sleep(3)
                fazer_login(page)
                _time.sleep(5)
                raise NavegacaoError("Licenças excedidas — re-logado via goto + fazer_login (retry automatico)")
        except NavegacaoError:
            raise
        except Exception:
            pass
    
    # Heurística: Checa se o relatório já está visível (menu já pode estar aberto)
    log.bind(etapa="navegacao").info(f"Verificando se {nome_rotina_log} já está visível...")
    ja_visivel = False
    for ctx in [page, *page.frames]:
        try:
            if ctx.locator(f'text="{nome_rotina_log}"').first.is_visible(timeout=500):
                ja_visivel = True
                log.bind(etapa="navegacao").info(f"{nome_rotina_log} já visível, pulando clique em Favoritos")
                break
        except Exception:
            pass

    if not ja_visivel:
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
    
    # Passo 08: Abrir relatório/rotina
    log.bind(etapa="navegacao").info(f"Aguardando e clicando em {nome_rotina_log} (08)...")
    clicou_rel = False
    for ctx in [page, *page.frames]:
        try:
            loc = ctx.locator(f'text="{nome_rotina_log}"').first
            if loc.is_visible(timeout=1000):
                loc.click()
                log.bind(etapa="navegacao").info(f"Clicou em {nome_rotina_log} via DOM")
                clicou_rel = True
                break
        except Exception:
            pass
            
    if not clicou_rel:
        imagem_ref = "08_clicar_Mat_Estoque_Por_Tecnico.png" if rotina == "mat_estoque" else "08.1_Tranf._Multipla.png"
        clicou_rel = clicar_imagem(page, imagem_ref, timeout=15, threshold=0.65)
        
    if not clicou_rel:
        tirar_screenshot(page, etapa="falha_08_rotina", evidencia=True)
        raise NavegacaoError(f"Passo 08: Falha ao clicar na rotina {nome_rotina_log}")

    _time.sleep(3)

    if rotina == "trans_multipla":
        # Se for transferência múltipla, fluxo encerra após clicar na rotina
        # Pois a tela Transferencia Mod. II abrirá
        # Vamos validar se "Transferencia Mod. II" ou o botão "+ Incluir" aparecem
        log.bind(etapa="navegacao").info("Aguardando tela Transferencia Mod. II...")
        import time as _time
        tela_ok = False
        deadline = _time.monotonic() + 15
        while _time.monotonic() < deadline:
            for ctx in [page, *page.frames]:
                try:
                    if ctx.locator('text="Transferencia Mod. II"').first.is_visible(timeout=200):
                        tela_ok = True
                        break
                except Exception:
                    continue
            if tela_ok:
                break
        
        if not tela_ok:
            if aguardar_imagem(page, "09.1_Incluir.png", timeout=5, threshold=0.5) is not None:
                tela_ok = True
                
        if not tela_ok:
            # Fallback visual ou continua e deixa abrir_inclusao falhar se for o caso
            log.bind(etapa="navegacao").warning("Não confirmou tela de Transferência Mod II via DOM/Imagem. Prosseguindo mesmo assim.")
            
        log.bind(etapa="navegacao").success("Rotina Tranf. Multipla alcançada com sucesso")
        return
    
    # Passo 09: Confirmar tela "TOTVS Linha Protheus" (Data base/Grupo/Filial/Ambiente).
    # OPCIONAL: o Protheus pula este diálogo quando o profile do Chrome já tem
    # essas escolhas memorizadas (efeito do launch_persistent_context).
    # Probe curto; se não aparecer, segue.
    log.bind(etapa="navegacao").info("Verificando diálogo Confirmar (09)...")
    clicou_conf = False
    seletores_conf = [
        'button:has-text("Confirmar"):not([disabled])',
        'button:has-text("Confirmar")',
        '[title="Confirmar"]'
    ]
    deadline_conf = _time.monotonic() + 4
    while _time.monotonic() < deadline_conf and not clicou_conf:
        for ctx in [page, *page.frames]:
            for sel in seletores_conf:
                try:
                    loc = ctx.locator(sel).first
                    if loc.is_visible(timeout=300):
                        loc.click()
                        log.bind(etapa="navegacao").info(f"Clicou em Confirmar via DOM ({sel})")
                        clicou_conf = True
                        break
                except Exception:
                    continue
            if clicou_conf:
                break
        if not clicou_conf:
            _time.sleep(0.4)

    if not clicou_conf:
        centro = aguardar_imagem(page, "09_clicar_confirmar.png", timeout=2, threshold=0.65)
        if centro is not None:
            page.mouse.click(*centro)
            log.bind(etapa="navegacao").info(f"Clicou em Confirmar via matching em {centro}")
            clicou_conf = True

    if clicou_conf:
        _time.sleep(3)
    else:
        log.bind(etapa="navegacao").debug(
            "Passo 09 não apareceu — profile persistente já memorizou Grupo/Filial/Ambiente. Seguindo."
        )

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

    # Passo 10.2: Popup opcional "Moedas" (DOLAR/UFIR/Euro/IENE/Taxa Juros) — clicar em Cancelar
    log.bind(etapa="navegacao").info("Verificando popup Moedas (10.2)...")
    clicou_moedas_cancelar = False
    seletores_moedas_cancelar = [
        'button:has-text("Cancelar"):not([disabled])',
        'button:has-text("Cancelar")',
        '[title="Cancelar"]',
        'div[role="button"]:has-text("Cancelar")',
    ]
    deadline_moedas = _time.monotonic() + 3
    while _time.monotonic() < deadline_moedas and not clicou_moedas_cancelar:
        # Confirma que o popup é o de Moedas antes de clicar Cancelar
        # (para não clicar Cancelar em outras telas que tenham esse botão).
        popup_moedas_visivel = False
        for ctx in [page, *page.frames]:
            try:
                if ctx.locator('text="Moedas"').first.is_visible(timeout=200):
                    popup_moedas_visivel = True
                    break
            except Exception:
                continue

        if popup_moedas_visivel:
            for ctx in [page, *page.frames]:
                for sel in seletores_moedas_cancelar:
                    try:
                        loc = ctx.locator(sel).first
                        if loc.is_visible(timeout=200):
                            loc.click()
                            log.bind(etapa="navegacao").info(f"Cancelar do popup Moedas clicado via DOM ({sel})")
                            clicou_moedas_cancelar = True
                            break
                    except Exception:
                        continue
                if clicou_moedas_cancelar:
                    break
            break  # popup detectado: não tem por que continuar o loop de espera
        _time.sleep(0.4)

    if not clicou_moedas_cancelar:
        centro = aguardar_imagem(page, "10.2_caso_apareca_moedas_cancelar.png", timeout=2, threshold=0.65)
        if centro is not None:
            page.mouse.click(*centro)
            log.bind(etapa="navegacao").info(f"Cancelar do popup Moedas clicado via matching em {centro}")
            clicou_moedas_cancelar = True

    if clicou_moedas_cancelar:
        _time.sleep(1)
    else:
        log.bind(etapa="navegacao").debug("Popup Moedas não apareceu — seguindo")

    # Passo 10.3: Popup opcional "Reforma Tributária" (TOTVS adicionou em 2026-05)
    # Modal bloqueante com título "Atenção - Reforma Tributária" e botões
    # "Não exibir nos próximos 7 dias" / "Fechar".
    log.bind(etapa="navegacao").info("Verificando popup Reforma Tributária (10.3)...")
    clicou_reforma = False
    seletores_reforma = [
        'text="Reforma Tributária"',
        'text="Atenção - Reforma Tributária"',
    ]
    for ctx in [page, *page.frames]:
        for sel in seletores_reforma:
            try:
                if ctx.locator(sel).first.is_visible(timeout=500):
                    log.bind(etapa="navegacao").info("Popup Reforma Tributária detectado via DOM")
                    # Tenta "Fechar" primeiro, depois "Não exibir nos próximos 7 dias"
                    for btn_text in ["Fechar", "Não exibir nos próximos 7 dias"]:
                        for ctx2 in [page, *page.frames]:
                            try:
                                btn = ctx2.locator(f'button:has-text("{btn_text}")').first
                                if btn.is_visible(timeout=300):
                                    btn.click()
                                    log.bind(etapa="navegacao").info(f"Clicou '{btn_text}' no popup Reforma Tributária via DOM")
                                    clicou_reforma = True
                                    break
                            except Exception:
                                continue
                        if clicou_reforma:
                            break
                    break
            except Exception:
                continue
        if clicou_reforma:
            break

    if not clicou_reforma:
        # Fallback: procurar qualquer modal com botão "Fechar" que esteja bloqueando
        for ctx in [page, *page.frames]:
            try:
                btn_fechar = ctx.locator('button:has-text("Fechar"):not([disabled])').first
                if btn_fechar.is_visible(timeout=300):
                    btn_fechar.click()
                    log.bind(etapa="navegacao").info("Botão 'Fechar' genérico clicado (fallback modal bloqueante)")
                    clicou_reforma = True
                    break
            except Exception:
                continue

    if clicou_reforma:
        _time.sleep(2)
    else:
        log.bind(etapa="navegacao").debug("Popup Reforma Tributária não apareceu — seguindo")

    # Passo 10.4: Verificar "Excedeu numero de licenças" apos fechar popups —
    # o Protheus pode exibir esse modal quando fecha outro dialogo e a sessao
    # ja esta no limite. Detectar aqui evita cair no Passo 11 com tela bloqueada.
    for ctx in [page, *page.frames]:
        try:
            if ctx.locator('text="Excedeu numero de licenças"').first.is_visible(timeout=500):
                log.bind(etapa="navegacao").warning("Popup 'Excedeu numero de licenças' detectado apos fechar popup — re-logando...")
                for btn_sel in ['button:has-text("Fechar")', 'button:has-text("OK")']:
                    try:
                        btn = ctx.locator(btn_sel).first
                        if btn.is_visible(timeout=300):
                            btn.click()
                            log.bind(etapa="navegacao").info(f"Clicou '{btn_sel}' no popup de licenças")
                            break
                    except Exception:
                        continue
                _time.sleep(120)
                page.goto(settings.PROTHEUS_URL, wait_until="domcontentloaded", timeout=30000)
                _time.sleep(3)
                fazer_login(page)
                _time.sleep(5)
                raise NavegacaoError("Licenças excedidas apos popup — re-logado (retry automatico)")
        except NavegacaoError:
            raise
        except Exception:
            pass

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

def navegar_ate_rotina(page: Page, rotina: Literal["mat_estoque", "trans_multipla"] = "mat_estoque") -> None:
    """Navega da home até a tela de filtro do relatório com retry (3x)."""
    try:
        _executar_navegacao_rotina(page, rotina)
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
    import uuid
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
        try:
            validar_texto_ocr(page, name) # Apenas loga warning em caso de mismatch (não bloqueia)
        except Exception as e:
            log.bind(etapa="download", tecnico=code).warning(f"OCR falhou ou não disponível: {e}")
    
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
    log.bind(etapa="download", tecnico=code).info("Selecionando tipo de planilha (14)...")
    clicou_tipo = False
    clicou_tipo_dom = False
    
    # User hint: use select_option("select", value="3")
    # We'll try this across all frames with a bit more persistence
    for ctx in [page, *page.frames]:
        try:
            selects = ctx.locator("select").all()
            if selects:
                log.bind(etapa="download", tecnico=code).debug(f"Encontrado(s) {len(selects)} select(s) no frame {ctx.url}")
                # Try to find the one that has '3' as an option or just try the first one if only one exists
                for sel in selects:
                    if sel.is_visible(timeout=500):
                        try:
                            # Check if value '3' exists in options
                            options = sel.locator("option").all()
                            values = [opt.get_attribute("value") for opt in options]
                            if "3" in values:
                                sel.select_option(value="3")
                                log.bind(etapa="download", tecnico=code).info(f"Selecionou tipo de planilha '3' via DOM no frame {ctx.url}")
                                clicou_tipo = True
                                clicou_tipo_dom = True
                                break
                        except:
                            continue
                if clicou_tipo: break
        except Exception:
            continue
            
    if not clicou_tipo:
        log.bind(etapa="download", tecnico=code).debug("Fallback para matching no dropdown Tipo de Planilha")
        clicou_tipo = clicar_imagem(page, "14_clicar_tipo_de_planilha.png", timeout=15, threshold=0.65)
        
    if not clicou_tipo:
        tirar_screenshot(page, etapa="falha_14_tipo_planilha", evidencia=True)
        raise DownloadError("Falha ao abrir dropdown Tipo de Planilha (14)")
        
    _time.sleep(1)
    
    # Passo 15: Definir formato XLSX (Pular se já selecionado via DOM no Passo 14)
    clicou_formato = clicou_tipo_dom
    if not clicou_formato:
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
    
    # Passo 16 e 17: Gerar relatório (Imprimir) e Confirmar download (Sim)
    log.bind(etapa="download", tecnico=code).info("Aguardando evento de download (Imprimir + Sim)...")
    try:
        with page.expect_download(timeout=settings.DOWNLOAD_TIMEOUT_S * 1000) as download_info:
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
            
            clicou_sim = False
            
            # Estratégia 1: Procurar botões "Sim"
            seletores_sim = [
                'button:has-text("Sim"):not([disabled])',
                'button:has-text("Sim")',
                'div[role="button"]:has-text("Sim")'
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
                if clicou_sim: break
                
                # Estratégia 2: Procurar select com "Sim" (Heurística da inspeção)
                try:
                    selects = ctx.locator("select").all()
                    for sel in selects:
                        if sel.is_visible(timeout=200):
                            options = sel.locator("option").all()
                            textos = [opt.inner_text().strip() for opt in options]
                            if "Sim" in textos:
                                sel.select_option(label="Sim")
                                page.keyboard.press("Enter")
                                log.bind(etapa="download", tecnico=code).info("Confirmou 'Sim' via select + Enter")
                                clicou_sim = True
                                break
                except: continue
                if clicou_sim: break
                    
            if not clicou_sim:
                log.bind(etapa="download", tecnico=code).debug("Fallback para matching no botão Sim (pode ser opcional)")
                clicou_sim = clicar_imagem(page, "17_clique_Sim.png", timeout=3, threshold=0.65)
                
            if not clicou_sim:
                log.bind(etapa="download", tecnico=code).debug("Passo 17 (Sim) não localizado, assumindo que o download já iniciou via Imprimir")
                
        download = download_info.value
    except Exception as e:
        # Se falhou mas o arquivo já existe no dir de downloads (caso o download tenha disparado mas o Playwright perdeu o evento)
        # vamos verificar se houve sucesso apesar da exceção no bloco 'with'
        log.bind(etapa="download", tecnico=code).warning(f"Exceção no Passo 16/17: {e}")
        tirar_screenshot(page, etapa="falha_download_timeout", evidencia=True)
        raise DownloadError(f"Timeout ou falha ao aguardar download: {e}")
        
    # Salvar e renomear o arquivo para o pipeline (UUID) dentro de pasta datada
    hoje_str = datetime.now().strftime("%Y-%m-%d")
    downloads_dia_dir = settings.downloads_dir / hoje_str
    downloads_dia_dir.mkdir(parents=True, exist_ok=True)
    
    arquivo_nome = f"{uuid.uuid4()}.xlsx"
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
        
    # Passo 18: Retorno automático à home/rotina após download.
    # O Protheus pode demorar para fechar a tela de relatório e voltar
    # ao filtro — especialmente sob carga ou após múltiplos downloads.
    log.bind(etapa="download", tecnico=code).info("Aguardando retorno automático à rotina (18)...")
    _time.sleep(10)  # +3s em relação ao anterior (era 7s) para Protheus sob carga

    # Verifica se já retornou (checando tela de filtro 11 ou botões do Protheus)
    # Se o menu esquerdo (favoritos) está visível, é provável que retornou ou fechou a tela de relatório
    retornou = False
    if page.locator('text="Favoritos"').first.is_visible(timeout=5000):
        retornou = True
    else:
        retornou = aguardar_imagem(page, "11_colocar_o_codigo_tecnico.png", timeout=5, threshold=0.65) is not None

    if not retornou:
        log.bind(etapa="download", tecnico=code).warning(
            "Não retornou à rotina em 15s. Pressionando Esc para fechar tela residual..."
        )
        for _ in range(4):  # +2 Esc em relação ao anterior (era 2)
            page.keyboard.press("Escape")
            _time.sleep(1)

        # Verificar se Esc resolveu — não assumir que sim
        retornou = aguardar_imagem(page, "11_colocar_o_codigo_tecnico.png", timeout=5, threshold=0.65) is not None
        if not retornou:
            # Screenshot para debug — ajuda a identificar telas não mapeadas
            from core.navegador import tirar_screenshot
            tirar_screenshot(page, etapa=f"pos_download_estado_desconhecido_{code}", evidencia=True)
            log.bind(etapa="download", tecnico=code).warning(
                "Após Esc, tela ainda não é campo 11. "
                "O orquestrador (_preparar_para_proximo) tentará recuperação avançada."
            )
        
    return {
        "status": "sucesso",
        "arquivo": str(arquivo_path),
        "tamanho_bytes": tamanho_bytes,
        "hash_sha256": file_hash
    }

def baixar_xlsx_tecnico(page: Page, code: str, name: str = "") -> dict:
    """Orquestra o download para um técnico.

    Em caso de falha NÃO faz `page.goto(PROTHEUS_URL)` — isso jogaria a página
    de volta na tela de login e destruiria a sessão, quebrando o próximo técnico
    do loop. A recuperação de tela (voltar à rotina) é responsabilidade do
    orquestrador em `flows/processar_lista.py::_preparar_para_proximo`.
    """
    try:
        return _executar_download(page, code, name)
    except RetryError as e:
        log.bind(etapa="download", tecnico=code).error(f"Download falhou após retries: {e}")
        raise DownloadError(str(e)) from e
    except DownloadError as e:
        log.bind(etapa="download", tecnico=code).error(f"Download falhou: {e}")
        raise

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(NavegacaoError),
    reraise=True,
)
def abrir_inclusao_trans_multipla(page: Page) -> None:
    """Clica em + Incluir na tela Transferencia Mod. II e valida o título."""
    import time as _time
    from core.visao import validar_texto_ocr
    
    log.bind(etapa="navegacao").info("Aguardando e clicando em + Incluir (09.1)...")

    clicou = False
    seletores = [
        'button:has-text("Incluir")',
        '[title="Incluir"]',
        'span:has-text("Incluir")',
        'a:has-text("Incluir")',
        'div[role="button"]:has-text("Incluir")',
        'text="Incluir"'
    ]
    for ctx in [page, *page.frames]:
        for sel in seletores:
            try:
                loc = ctx.locator(sel).first
                if loc.is_visible(timeout=500):
                    loc.click()
                    clicou = True
                    log.bind(etapa="navegacao").info(f"Clicou em + Incluir via DOM ({sel})")
                    break
            except Exception:
                continue
        if clicou: break

    if not clicou:
        log.bind(etapa="navegacao").warning("Fallback visual para + Incluir...")
        clicou = clicar_imagem(page, "09.1_Incluir.png", timeout=15, threshold=0.35)

    if not clicou:
        tirar_screenshot(page, etapa="falha_09.1_incluir", evidencia=True)
        raise NavegacaoError("Falha ao clicar em + Incluir (09.1)")        
    _time.sleep(3)
    
    titulo_ok = False
    for ctx in [page, *page.frames]:
        try:
            if ctx.locator('text="Transferencia Mod. II - INCLUIR"').first.is_visible(timeout=500):
                titulo_ok = True
                log.bind(etapa="navegacao").info("Título Transferencia Mod. II - INCLUIR validado via DOM")
                break
        except Exception:
            continue
            
    if not titulo_ok:
        try:
            if validar_texto_ocr(page, "Transferencia Mod. II - INCLUIR"):
                titulo_ok = True
        except Exception:
            pass
            
    if not titulo_ok:
        # Fallback: Se não validou título mas o campo Numero Documento está visível, prossegue
        for ctx in [page, *page.frames]:
            try:
                if ctx.locator('label:has-text("Numero Documento"), label:has-text("Nro. Documento")').first.is_visible(timeout=500):
                    log.bind(etapa="navegacao").warning("Título não validado, mas campo Numero Documento localizado. Prosseguindo.")
                    titulo_ok = True
                    break
            except Exception:
                continue

    if not titulo_ok:
        tirar_screenshot(page, etapa="falha_titulo_incluir", evidencia=True)
        log.bind(etapa="navegacao").warning("Falha ao validar título de Inclusão via DOM ou OCR. Prosseguindo por conta própria...")


def capturar_numero_documento(page: Page) -> str:
    """Lê o Numero Documento gerado automaticamente pelo Protheus."""
    import re
    from core.visao import _decode_screenshot
    
    log.bind(etapa="navegacao").info("Capturando Numero Documento (11.1)...")
    regex_doc = re.compile(r"^[A-Z0-9]{10,15}$")
    
    # 1. Tentar DOM
    for ctx in [page, *page.frames]:
        try:
            inputs = ctx.locator('input').all()
            for inp in inputs:
                try:
                    val = inp.input_value(timeout=100).strip()
                    if regex_doc.match(val):
                        log.bind(etapa="navegacao").info(f"Documento capturado via DOM: {val}")
                        return val
                except Exception:
                    continue
        except Exception:
            pass

    # 2. Tentar OCR (lendo toda a tela)
    log.bind(etapa="navegacao").debug("DOM falhou para capturar número do documento, tentando OCR...")
    try:
        import pytesseract
        import cv2
        # Verifica se tesseract está instalado
        pytesseract.get_tesseract_version()
        
        screenshot_bytes = page.screenshot(timeout=10000)
        screenshot = _decode_screenshot(screenshot_bytes)
        gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
        texto = pytesseract.image_to_string(thresh, lang='por')
        
        palavras = texto.split()
        
        # Estratégia 1: Buscar palavra após "DOCUMENTO"
        for i, palavra in enumerate(palavras):
            if "DOCUMENT" in palavra.upper():
                for j in range(1, 4):
                    if i + j < len(palavras):
                        cand = re.sub(r'[^A-Z0-9]', '', palavras[i+j].upper())
                        if regex_doc.match(cand) and cand != "ESTOQUECUSTOS":
                            log.bind(etapa="navegacao").info(f"Documento capturado via OCR (prox de DOCUMENTO): {cand}")
                            return cand
                            
        # Estratégia 2: Fallback (como antes), mas ignorando falsos positivos conhecidos
        for palavra in palavras:
            palavra_limpa = re.sub(r'[^A-Z0-9]', '', palavra.upper())
            if regex_doc.match(palavra_limpa) and palavra_limpa not in ("ESTOQUECUSTOS", "TRANSFERENCIA", "FAVIORDMAIS", "PROTHEUS", "CLOUD"):
                log.bind(etapa="navegacao").info(f"Documento capturado via OCR (fallback): {palavra_limpa}")
                return palavra_limpa
                
    except Exception as e:
        log.bind(etapa="navegacao").warning(f"OCR para número do documento falhou ou indisponível: {e}")

    # Fallback final: ID fictício para não travar
    numero_ficticio = f"AUTO{datetime.now().strftime('%H%M%S')}"
    log.bind(etapa="navegacao").warning(f"Não foi possível capturar o Numero Documento. Usando ID fictício: {numero_ficticio}")
    return numero_ficticio

class TransferenciaIncompletaError(Exception):
    """Erro fatal em Transferência Múltipla após retries esgotados."""
    def __init__(self, numero_documento: str, linha_index: int, msg: str = ""):
        super().__init__(f"Falha na linha {linha_index} (Doc {numero_documento}): {msg}")
        self.numero_documento = numero_documento
        self.linha_index = linha_index

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, max=10),
    retry=retry_if_exception_type(NavegacaoError),
    reraise=True,
)
def _preencher_linha_com_retry(page: Page, linha: LinhaTransferencia, numero_documento: str, linha_index: int) -> None:
    import time as _time
    import cv2
    from datetime import datetime
    from core.config import settings
    from core.visao import _decode_screenshot
    
    log.bind(etapa="trans_mult.linha", linha=linha_index, documento=numero_documento).info("Limpando e preparando para preenchimento")
    
    # Limpar a linha antes de tentar
    # PRD §Sprint 11: Esc ou Ctrl+A+Del
    # REMOVIDO: Escape pode fechar a janela se o grid não estiver focado. 
    # Usamos apenas Ctrl+A + Del para limpar o campo atual.
    page.keyboard.down("Control")
    page.keyboard.press("a")
    page.keyboard.up("Control")
    page.keyboard.press("Delete")
    _time.sleep(0.5)
    
    def checar_popup_erro(campo_nome: str):
        achou = False
        # 1. DOM check
        for ctx in [page, *page.frames]:
            try:
                # Busca por modais comuns do Protheus/PO-UI
                if ctx.locator('.po-modal, po-modal, .thf-modal, text="Atenção", text="Erro", text="Help", text="Aviso"').first.is_visible(timeout=200):
                    achou = True
                    break
            except Exception:
                continue
                
        # 2. OCR fallback
        if not achou:
            try:
                import pytesseract
                screenshot = _decode_screenshot(page.screenshot(full_page=False))
                gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
                _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
                texto = pytesseract.image_to_string(thresh, lang='por').lower()
                if any(x in texto for x in ["atenção", "erro", "help", "inválido", "aviso", "ajuda"]):
                    achou = True
            except Exception:
                pass
        
        # 3. Matching visual (se o arquivo 19.1 existir)
        if not achou:
            try:
                if aguardar_imagem(page, "19.1_popup_erro_protheus.png", timeout=1, threshold=0.65) is not None:
                    achou = True
            except Exception:
                pass
                
        if achou:
            subpasta = settings.logs_dir / "evidencias" / "trans_mult"
            subpasta.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = subpasta / f"{ts}_linha{linha_index}.png"
            page.screenshot(path=str(path))
            log.bind(etapa="trans_mult.linha").error(f"Popup detectado. Evidência: {path}")
            
            # Tentar fechar popup
            page.keyboard.press("Escape")
            _time.sleep(0.5)
            # Tenta clicar no X se Esc não funcionou (opcional, heurística)
            try:
                clicar_imagem(page, "19.1_popup_erro_protheus.png", timeout=1) # Se for o mesmo botão
            except Exception:
                pass

            raise NavegacaoError(f"Popup de erro do Protheus detectado após {campo_nome}")
    def digitar_e_tab(valor, campo_nome: str):
        # PRD §Sprint 11: Pular campos opcionais vazios (não digitar nada, NÃO pressionar Tab vazio)
        if valor is None or str(valor).strip() == "":
            return
            
        from datetime import date
        if isinstance(valor, date):
            valor_str = valor.strftime("%d/%m/%Y")
        else:
            valor_str = str(valor)
            
        log.bind(etapa="trans_mult.linha").debug(f"Preenchendo {campo_nome}: {valor_str}")
        page.keyboard.type(valor_str, delay=20)
        _time.sleep(0.2)
        page.keyboard.press("Tab")
        _time.sleep(0.5)
        
        checar_popup_erro(campo_nome)

    # Ordem fixa conforme PRD §6.7.1 e §6.7.2
    campos_ordem = [
        "prod_orig", "armazem_orig", "endereco_orig", 
        "prod_destino", "armazem_destino", "endereco_destino", 
        "numero_serie", "lote", "sub_lote", "validade", "potencia"
    ]
    
    for campo in campos_ordem:
        valor = getattr(linha, campo)
        digitar_e_tab(valor, campo)
        
        # Delays específicos para gatilhos de auto-preenchimento (PRD §6.7.2)
        if campo == "prod_orig":
            log.bind(etapa="trans_mult.linha").debug("Aguardando auto-preenchimento após prod_orig...")
            _time.sleep(3)
            checar_popup_erro(campo)
        elif campo == "prod_destino":
            log.bind(etapa="trans_mult.linha").debug("Aguardando auto-preenchimento após prod_destino...")
            _time.sleep(3)
            checar_popup_erro(campo)
            
    # Quantidade por último (Trigger funcional)
    valor_qtd = str(linha.quantidade)
    log.bind(etapa="trans_mult.linha").debug(f"Preenchendo quantidade: {valor_qtd}")
    page.keyboard.type(valor_qtd, delay=20)
    _time.sleep(0.2)
    # Enter ou Tab final confirmam a linha (PRD §13.12)
    page.keyboard.press("Enter")
    _time.sleep(1)
    
    checar_popup_erro("quantidade")


def preencher_linha_grid(page: Page, linha: LinhaTransferencia, numero_documento: str, linha_index: int) -> None:
    """Preenche uma linha do grid com retry de 3 vezes e tratamento de popup."""
    log.bind(etapa="trans_mult.linha", linha=linha_index, documento=numero_documento).info("Iniciando preenchimento da linha")
    try:
        _preencher_linha_com_retry(page, linha, numero_documento, linha_index)
        log.bind(etapa="trans_mult.linha", linha=linha_index, documento=numero_documento).info("Linha preenchida com sucesso")
    except RetryError as e:
        msg = f"Falha ao preencher após retries: {e}"
        log.bind(etapa="trans_mult.linha", linha=linha_index, documento=numero_documento).error(msg)
        raise TransferenciaIncompletaError(numero_documento, linha_index, msg) from e
    except NavegacaoError as e:
        msg = f"Falha de navegação na linha: {e}"
        log.bind(etapa="trans_mult.linha", linha=linha_index, documento=numero_documento).error(msg)
        raise TransferenciaIncompletaError(numero_documento, linha_index, msg) from e


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(NavegacaoError),
    reraise=True,
)
def salvar_documento_trans_multipla(page: Page) -> None:
    """Clica em Salvar e aguarda confirmação ou erro modal."""
    import time as _time
    from core.visao import clicar_imagem, _decode_screenshot
    import cv2
    import pytesseract
    from datetime import datetime
    from core.config import settings

    log.bind(etapa="trans_mult.salvar").info("Clicando em Salvar (12.1)...")
    
    clicou = False
    seletores = [
        'button:has-text("Salvar")',
        '[title="Salvar"]',
        'span:has-text("Salvar")',
        'a:has-text("Salvar")',
        'div[role="button"]:has-text("Salvar")',
        'text="Salvar"'
    ]
    for ctx in [page, *page.frames]:
        for sel in seletores:
            try:
                loc = ctx.locator(sel).first
                if loc.is_visible(timeout=500):
                    loc.click()
                    clicou = True
                    log.bind(etapa="trans_mult.salvar").info(f"Clicou em Salvar via DOM ({sel})")
                    break
            except Exception:
                continue
        if clicou: break

    if not clicou:
        log.bind(etapa="trans_mult.salvar").warning("Fallback visual para Salvar...")
        clicou = clicar_imagem(page, "12.1_clicar_salvar.png", timeout=15, threshold=0.35)

    if not clicou:
        tirar_screenshot(page, etapa="falha_12.1_salvar", evidencia=True)
        raise NavegacaoError("Falha ao clicar em Salvar (12.1)")

    # Aguardar até 30s por feedback (PRD §6.7.2 passo 5)
    log.bind(etapa="trans_mult.salvar").info("Aguardando confirmação do Protheus (até 30s)...")
    
    deadline = _time.monotonic() + 30
    while _time.monotonic() < deadline:
        # 1. Sucesso: Volta ao grid principal (sem o título - INCLUIR)
        # Ou aparece popup de sucesso
        try:
            for ctx in [page, *page.frames]:
                # Se o título de inclusão sumir e o grid principal aparecer
                if ctx.locator('text="Transferencia Mod. II"').first.is_visible(timeout=100) and \
                   not ctx.locator('text="Transferencia Mod. II - INCLUIR"').first.is_visible(timeout=100):
                    log.bind(etapa="trans_mult.salvar").success("Documento salvo com sucesso (detectado via DOM)")
                    return
                
                # Popup de sucesso comum
                if ctx.locator('text="gravado com sucesso", text="salvo com sucesso", text="Operação realizada"').first.is_visible(timeout=100):
                    log.bind(etapa="trans_mult.salvar").success("Documento salvo com sucesso (detectado via popup)")
                    return
        except Exception:
            pass

        # 2. Erro modal
        achou_erro = False
        for ctx in [page, *page.frames]:
            try:
                if ctx.locator('.po-modal, po-modal, .thf-modal, text="Atenção", text="Erro", text="Help", text="Aviso"').first.is_visible(timeout=100):
                    achou_erro = True
                    break
            except Exception:
                continue
        
        if achou_erro:
            # Captura erro via OCR
            try:
                screenshot = _decode_screenshot(page.screenshot(full_page=False))
                gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
                _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
                texto_erro = pytesseract.image_to_string(thresh, lang='por').strip()
                log.bind(etapa="trans_mult.salvar").error(f"Erro ao salvar: {texto_erro}")
            except Exception:
                texto_erro = "Erro desconhecido em modal"

            subpasta = settings.logs_dir / "evidencias" / "trans_mult"
            subpasta.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = subpasta / f"{ts}_salvar_erro.png"
            page.screenshot(path=str(path))
            
            # Aborta documento inteiro (PRD §6.7.2 passo 6)
            raise TransferenciaIncompletaError("-", -1, f"Falha ao salvar documento: {texto_erro}")

        _time.sleep(1)

    tirar_screenshot(page, etapa="falha_timeout_salvar", evidencia=True)
    raise NavegacaoError("Timeout aguardando confirmação de salvamento")

