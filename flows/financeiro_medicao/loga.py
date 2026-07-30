import os
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .cycles import CycleWindow


AUTHENTICATED_TITLE = "Medição de Pagamento à Terceiros"
DOWNLOAD_TIMEOUT_MS = 120_000


class CollectionError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def apply_period(page, window: CycleWindow) -> None:
    page.locator("#dti").fill(window.query_start.isoformat())
    page.locator("#dtf").fill(window.query_end.isoformat())


def _authenticated(page) -> bool:
    return (
        page.title() == AUTHENTICATED_TITLE
        and page.locator('input[type="password"]').count() == 0
    )


def _temporary_download_path(destination: Path) -> Path:
    temporary = destination.parent / (
        f".{destination.name}.{uuid4().hex}.tmp"
    )
    descriptor = os.open(
        temporary,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )
    os.close(descriptor)
    return temporary


def _remove_temporary_download(temporary: Path | None) -> None:
    if temporary is None:
        return
    try:
        temporary.unlink(missing_ok=True)
    except OSError:
        pass


def collect(
    page,
    window: CycleWindow,
    settings,
    destination: Path,
) -> Path:
    destination = Path(destination)
    if destination.exists() or not destination.parent.is_dir():
        raise CollectionError("DOWNLOAD_FAILED")

    try:
        page.goto(settings.loga_url, wait_until="networkidle")
    except PlaywrightTimeoutError as exc:
        raise CollectionError("NAVIGATION_TIMEOUT") from exc

    if not _authenticated(page):
        raise CollectionError("AUTH_EXPIRED")

    start_control = page.locator("#dti")
    if not start_control.is_visible():
        page.get_by_role(
            "button",
            name="Filtros",
            exact=True,
        ).click()

    controls = {
        "#modoCalculo": "Expurgados",
        "#tipoMedicao": "",
        "#tipoAgrupamento": "cidade",
    }
    for selector, value in controls.items():
        page.locator(selector).select_option(value=value)

    apply_period(page, window)
    page.get_by_role(
        "button",
        name="Pesquisar",
        exact=True,
    ).click()

    try:
        calculation_dialog = page.get_by_text(
            "Calculando medição...",
            exact=True,
        )
        if calculation_dialog.count():
            calculation_dialog.wait_for(
                state="hidden",
                timeout=DOWNLOAD_TIMEOUT_MS,
            )
        export_button = page.get_by_role(
            "button",
            name="Exportar Atendimentos",
            exact=True,
        )
        export_button.wait_for(
            state="visible",
            timeout=DOWNLOAD_TIMEOUT_MS,
        )
    except PlaywrightTimeoutError as exc:
        raise CollectionError("DOWNLOAD_TIMEOUT") from exc

    if any(
        page.locator(selector).input_value() != expected
        for selector, expected in controls.items()
    ):
        raise CollectionError("FILTER_MISMATCH")
    if (
        page.locator("#dti").input_value()
        != window.query_start.isoformat()
        or page.locator("#dtf").input_value()
        != window.query_end.isoformat()
    ):
        raise CollectionError("FILTER_MISMATCH")

    temporary = None
    try:
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_info:
            export_button.click()
        temporary = _temporary_download_path(destination)
        download_info.value.save_as(temporary)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise CollectionError("DOWNLOAD_FAILED")
        with temporary.open("r+b") as download_file:
            download_file.flush()
            os.fsync(download_file.fileno())
        os.link(temporary, destination)
    except PlaywrightTimeoutError as exc:
        raise CollectionError("DOWNLOAD_TIMEOUT") from exc
    except CollectionError:
        raise
    except Exception as exc:
        raise CollectionError("DOWNLOAD_FAILED") from exc
    finally:
        _remove_temporary_download(temporary)

    return destination
