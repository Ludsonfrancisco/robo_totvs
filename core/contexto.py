"""Integração com MCP Context7 para classificação de tela."""

from core.log import log

def classificar_tela(screenshot_path: str) -> dict:
    """Stub para classificação de tela via MCP Context7.
    
    Retorna sempre 'desconhecido' na implementação inicial, conforme PRD 8.3.
    """
    log.bind(etapa="contexto").info(f"Classificando tela (stub): {screenshot_path}")
    return {"tela": "desconhecido", "acao_sugerida": "nenhuma"}
