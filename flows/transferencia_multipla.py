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
)

def executar_transferencia_multipla(page: Page, planilha_path: Path) -> int:
    """Esqueleto inicial do fluxo F7 (Sprint 10)."""
    logger = log.bind(etapa="trans_mult.abrir", tecnico="-")
    logger.info(f"Iniciando transferência múltipla com {planilha_path}")
    
    # 1. Valida e carrega a planilha
    # Vai dar PlanilhaInvalidaError se der errado (tratado no main.py para exit 3)
    planilha = carregar_transferencias(planilha_path)
    
    # Aqui o Sprint 12 fará verificação de idempotência (já vamos prever, mas não interromper)
    
    # F1: Login
    fazer_login(page)
    
    # F2: Navegar até Tranf. Multipla
    navegar_ate_rotina(page, rotina="trans_multipla")
    
    # Sprint 10: Abrir inclusão e capturar documento
    abrir_inclusao_trans_multipla(page)
    numero = capturar_numero_documento(page)
    
    logger_doc = log.bind(etapa="trans_mult.abrir", tecnico="-", documento=numero)
    logger_doc.info("Documento aberto no Protheus")
    
    # Salva o checkpoint imediatamente
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
    
    logger_doc.success("Checkpoint inicial gravado. (Fim do Sprint 10)")
    
    # No Sprint 10, nós paramos por aqui (sair com exit 0 sem preencher nada)
    return 0
