"""robo-totvs — entrypoint CLI.

Sprint 4 test: Single hardcoded technician download.
"""

from core.config import settings
from core.log import log
from core.navegador import iniciar_navegador, tirar_screenshot
from core.acoes import fazer_login, navegar_ate_rotina, baixar_xlsx_tecnico, CredenciaisInvalidasError

def main() -> int:
    log.bind(etapa="main").info(f"alvo: {settings.PROTHEUS_URL}")

    with iniciar_navegador() as (_, _, page):
        try:
            fazer_login(page)
            navegar_ate_rotina(page)
            
            # Hardcoded test for Sprint 4
            resultado = baixar_xlsx_tecnico(
                page, 
                code="HK", 
                name="ALEXANDRE MENEZES DE SOUZA - DMAIS (VAREJO)"
            )
            
            path = tirar_screenshot(page, etapa="sucesso_demo_sprint_4", evidencia=False)
            log.bind(etapa="main").success(f"Sprint 4 demo concluída com sucesso! Resultado: {resultado} | Evidência: {path}")
        except CredenciaisInvalidasError:
            log.bind(etapa="main").error("Abortando por credenciais inválidas.")
            return 2
        except Exception as e:
            log.bind(etapa="main").error(f"Erro fatal: {e}")
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())