from pathlib import Path

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


def _remove_invalid_download(destination: Path) -> None:
    try:
        if destination.is_file():
            destination.unlink()
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

    try:
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_info:
            export_button.click()
        download_info.value.save_as(destination)
    except PlaywrightTimeoutError as exc:
        _remove_invalid_download(destination)
        raise CollectionError("DOWNLOAD_TIMEOUT") from exc
    except Exception as exc:
        _remove_invalid_download(destination)
        raise CollectionError("DOWNLOAD_FAILED") from exc

    try:
        if not destination.is_file() or destination.stat().st_size == 0:
            _remove_invalid_download(destination)
            raise CollectionError("DOWNLOAD_FAILED")
    except OSError as exc:
        _remove_invalid_download(destination)
        raise CollectionError("DOWNLOAD_FAILED") from exc

    return destination
