from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    PROTHEUS_URL: str
    PROTHEUS_USER: str
    PROTHEUS_PASS: str

    HEADLESS: bool = False
    VIEWPORT_W: int = 1366
    VIEWPORT_H: int = 768
    DOWNLOAD_TIMEOUT_S: int = 60
    TECNICOS_JSON: str = "technicians.json"

    @property
    def tecnicos_path(self) -> Path:
        p = Path(self.TECNICOS_JSON)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def downloads_dir(self) -> Path:
        return PROJECT_ROOT / "downloads"

    @property
    def logs_dir(self) -> Path:
        return PROJECT_ROOT / "logs"

    @property
    def state_dir(self) -> Path:
        return PROJECT_ROOT / "state"

    @property
    def referencias_dir(self) -> Path:
        return PROJECT_ROOT / "referencias"


settings = Settings()
