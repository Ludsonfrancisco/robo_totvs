from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
import json
import logging
import math
import os
from pathlib import Path
import re
import signal
import threading
import time
from typing import Iterator

from .config import Instance


logger = logging.getLogger(__name__)

MENU_TOGGLE = 'xpath=//*[@id="idMenuHeader"]/td/header/div/div[1]/div/div[1]'
REPORT_NAME = "Agendamento de atendimentos"
QUEUE_MESSAGE = "Seu relatório foi enviado para fila"
CUSTOM_REPORTS_NAMES = (
    "Relatórios Personalizado",
    "Relatórios Personalizados",
)
POST_LOGIN_MODAL_CLOSE = (
    '.modal_menu .closed span, .modal_menu span:has-text("x")'
)
REPORT_SELECT = '#id_sc_field_relatorio, select[name="relatorio"]'
SCRIPTCASE_GENERATE_SELECTOR = "#sc_Novo_top"
GENERATE_CONTROL_SELECTOR = (
    'a[title="Gerar"], a[aria-label="Gerar"], '
    'button[title="Gerar"], button[aria-label="Gerar"], '
    'input[type="button"][value="Gerar"], '
    'input[type="submit"][value="Gerar"], '
    f'{SCRIPTCASE_GENERATE_SELECTOR}, #sc_b_gerar_top, #sc_b_gerar_bot, '
    '#sc_b_gerar_t, #sc_b_gerar_b'
)
REFRESH_CONTROL_SELECTOR = (
    '#sc_Refresh_top, '
    'a[title="Atualiza/Recarrega dados"], '
    'a[aria-label="Atualiza/Recarrega dados"], '
    'a[title="Atualizar/Recarregar dados"], '
    'a[aria-label="Atualizar/Recarregar dados"], '
    'a[title="Recarregar/Atualizar"], '
    'a[aria-label="Recarregar/Atualizar"], '
    'button[title="Atualiza/Recarrega dados"], '
    'button[aria-label="Atualiza/Recarrega dados"], '
    'button[title="Atualizar/Recarregar dados"], '
    'button[aria-label="Atualizar/Recarregar dados"]'
)
REFRESH_ACCESSIBLE_NAME = re.compile(
    r"^(?:Atualiza/Recarrega dados|Atualizar/Recarregar dados|"
    r"Recarregar/Atualizar)$",
    re.I,
)
DATE_FIELD_SELECTORS = {
    "Início": (
        '#id_sc_field_filtro_data1, input[name="filtro_data1"]'
    ),
    "Fim": '#id_sc_field_filtro_data2, input[name="filtro_data2"]',
}

_AUTH_MARKERS = (
    "Credenciais inválidas",
    "Usuário ou senha inválidos",
    "Autenticação rejeitada",
)
_CAPTCHA_MARKERS = ("CAPTCHA", "Não sou um robô")
_PROFILE_MARKERS = ("Perfil incorreto", "Perfil inválido")
_DISCOVERY_INTERVAL_MS = 200
_EXPIRED_TIMER_DELAY_SECONDS = 0.000001


class CollectionError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _DownloadDeadline(RuntimeError):
    """Interrompe save_as no processo que detem o contexto Playwright."""


def _now() -> datetime:
    return datetime.now()


def _remaining_ms(deadline: datetime) -> int:
    seconds = (deadline - _now()).total_seconds()
    return max(0, math.ceil(seconds * 1000))


def _timeout_ms(
    deadline: datetime,
    maximum: int | None = None,
) -> int:
    remaining = _remaining_ms(deadline)
    if remaining <= 0:
        raise CollectionError("REPORT_TIMEOUT")
    return min(remaining, maximum) if maximum is not None else remaining


def _can_interrupt_download() -> bool:
    return (
        os.name == "posix"
        and hasattr(signal, "SIGALRM")
        and hasattr(signal, "setitimer")
        and threading.current_thread() is threading.main_thread()
    )


@contextmanager
def _save_as_deadline(deadline: datetime) -> Iterator[None]:
    """Limita ``Download.save_as`` no container POSIX sem criar thread solta."""
    if not _can_interrupt_download():
        raise CollectionError("DOWNLOAD_DEADLINE_UNSUPPORTED")
    timeout_ms = _timeout_ms(deadline)

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer: tuple[float, float] | None = None
    started = time.monotonic()
    handler_installed = False

    def expire(_signum, _frame):
        raise _DownloadDeadline("save_as deadline")

    try:
        signal.signal(signal.SIGALRM, expire)
        handler_installed = True
        previous_timer = signal.setitimer(
            signal.ITIMER_REAL, timeout_ms / 1000
        )
        yield
    finally:
        if previous_timer is not None:
            elapsed = time.monotonic() - started
            restored_delay = previous_timer[0] - elapsed
            signal.setitimer(signal.ITIMER_REAL, 0)
        if handler_installed:
            signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer is not None:
            signal.setitimer(
                signal.ITIMER_REAL,
                (
                    restored_delay
                    if restored_delay > 0
                    else _EXPIRED_TIMER_DELAY_SECONDS
                ),
                previous_timer[1],
            )


def _cancel_download(download) -> None:
    try:
        download.cancel()
    except Exception:
        pass


def _save_download_until(download, destination: Path, deadline: datetime) -> None:
    """Salva no prazo apenas quando o processo oferece interrupcao forte."""
    if not _can_interrupt_download():
        raise CollectionError("DOWNLOAD_DEADLINE_UNSUPPORTED")
    try:
        with _save_as_deadline(deadline):
            download.save_as(str(destination))
    except _DownloadDeadline as exc:
        _cancel_download(download)
        raise CollectionError("REPORT_TIMEOUT") from exc

    if _remaining_ms(deadline) <= 0:
        _cancel_download(download)
        raise CollectionError("REPORT_TIMEOUT")


def _wait_limited(
    page,
    milliseconds: int,
    deadline: datetime,
    *,
    expiry_code: str = "REPORT_TIMEOUT",
) -> None:
    remaining = _remaining_ms(deadline)
    if remaining <= 0:
        raise CollectionError(expiry_code)
    duration = min(milliseconds, remaining)
    page.wait_for_timeout(duration)
    if duration < milliseconds:
        raise CollectionError(expiry_code)


def _scope_children(scope) -> tuple:
    children = getattr(scope, "child_frames", ())
    return tuple(children() if callable(children) else children)


def _scopes(page) -> Iterator:
    seen: set[int] = set()

    def visit(scope):
        identity = id(scope)
        if identity in seen:
            return
        seen.add(identity)
        yield scope
        for child in _scope_children(scope):
            yield from visit(child)

    yield from visit(page)
    for frame in tuple(getattr(page, "frames", ())):
        yield from visit(frame)


def _scope_with_text_once(page, text: str):
    for scope in _scopes(page):
        try:
            locator = scope.get_by_text(text, exact=False).first
            if locator.count():
                return scope
        except Exception:
            continue
    return None


def scope_with_text(
    page,
    text: str,
    deadline: datetime | None = None,
    *,
    expiry_code: str = "DOM_MISSING",
):
    scope = _scope_with_text_once(page, text)
    if scope is not None:
        return scope
    if deadline is None:
        raise CollectionError("DOM_MISSING")
    while _remaining_ms(deadline) > 0:
        _wait_limited(
            page,
            _DISCOVERY_INTERVAL_MS,
            deadline,
            expiry_code=expiry_code,
        )
        scope = _scope_with_text_once(page, text)
        if scope is not None:
            return scope
    raise CollectionError(expiry_code)


def _find_until(
    page,
    finder,
    deadline: datetime,
    *,
    expiry_code: str = "DOM_MISSING",
):
    while True:
        result = finder()
        if result is not None:
            return result
        if _remaining_ms(deadline) <= 0:
            raise CollectionError(expiry_code)
        _wait_limited(
            page,
            _DISCOVERY_INTERVAL_MS,
            deadline,
            expiry_code=expiry_code,
        )


def _click(locator, deadline: datetime) -> None:
    _retry_once(
        lambda: locator.click(timeout=_timeout_ms(deadline, 30_000)),
        deadline,
    )


def _fill(locator, value: str, deadline: datetime) -> None:
    locator.fill(value, timeout=_timeout_ms(deadline, 30_000))


def _select_option(
    locator,
    label: str,
    deadline: datetime,
    *,
    force: bool = False,
) -> None:
    locator.select_option(
        label=label,
        timeout=_timeout_ms(deadline, 30_000),
        force=force,
    )


def _retry_once(action, deadline: datetime):
    last_error = None
    for attempt in range(2):
        _timeout_ms(deadline)
        try:
            return action()
        except CollectionError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt:
                break
            _timeout_ms(deadline)
    raise CollectionError("DOM_MISSING") from last_error


def _first_locator(page, factory):
    for scope in _scopes(page):
        try:
            locator = factory(scope).first
            if locator.count():
                return locator
        except Exception:
            continue
    return None


def _first_visible(locator):
    try:
        count = locator.count()
    except Exception:
        return None
    for index in range(count):
        try:
            candidate = locator.nth(index)
            if candidate.is_visible():
                return candidate
        except Exception:
            continue
    return None


def _first_visible_locator(page, factory):
    for scope in _scopes(page):
        try:
            locator = _first_visible(factory(scope))
            if locator is not None:
                return locator
        except Exception:
            continue
    return None

def _first_actionable(locator):
    try:
        count = locator.count()
    except Exception:
        return None
    for index in range(count):
        try:
            candidate = locator.nth(index)
            if not candidate.is_visible():
                continue
            classes = set((candidate.get_attribute("class") or "").split())
            if "disabled" in classes:
                continue
            if (candidate.get_attribute("aria-disabled") or "").casefold() == "true":
                continue
            if candidate.get_attribute("disabled") is not None:
                continue
            is_enabled = getattr(candidate, "is_enabled", None)
            if callable(is_enabled) and not is_enabled():
                continue
            return candidate
        except Exception:
            continue
    return None


def _first_actionable_locator(page, factory):
    for scope in _scopes(page):
        try:
            locator = _first_actionable(factory(scope))
            if locator is not None:
                return locator
        except Exception:
            continue
    return None


def _visible_text_locator(page, texts: tuple[str, ...]):
    for text in texts:
        locator = _first_visible_locator(
            page,
            lambda scope, candidate=text: scope.get_by_text(
                candidate,
                exact=True,
            ),
        )
        if locator is not None:
            return locator
    return None


def _scope_with_visible_control_once(page, factory):
    for scope in _scopes(page):
        try:
            control = _first_visible(factory(scope))
            if control is not None:
                return scope, control
        except Exception:
            continue
    return None


def _scope_with_visible_control(
    page,
    factory,
    deadline: datetime | None = None,
):
    result = _scope_with_visible_control_once(page, factory)
    if result is not None:
        return result
    if deadline is None:
        raise CollectionError("DOM_MISSING")
    return _find_until(
        page,
        lambda: _scope_with_visible_control_once(page, factory),
        deadline,
    )


def _report_form_in_scope(scope):
    report = scope.locator(REPORT_SELECT).first
    if not report.count():
        report = _first_visible(
            scope.get_by_label("Selecione", exact=False)
        )
    if report is None or not report.count():
        return None
    form = report.locator("xpath=ancestor::form[1]").first
    if form.count():
        return form
    return scope


def _report_form_once(page):
    for scope in _scopes(page):
        try:
            form = _report_form_in_scope(scope)
            if form is not None:
                return form
        except Exception:
            continue
    return None


def _report_form(page, deadline: datetime | None = None):
    result = _report_form_once(page)
    if result is not None:
        return result
    if deadline is None:
        raise CollectionError("DOM_MISSING")
    return _find_until(
        page,
        lambda: _report_form_once(page),
        deadline,
    )


def _anchored_custom_reports_container(scope, anchor):
    container = anchor.locator(
        "xpath=ancestor-or-self::*["
        "self::main or @role='main' or self::form or "
        "contains(concat(' ', normalize-space(@class), ' '), "
        "' scGridPage ') or starts-with(@id, 'sc_grid')"
        "][1]"
    ).first
    if container.count():
        return container
    return scope


def _custom_reports_panel(scope):
    for name in CUSTOM_REPORTS_NAMES:
        heading = _first_visible(
            scope.get_by_role(
                "heading",
                name=name,
                exact=True,
            )
        )
        if heading is not None:
            return _anchored_custom_reports_container(
                scope,
                heading,
            )

    grid = _first_visible(
        scope.locator(
            '[role="grid"], table, #sc_grid_body, .scGridPage'
        )
    )
    if grid is None:
        return None
    for name in CUSTOM_REPORTS_NAMES:
        title = _first_visible(
            scope.get_by_text(name, exact=True)
        )
        if title is not None:
            return _anchored_custom_reports_container(
                scope,
                title,
            )
    return None


def _custom_reports_panel_once(page):
    for scope in _scopes(page):
        try:
            panel = _custom_reports_panel(scope)
            if panel is not None:
                return panel
        except Exception:
            continue
    return None


def _custom_reports_panel_until(
    page,
    deadline: datetime | None = None,
):
    result = _custom_reports_panel_once(page)
    if result is not None:
        return result
    if deadline is None:
        raise CollectionError("DOM_MISSING")
    return _find_until(
        page,
        lambda: _custom_reports_panel_once(page),
        deadline,
    )


def _generate_in_panel_once(panel):
    for role in ("button", "link"):
        generate = _first_visible(
            panel.get_by_role(
                role,
                name="Gerar",
                exact=True,
            )
        )
        if generate is not None:
            return generate
    generate = _first_visible(
        panel.locator(GENERATE_CONTROL_SELECTOR)
    )
    if generate is not None:
        return generate
    return _first_visible(
        panel.locator(
            "a, button, input, [onclick]"
        ).filter(
            has_text=re.compile(r"^\s*Gerar\s*$"),
        )
    )


def _generate_in_scope_once(page):
    return _first_visible_locator(
        page,
        lambda scope: scope.locator(SCRIPTCASE_GENERATE_SELECTOR),
    )


def _generate_in_panel(
    page,
    panel,
    deadline: datetime | None = None,
):
    result = _generate_in_panel_once(panel) or _generate_in_scope_once(page)
    if result is not None:
        return result
    if deadline is None:
        raise CollectionError("DOM_MISSING")
    return _find_until(
        page,
        lambda: _generate_in_panel_once(panel) or _generate_in_scope_once(page),
        deadline,
    )


def click_in_any_scope(
    page,
    *,
    text: str,
    deadline: datetime | None = None,
) -> None:
    action_deadline = deadline or datetime.max
    locator = _find_until(
        page,
        lambda: _first_locator(
            page,
            lambda scope: scope.get_by_text(text, exact=True),
        ),
        action_deadline,
    )
    _click(locator, action_deadline)


def _click_visible_text_in_any_scope(
    page,
    *,
    texts: tuple[str, ...],
    deadline: datetime,
) -> None:
    locator = _find_until(
        page,
        lambda: _visible_text_locator(page, texts),
        deadline,
    )
    _click(locator, deadline)


def _has_text(page, text: str) -> bool:
    try:
        scope_with_text(page, text)
    except CollectionError:
        return False
    return True


def _detect_blocker(page) -> None:
    for marker in _CAPTCHA_MARKERS:
        if _has_text(page, marker):
            raise CollectionError("CAPTCHA_DETECTED")
    for marker in _AUTH_MARKERS:
        if _has_text(page, marker):
            raise CollectionError("AUTH_REJECTED")
    for marker in _PROFILE_MARKERS:
        if _has_text(page, marker):
            raise CollectionError("PROFILE_MISMATCH")


def _selector_in_any_scope(page, selector: str):
    return _first_locator(page, lambda scope: scope.locator(selector))


def _authenticate(page, instance: Instance, deadline: datetime) -> None:
    _retry_once(
        lambda: page.goto(
            instance.url,
            wait_until="domcontentloaded",
            timeout=_timeout_ms(deadline, 60_000),
        ),
        deadline,
    )
    _detect_blocker(page)

    password = _selector_in_any_scope(
        page,
        'input[name="senha"], input[name="password"], input[type="password"]',
    )
    if password is None:
        _close_post_login_modal_if_visible(page, deadline)
        return

    username = _selector_in_any_scope(
        page,
        'input[name="usuario"], input[name="username"], input[type="email"]',
    )
    submit = _first_locator(
        page,
        lambda scope: scope.get_by_role(
            "button",
            name=re.compile(r"Entrar|Acessar", re.I),
        ),
    )
    if submit is None:
        submit = _selector_in_any_scope(
            page,
            '#sub_form_b, input[type="submit"]',
        )
    if username is None or submit is None:
        raise CollectionError("DOM_MISSING")
    _fill(username, instance.user, deadline)
    _fill(password, instance.password, deadline)
    _click(submit, deadline)
    try:
        page.wait_for_load_state(
            "domcontentloaded",
            timeout=_timeout_ms(deadline, 30_000),
        )
    except CollectionError:
        raise
    except Exception:
        pass
    _detect_blocker(page)
    if _selector_in_any_scope(
        page,
        'input[name="senha"], input[name="password"], input[type="password"]',
    ) is not None:
        raise CollectionError("AUTH_REJECTED")
    _close_post_login_modal_if_visible(page, deadline)


def _close_post_login_modal_if_visible(page, deadline: datetime) -> None:
    close = _first_visible_locator(
        page,
        lambda scope: scope.locator(POST_LOGIN_MODAL_CLOSE),
    )
    if close is not None:
        _click(close, deadline)


def navigate_to_custom_reports(
    page,
    deadline: datetime | None = None,
) -> None:
    action_deadline = deadline or datetime.max
    toggle = _find_until(
        page,
        lambda: _first_visible_locator(
            page,
            lambda scope: scope.locator(MENU_TOGGLE),
        ),
        action_deadline,
    )
    _click(toggle, action_deadline)
    _click_visible_text_in_any_scope(
        page,
        texts=("Empresa",),
        deadline=action_deadline,
    )
    _click_visible_text_in_any_scope(
        page,
        texts=("Relatórios",),
        deadline=action_deadline,
    )
    _click_visible_text_in_any_scope(
        page,
        texts=CUSTOM_REPORTS_NAMES,
        deadline=action_deadline,
    )


def prepare_custom_reports(
    page,
    instance: Instance,
    deadline: datetime,
) -> None:
    _authenticate(page, instance, deadline)
    navigate_to_custom_reports(page, deadline)
    _detect_blocker(page)


def fill_date(
    scope,
    label: str,
    value: str,
    deadline: datetime | None = None,
) -> None:
    try:
        control = _first_visible(
            scope.get_by_label(label, exact=False)
        )
        selector = DATE_FIELD_SELECTORS.get(label)
        if control is None and selector is not None:
            control = _first_visible(scope.locator(selector))
        if control is None:
            raise CollectionError("DOM_MISSING")
        if deadline is None:
            control.fill(value)
        else:
            _fill(control, value, deadline)
    except CollectionError:
        raise
    except Exception as exc:
        raise CollectionError("DOM_MISSING") from exc


def accept_dialog_or_modal(
    page,
    message: str,
    *,
    dialog_messages: list[str] | tuple[str, ...] = (),
    deadline: datetime | None = None,
    stage_callback=None,
) -> None:
    def find_confirmation():
        if dialog_messages:
            normalized = " ".join(
                dialog_messages[-1].casefold().split()
            )
            allowed = (
                "seu relatório foi enviado para fila",
                "seu relatorio foi enviado para fila",
            )
            if any(marker in normalized for marker in allowed):
                return "dialog", None
            _emit_stage(
                stage_callback,
                "queue_confirmation_unexpected_dialog",
            )
            raise CollectionError("UNEXPECTED_DIALOG")
        scope = _scope_with_text_once(page, message)
        if scope is not None:
            return "modal", scope
        return None

    confirmation = find_confirmation()
    if confirmation is None:
        if deadline is None:
            raise CollectionError("DOM_MISSING")
        confirmation = _find_until(
            page,
            find_confirmation,
            deadline,
        )
    confirmation_type, scope = confirmation
    if confirmation_type == "dialog":
        _emit_stage(stage_callback, "queue_confirmation_dialog")
        return
    _emit_stage(stage_callback, "queue_confirmation_modal")
    message_locator = scope.get_by_text(message, exact=False).first
    modal = message_locator.locator(
        "xpath=ancestor-or-self::dialog | "
        "ancestor-or-self::*[@role='dialog' or @role='alertdialog' "
        "or @aria-modal='true']"
    ).first
    if not modal.count():
        raise CollectionError("DOM_MISSING")
    modal_ok = modal.get_by_role(
        "button",
        name=re.compile(r"^ok$", re.I),
    ).first
    if not modal_ok.count():
        modal_ok = modal.get_by_text("OK", exact=True).first
    if not modal_ok.count():
        raise CollectionError("DOM_MISSING")
    _click(modal_ok, deadline or datetime.max)


def queue_report(
    page,
    period_start: date,
    period_end: date,
    deadline: datetime | None = None,
    *,
    stage_callback=None,
) -> None:
    action_deadline = deadline or datetime.max
    panel = _custom_reports_panel_until(
        page,
        deadline=deadline,
    )
    _emit_stage(stage_callback, "queue_panel_found")
    generate = _generate_in_panel(
        page,
        panel,
        deadline=deadline,
    )
    _emit_stage(stage_callback, "queue_generate_found")
    _click(generate, action_deadline)
    _emit_stage(stage_callback, "queue_generate_clicked")
    scope = _report_form(page, deadline)
    _emit_stage(stage_callback, "queue_controls_ready")

    native_report = scope.locator(REPORT_SELECT).first
    if native_report.count():
        report = native_report
        force_native = True
    else:
        report = _first_visible(
            scope.get_by_label("Selecione", exact=False)
        )
        force_native = False
    if report is None:
        raise CollectionError("DOM_MISSING")
    _select_option(
        report,
        REPORT_NAME,
        action_deadline,
        force=force_native,
    )
    _emit_stage(stage_callback, "queue_report_selected")
    _wait_limited(page, 500, action_deadline)
    scope = _report_form(page, deadline)
    fill_date(
        scope,
        "Início",
        period_start.strftime("%d/%m/%Y"),
        action_deadline,
    )
    _emit_stage(stage_callback, "queue_dates_filled")
    fill_date(
        scope,
        "Fim",
        period_end.strftime("%d/%m/%Y"),
        action_deadline,
    )

    dialog_messages: list[str] = []
    dialog_handler = None
    if hasattr(page, "once"):
        def handle_dialog(dialog):
            dialog_messages.append(dialog.message)
            dialog.accept()

        dialog_handler = handle_dialog
        page.once("dialog", handle_dialog)
    try:
        confirm = _first_visible(
            scope.get_by_role(
                "button",
                name=re.compile(r"^ok$", re.I),
            )
        )
        if confirm is None:
            confirm = _first_visible(scope.locator("#sub_form_b"))
        if confirm is None:
            raise CollectionError("DOM_MISSING")
        _click(confirm, action_deadline)
        _emit_stage(stage_callback, "queue_confirm_clicked")
        accept_dialog_or_modal(
            page,
            QUEUE_MESSAGE,
            dialog_messages=dialog_messages,
            deadline=action_deadline,
            stage_callback=stage_callback,
        )
        _emit_stage(stage_callback, "queue_confirmation_complete")
    finally:
        if dialog_handler is not None and hasattr(page, "remove_listener"):
            page.remove_listener("dialog", dialog_handler)


def _row_for_period(
    page,
    period_start: date,
    period_end: date,
    deadline: datetime,
):
    expected_report = REPORT_NAME.casefold()
    expected_starts = (
        period_start.strftime("%d/%m/%Y").casefold(),
        period_start.isoformat().casefold(),
    )
    expected_ends = (
        period_end.strftime("%d/%m/%Y").casefold(),
        period_end.isoformat().casefold(),
    )
    for scope in _scopes(page):
        try:
            rows = scope.locator("tr")
            for index in range(rows.count()):
                row = rows.nth(index)
                text = " ".join(
                    row.inner_text(
                        timeout=_timeout_ms(deadline, 30_000),
                    ).split()
                ).casefold()
                if (
                    expected_report in text
                    and any(value in text for value in expected_starts)
                    and any(value in text for value in expected_ends)
                ):
                    return row, text
        except CollectionError:
            raise
        except Exception:
            continue
    return None, ""


def _emit_stage(stage_callback, stage: str) -> None:
    logger.info("stage=%s", stage)
    if stage_callback is not None:
        stage_callback(stage)


def _refresh_grid(
    page,
    deadline: datetime,
    *,
    stage_callback=None,
) -> None:
    def find_refresh():
        refresh = _first_locator(
            page,
            lambda scope: scope.get_by_role(
                "button",
                name=REFRESH_ACCESSIBLE_NAME,
            ),
        )
        if refresh is None:
            refresh = _first_visible_locator(
                page,
                lambda scope: scope.get_by_role(
                    "img",
                    name=REFRESH_ACCESSIBLE_NAME,
                ),
            )
        if refresh is None:
            refresh = _first_visible_locator(
                page,
                lambda scope: scope.get_by_role(
                    "link",
                    name=REFRESH_ACCESSIBLE_NAME,
                ),
            )
        if refresh is None:
            refresh = _first_visible_locator(
                page,
                lambda scope: scope.locator(
                    REFRESH_CONTROL_SELECTOR
                ),
            )
        if refresh is None:
            refresh = _first_locator(
                page,
                lambda scope: scope.get_by_text(
                    REFRESH_ACCESSIBLE_NAME,
                ),
            )
        return refresh

    refresh = _find_until(page, find_refresh, deadline)
    _emit_stage(stage_callback, "queue_refresh_found")
    _click(refresh, deadline)
    _emit_stage(stage_callback, "queue_refresh_clicked")


def _wait_until_complete(
    page,
    period_start: date,
    period_end: date,
    deadline: datetime,
    *,
    stage_callback=None,
):
    _wait_limited(page, 10_000, deadline)
    while _remaining_ms(deadline) > 0:
        _refresh_grid(
            page,
            deadline,
            stage_callback=stage_callback,
        )
        _wait_limited(page, 500, deadline)
        row, _text = _row_for_period(
            page,
            period_start,
            period_end,
            deadline,
        )
        if row is not None:
            try:
                status = row.get_by_text("Concluído", exact=True).first
                if status.count():
                    _emit_stage(
                        stage_callback,
                        "queue_row_complete",
                    )
                    return row
            except Exception:
                pass
        _wait_limited(page, 5_000, deadline)
    raise CollectionError("REPORT_TIMEOUT")


def _open_report(row, deadline: datetime) -> None:
    locator = _first_visible(
        row.locator(
            '[title="Visualizar relatório personalizado"], '
            '[aria-label="Visualizar relatório personalizado"]'
        )
    )
    if locator is None:
        locator = _first_visible(
            row.locator('a[href*="cons_relatorio_dinamico"]')
        )
    if locator is None:
        raise CollectionError("DOM_MISSING")
    _click(locator, deadline)


def _named_control_once(page, name: str):
    control = _first_locator(
        page,
        lambda scope: scope.get_by_role(
            "button",
            name=name,
            exact=True,
        ),
    )
    if control is not None:
        return control
    return _first_locator(
        page,
        lambda scope: scope.get_by_text(name, exact=True),
    )


def _wait_for_download_control(page, deadline: datetime):
    def find_download():
        control = _first_actionable_locator(
            page,
            lambda scope: scope.get_by_title("Baixar", exact=True),
        )
        if control is None:
            control = _first_actionable_locator(
                page,
                lambda scope: scope.get_by_role(
                    "button",
                    name="Baixar",
                    exact=True,
                ),
            )
        if control is None:
            control = _first_actionable_locator(
                page,
                lambda scope: scope.get_by_text("Baixar", exact=True),
            )
        return control

    return _find_until(page, find_download, deadline)


def _export_report(page, destination: Path, deadline: datetime) -> Path:
    for name in ("Avançado", "Excel"):
        control = _find_until(
            page,
            lambda name=name: _named_control_once(page, name),
            deadline,
        )
        _click(control, deadline)

    destination.parent.mkdir(parents=True, exist_ok=True)
    last_error = None
    for attempt in range(2):
        download_control = _wait_for_download_control(page, deadline)
        try:
            with page.expect_download(
                timeout=_timeout_ms(deadline, 30_000),
            ) as download_info:
                _click(download_control, deadline)
            _save_download_until(download_info.value, destination, deadline)
            return destination
        except CollectionError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt:
                break
            _timeout_ms(deadline)
    raise CollectionError("DOWNLOAD_FAILED") from last_error


def _default_evidence_directory(instance: Instance) -> Path:
    run_id = f"{_now().strftime('%Y%m%dT%H%M%S')}-local"
    return Path("evidence") / run_id / instance.name.casefold()


def _stage_callback_for(directory: Path):
    target = directory / "stage.json"
    temporary = directory / ".stage.json.tmp"

    def persist(stage: str) -> None:
        payload = {
            "schema_version": 1,
            "stage": stage,
            "updated_at": _now().isoformat(),
        }
        try:
            directory.mkdir(parents=True, exist_ok=True)
            temporary.unlink(missing_ok=True)
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
            ) as stream:
                json.dump(payload, stream, ensure_ascii=True)
            os.replace(temporary, target)
            target.chmod(0o600)
        except OSError:
            logger.warning("stage=evidence_stage_write_failed")
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    return persist


def _capture_failure(
    page,
    instance: Instance,
    code: str,
    evidence_root: Path | None,
) -> None:
    directory = (
        Path(evidence_root)
        if evidence_root is not None
        else _default_evidence_directory(instance)
    )
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{code.casefold()}.png"
    try:
        inputs = page.locator("input, textarea")
        page.screenshot(path=str(target), full_page=True, mask=[inputs])
        target.chmod(0o600)
    except Exception:
        if target.exists():
            target.chmod(0o600)


def download_report(
    page,
    instance: Instance,
    destination: Path,
    period_start: date,
    period_end: date,
    deadline: datetime,
    *,
    evidence_root: Path | None = None,
) -> Path:
    evidence_directory = (
        Path(evidence_root)
        if evidence_root is not None
        else _default_evidence_directory(instance)
    )
    stage_callback = _stage_callback_for(evidence_directory)
    try:
        _timeout_ms(deadline)
        _emit_stage(stage_callback, "prepare_started")
        prepare_custom_reports(page, instance, deadline)
        _emit_stage(stage_callback, "queue_started")
        queue_report(
            page,
            period_start,
            period_end,
            deadline,
            stage_callback=stage_callback,
        )
        _emit_stage(stage_callback, "grid_wait_started")
        row = _wait_until_complete(
            page,
            period_start,
            period_end,
            deadline,
            stage_callback=stage_callback,
        )
        _emit_stage(stage_callback, "report_open_started")
        _open_report(row, deadline)
        _emit_stage(stage_callback, "export_started")
        result = _export_report(
            page,
            Path(destination),
            deadline,
        )
        _emit_stage(stage_callback, "download_complete")
        return result
    except CollectionError as exc:
        _capture_failure(
            page,
            instance,
            exc.code,
            evidence_directory,
        )
        raise
    except Exception as exc:
        error = CollectionError("DOM_MISSING")
        _capture_failure(
            page,
            instance,
            error.code,
            evidence_directory,
        )
        raise error from exc
