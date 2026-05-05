import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page

from core.log import log
from core.estado import (
    carregar_checkpoint_trans_multipla,
    salvar_checkpoint_trans_multipla,
)
from core.schema import CheckpointTransferenciaMultipla
from core.planilha import carregar_transferencias
from core.acoes import (
    fazer_login,
    navegar_ate_rotina,
    abrir_inclusao_trans_multipla,
    capturar_numero_documento,
    preencher_linha_grid,
    salvar_documento_trans_multipla,
    detectar_logout,
)

def executar_transferencia_multipla(page: Page, planilha_path: Path) -> int:
    """Orquestrador completo do fluxo F7 (PRD §7.3)."""
    start_time = time.monotonic()
    
    # 1. Valida e carrega a planilha
    planilha = carregar_transferencias(planilha_path)
    
    # 2. Idempotência (PRD §6.7.5)
    checkpoint_existente = carregar_checkpoint_trans_multipla()
    if checkpoint_existente and \
       checkpoint_existente.planilha_sha256 == planilha.sha256 and \
       checkpoint_existente.status == "sucesso":
        log.bind(etapa="trans_mult.idempotencia").info(
            f"Planilha {planilha_path.name} já processada com sucesso hoje (Doc {checkpoint_existente.numero_documento})."
        )
        return 0

    log.bind(etapa="trans_mult").info(f"Iniciando transferência múltipla: {planilha_path.name}")
    
    # F1: Login
    fazer_login(page)
    
    # F2: Navegar até Tranf. Multipla
    navegar_ate_rotina(page, rotina="trans_multipla")
    
    # Abrir inclusão e capturar documento
    abrir_inclusao_trans_multipla(page)
    numero = capturar_numero_documento(page)
    
    logger_doc = log.bind(etapa="trans_mult", documento=numero)
    logger_doc.info(f"Documento {numero} aberto. Iniciando preenchimento de {len(planilha.linhas)} linhas.")
    
    # Checkpoint inicial
    checkpoint = CheckpointTransferenciaMultipla(
        id_execucao=f"{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}_{planilha.sha256[:6]}",
        planilha_origem=str(planilha.caminho),
        planilha_sha256=planilha.sha256,
        numero_documento=numero,
        linhas_total=len(planilha.linhas),
        linhas_ok=0,
        status="em_andamento",
        iniciada_em=datetime.now().isoformat()
    )
    salvar_checkpoint_trans_multipla(checkpoint)

    # Foco na 1ª célula do grid
    from core.visao import clicar_imagem
    if not clicar_imagem(page, "10.1_loop_de_materail_baseado_na_planilha.png", timeout=10, threshold=0.65):
        logger_doc.warning("Foco no grid via imagem falhou, tentando prosseguir.")
    
    time.sleep(1)

    try:
        for i, linha in enumerate(planilha.linhas, start=1):
            # F5: Detecção de logout durante o loop (PRD §6.7.3)
            if detectar_logout(page):
                logger_doc.error("Sessão expirada durante o preenchimento. Abortando documento.")
                checkpoint.status = "falhou"
                checkpoint.erro_msg = "Sessão expirada durante o loop"
                salvar_checkpoint_trans_multipla(checkpoint)
                return 1

            # Preencher linha
            preencher_linha_grid(page, linha, numero, i)
            checkpoint.linhas_ok = i
            salvar_checkpoint_trans_multipla(checkpoint)
            
            # Navegação para próxima linha via seta ↓ (PRD §6.7.2)
            if i < len(planilha.linhas):
                page.keyboard.press("ArrowDown")
                time.sleep(0.5)

        # Salvar documento
        salvar_documento_trans_multipla(page)
        
        checkpoint.status = "sucesso"
        checkpoint.salvo_em = datetime.now().isoformat()
        salvar_checkpoint_trans_multipla(checkpoint)
        
        duration = time.monotonic() - start_time
        _imprimir_resumo(checkpoint, duration)
        return 0

    except Exception as e:
        logger_doc.error(f"Erro fatal durante o processamento: {e}")
        checkpoint.status = "falhou"
        checkpoint.erro_msg = str(e)
        salvar_checkpoint_trans_multipla(checkpoint)
        return 1

def _imprimir_resumo(cp: CheckpointTransferenciaMultipla, duration: float):
    """Imprime resumo final no terminal (PRD §12.2)."""
    m, s = divmod(int(duration), 60)
    print("\n" + "="*50)
    print("RESUMO — Transferência Múltipla")
    print(f"Status:    {cp.status.upper()}")
    print(f"Documento: {cp.numero_documento}")
    print(f"Linhas:    {cp.linhas_ok}/{cp.linhas_total}")
    print(f"Duração:   {m:02d}:{s:02d}")
    print("="*50 + "\n")
