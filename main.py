"""robo-totvs — entrypoint CLI.

Sprint 5: Loop pela lista JSON com checkpoint e idempotência.

Exit codes (PRD §9.3):
  0 — todos os técnicos baixados com sucesso
  1 — concluído com falhas individuais (parciais)
  2 — aborto crítico (credenciais inválidas, sessão irrecuperável)
  3 — erro de configuração (JSON inválido, env vars faltando, schema)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from core.acoes import CredenciaisInvalidasError, PlanilhaInvalidaError, TransferenciaIncompletaError, fazer_login, navegar_ate_rotina
from core.log import log
from core.navegador import iniciar_navegador
from flows.processar_lista import processar_lista
from flows.transferencia_multipla import executar_transferencia_multipla


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="robo-totvs",
        description="RPA Protheus — automações de Estoque.",
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Comandos disponíveis")

    # Subcomando default/processar-lista (implícito quando não há comando para não quebrar compatibilidade)
    # Mas argparse add_subparsers torna mais difícil ser 100% retrocompatível sem truques.
    # Vamos adicionar um comando 'baixar-mat-estoque' e tratar o None como default fallback.

    cmd_trans_multipla = subparsers.add_parser("trans-multipla", help="Executa o fluxo de Transferência Múltipla")
    cmd_trans_multipla.add_argument(
        "--planilha",
        type=Path,
        help="Caminho para a planilha XLSX. Se omitido, usa a configurada no .env",
    )

    # Argumentos globais/antigos continuam no parser principal para fallback
    parser.add_argument(
        "--retry-falhos",
        action="store_true",
        help="Reprocessa apenas técnicos com status=falhou no checkpoint atual.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reinicia o processamento do zero, ignorando o progresso anterior (checkpoint).",
    )
    parser.add_argument(
        "--incluir-desligados",
        action="store_true",
        help="Inclui técnicos com status != 'Ativo' no processamento.",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=None,
        help="Processa apenas os N primeiros técnicos elegíveis (útil para demos).",
    )
    
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        from core.config import settings  # validação tardia para capturar erro de config
    except ValidationError as e:
        log.bind(etapa="main").error(f"Erro de configuração (.env inválido): {e}")
        return 3

    log.bind(etapa="main").info(f"alvo: {settings.PROTHEUS_URL}")
    log.bind(etapa="main").info(f"comando: {args.command or 'processar_lista'}")

    with iniciar_navegador() as (_, _, page):
        try:
            if args.command == "trans-multipla":
                planilha_path = args.planilha if args.planilha else settings.transferencia_xlsx_path
                return executar_transferencia_multipla(page, planilha_path)
            else:
                # Comportamento default (F1-F6)
                fazer_login(page)
                navegar_ate_rotina(page)
                return processar_lista(
                    page,
                    incluir_desligados=args.incluir_desligados,
                    retry_falhos=args.retry_falhos,
                    reset=args.reset,
                    limite=args.limite,
                )
        except CredenciaisInvalidasError:
            log.bind(etapa="main").error("Abortando por credenciais inválidas.")
            return 2
        except PlanilhaInvalidaError as e:
            log.bind(etapa="main").error(f"Erro de validação na planilha: {e}")
            return 3
        except TransferenciaIncompletaError as e:
            log.bind(etapa="main").error(f"Erro incompleto de transferência: {e}")
            return 1
        except FileNotFoundError as e:
            log.bind(etapa="main").error(f"Arquivo de configuração não encontrado: {e}")
            return 3
        except (json.JSONDecodeError, ValidationError) as e:
            log.bind(etapa="main").error(f"Erro ao validar entrada: {e}")
            return 3
        except Exception as e:
            log.bind(etapa="main").error(f"Erro fatal: {e}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
