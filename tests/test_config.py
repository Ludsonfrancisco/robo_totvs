from core.config import Settings


def test_settings_allow_routerbox_without_obsolete_protheus_credentials(monkeypatch):
    for name in ("PROTHEUS_URL", "PROTHEUS_USER", "PROTHEUS_PASS"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.PROTHEUS_URL == ""
    assert settings.PROTHEUS_USER == ""
    assert settings.PROTHEUS_PASS == ""


def test_settings_expose_financeiro_hoje_disabled_defaults(monkeypatch):
    for name in (
        "FINANCEIRO_HOJE_ROOT",
        "FINANCEIRO_HOJE_SCHEDULE_ENABLED",
        "FINANCEIRO_HOJE_TIMEZONE",
        "FINANCEIRO_HOJE_DEADLINE_SECONDS",
        "FINANCEIRO_HOJE_PERIOD_DAYS",
        "FINANCEIRO_HOJE_POLL_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.FINANCEIRO_HOJE_ROOT.endswith("/financeiro_hoje")
    assert settings.FINANCEIRO_HOJE_SCHEDULE_ENABLED is False
    assert settings.FINANCEIRO_HOJE_TIMEZONE == "America/Sao_Paulo"
    assert settings.FINANCEIRO_HOJE_DEADLINE_SECONDS == 480
    assert settings.FINANCEIRO_HOJE_PERIOD_DAYS == 10
    assert settings.FINANCEIRO_HOJE_POLL_SECONDS == 5
