"""Falha o build quando uma capacidade financeira não está na imagem."""

from importlib import import_module as _import_module
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


REQUIRED_CAPABILITIES = (
    "flows.financeiro_hoje.runner",
    "flows.financeiro_medicao.runner",
)


class CapabilityMissing(RuntimeError):
    """A imagem não contém todas as capacidades obrigatórias."""


def validate_capabilities(*, import_module=_import_module) -> None:
    for module_name in REQUIRED_CAPABILITIES:
        try:
            import_module(module_name)
        except ImportError as error:
            raise CapabilityMissing(
                f"capacidade obrigatória ausente: {module_name}"
            ) from error


def main() -> int:
    validate_capabilities()
    print("automation-capabilities-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
