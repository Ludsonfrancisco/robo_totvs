import json
import time
from pathlib import Path

from playwright.sync_api import Page

from core.config import settings
from core.schema import Tecnico, CheckpointItem
from core.estado import carregar_checkpoint, salvar_checkpoint, Checkpoint
from core.acoes import (
    baixar_xlsx_tecnico,
    navegar_ate_rotina,
    fazer_login,
    detectar_logout,
    detectar_limite_conexoes,
    fechar_modal_limite_conexoes,
    validar_esta_na_home,
    CredenciaisInvalidasError,
    NavegacaoError,
    SessaoEsgotadaError,
)
from core.log import log
from core.visao import aguardar_imagem
from core.navegador import tirar_screenshot


def carregar_tecnicos(incluir_desligados: bool = False) -> list[Tecnico]:
    path = settings.tecnicos_path
    if not path.exists():
        log.bind(etapa="processar_lista").error(f"Arquivo de técnicos não encontrado: {path}")
        raise FileNotFoundError(f"Arquivo {path} não existe")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    tecnicos = [Tecnico(**item) for item in data]

    if not incluir_desligados:
        tecnicos = [t for t in tecnicos if t.status == "Ativo"]

    return tecnicos


def _imprimir_resumo(total: int, sucesso: int, falha: int, pulados: int, duracao_s: float) -> None:
    """Imprime resumo final formatado (PRD §10.1)."""
    minutos, segundos = divmod(int(duracao_s), 60)
    logger = log.bind(etapa="resumo")
    logger.info("═" * 51)
    
    msg = f"RESUMO  total: {total}  sucesso: {sucesso}  falha: {falha}  pulados: {pulados}"
    if falha == 0 and sucesso > 0:
        logger.success(msg)
    elif falha > 0 and sucesso > 0:
        logger.warning(msg)
    elif falha > 0:
        logger.error(msg)
    else:
        logger.info(msg)
        
    logger.info(f"duração: {minutos:02d}:{segundos:02d}")
    logger.info("═" * 51)


def _preparar_para_proximo(page: Page, code_anterior: str) -> bool:
    """Garante que a página está na tela de filtro (campo 11) pronta para o próximo técnico.

    Estados possíveis após terminar um técnico (sucesso ou falha):
    - Já na tela de filtro (caminho feliz pós-sucesso): segue.
    - Modal/erro residual aberto: Esc até cair em estado conhecido.
    - Na home (Favoritos visível): re-navega via `navegar_ate_rotina`.

    Retorna True se a tela está pronta, False se não conseguiu recuperar
    (nesse caso o orquestrador deve abortar o restante do loop).
    """
    # 1. Caminho feliz: já estamos no campo 11 (Protheus auto-retornou após Imprimir).
    if aguardar_imagem(page, "11_colocar_o_codigo_tecnico.png", timeout=5, threshold=0.65) is not None:
        log.bind(etapa="recuperacao", tecnico=code_anterior).info(
            "Tela da rotina pronta para o próximo técnico"
        )
        return True

    # 1.5. Modal "Limite de conexões do Usuário excedido" — Protheus matou a
    # sessão. Tentar fechar o modal e re-logar; se ainda persistir, escalar.
    if detectar_limite_conexoes(page):
        fechar_modal_limite_conexoes(page)
        time.sleep(1)
        if detectar_limite_conexoes(page):
            log.bind(etapa="recuperacao", tecnico=code_anterior).error(
                "Modal de limite de conexões reapareceu após Fechar — sessão irrecuperável"
            )
            raise SessaoEsgotadaError(
                "Limite de conexões do Protheus excedido (modal persistente)"
            )
        try:
            fazer_login(page)
            navegar_ate_rotina(page)
            return True
        except CredenciaisInvalidasError:
            raise SessaoEsgotadaError(
                "Re-login após limite de conexões foi rejeitado pelo Protheus"
            )
        except NavegacaoError as e:
            log.bind(etapa="recuperacao", tecnico=code_anterior).error(
                f"Re-navegação após limite de conexões falhou: {e}"
            )
            raise SessaoEsgotadaError(
                "Não foi possível re-navegar após fechar modal de limite de conexões"
            )

    # 2. Pode haver modal/diálogo residual de uma falha — Esc para tentar fechar.
    log.bind(etapa="recuperacao", tecnico=code_anterior).info(
        "Tela da rotina não detectada — pressionando Esc para limpar estado residual"
    )
    for _ in range(3):
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        time.sleep(1)

    # Re-checar campo 11 após Esc.
    if aguardar_imagem(page, "11_colocar_o_codigo_tecnico.png", timeout=3, threshold=0.65) is not None:
        log.bind(etapa="recuperacao", tecnico=code_anterior).info("Recuperado para tela de filtro via Esc")
        return True

    # 3. Verifica se caímos na home (Favoritos visível ou logo Protheus) — daí re-navega.
    home_visivel = False
    try:
        seletores_home = ['text="Favoritos"', '[title="Favoritos"]', '.wa-menu-item', 'po-menu']
        for ctx in [page, *page.frames]:
            for sel in seletores_home:
                try:
                    if ctx.locator(sel).first.is_visible(timeout=1000):
                        home_visivel = True
                        break
                except Exception:
                    continue
            if home_visivel:
                break
    except Exception:
        pass

    if not home_visivel:
        # Fallback para matching da home
        if aguardar_imagem(page, "07_pagina_home_clicar_favoritos.png", timeout=5) is not None:
            home_visivel = True

    if home_visivel:
        log.bind(etapa="recuperacao", tecnico=code_anterior).info(
            "Detectada home ou estado inicial — re-navegando até a rotina"
        )
        try:
            navegar_ate_rotina(page)
            return True
        except NavegacaoError as e:
            log.bind(etapa="recuperacao", tecnico=code_anterior).error(
                f"Falha ao re-navegar da home: {e}"
            )
            return False

    log.bind(etapa="recuperacao", tecnico=code_anterior).error(
        "Estado desconhecido — não foi possível recuperar para o próximo técnico"
    )
    return False


def processar_lista(
    page: Page,
    incluir_desligados: bool = False,
    retry_falhos: bool = False,
    reset: bool = False,
    limite: int | None = None,
) -> int:
    """Itera technicians.json, processa cada um, atualiza checkpoint após cada técnico.

    Args:
        limite: se informado, processa apenas os primeiros N técnicos elegíveis
            (útil para demos curtos da Sprint 5 sem percorrer a lista inteira).

    Retorna exit code:
      0 — todos os técnicos elegíveis terminaram com sucesso
      1 — pelo menos um técnico falhou nesta execução
    """
    inicio = time.monotonic()
    tecnicos = carregar_tecnicos(incluir_desligados)
    if limite is not None and limite > 0:
        log.bind(etapa="processar_lista").info(
            f"Aplicando --limite={limite} (de {len(tecnicos)} elegíveis)"
        )
        tecnicos = tecnicos[:limite]
    
    if reset:
        log.bind(etapa="processar_lista").info("Flag --reset ativa: ignorando checkpoint anterior.")
        checkpoint = Checkpoint()
    else:
        checkpoint = carregar_checkpoint()

    total = len(tecnicos)
    sucesso = 0
    falha = 0
    pulados = 0
    relogins = 0

    log.bind(etapa="processar_lista").info(
        f"Total de técnicos elegíveis: {total} "
        f"(incluir_desligados={incluir_desligados}, retry_falhos={retry_falhos}, reset={reset})"
    )

    for idx, t in enumerate(tecnicos, start=1):
        prefixo = f"[{idx}/{total}] {t.code}"
        if t.name:
            prefixo += f" {t.name}"
        log.bind(etapa="processar_lista", tecnico=t.code).info(prefixo)

        item = checkpoint.items.get(t.code)
        if not item:
            item = CheckpointItem(code=t.code)
            checkpoint.items[t.code] = item
            salvar_checkpoint(checkpoint)

        if not reset and item.status == "sucesso":
            log.bind(etapa="processar_lista", tecnico=t.code).info(
                "→ já baixado em execução anterior, pulando"
            )
            sucesso += 1
            pulados += 1
            continue

        if not reset and item.status == "falhou" and not retry_falhos:
            log.bind(etapa="processar_lista", tecnico=t.code).warning(
                "→ falhou em execução anterior; use --retry-falhos para reprocessar"
            )
            falha += 1
            pulados += 1
            continue

        # Sprint 8.1: Verificação proativa do modal de limite de conexões
        # antes de cada técnico — se aparecer no meio do download, o loop
        # detecta na próxima iteração e aborta sem marcar dezenas de
        # técnicos como falha silenciosa.
        if detectar_limite_conexoes(page):
            fechar_modal_limite_conexoes(page)
            time.sleep(1)
            if detectar_limite_conexoes(page):
                log.bind(etapa="sessao", tecnico=t.code).error(
                    "Limite de conexões do Protheus excedido — abortando execução."
                )
                _imprimir_resumo(total, sucesso, falha, pulados, time.monotonic() - inicio)
                raise SessaoEsgotadaError(
                    "Limite de conexões do Protheus excedido"
                )

        # Sprint 6: Verificação de sessão antes de processar técnico
        if detectar_logout(page):
            relogins += 1
            log.bind(etapa="sessao").warning(f"Sessão expirada. Re-login automático ({relogins}/3)")
            if relogins > 3:
                log.bind(etapa="sessao").error("Limite de re-logins atingido. Abortando.")
                raise CredenciaisInvalidasError("Sessão irrecuperável (limite de re-logins)")
            
            fazer_login(page)
            navegar_ate_rotina(page)
        elif not aguardar_imagem(page, "11_colocar_o_codigo_tecnico.png", timeout=2, threshold=0.65):
            # Se não está logado mas também não está na rotina, tenta recuperar
            log.bind(etapa="sessao").info("Não detectado campo de filtro. Tentando recuperar tela.")
            if not _preparar_para_proximo(page, t.code):
                 log.bind(etapa="sessao").warning("Falha ao recuperar tela. Tentando re-navegar.")
                 navegar_ate_rotina(page)

        item.tentativas += 1
        item.status = "processando"
        salvar_checkpoint(checkpoint)

        try:
            resultado = baixar_xlsx_tecnico(page, code=t.code, name=t.name or "")
            item.status = "sucesso"
            item.arquivo = str(resultado["arquivo"])
            item.hash_sha256 = resultado["hash_sha256"]
            item.erro_msg = None
            sucesso += 1
            log.bind(etapa="processar_lista", tecnico=t.code).success(
                f"✓ sucesso — {item.arquivo}"
            )
        except CredenciaisInvalidasError:
            item.status = "falhou"
            item.erro_msg = "Credenciais inválidas"
            salvar_checkpoint(checkpoint)
            _imprimir_resumo(total, sucesso, falha + 1, pulados, time.monotonic() - inicio)
            raise
        except Exception as e:
            # Sprint 6: Screenshot automático em toda falha
            screenshot_path = tirar_screenshot(page, etapa=f"falha_{t.code}", evidencia=True)
            item.status = "falhou"
            item.erro_msg = str(e)
            falha += 1
            log.bind(etapa="processar_lista", tecnico=t.code).error(
                f"✗ falhou após {item.tentativas} tentativa(s) — {e} (Evidência: {screenshot_path})"
            )

        salvar_checkpoint(checkpoint)

        # Antes de prosseguir para o próximo técnico, garantir que a tela
        # esteja na rotina (campo 11). Sucesso ou falha, o estado pode
        # variar — sem isso, o próximo `_executar_download` tenta clicar no
        # campo 11 a partir da tela errada e falha por consequência (não
        # mérito próprio). Se não conseguir recuperar, abortamos o restante.
        if idx < total:
            try:
                pronto = _preparar_para_proximo(page, t.code)
            except SessaoEsgotadaError:
                # Restantes ficam como "pendentes" no checkpoint, não como
                # falha — assim o próximo run (após admin liberar sessões)
                # retoma sem precisar de --retry-falhos.
                nao_tentados = total - idx
                log.bind(etapa="processar_lista").error(
                    f"Abortando loop — limite de conexões excedido. "
                    f"{nao_tentados} técnico(s) não tentado(s) permanecem pendentes."
                )
                _imprimir_resumo(total, sucesso, falha, pulados, time.monotonic() - inicio)
                raise

            if not pronto:
                log.bind(etapa="processar_lista").error(
                    "Abortando loop — tela não recuperável para o próximo técnico"
                )
                falha += (total - idx)
                break

    _imprimir_resumo(total, sucesso, falha, pulados, time.monotonic() - inicio)
    return 1 if falha > 0 else 0
