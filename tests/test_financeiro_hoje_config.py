import pytest
from pathlib import Path

from flows.financeiro_hoje.config import Settings, resolve_root


BASE = {
    "ROUTERBOX_USER": "robot",
    "ROUTERBOX_PASS": "acerta-secret",
    "ROUTERBOX_LOGA_PASS": "loga-secret",
    "ROUTERBOX_LOGA_URL": "https://loga.invalid",
    "ROUTERBOX_ACERTA_URL": "https://acerta.invalid",
    "FINANCEIRO_HOJE_ROOT": "/app/data_pipeline/financeiro_hoje",
}


@pytest.mark.parametrize(
    "configured_root",
    [
        "/app/data_pipeline/financeiro_hoje",
        Path("/app/data_pipeline/financeiro_hoje"),
    ],
)
def test_resolve_root_aceita_caminho_linux_do_container_no_windows(
    configured_root,
):
    root = resolve_root(configured_root)

    assert root.name == "financeiro_hoje"
    assert "routerbox_backlog" not in root.parts


def test_settings_cria_runtime_isolado(tmp_path):
    values = {
        **BASE,
        "DATA_PIPELINE_DIR": str(tmp_path),
        "FINANCEIRO_HOJE_ROOT": str(tmp_path / "financeiro_hoje"),
    }

    settings = Settings.from_mapping(values)

    assert settings.root.name == "financeiro_hoje"
    assert settings.deadline_seconds == 480
    assert settings.period_days == 10
    assert settings.schedule_enabled is False
    assert {item.name for item in settings.instances} == {"LOGA", "ACERTA"}


def test_settings_root_default_acompanha_data_pipeline_dir(tmp_path):
    values = {
        key: value for key, value in BASE.items() if key != "FINANCEIRO_HOJE_ROOT"
    }
    values["DATA_PIPELINE_DIR"] = str(tmp_path)

    settings = Settings.from_mapping(values)

    assert settings.root == (tmp_path / "financeiro_hoje").resolve()


def test_settings_rejeita_raiz_do_backlog():
    with pytest.raises(ValueError, match="FINANCEIRO_HOJE_ROOT"):
        Settings.from_mapping({
            **BASE,
            "FINANCEIRO_HOJE_ROOT": "/app/data_pipeline/routerbox_backlog",
        })


def test_settings_rejeita_raiz_aninhada_no_backlog():
    with pytest.raises(ValueError, match="FINANCEIRO_HOJE_ROOT"):
        Settings.from_mapping({
            **BASE,
            "FINANCEIRO_HOJE_ROOT": (
                "/app/data_pipeline/routerbox_backlog/financeiro_hoje"
            ),
        })


def test_settings_rejeita_raiz_aninhada_no_backlog_normalizada():
    with pytest.raises(ValueError, match="FINANCEIRO_HOJE_ROOT"):
        Settings.from_mapping({
            **BASE,
            "FINANCEIRO_HOJE_ROOT": (
                "C:/app/data_pipeline/routerbox_backlog/financeiro_hoje"
            ),
        })


def test_settings_rejeita_root_fora_do_volume_compartilhado(tmp_path):
    with pytest.raises(ValueError, match="FINANCEIRO_HOJE_ROOT"):
        Settings.from_mapping({
            **BASE,
            "DATA_PIPELINE_DIR": str(tmp_path / "pipeline"),
            "FINANCEIRO_HOJE_ROOT": str(tmp_path / "outro" / "financeiro_hoje"),
        })


@pytest.mark.parametrize(
    ("key", "url"),
    [
        ("ROUTERBOX_LOGA_URL", ""),
        ("ROUTERBOX_ACERTA_URL", "http://acerta.invalid"),
        ("ROUTERBOX_LOGA_URL", "https://host invalido"),
        ("ROUTERBOX_ACERTA_URL", "https:///sem-host"),
    ],
)
def test_settings_rejeita_url_insegura_antes_de_criar_runtime(tmp_path, key, url):
    pipeline = tmp_path / "pipeline"
    values = {
        **BASE,
        "DATA_PIPELINE_DIR": str(pipeline),
        "FINANCEIRO_HOJE_ROOT": str(pipeline / "financeiro_hoje"),
        "ROUTERBOX_LOGA_URL": "https://loga.invalid",
        "ROUTERBOX_ACERTA_URL": "https://acerta.invalid",
        key: url,
    }

    with pytest.raises(ValueError, match="URL"):
        Settings.from_mapping(values)

    assert not pipeline.exists()
