import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from decimal import Decimal

sys.path.append(str(Path(__file__).parent.parent))
from flows.transferencia_multipla import executar_transferencia_multipla

mock_page = MagicMock()
mock_page.keyboard = MagicMock()
mock_page.frames = []

# Mock carregar_transferencias
mock_planilha = MagicMock()
mock_planilha.sha256 = "abc123sha"
mock_planilha.caminho = Path("test.xlsx")
mock_planilha.linhas = [MagicMock() for _ in range(3)]

print("Simulando Sprint 12 - Happy Path...")
# Patch no core.visao.clicar_imagem já que é importado localmente
with patch('flows.transferencia_multipla.carregar_transferencias', return_value=mock_planilha),      patch('flows.transferencia_multipla.fazer_login'),      patch('flows.transferencia_multipla.navegar_ate_rotina'),      patch('flows.transferencia_multipla.abrir_inclusao_trans_multipla'),      patch('flows.transferencia_multipla.capturar_numero_documento', return_value="SIM12345"),      patch('core.visao.clicar_imagem', return_value=True),      patch('flows.transferencia_multipla.detectar_logout', return_value=False),      patch('flows.transferencia_multipla.preencher_linha_grid'),      patch('flows.transferencia_multipla.salvar_documento_trans_multipla'),      patch('flows.transferencia_multipla.salvar_checkpoint_trans_multipla'),      patch('flows.transferencia_multipla.carregar_checkpoint_trans_multipla', return_value=None),      patch('time.sleep'):
    
    res = executar_transferencia_multipla(mock_page, Path("test.xlsx"))
    print(f"Resultado final: {res}")
