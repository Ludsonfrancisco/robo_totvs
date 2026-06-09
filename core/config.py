from pathlib import Path
from typing import Optional

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

    # Diretório base onde o robô grava os XLSX baixados. Estrutura final
    # é `<DOWNLOAD_DIR>/<YYYY-MM-DD>/<COD>.xlsx`. Em produção (container)
    # aponta pro volume compartilhado: /app/data_pipeline/entrada.
    # Se vazio, mantém comportamento legado (dev local).
    DOWNLOAD_DIR: Optional[str] = None

    # RouterBox Backlog hourly automation
    ROUTERBOX_USER: Optional[str] = None
    ROUTERBOX_PASS: Optional[str] = None
    ROUTERBOX_ACERTA_URL: str = "https://integra.acertasolucoes.net.br/routerbox/app_login/index.php"
    ROUTERBOX_LOGA_URL: str = "https://integra.loga.net.br/routerbox/app_login/index.php"
    ROUTERBOX_FILTER_ACERTA: str = "..#### BACKLOG GERAL ACERTA ####"
    ROUTERBOX_FILTER_LOGA: str = "..#### BACKLOG GERAL LOGA ####"
    ROUTERBOX_OUTPUT_DIR: str = "/app/data_pipeline/routerbox_backlog"
    ROUTERBOX_DOWNLOAD_TIMEOUT_S: int = 180
    ROUTERBOX_HOURLY_ENABLED: bool = True
    ROUTERBOX_INTERVAL_MINUTES: int = 60
    ROUTERBOX_RUN_ON_START: bool = False

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
        if self.DOWNLOAD_DIR:
            return Path(self.DOWNLOAD_DIR)
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
