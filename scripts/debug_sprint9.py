import sys
from pathlib import Path
import openpyxl
from decimal import Decimal

# Adiciona o diretório raiz ao path para importar core
sys.path.append(str(Path(__file__).parent.parent))

from core.planilha import carregar_transferencias, PlanilhaInvalidaError

def criar_planilha_teste(path: Path, colunas: list, dados: list):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(colunas)
    for row in dados:
        ws.append(row)
    wb.save(path)
    print(f"Planilha criada: {path}")

def testar_cenario(nome, path):
    print(f"\n>>> Testando cenário: {nome}")
    try:
        resultado = carregar_transferencias(path)
        print(f"✅ SUCESSO: {len(resultado.linhas)} linhas carregadas.")
        print(f"SHA256: {resultado.sha256}")
    except PlanilhaInvalidaError as e:
        print(f"❌ ERRO ESPERADO: {e}")
    except Exception as e:
        print(f"💥 ERRO INESPERADO: {type(e).__name__}: {e}")

# Setup
tmp_dir = Path("logs/debug_sprint9")
tmp_dir.mkdir(parents=True, exist_ok=True)

# 1. Planilha Boa
col_boas = ["Prod.Orig.", "Armazem Orig.", "Prod.Destino", "Armazem Destino", "Numero Serie", "Quantidade"]
dados_bons = [["PROD01", "01", "PROD01", "02", "SERIE001", 10.5]]
path_boa = tmp_dir / "boa.xlsx"
criar_planilha_teste(path_boa, col_boas, dados_bons)
testar_cenario("Planilha Boa", path_boa)

# 2. Coluna Faltante
col_faltante = ["Prod.Orig.", "Armazem Orig."]
path_faltante = tmp_dir / "faltante.xlsx"
criar_planilha_teste(path_faltante, col_faltante, [["A", "B"]])
testar_cenario("Coluna Faltante", path_faltante)

# 3. Numero Serie Vazio (Linha 2 - data row 1)
dados_serie_vazia = [["PROD01", "01", "PROD01", "02", "", 10.5]]
path_serie_vazia = tmp_dir / "serie_vazia.xlsx"
criar_planilha_teste(path_serie_vazia, col_boas, dados_serie_vazia)
testar_cenario("Numero Serie Vazio", path_serie_vazia)

# 4. Quantidade Inválida
dados_qtd_invalida = [["PROD01", "01", "PROD01", "02", "SERIE001", "abc"]]
path_qtd_invalida = tmp_dir / "qtd_invalida.xlsx"
criar_planilha_teste(path_qtd_invalida, col_boas, dados_qtd_invalida)
testar_cenario("Quantidade Inválida", path_qtd_invalida)

