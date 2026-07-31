import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "smoke_automation_capabilities.py"
)


def load_smoke_module():
    assert SCRIPT.exists(), "o build precisa validar as duas automações"
    spec = importlib.util.spec_from_file_location(
        "smoke_automation_capabilities",
        SCRIPT,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_smoke_requires_financeiro_hoje_and_medicao():
    module = load_smoke_module()
    imported = []

    module.validate_capabilities(import_module=imported.append)

    assert imported == [
        "flows.financeiro_hoje.runner",
        "flows.financeiro_medicao.runner",
    ]


def test_smoke_rejects_missing_capability_without_private_details():
    module = load_smoke_module()

    def missing(_module_name):
        raise ImportError("private filesystem detail")

    with pytest.raises(module.CapabilityMissing) as error:
        module.validate_capabilities(import_module=missing)

    assert "private filesystem detail" not in str(error.value)


def test_smoke_runs_from_scripts_path_like_docker_build():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=SCRIPT.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "automation-capabilities-ok"
