import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from decimal import Decimal

# Adiciona o diretório raiz ao path para importar o core
sys.path.append(str(Path(__file__).parent.parent))

from core.schema import LinhaTransferencia
from core.acoes import preencher_linha_grid, NavegacaoError, TransferenciaIncompletaError

def run_simulation(scenario_name, linha_data, should_fail_at_step=None):
    print(f"\n{'='*20} CENÁRIO: {scenario_name} {'='*20}")
    
    # Mock do Page e Keyboard
    mock_page = MagicMock()
    mock_keyboard = MagicMock()
    mock_page.keyboard = mock_keyboard
    
    # Mock do locator para o popup de erro
    mock_locator = MagicMock()
    mock_page.locator.return_value = mock_locator
    
    # Simulação de comportamento de erro
    current_attempt = [0]
    
    def mock_is_visible(timeout=None):
        if should_fail_at_step and current_attempt[0] < 3:
            # Simula que o popup de erro aparece
            return True
        return False

    mock_locator.first.is_visible.side_effect = mock_is_visible

    linha = LinhaTransferencia(**linha_data)
    
    try:
        # Patch no time.sleep para o teste ser rápido
        with patch('time.sleep'):
            # Patch no tirar_screenshot para não precisar de navegador
            with patch('core.acoes.tirar_screenshot'):
                # Patch no aguardar_imagem/clicar_imagem
                with patch('core.acoes.aguardar_imagem', return_value=None):
                    with patch('core.acoes.clicar_imagem', return_value=True):
                        # Patch no _decode_screenshot e OCR onde eles são usados
                        with patch('core.visao._decode_screenshot'):
                            with patch('pytesseract.image_to_string', return_value=""):
                                preencher_linha_grid(mock_page, linha, "SIMULACAO_DOC", 1)
                                print("✅ Execução concluída com SUCESSO")
    except TransferenciaIncompletaError as e:
        print(f"❌ Falha esperada: {e}")
    except Exception as e:
        print(f"🔥 Erro inesperado: {type(e).__name__}: {e}")

if __name__ == "__main__":
    # Caso 1: Happy Path
    happy_data = {
        "prod_orig": "PROD001",
        "armazem_orig": "01",
        "prod_destino": "PROD002",
        "armazem_destino": "02",
        "numero_serie": "SN123456",
        "quantidade": Decimal("10.0")
    }
    run_simulation("HAPPY PATH (Demo Parte 1)", happy_data)

    # Caso 2: Falha e Retry (Demo Parte 2)
    fail_data = {
        "prod_orig": "PROD_INEXISTENTE",
        "armazem_orig": "01",
        "prod_destino": "PROD002",
        "armazem_destino": "02",
        "numero_serie": "SN999999",
        "quantidade": Decimal("1.0")
    }
    # Simula erro que persiste após 3 tentativas
    run_simulation("FALHA COM RETRY (Demo Parte 2)", fail_data, should_fail_at_step="prod_orig")
