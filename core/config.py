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
    TRANSFERENCIA_XLSX: str = "referencias/trans_mult.xlsx"
    BROWSER_CHANNEL: str = "chrome"
    BROWSER_USER_DATA_DIR: str = ".browser-profile/protheus"

    @property
    def tecnicos_path(self) -> Path:
        p = Path(self.TECNICOS_JSON)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def transferencia_xlsx_path(self) -> Path:
        p = Path(self.TRANSFERENCIA_XLSX)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def downloads_dir(self) -> Path:
        return Path.home() / "Documentos" / "projects" / "data_pipeline" / "robo_totvs" / "entrada"

    @property
    def logs_dir(self) -> Path:
        return PROJECT_ROOT / "logs"

    @property
    def state_dir(self) -> Path:
        return PROJECT_ROOT / "state"

    @property
    def referencias_dir(self) -> Path:
        return PROJECT_ROOT / "referencias"

    @property
    def browser_user_data_dir(self) -> Path:
        p = Path(self.BROWSER_USER_DATA_DIR).expanduser()
        return p if p.is_absolute() else PROJECT_ROOT / p


settings = Settings()
