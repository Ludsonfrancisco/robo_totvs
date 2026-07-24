from core.config import Settings


def test_settings_allow_routerbox_without_obsolete_protheus_credentials(monkeypatch):
    for name in ("PROTHEUS_URL", "PROTHEUS_USER", "PROTHEUS_PASS"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.PROTHEUS_URL == ""
    assert settings.PROTHEUS_USER == ""
    assert settings.PROTHEUS_PASS == ""
