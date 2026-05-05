import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from decimal import Decimal

sys.path.append(str(Path(__file__).parent.parent))
from core.schema import LinhaTransferencia
from core.acoes import preencher_linha_grid, TransferenciaIncompletaError

mock_page = MagicMock()
mock_page.keyboard = MagicMock()
mock_locator = MagicMock()
mock_page.locator.return_value = mock_locator

# Simula que o popup de erro SEMPRE aparece (produto inválido)
mock_locator.first.is_visible.return_value = True

linha = LinhaTransferencia(
    prod_orig="PROD_INVALIDO", armazem_orig="01", prod_destino="02", 
    armazem_destino="02", numero_serie="ERRO_TESTE", quantidade=Decimal("1")
)

print("Iniciando Demo Parte 2: Simulação de Erro Crítico no Protheus...")
try:
    with patch('time.sleep'), patch('core.acoes.tirar_screenshot'),          patch('core.acoes.clicar_imagem', return_value=True),          patch('core.visao.aguardar_imagem', return_value=None),          patch('core.visao._decode_screenshot'),          patch('pytesseract.image_to_string', return_value="Erro: Produto Inexistente"):
        
        preencher_linha_grid(mock_page, linha, "DOC_ERRO_123", 1)
except TransferenciaIncompletaError as e:
    print(f"\nRESULTADO FINAL DA DEMO:")
    print(f"Status: ❌ FALHA DETECTADA (OK)")
    print(f"Mensagem: {e}")
except Exception as e:
    print(f"Erro inesperado: {e}")
