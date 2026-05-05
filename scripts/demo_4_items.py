import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from decimal import Decimal

# Add root to sys.path
sys.path.append(str(Path(__file__).parent.parent))

from flows.transferencia_multipla import executar_transferencia_multipla
from core.planilha import carregar_transferencias

def run_test():
    print("🚀 Iniciando Teste de Transferência de 4 Itens (Simulação)")
    print("Configuração: HEADLESS=False")
    
    mock_page = MagicMock()
    mock_page.keyboard = MagicMock()
    mock_page.frames = []
    
    planilha_path = Path("referencias/trans_mult.xlsx")
    
    # We want to use the real carregar_transferencias to validate the file we just fixed
    with patch('flows.transferencia_multipla.fazer_login'),          patch('flows.transferencia_multipla.navegar_ate_rotina'),          patch('flows.transferencia_multipla.abrir_inclusao_trans_multipla'),          patch('flows.transferencia_multipla.capturar_numero_documento', return_value="TEST000004IT"),          patch('core.visao.clicar_imagem', return_value=True),          patch('flows.transferencia_multipla.detectar_logout', return_value=False),          patch('flows.transferencia_multipla.preencher_linha_grid') as mock_fill,          patch('flows.transferencia_multipla.salvar_documento_trans_multipla'),          patch('flows.transferencia_multipla.salvar_checkpoint_trans_multipla'),          patch('flows.transferencia_multipla.carregar_checkpoint_trans_multipla', return_value=None),          patch('time.sleep'):
        
        # We don't patch carregar_transferencias because we want to see it work with the real file
        res = executar_transferencia_multipla(mock_page, planilha_path)
        
        print(f"\n✅ Teste finalizado com status: {res}")
        print(f"Total de linhas processadas: {mock_fill.call_count}")

if __name__ == "__main__":
    run_test()
