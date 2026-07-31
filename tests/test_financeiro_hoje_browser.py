from contextlib import nullcontext
from datetime import date, datetime, timedelta
from pathlib import Path
import json
import os
import re
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

import flows.financeiro_hoje.browser as browser
import scripts.financeiro_hoje_dom_probe as dom_probe
from flows.financeiro_hoje.browser import (
    CollectionError,
    REPORT_NAME,
    download_report,
    scope_with_text,
)
from flows.financeiro_hoje.config import Instance
from scripts.financeiro_hoje_dom_probe import (
    _browser_launch_options,
    snapshot_html,
    snapshot_page,
)


START = date(2026, 7, 27)
END = date(2026, 8, 6)
NOW = datetime(2026, 7, 27, 8, 20)
DEADLINE = datetime(2026, 7, 27, 8, 28)
EXPIRED = datetime(2026, 7, 27, 8, 19)
INSTANCE = Instance(
    name="LOGA",
    url="https://example.invalid/routerbox",
    user="usuario-sintetico",
    password="senha-sintetica",
)


class FakeDownload:
    def save_as(self, target):
        Path(target).write_bytes(b"xlsx-sintetico")


class FakeDownloadInfo:
    def __init__(self, download):
        self.value = download


class FakeDownloadContext:
    def __init__(self, download):
        self.download = download

    def __enter__(self):
        return FakeDownloadInfo(self.download)

    def __exit__(self, *_args):
        return False


class FakeDialog:
    message = "Seu relatório foi enviado para fila, confirmação sintética"

    def __init__(self):
        self.accepted = False

    def accept(self):
        self.accepted = True


class FakeLocator:
    def __init__(
        self,
        page,
        action=None,
        *,
        count=1,
        text="",
        rows=None,
        cells=None,
    ):
        self.page = page
        self.action = action
        self._count = count
        self._text = text
        self._rows = rows
        self._cells = cells or []

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    def count(self):
        if self._rows is not None:
            return len(self._rows)
        return self._count

    def is_visible(self):
        return bool(self.count())

    def nth(self, index):
        if self._rows is None:
            if index != 0:
                raise IndexError(index)
            return self
        return self._rows[index]

    def click(self, **_kwargs):
        if self.action:
            self.page.click(self.action)

    def fill(self, value, **_kwargs):
        self.page.filled[self.action] = value
        if {"Início", "Fim"}.issubset(self.page.filled) and self.page.on_period:
            self.page.on_period(
                self.page.filled["Início"],
                self.page.filled["Fim"],
            )
            self.page.on_period = None

    def select_option(self, *, label, **_kwargs):
        self.page.selected_report = label

    def inner_text(self, **_kwargs):
        return self._text

    def get_by_text(self, text, **kwargs):
        exact = kwargs.get("exact", False)
        normalized = " ".join(self._text.split())
        count = int(
            any(" ".join(cell.split()) == text for cell in self._cells)
            if exact
            else text.casefold() in normalized.casefold()
        )
        return FakeLocator(self.page, count=count, text=text)

    def get_by_role(self, role, *, name, **_kwargs):
        label = name.pattern if isinstance(name, re.Pattern) else name
        if self.action == "modal" and role == "button" and label == "^ok$":
            return FakeLocator(self.page, "modal_ok")
        return FakeLocator(self.page, count=0)

    def get_attribute(self, name):
        if name == "title" and self.action == "open_report":
            return "Visualizar relatório personalizado"
        return None

    def locator(self, selector):
        if "Visualizar relatório personalizado" in selector:
            return FakeLocator(self.page, "open_report")
        if (
            "ancestor-or-self" in selector
            and self.action == "text:Seu relatório foi enviado para fila"
        ):
            return FakeLocator(self.page, "modal")
        return FakeLocator(self.page, count=0)


class FakeScope:
    def __init__(self, page, texts=()):
        self.page = page
        self.texts = set(texts)
        self.child_frames = []

    def get_by_text(self, text, **_kwargs):
        action = {
            "Avançado": "advanced",
            "Excel": "excel",
        }.get(text, f"text:{text}")
        count = int(
            text in self.texts
            or text in {
                "Empresa",
                "Relatórios",
                "Seu relatório foi enviado para fila",
                "Exportação XLS",
            }
        )
        return FakeLocator(self.page, action, count=count)

    def get_by_role(self, role, *, name, **_kwargs):
        label = name.pattern if isinstance(name, re.Pattern) else name
        if (
            role == "heading"
            and label in browser.CUSTOM_REPORTS_NAMES
            and label in self.texts
        ):
            return FakeLocator(self.page, "reports_heading")
        actions = {
            "Gerar": "generate",
            "^ok$": "ok",
            "Avançado": "advanced",
            "Excel": "excel",
            "Baixar": "download",
        }
        if isinstance(name, re.Pattern) and "Atual" in name.pattern:
            return FakeLocator(self.page, "refresh")
        if (
            self.page.export_controls_by_text_only
            and label in {"Avançado", "Excel"}
        ):
            return FakeLocator(self.page, count=0)
        if label == "Baixar":
            return FakeLocator(
                self.page,
                "download",
                count=int(
                    self.page.download_queries
                    >= self.page.download_available_after
                ),
            )
        return FakeLocator(self.page, actions.get(label), count=int(label in actions))

    def get_by_label(self, name, **_kwargs):
        if name == "Selecione":
            return FakeLocator(self.page, "report")
        if name in {"Início", "Fim"}:
            return FakeLocator(self.page, name)
        return FakeLocator(self.page, count=0)

    def get_by_title(self, name, **_kwargs):
        if name == "Visualizar relatório personalizado":
            return FakeLocator(self.page, "open_report")
        if name == "Baixar":
            self.page.download_queries += 1
            return FakeLocator(
                self.page,
                "download",
                count=int(
                    self.page.download_queries
                    >= self.page.download_available_after
                ),
            )
        return FakeLocator(self.page, count=0)

    def locator(self, selector):
        if (
            selector
            == '.modal_menu .closed span, .modal_menu span:has-text("x")'
            and self.page.post_login_modal_open
        ):
            return FakeLocator(self.page, "close_post_login_modal")
        if "input[name=\"senha\"]" in selector and self.page.login_required:
            return FakeLocator(self.page, "Senha")
        if "input[name=\"usuario\"]" in selector and self.page.login_required:
            return FakeLocator(self.page, "Usuário")
        if "#sub_form_b" in selector and self.page.login_required:
            return FakeLocator(self.page, "login_submit")
        if selector == "tr":
            unrelated = FakeLocator(
                self.page,
                text=f"{REPORT_NAME} 01/01/2020 02/01/2020 Concluído",
                cells=[
                    REPORT_NAME,
                    "01/01/2020",
                    "02/01/2020",
                    "Concluído",
                ],
            )
            status = self.page.current_status or "Gerando Relatório"
            requested = FakeLocator(
                self.page,
                text=f"{REPORT_NAME} 27/07/2026 06/08/2026 {status}",
                cells=[
                    REPORT_NAME,
                    "27/07/2026",
                    "06/08/2026",
                    status,
                ],
            )
            return FakeLocator(self.page, rows=[unrelated, requested])
        if selector == browser.MENU_TOGGLE:
            return FakeLocator(self.page, "menu")
        if selector == "input, textarea":
            return FakeLocator(self.page)
        return FakeLocator(self.page, count=0)


class FakePage(FakeScope):
    def __init__(self):
        super().__init__(
            self,
            texts={
                "Relatórios Personalizados",
                "Seu relatório foi enviado para fila",
                "Exportação XLS",
            },
        )
        self.frames = []
        self.statuses = ["Concluído"]
        self.current_status = None
        self.refresh_count = 0
        self.excel_clicked = False
        self.download_clicked = False
        self.download_queries = 0
        self.download_available_after = 1
        self.selected_report = None
        self.filled = {}
        self.on_period = None
        self.waits = []
        self.goto_calls = []
        self.screenshots = []
        self.fail_once = set()
        self.failed = set()
        self.login_required = False
        self.login_rejected = False
        self.login_clicks = 0
        self.actions = []
        self.ok_clicks = 0
        self.expect_download_timeouts = []
        self.export_controls_by_text_only = False
        self.delayed_frame = None
        self.js_dialog = None
        self.dialog_handler = None
        self.download = FakeDownload()
        self.removed_dialog_handlers = []
        self.use_js_dialog = False
        self.post_login_modal_open = False

    def goto(self, url, **_kwargs):
        self.goto_calls.append(url)

    def click(self, action):
        self.actions.append(action)
        if action == "menu" and self.post_login_modal_open:
            raise RuntimeError("modal bloqueador interceptou o clique")
        if action in self.fail_once and action not in self.failed:
            self.failed.add(action)
            raise RuntimeError("falha transitória")
        if action == "refresh":
            index = min(self.refresh_count, len(self.statuses) - 1)
            self.current_status = self.statuses[index]
            self.refresh_count += 1
        elif action == "excel":
            self.excel_clicked = True
            self.texts.add("Exportação XLS")
        elif action == "download":
            self.download_clicked = True
        elif action == "login_submit":
            self.login_clicks += 1
            if not self.login_rejected:
                self.login_required = False
        elif action == "close_post_login_modal":
            self.post_login_modal_open = False
        elif action in {"ok", "modal_ok"}:
            self.ok_clicks += 1
            if (
                action == "ok"
                and self.use_js_dialog
                and self.dialog_handler is not None
            ):
                handler = self.dialog_handler
                self.dialog_handler = None
                self.js_dialog = FakeDialog()
                handler(self.js_dialog)

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)
        self.clock["now"] += timedelta(milliseconds=milliseconds)
        if self.delayed_frame is not None:
            self.frames.append(self.delayed_frame)
            self.delayed_frame = None

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def once(self, event, handler):
        assert event == "dialog"
        self.dialog_handler = handler

    def remove_listener(self, event, handler):
        assert event == "dialog"
        self.removed_dialog_handlers.append(handler)
        if self.dialog_handler is handler:
            self.dialog_handler = None

    def emit_dialog(self):
        if self.dialog_handler is not None:
            handler = self.dialog_handler
            self.dialog_handler = None
            dialog = FakeDialog()
            handler(dialog)
            return dialog
        return FakeDialog()

    def expect_download(self, **_kwargs):
        self.expect_download_timeouts.append(_kwargs.get("timeout"))
        return FakeDownloadContext(self.download)

    def screenshot(self, *, path, **_kwargs):
        Path(path).write_bytes(b"png-sintetico")
        self.screenshots.append(Path(path))


@pytest.fixture
def fake_page(monkeypatch):
    clock = {"now": NOW}
    monkeypatch.setattr(browser, "_now", lambda: clock["now"])
    monkeypatch.setattr(browser, "_can_interrupt_download", lambda: True)
    monkeypatch.setattr(browser, "_save_as_deadline", lambda _deadline: nullcontext())
    page = FakePage()
    page.clock = clock
    return page


def test_download_preenche_hoje_ate_mais_dez_dias(fake_page, tmp_path):
    requested = []
    fake_page.on_period = lambda start, end: requested.append((start, end))

    result = download_report(
        fake_page,
        instance=INSTANCE,
        destination=tmp_path / "original.xlsx",
        period_start=START,
        period_end=END,
        deadline=DEADLINE,
        evidence_root=tmp_path / "evidence",
    )

    assert requested == [("27/07/2026", "06/08/2026")]
    assert result.name == "original.xlsx"
    assert result.read_bytes() == b"xlsx-sintetico"


def test_login_usa_ids_observados_e_preenche_credenciais(fake_page, tmp_path):
    fake_page.login_required = True

    download_report(
        fake_page,
        INSTANCE,
        tmp_path / "original.xlsx",
        START,
        END,
        DEADLINE,
        evidence_root=tmp_path / "evidence",
    )

    assert fake_page.filled["Usuário"] == INSTANCE.user
    assert fake_page.filled["Senha"] == INSTANCE.password
    assert fake_page.login_clicks == 1


def test_login_rejeitado_preserva_codigo_distinto(fake_page, tmp_path):
    fake_page.login_required = True
    fake_page.login_rejected = True

    with pytest.raises(CollectionError, match="AUTH_REJECTED"):
        download_report(
            fake_page,
            INSTANCE,
            tmp_path / "original.xlsx",
            START,
            END,
            DEADLINE,
            evidence_root=tmp_path / "evidence",
        )


def test_polling_exige_concluido_na_linha_do_periodo(fake_page, tmp_path):
    fake_page.statuses = [
        "Gerando Relatório",
        "Gerando Relatório",
        "Concluído",
    ]

    download_report(
        fake_page,
        INSTANCE,
        tmp_path / "original.xlsx",
        START,
        END,
        DEADLINE,
        evidence_root=tmp_path / "evidence",
    )

    assert fake_page.refresh_count == 3
    assert fake_page.waits == [500, 10_000, 500, 5_000, 500, 5_000, 500]


def test_polling_nao_aceita_status_que_apenas_contem_concluido(
    fake_page,
    tmp_path,
):
    fake_page.statuses = ["Não concluído", "Concluído"]

    download_report(
        fake_page,
        INSTANCE,
        tmp_path / "original.xlsx",
        START,
        END,
        DEADLINE,
        evidence_root=tmp_path / "evidence",
    )

    assert fake_page.refresh_count == 2


def test_timeout_nao_clica_em_excel(fake_page, tmp_path):
    fake_page.statuses = ["Gerando Relatório"]

    with pytest.raises(CollectionError, match="REPORT_TIMEOUT"):
        download_report(
            fake_page,
            INSTANCE,
            tmp_path / "original.xlsx",
            START,
            END,
            EXPIRED,
            evidence_root=tmp_path / "evidence",
        )

    assert fake_page.excel_clicked is False
    assert fake_page.waits == []


def test_deadline_expira_durante_polling_sem_ultrapassar_limite(
    fake_page,
    tmp_path,
):
    fake_page.statuses = ["Gerando Relatório"]
    deadline = NOW + timedelta(seconds=12)

    with pytest.raises(CollectionError, match="REPORT_TIMEOUT"):
        download_report(
            fake_page,
            INSTANCE,
            tmp_path / "original.xlsx",
            START,
            END,
            deadline,
            evidence_root=tmp_path / "evidence",
        )

    assert fake_page.waits == [500, 10_000, 500, 1_000]
    assert fake_page.clock["now"] == deadline
    assert fake_page.excel_clicked is False


def test_download_usa_linha_do_periodo_e_salva_destino(fake_page, tmp_path):
    destination = tmp_path / "nested" / "original.xlsx"

    result = download_report(
        fake_page,
        INSTANCE,
        destination,
        START,
        END,
        DEADLINE,
        evidence_root=tmp_path / "evidence",
    )

    assert result == destination
    assert fake_page.download_clicked is True


def test_exportacao_aguarda_controle_baixar(fake_page, tmp_path):
    fake_page.download_available_after = 3

    download_report(
        fake_page,
        INSTANCE,
        tmp_path / "original.xlsx",
        START,
        END,
        DEADLINE,
        evidence_root=tmp_path / "evidence",
    )

    assert fake_page.download_queries == 3
    assert fake_page.download_clicked is True


def test_deadline_expira_aguardando_baixar_sem_teto_paralelo(
    fake_page,
    tmp_path,
):
    fake_page.download_available_after = 10_000
    deadline = NOW + timedelta(seconds=12)

    with pytest.raises(CollectionError, match="DOM_MISSING"):
        download_report(
            fake_page,
            INSTANCE,
            tmp_path / "original.xlsx",
            START,
            END,
            deadline,
            evidence_root=tmp_path / "evidence",
        )

    assert fake_page.clock["now"] == deadline
    assert fake_page.expect_download_timeouts == []


def test_expect_download_recebe_apenas_tempo_restante(fake_page, tmp_path):
    deadline = NOW + timedelta(seconds=25)

    download_report(
        fake_page,
        INSTANCE,
        tmp_path / "original.xlsx",
        START,
        END,
        deadline,
        evidence_root=tmp_path / "evidence",
    )

    assert fake_page.expect_download_timeouts == [14_000]


class TimeoutOnceDownloadContext(FakeDownloadContext):
    def __init__(self, page, timeout):
        super().__init__(page.download)
        self.page = page
        self.timeout = timeout

    def __exit__(self, *_args):
        if self.page.download_attempts == 1:
            self.page.clock["now"] += timedelta(milliseconds=self.timeout)
            raise RuntimeError("evento de download nao emitido")
        return False


class TimeoutOnceDownloadPage(FakePage):
    def __init__(self):
        super().__init__()
        self.download_attempts = 0

    def expect_download(self, **kwargs):
        timeout = kwargs.get("timeout")
        self.expect_download_timeouts.append(timeout)
        self.download_attempts += 1
        return TimeoutOnceDownloadContext(self, timeout)


def test_exportacao_repete_clique_quando_primeiro_evento_nao_chega(
    fake_page,
    tmp_path,
):
    page = TimeoutOnceDownloadPage()
    page.clock = fake_page.clock
    destination = tmp_path / "original.xlsx"

    result = download_report(
        page,
        INSTANCE,
        destination,
        START,
        END,
        NOW + timedelta(seconds=60),
        evidence_root=tmp_path / "evidence",
    )

    assert result == destination
    assert destination.read_bytes() == b"xlsx-sintetico"
    assert page.actions.count("download") == 2
    assert page.expect_download_timeouts[0] == 30_000


class ClassDisabledDownloadLocator(FakeLocator):
    def get_attribute(self, name):
        if name == "class" and self.page.download_queries < 3:
            return "scButton_default disabled"
        if name == "aria-disabled":
            return "false"
        return super().get_attribute(name)


class ClassDisabledDownloadPage(FakePage):
    def get_by_title(self, name, **_kwargs):
        if name == "Baixar":
            self.download_queries += 1
            return ClassDisabledDownloadLocator(self, "download")
        return super().get_by_title(name, **_kwargs)

    def get_by_role(self, role, *, name, **kwargs):
        if name == "Baixar":
            return FakeLocator(self, count=0)
        return super().get_by_role(role, name=name, **kwargs)

    def get_by_text(self, text, **kwargs):
        if text == "Baixar":
            return FakeLocator(self, count=0)
        return super().get_by_text(text, **kwargs)


def test_baixar_aguarda_classe_disabled_sumir(fake_page):
    page = ClassDisabledDownloadPage()
    page.clock = fake_page.clock

    control = browser._wait_for_download_control(
        page,
        NOW + timedelta(seconds=1),
    )

    assert control.action == "download"
    assert page.download_queries == 3
    assert page.waits == [200, 200]

def test_save_as_expirado_cancela_download_e_retorna_timeout(fake_page, tmp_path):
    deadline = NOW + timedelta(seconds=15)

    class DownloadQueUltrapassaPrazo:
        def __init__(self):
            self.cancelled = False

        def save_as(self, target):
            Path(target).write_bytes(b"xlsx-incompleto")
            fake_page.clock["now"] = deadline

        def cancel(self):
            self.cancelled = True

    fake_page.download = DownloadQueUltrapassaPrazo()

    with pytest.raises(CollectionError, match="REPORT_TIMEOUT"):
        download_report(
            fake_page,
            INSTANCE,
            tmp_path / "original.xlsx",
            START,
            END,
            deadline,
            evidence_root=tmp_path / "evidence",
        )

    assert fake_page.download.cancelled is True


def test_save_as_interrompido_restaura_timer_e_handler_anterior(monkeypatch, tmp_path):
    previous_handler = object()

    class FakeSignal:
        SIGALRM = object()
        ITIMER_REAL = object()

        def __init__(self):
            self.handler = previous_handler
            self.timer = (9.0, 2.0)
            self.calls = []

        def getsignal(self, _signal):
            return self.handler

        def signal(self, _signal, handler):
            self.calls.append(("signal", handler))
            self.handler = handler

        def setitimer(self, _which, delay, interval=0.0):
            self.calls.append(("timer", delay, interval))
            previous = self.timer
            self.timer = (delay, interval)
            return previous

    class BlockingDownload:
        def __init__(self):
            self.cancelled = False

        def save_as(self, _target):
            fake_signal.handler(None, None)

        def cancel(self):
            self.cancelled = True

    fake_signal = FakeSignal()
    download = BlockingDownload()
    monkeypatch.setattr(browser, "signal", fake_signal)
    monkeypatch.setattr(browser, "_can_interrupt_download", lambda: True)
    monkeypatch.setattr(browser, "_now", lambda: NOW)

    with pytest.raises(CollectionError, match="REPORT_TIMEOUT"):
        browser._save_download_until(download, tmp_path / "original.xlsx", DEADLINE)

    assert download.cancelled is True
    assert fake_signal.handler is previous_handler
    assert fake_signal.timer[1] == 2.0


def test_save_as_nao_desarma_timer_anterior_vencido(monkeypatch, tmp_path):
    previous_handler = object()

    class FakeSignal:
        SIGALRM = object()
        ITIMER_REAL = object()

        def __init__(self):
            self.handler = previous_handler
            self.timer = (2.0, 0.0)
            self.calls = []

        def getsignal(self, _signal):
            return self.handler

        def signal(self, _signal, handler):
            self.calls.append(("signal", handler))
            self.handler = handler

        def setitimer(self, _which, delay, interval=0.0):
            self.calls.append(("timer", delay, interval))
            previous = self.timer
            self.timer = (delay, interval)
            return previous

    class DownloadCompleto:
        def save_as(self, target):
            Path(target).write_bytes(b"xlsx-sintetico")

    fake_signal = FakeSignal()
    ticks = iter((0.0, 3.0))
    monkeypatch.setattr(browser, "signal", fake_signal)
    monkeypatch.setattr(browser, "_can_interrupt_download", lambda: True)
    monkeypatch.setattr(browser, "_now", lambda: NOW)
    monkeypatch.setattr(browser.time, "monotonic", lambda: next(ticks))

    browser._save_download_until(
        DownloadCompleto(), tmp_path / "original.xlsx", DEADLINE
    )

    assert fake_signal.handler is previous_handler
    assert fake_signal.timer[0] > 0
    assert fake_signal.calls[-2:] == [
        ("signal", previous_handler),
        ("timer", fake_signal.timer[0], 0.0),
    ]


@pytest.mark.parametrize(
    ("platform", "main_thread"),
    [("nt", True), ("posix", False)],
)
def test_save_as_sem_deadline_forte_falha_antes_de_iniciar(
    monkeypatch, tmp_path, platform, main_thread
):
    calls = []
    current = object()
    main = current if main_thread else object()

    class Download:
        def save_as(self, _target):
            calls.append("save_as")

    monkeypatch.setattr(browser, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(
        browser,
        "signal",
        SimpleNamespace(SIGALRM=object(), setitimer=lambda *_args: None),
    )
    monkeypatch.setattr(
        browser,
        "threading",
        SimpleNamespace(
            current_thread=lambda: current,
            main_thread=lambda: main,
        ),
    )
    monkeypatch.setattr(browser, "_now", lambda: NOW)

    with pytest.raises(
        CollectionError, match="DOWNLOAD_DEADLINE_UNSUPPORTED"
    ):
        browser._save_download_until(
            Download(), tmp_path / "original.xlsx", DEADLINE
        )

    assert calls == []


def test_exportacao_respeita_ordem_sem_exigir_titulo_antes(
    fake_page,
    tmp_path,
):
    fake_page.texts.discard("Exportação XLS")

    download_report(
        fake_page,
        INSTANCE,
        tmp_path / "original.xlsx",
        START,
        END,
        DEADLINE,
        evidence_root=tmp_path / "evidence",
    )

    relevant = [
        action
        for action in fake_page.actions
        if action in {"open_report", "advanced", "excel", "download"}
    ]
    assert relevant == ["open_report", "advanced", "excel", "download"]


def test_exportacao_redescobre_frame_tardio_e_controles_sem_role(
    fake_page,
    tmp_path,
):
    fake_page.export_controls_by_text_only = True
    fake_page.texts.discard("Exportação XLS")
    fake_page.delayed_frame = FakeScope(
        fake_page,
        texts={"Avançado", "Excel"},
    )

    download_report(
        fake_page,
        INSTANCE,
        tmp_path / "original.xlsx",
        START,
        END,
        DEADLINE,
        evidence_root=tmp_path / "evidence",
    )

    assert fake_page.excel_clicked is True
    assert "advanced" in fake_page.actions


def test_scope_with_text_percorre_frames_recursivamente_sem_indice():
    page = FakePage()
    frame = FakeScope(page)
    nested = FakeScope(page, texts={"Alvo semântico"})
    frame.child_frames.append(nested)
    page.frames = [frame]

    assert scope_with_text(page, "Alvo semântico") is nested


def test_clique_de_navegacao_repete_uma_vez_antes_do_deadline(
    fake_page,
    tmp_path,
):
    fake_page.fail_once.add("menu")

    download_report(
        fake_page,
        INSTANCE,
        tmp_path / "original.xlsx",
        START,
        END,
        DEADLINE,
        evidence_root=tmp_path / "evidence",
    )

    assert "menu" in fake_page.failed


class LateMenuScope(FakeScope):
    def get_by_text(self, text, **_kwargs):
        return FakeLocator(self.page, f"text:{text}")


def test_navegacao_aguarda_menu_em_frame_tardio_ate_o_deadline(
    fake_page,
):
    fake_page.texts.clear()
    delayed_menu = LateMenuScope(fake_page)
    fake_page.delayed_frame = delayed_menu

    browser.navigate_to_custom_reports(
        fake_page,
        deadline=NOW + timedelta(seconds=1),
    )

    assert fake_page.waits == [200]
    assert fake_page.frames == [delayed_menu]
    assert len(fake_page.actions) == 4
    assert all(action.startswith("text:") for action in fake_page.actions[-3:])


def test_navegacao_aceita_relatorios_personalizado_singular(
    fake_page,
):
    fake_page.texts.discard("Relatórios Personalizados")
    fake_page.texts.add("Relatórios Personalizado")

    browser.navigate_to_custom_reports(fake_page, deadline=DEADLINE)

    assert "text:Relatórios Personalizado" in fake_page.actions


def test_preparo_e_fila_aceitam_relatorios_personalizado_singular(
    fake_page,
):
    fake_page.texts.discard("Relatórios Personalizados")
    fake_page.texts.add("Relatórios Personalizado")

    browser.prepare_custom_reports(fake_page, INSTANCE, DEADLINE)
    browser.queue_report(fake_page, START, END, DEADLINE)

    assert fake_page.selected_report == REPORT_NAME
    assert fake_page.filled["Início"] == "27/07/2026"
    assert fake_page.filled["Fim"] == "06/08/2026"
    assert 500 in fake_page.waits


class SidebarWithoutGenerateScope(FakeScope):
    def get_by_role(self, role, *, name, **kwargs):
        if role in {"button", "link"} and name == "Gerar":
            return FakeLocator(self.page, count=0)
        return super().get_by_role(role, name=name, **kwargs)


def test_fila_aguarda_painel_tardio_com_gerar_visivel(
    fake_page,
):
    fake_page.texts.discard("Relatórios Personalizados")
    fake_page.texts.add("Relatórios Personalizado")
    sidebar = SidebarWithoutGenerateScope(fake_page)
    fake_page.get_by_role = sidebar.get_by_role
    content = FakeScope(
        fake_page,
        texts={browser.CUSTOM_REPORTS_NAMES[0]},
    )
    fake_page.delayed_frame = content

    browser.prepare_custom_reports(
        fake_page,
        INSTANCE,
        NOW + timedelta(seconds=1),
    )
    browser.queue_report(
        fake_page,
        START,
        END,
        NOW + timedelta(seconds=1),
    )

    assert fake_page.waits == [200, 500]
    assert fake_page.frames == [content]
    assert "generate" in fake_page.actions
    assert fake_page.selected_report == REPORT_NAME


class OutsideGenerateScope(FakeScope):
    def get_by_role(self, role, *, name, **_kwargs):
        if role == "button" and name == "Gerar":
            return FakeLocator(self.page, "outside_generate")
        return FakeLocator(self.page, count=0)

    def get_by_label(self, _name, **_kwargs):
        return FakeLocator(self.page, count=0)


class LinkGeneratePanel(FakeScope):
    def get_by_role(self, role, *, name, **kwargs):
        if role == "button" and name == "Gerar":
            return FakeLocator(self.page, count=0)
        if role == "link" and name == "Gerar":
            return FakeLocator(self.page, "generate")
        return super().get_by_role(role, name=name, **kwargs)

    def locator(self, selector):
        if selector == browser.REPORT_SELECT:
            return FakeLocator(self.page, "report")
        if selector == "#sub_form_b":
            return FakeLocator(self.page, "confirm_data")
        return super().locator(selector)


class ListLinkGeneratePanel(FakeScope):
    def get_by_role(self, role, *, name, **kwargs):
        if role == "heading" and name in browser.CUSTOM_REPORTS_NAMES:
            return FakeLocator(self.page, "reports_heading")
        if role == "button" and name == "Gerar":
            return FakeLocator(self.page, count=0)
        if role == "link" and name == "Gerar":
            return FakeLocator(self.page, "generate")
        return super().get_by_role(role, name=name, **kwargs)

    def get_by_label(self, _name, **_kwargs):
        return FakeLocator(self.page, count=0)


def test_fila_usa_link_gerar_apenas_no_painel_do_relatorio(fake_page):
    outside = OutsideGenerateScope(fake_page)
    fake_page.get_by_role = outside.get_by_role
    fake_page.get_by_label = outside.get_by_label
    form = LinkGeneratePanel(fake_page)
    content = ListLinkGeneratePanel(fake_page)
    fake_page.delayed_frame = content
    stages = []
    original_click = fake_page.click

    def reveal_form_after_generate(action):
        original_click(action)
        if action == "generate":
            fake_page.frames.append(form)

    fake_page.click = reveal_form_after_generate

    browser.queue_report(
        fake_page,
        START,
        END,
        NOW + timedelta(seconds=1),
        stage_callback=stages.append,
    )

    assert "outside_generate" not in fake_page.actions
    assert "generate" in fake_page.actions
    assert stages[:4] == [
        "queue_panel_found",
        "queue_generate_found",
        "queue_generate_clicked",
        "queue_controls_ready",
    ]


class SelectorGeneratePanel(ListLinkGeneratePanel):
    def get_by_role(self, role, *, name, **kwargs):
        if name == "Gerar":
            return FakeLocator(self.page, count=0)
        return super().get_by_role(role, name=name, **kwargs)

    def locator(self, selector):
        if 'input[type="submit"][value="Gerar"]' in selector:
            return FakeLocator(self.page, "generate")
        return super().locator(selector)


class VisualGenerateTextCandidate(FakeLocator):
    def __init__(self, page, clickable_action=None):
        super().__init__(page, "visual_generate_text")
        self.clickable_action = clickable_action

    def locator(self, selector):
        if "ancestor-or-self" in selector and "@onclick" in selector:
            return FakeLocator(
                self.page,
                self.clickable_action,
                count=int(self.clickable_action is not None),
            )
        return super().locator(selector)


class VisualGenerateTextCandidates(FakeLocator):
    def __init__(self, page):
        super().__init__(page, count=2)

    def nth(self, index):
        return VisualGenerateTextCandidate(
            self.page,
            None if index == 0 else "generate",
        )


class BoundedVisualClickables(FakeLocator):
    def __init__(self, page):
        super().__init__(
            page,
            rows=[
                FakeLocator(
                    page,
                    "wrong_generate",
                    text="Gerar novamente",
                ),
                FakeLocator(page, "generate", text="  Gerar  "),
            ],
        )

    def filter(self, *, has_text):
        return FakeLocator(
            self.page,
            rows=[
                candidate
                for candidate in self._rows
                if has_text.fullmatch(candidate._text)
            ],
        )


class EmptyBoundedClickables(FakeLocator):
    def __init__(self, page):
        super().__init__(page, count=0)

    def filter(self, *, has_text):
        return self


class VisualTextGeneratePanel(ListLinkGeneratePanel):
    def get_by_role(self, role, *, name, **kwargs):
        if name == "Gerar":
            return FakeLocator(self.page, count=0)
        return super().get_by_role(role, name=name, **kwargs)

    def get_by_text(self, text, **kwargs):
        if text == "Gerar" and kwargs.get("exact") is True:
            return VisualGenerateTextCandidates(self.page)
        return super().get_by_text(text, **kwargs)

    def locator(self, selector):
        if selector == "a, button, input, [onclick]":
            return BoundedVisualClickables(self.page)
        if selector == browser.GENERATE_CONTROL_SELECTOR:
            return FakeLocator(self.page, count=0)
        return super().locator(selector)



def test_seletor_gerar_inclui_controle_real_scriptcase():
    assert "#sc_Novo_top" in browser.GENERATE_CONTROL_SELECTOR


class EmptyGeneratePanel(FakeScope):
    def get_by_role(self, role, *, name, **kwargs):
        if name == "Gerar":
            return FakeLocator(self.page, count=0)
        return super().get_by_role(role, name=name, **kwargs)

    def locator(self, selector):
        if selector == "a, button, input, [onclick]":
            return EmptyBoundedClickables(self.page)
        return FakeLocator(self.page, count=0)


class ScriptcaseToolbarScope(FakeScope):
    def get_by_role(self, role, *, name, **kwargs):
        if name == "Gerar":
            return FakeLocator(self.page, count=0)
        return super().get_by_role(role, name=name, **kwargs)

    def locator(self, selector):
        if selector == browser.SCRIPTCASE_GENERATE_SELECTOR:
            return FakeLocator(self.page, "generate")
        return super().locator(selector)


def test_gerar_busca_id_scriptcase_fora_do_grid(fake_page):
    panel = EmptyGeneratePanel(fake_page)
    fake_page.frames.append(ScriptcaseToolbarScope(fake_page))

    generate = browser._generate_in_panel(fake_page, panel, DEADLINE)
    generate.click()

    assert fake_page.actions == ["generate"]
def test_fila_sobe_texto_gerar_ao_clicavel_no_painel(fake_page):
    content = VisualTextGeneratePanel(fake_page)
    form = LinkGeneratePanel(fake_page)
    outside = OutsideGenerateScope(fake_page)
    fake_page.get_by_role = outside.get_by_role
    fake_page.get_by_label = outside.get_by_label
    fake_page.frames.append(content)
    stages = []
    original_click = fake_page.click

    def reveal_form_after_generate(action):
        original_click(action)
        if action == "generate":
            fake_page.frames.append(form)

    fake_page.click = reveal_form_after_generate

    browser.queue_report(
        fake_page,
        START,
        END,
        DEADLINE,
        stage_callback=stages.append,
    )

    assert fake_page.actions[0] == "generate"
    assert "outside_generate" not in fake_page.actions
    assert "visual_generate_text" not in fake_page.actions
    assert stages[:4] == [
        "queue_panel_found",
        "queue_generate_found",
        "queue_generate_clicked",
        "queue_controls_ready",
    ]


class EscapingVisualTextGeneratePanel(VisualTextGeneratePanel):
    def get_by_text(self, text, **kwargs):
        if text == "Gerar" and kwargs.get("exact") is True:
            return VisualGenerateTextCandidate(
                self.page,
                "outside_generate",
            )
        return super().get_by_text(text, **kwargs)

    def locator(self, selector):
        if selector == "a, button, input, [onclick]":
            return EmptyBoundedClickables(self.page)
        return super().locator(selector)


def test_fila_nao_sobe_texto_gerar_para_fora_do_painel(fake_page):
    content = EscapingVisualTextGeneratePanel(fake_page)
    outside = OutsideGenerateScope(fake_page)
    fake_page.get_by_role = outside.get_by_role
    fake_page.get_by_label = outside.get_by_label
    fake_page.frames.append(content)

    with pytest.raises(CollectionError, match="DOM_MISSING"):
        browser.queue_report(
            fake_page,
            START,
            END,
            NOW + timedelta(seconds=1),
        )

    assert "outside_generate" not in fake_page.actions
    assert "generate" not in fake_page.actions


def test_fila_aceita_fallback_exato_scriptcase_para_gerar(fake_page):
    content = SelectorGeneratePanel(fake_page)
    form = LinkGeneratePanel(fake_page)
    outside = OutsideGenerateScope(fake_page)
    fake_page.get_by_role = outside.get_by_role
    fake_page.get_by_label = outside.get_by_label
    fake_page.frames.append(content)
    stages = []
    original_click = fake_page.click

    def reveal_form_after_generate(action):
        original_click(action)
        if action == "generate":
            fake_page.frames.append(form)

    fake_page.click = reveal_form_after_generate

    browser.queue_report(
        fake_page,
        START,
        END,
        DEADLINE,
        stage_callback=stages.append,
    )

    assert fake_page.actions[0] == "generate"
    assert stages[:4] == [
        "queue_panel_found",
        "queue_generate_found",
        "queue_generate_clicked",
        "queue_controls_ready",
    ]


class RealReportControlsScope(FakeScope):
    def __init__(self, page):
        super().__init__(
            page,
            texts={browser.CUSTOM_REPORTS_NAMES[0]},
        )
        self.queried_selectors = []

    def get_by_label(self, _name, **_kwargs):
        return FakeLocator(self.page, count=0)

    def get_by_role(self, role, *, name, **kwargs):
        label = name.pattern if isinstance(name, re.Pattern) else name
        if role == "button" and label == "^ok$":
            return FakeLocator(self.page, count=0)
        return super().get_by_role(role, name=name, **kwargs)

    def locator(self, selector):
        self.queried_selectors.append(selector)
        controls = {
            '#id_sc_field_relatorio, select[name="relatorio"]': "report",
            (
                '#id_sc_field_filtro_data1, '
                'input[name="filtro_data1"]'
            ): "Início",
            (
                '#id_sc_field_filtro_data2, '
                'input[name="filtro_data2"]'
            ): "Fim",
            "#sub_form_b": "confirm_data",
        }
        if selector in controls:
            return FakeLocator(self.page, controls[selector])
        return super().locator(selector)


def test_fila_usa_ids_reais_quando_controles_nao_tem_labels(
    fake_page,
):
    content = RealReportControlsScope(fake_page)
    fake_page.get_by_label = content.get_by_label
    fake_page.get_by_role = content.get_by_role
    fake_page.locator = content.locator

    browser.queue_report(fake_page, START, END, DEADLINE)

    assert fake_page.selected_report == REPORT_NAME
    assert fake_page.filled == {
        "Início": "27/07/2026",
        "Fim": "06/08/2026",
    }
    assert (
        '#id_sc_field_relatorio, select[name="relatorio"]'
        in content.queried_selectors
    )
    assert (
        '#id_sc_field_filtro_data1, input[name="filtro_data1"]'
        in content.queried_selectors
    )
    assert (
        '#id_sc_field_filtro_data2, input[name="filtro_data2"]'
        in content.queried_selectors
    )
    assert "#sub_form_b" in content.queried_selectors
    assert "confirm_data" in fake_page.actions


class Select2TextboxLocator(FakeLocator):
    def select_option(self, **_kwargs):
        self.page.widget_select_attempts += 1
        raise RuntimeError("textbox Select2 não é um select nativo")


class NativeSelectLocator(FakeLocator):
    def select_option(self, *, label, **kwargs):
        self.page.native_select_force = kwargs.get("force")
        super().select_option(label=label, **kwargs)


class NativeSelectAlongsideWidgetScope(FakeScope):
    def get_by_label(self, name, **kwargs):
        if name == "Selecione":
            return Select2TextboxLocator(self.page, "select2_widget")
        return super().get_by_label(name, **kwargs)

    def locator(self, selector):
        if selector == browser.REPORT_SELECT:
            return NativeSelectLocator(self.page, "report")
        return super().locator(selector)


def test_fila_prioriza_select_nativo_quando_select2_tambem_existe(
    fake_page,
):
    content = NativeSelectAlongsideWidgetScope(fake_page)
    fake_page.widget_select_attempts = 0
    fake_page.native_select_force = None
    fake_page.get_by_label = content.get_by_label
    fake_page.locator = content.locator

    browser.queue_report(fake_page, START, END, DEADLINE)

    assert fake_page.selected_report == REPORT_NAME
    assert fake_page.widget_select_attempts == 0
    assert fake_page.native_select_force is True


class AsyncDialogOnlyPage(FakePage):
    def __init__(self):
        super().__init__()
        self.async_dialog = None

    def get_by_text(self, text, **kwargs):
        if text == browser.QUEUE_MESSAGE:
            return FakeLocator(self, count=0)
        return super().get_by_text(text, **kwargs)

    def emit_dialog(self):
        if self.dialog_handler is not None:
            handler = self.dialog_handler
            self.dialog_handler = None
            dialog = FakeDialog()
            dialog.message = (
                "Seu relatório foi enviado para fila, assim que "
                "processado vamos te notificar"
            )
            handler(dialog)
            return dialog
        return FakeDialog()

    def wait_for_timeout(self, milliseconds):
        super().wait_for_timeout(milliseconds)
        if self.dialog_handler is not None and self.async_dialog is None:
            self.async_dialog = self.emit_dialog()


def test_fila_aceita_dialogo_js_entregue_apos_clique(
    fake_page,
    caplog,
):
    page = AsyncDialogOnlyPage()
    page.clock = fake_page.clock
    caplog.set_level("INFO", logger="flows.financeiro_hoje.browser")

    browser.queue_report(
        page,
        START,
        END,
        NOW + timedelta(seconds=1),
    )

    assert page.async_dialog.accepted is True
    assert page.waits == [500, 200]
    assert "stage=queue_confirm_clicked" in caplog.text
    assert "stage=queue_confirmation_dialog" in caplog.text
    assert INSTANCE.user not in caplog.text
    assert INSTANCE.password not in caplog.text


class UnexpectedDialogPage(AsyncDialogOnlyPage):
    def emit_dialog(self):
        if self.dialog_handler is not None:
            handler = self.dialog_handler
            self.dialog_handler = None
            dialog = FakeDialog()
            dialog.message = "Erro ao validar filtros"
            handler(dialog)
            return dialog
        return FakeDialog()


def test_dialogo_inesperado_fecha_e_falha_sem_abrir_linha_historica(
    fake_page,
):
    page = UnexpectedDialogPage()
    page.clock = fake_page.clock

    with pytest.raises(CollectionError, match="UNEXPECTED_DIALOG"):
        browser.queue_report(
            page,
            START,
            END,
            NOW + timedelta(seconds=1),
        )

    assert page.async_dialog.accepted is True
    assert page.refresh_count == 0
    assert "open_report" not in page.actions
    assert page.download_clicked is False


class ImageRefreshPage(FakePage):
    def get_by_role(self, role, *, name, **kwargs):
        label = name.pattern if isinstance(name, re.Pattern) else name
        if (
            role == "img"
            and isinstance(name, re.Pattern)
            and "Atual" in label
        ):
            return FakeLocator(self, "refresh")
        if role == "button" and isinstance(name, re.Pattern):
            return FakeLocator(self, count=0)
        return super().get_by_role(role, name=name, **kwargs)

    def get_by_text(self, _text, **_kwargs):
        return FakeLocator(self, count=0)


def test_recarga_aceita_imagem_com_nome_acessivel(fake_page):
    page = ImageRefreshPage()
    page.clock = fake_page.clock

    browser._refresh_grid(page, DEADLINE)

    assert page.refresh_count == 1
    assert page.actions == ["refresh"]


def test_recarga_inclui_id_real_da_imagem_scriptcase():
    assert "#sc_Refresh_top" in browser.REFRESH_CONTROL_SELECTOR


class TitleRefreshPage(ImageRefreshPage):
    def get_by_role(self, _role, *, name, **_kwargs):
        return FakeLocator(self, count=0)

    def locator(self, selector):
        if selector == browser.REFRESH_CONTROL_SELECTOR:
            return FakeLocator(self, "refresh")
        return super().locator(selector)


def test_recarga_aceita_link_com_title_e_registra_etapas(fake_page):
    page = TitleRefreshPage()
    page.clock = fake_page.clock
    stages = []

    browser._refresh_grid(
        page,
        DEADLINE,
        stage_callback=stages.append,
    )

    assert page.refresh_count == 1
    assert page.actions == ["refresh"]
    assert stages == [
        "queue_refresh_found",
        "queue_refresh_clicked",
    ]


class RefreshNameCollisionPage(ImageRefreshPage):
    def get_by_role(self, role, *, name, **_kwargs):
        if (
            role == "button"
            and isinstance(name, re.Pattern)
            and name.fullmatch("Atualizar perfil")
        ):
            return FakeLocator(self, "profile_update")
        if (
            role == "link"
            and isinstance(name, re.Pattern)
            and name.fullmatch("Atualiza/Recarrega dados")
        ):
            return FakeLocator(self, "refresh")
        return FakeLocator(self, count=0)

    def get_by_text(self, _text, **_kwargs):
        return FakeLocator(self, count=0)


def test_recarga_ignora_atualizar_perfil(fake_page):
    page = RefreshNameCollisionPage()
    page.clock = fake_page.clock

    browser._refresh_grid(page, DEADLINE)

    assert page.actions == ["refresh"]
    assert "profile_update" not in page.actions


class NoRefreshPage(FakePage):
    def get_by_role(self, role, *, name, **kwargs):
        if (
            isinstance(name, re.Pattern)
            and ("Atual" in name.pattern or "Recarreg" in name.pattern)
        ):
            return FakeLocator(self, count=0)
        return super().get_by_role(role, name=name, **kwargs)

    def get_by_text(self, text, **kwargs):
        if isinstance(text, re.Pattern):
            return FakeLocator(self, count=0)
        return super().get_by_text(text, **kwargs)

    def locator(self, selector):
        if selector == browser.REFRESH_CONTROL_SELECTOR:
            return FakeLocator(self, count=0)
        return super().locator(selector)


def test_falha_persiste_ultimo_estagio_sem_segredos(
    fake_page,
    tmp_path,
):
    page = NoRefreshPage()
    page.clock = fake_page.clock
    evidence = tmp_path / "evidence"

    with pytest.raises(CollectionError, match="DOM_MISSING"):
        download_report(
            page,
            INSTANCE,
            tmp_path / "original.xlsx",
            START,
            END,
            NOW + timedelta(seconds=11),
            evidence_root=evidence,
        )

    payload = json.loads(
        (evidence / "stage.json").read_text(encoding="utf-8")
    )
    assert payload["schema_version"] == 1
    assert payload["stage"] == "grid_wait_started"
    serialized = json.dumps(payload)
    assert INSTANCE.user not in serialized
    assert INSTANCE.password not in serialized


def test_stage_temporario_nasce_com_modo_0600(
    tmp_path,
    monkeypatch,
):
    requested_modes = []
    original_open = os.open

    def record_open(path, flags, mode=0o777, **kwargs):
        requested_modes.append(mode)
        return original_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(os, "open", record_open)

    browser._stage_callback_for(tmp_path)("grid_wait_started")

    assert requested_modes == [0o600]


class IsoGridScope(FakeScope):
    def __init__(self, page):
        super().__init__(page)
        self.row = FakeLocator(
            page,
            text=(
                f"{REPORT_NAME} Inicio: 2026-07-27 "
                "Fim: 2026-08-06 Concluído"
            ),
        )

    def locator(self, selector):
        if selector == "tr":
            return FakeLocator(self.page, rows=[self.row])
        return super().locator(selector)


def test_linha_do_periodo_aceita_datas_iso(fake_page):
    grid = IsoGridScope(fake_page)
    fake_page.locator = grid.locator

    row, text = browser._row_for_period(
        fake_page,
        START,
        END,
        DEADLINE,
    )

    assert row is grid.row
    assert "inicio: 2026-07-27" in text
    assert "fim: 2026-08-06" in text


class HrefOnlyReportRow:
    def __init__(self, page):
        self.page = page

    def locator(self, selector):
        if selector == 'a[href*="cons_relatorio_dinamico"]':
            return FakeLocator(self.page, "open_report")
        return FakeLocator(self.page, count=0)


def test_abertura_aceita_link_real_sem_title(fake_page):
    browser._open_report(HrefOnlyReportRow(fake_page), DEADLINE)

    assert fake_page.actions == ["open_report"]


class VisibilityLocator(FakeLocator):
    def __init__(self, page, action, *, visible):
        super().__init__(page, action)
        self.visible = visible

    def is_visible(self):
        return self.visible

    def click(self, **_kwargs):
        if not self.visible:
            raise RuntimeError("elemento oculto")
        super().click(**_kwargs)


class VisibilityCollection:
    def __init__(self, locators):
        self.locators = locators

    @property
    def first(self):
        return self.locators[0]

    def count(self):
        return len(self.locators)

    def nth(self, index):
        return self.locators[index]


class HiddenThenVisibleMenuScope(FakeScope):
    def get_by_text(self, text, **_kwargs):
        if text in {"Empresa", "Relatórios", "Relatórios Personalizado"}:
            return VisibilityCollection(
                [
                    VisibilityLocator(
                        self.page,
                        f"hidden:{text}",
                        visible=False,
                    ),
                    VisibilityLocator(
                        self.page,
                        f"visible:{text}",
                        visible=True,
                    ),
                ]
            )
        return super().get_by_text(text, **_kwargs)


def test_navegacao_escolhe_item_visivel_quando_primeiro_esta_oculto(
    fake_page,
):
    fake_page.texts.discard("Relatórios Personalizados")
    fake_page.texts.add("Relatórios Personalizado")
    menu_scope = HiddenThenVisibleMenuScope(fake_page)
    fake_page.get_by_text = menu_scope.get_by_text

    browser.navigate_to_custom_reports(fake_page, deadline=DEADLINE)

    assert "visible:Empresa" in fake_page.actions
    assert "visible:Relatórios" in fake_page.actions
    assert "visible:Relatórios Personalizado" in fake_page.actions
    assert not any(action.startswith("hidden:") for action in fake_page.actions)


def test_preparo_fecha_modal_pos_login_visivel_antes_do_menu(fake_page):
    fake_page.login_required = True
    fake_page.post_login_modal_open = True

    browser.prepare_custom_reports(fake_page, INSTANCE, DEADLINE)

    close_index = fake_page.actions.index("close_post_login_modal")
    menu_index = fake_page.actions.index("menu")
    assert close_index < menu_index
    assert fake_page.post_login_modal_open is False


class ModalConfirmationLocator:
    def __init__(self, page, action, count=1):
        self.page = page
        self.action = action
        self._count = count

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    def count(self):
        return self._count

    def click(self, **_kwargs):
        self.page.confirmation_clicks.append(self.action)

    def locator(self, selector):
        if self.action == "message" and "ancestor-or-self" in selector:
            return ModalConfirmationLocator(self.page, "modal")
        return ModalConfirmationLocator(self.page, "missing", count=0)

    def get_by_role(self, role, *, name, **_kwargs):
        label = name.pattern if isinstance(name, re.Pattern) else name
        if self.action == "modal" and role == "button" and label == "^ok$":
            return ModalConfirmationLocator(self.page, "modal_ok")
        return ModalConfirmationLocator(self.page, "missing", count=0)

    def get_by_text(self, text, **_kwargs):
        if self.action == "modal" and text == "OK":
            return ModalConfirmationLocator(self.page, "modal_ok")
        return ModalConfirmationLocator(self.page, "missing", count=0)


class ModalBeforeFormPage:
    frames = []

    def __init__(self):
        self.confirmation_clicks = []

    def get_by_text(self, text, **_kwargs):
        if text == browser.QUEUE_MESSAGE:
            return ModalConfirmationLocator(self, "message")
        return ModalConfirmationLocator(self, "missing", count=0)

    def get_by_role(self, role, *, name, **_kwargs):
        label = name.pattern if isinstance(name, re.Pattern) else name
        if role == "button" and label == "^ok$":
            # This models a modal that precedes the form in DOM order: the old
            # global `.last` strategy selects the form confirmation instead.
            return ModalConfirmationLocator(self, "form_ok")
        return ModalConfirmationLocator(self, "missing", count=0)


def test_confirmacao_html_encontra_ok_no_modal_antes_do_formulario():
    page = ModalBeforeFormPage()

    browser.accept_dialog_or_modal(page, browser.QUEUE_MESSAGE)

    assert page.confirmation_clicks == ["modal_ok"]


def test_confirmacao_in_page_clica_ok_do_modal(fake_page, tmp_path):
    download_report(
        fake_page,
        INSTANCE,
        tmp_path / "original.xlsx",
        START,
        END,
        DEADLINE,
        evidence_root=tmp_path / "evidence",
    )

    assert fake_page.ok_clicks == 2
    assert len(fake_page.removed_dialog_handlers) == 1
    future_dialog = fake_page.emit_dialog()
    assert future_dialog.accepted is False


def test_confirmacao_por_dialogo_js_aceita_sem_clicar_modal(
    fake_page,
    tmp_path,
):
    fake_page.use_js_dialog = True

    download_report(
        fake_page,
        INSTANCE,
        tmp_path / "original.xlsx",
        START,
        END,
        DEADLINE,
        evidence_root=tmp_path / "evidence",
    )

    assert fake_page.js_dialog.accepted is True
    assert fake_page.ok_clicks == 1
    assert len(fake_page.removed_dialog_handlers) == 1


@pytest.mark.parametrize(
    ("marker", "code"),
    [
        ("Credenciais inválidas", "AUTH_REJECTED"),
        ("CAPTCHA", "CAPTCHA_DETECTED"),
        ("Perfil incorreto", "PROFILE_MISMATCH"),
    ],
)
def test_bloqueios_recebem_codigos_distintos(
    fake_page,
    tmp_path,
    marker,
    code,
):
    fake_page.texts.add(marker)

    with pytest.raises(CollectionError, match=code):
        download_report(
            fake_page,
            INSTANCE,
            tmp_path / "original.xlsx",
            START,
            END,
            DEADLINE,
            evidence_root=tmp_path / "evidence",
        )


def test_dom_ausente_gera_codigo_distinto_e_screenshot_0600(
    fake_page,
    tmp_path,
    monkeypatch,
):
    fake_page.texts.discard("Relatórios Personalizados")
    evidence = tmp_path / "evidence" / "20260727T082000-03a1" / "loga"
    requested_modes = []
    original_chmod = Path.chmod

    def record_chmod(path, mode):
        requested_modes.append(mode)
        return original_chmod(path, mode)

    monkeypatch.setattr(Path, "chmod", record_chmod)

    with pytest.raises(CollectionError, match="DOM_MISSING"):
        download_report(
            fake_page,
            INSTANCE,
            tmp_path / "original.xlsx",
            START,
            END,
            DEADLINE,
            evidence_root=evidence,
        )

    screenshot = fake_page.screenshots[0]
    assert screenshot.parent == evidence
    assert screenshot.is_file()
    assert requested_modes
    assert set(requested_modes) == {0o600}


def test_probe_remove_query_valores_e_texto_de_linha():
    html = """
    <input id="usuario" name="usuario" value="SEGREDO_NAO_EXPORTAR">
    <table aria-label="Resultados"><tr><td>DADO_OPERACIONAL</td>
      <td><button title="Visualizar relatório personalizado"></button></td>
    </tr></table>
    <button aria-label="Gerar">Gerar</button>
    """

    result = snapshot_html(
        html,
        source_url="https://example.invalid/app?token=SEGREDO_QUERY",
    )
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["url"] == "https://example.invalid/app"
    assert "SEGREDO_NAO_EXPORTAR" not in serialized
    assert "SEGREDO_QUERY" not in serialized
    assert "DADO_OPERACIONAL" not in serialized
    assert "Visualizar relatório personalizado" in serialized


def test_probe_omite_texto_e_valor_de_textarea_e_role_row():
    html = """
    <textarea name="observacao">SEGREDO_TEXTAREA</textarea>
    <div role="row">SEGREDO_ROLE_ROW
      <button title="Visualizar relatório personalizado"></button>
    </div>
    """

    serialized = json.dumps(snapshot_html(html), ensure_ascii=False)

    assert "SEGREDO_TEXTAREA" not in serialized
    assert "SEGREDO_ROLE_ROW" not in serialized
    assert '"value"' not in serialized
    assert "Visualizar relatório personalizado" in serialized


class ProbeElement:
    def __init__(self, raw=None, error=None):
        self.raw = raw
        self.error = error

    def evaluate(self, _script):
        if self.error:
            raise self.error
        return self.raw


class ProbeElements:
    def __init__(self, elements, count_error=None):
        self.elements = elements
        self.count_error = count_error

    def count(self):
        if self.count_error:
            raise self.count_error
        return len(self.elements)

    def nth(self, index):
        return self.elements[index]


class ProbePage:
    url = "https://example.invalid/app?secret=query"
    frames = []

    def __init__(self, elements):
        self.elements = elements

    def locator(self, _selector):
        return self.elements


def test_snapshot_real_ignora_elemento_ruim_e_sanitiza_restantes():
    page = ProbePage(
        ProbeElements(
            [
                ProbeElement(error=RuntimeError("elemento removido")),
                ProbeElement(
                    raw={
                        "tag": "textarea",
                        "name": "observacao",
                        "value": "SEGREDO_VALUE",
                        "text": "SEGREDO_TEXTAREA",
                    }
                ),
                ProbeElement(
                    raw={
                        "tag": "div",
                        "role": "row",
                        "text": "SEGREDO_ROLE_ROW",
                    }
                ),
            ]
        )
    )

    serialized = json.dumps(snapshot_page(page), ensure_ascii=False)

    assert "SEGREDO_TEXTAREA" not in serialized
    assert "SEGREDO_VALUE" not in serialized
    assert "SEGREDO_ROLE_ROW" not in serialized
    assert '"name": "observacao"' in serialized


def test_snapshot_real_falha_claro_se_scope_inteiro_ilegivel():
    page = ProbePage(
        ProbeElements([], count_error=RuntimeError("documento destruído"))
    )

    with pytest.raises(CollectionError, match="PROBE_DOM_UNREADABLE"):
        snapshot_page(page)


def test_snapshot_real_falha_se_nenhum_elemento_pode_ser_lido():
    page = ProbePage(
        ProbeElements(
            [ProbeElement(error=RuntimeError("elemento sempre removido"))]
        )
    )

    with pytest.raises(CollectionError, match="PROBE_DOM_UNREADABLE"):
        snapshot_page(page)


def test_probe_live_usa_chrome_e_argumentos_do_container():
    assert _browser_launch_options() == {
        "headless": True,
        "channel": "chrome",
        "args": ["--no-sandbox", "--disable-dev-shm-usage"],
    }


def test_sonda_gerar_sanitiza_campos_e_ancestrais():
    raw = {
        "tag": "input",
        "id": "sc_b_gerar_top",
        "name": "",
        "type": "image",
        "role": "",
        "title": "",
        "aria-label": "",
        "value": "",
        "alt": "Gerar relatorio",
        "src": "https://router.invalid/img/btn_gerar.png?token=SEGREDO_QUERY#frag",
        "matched_attributes": ["id", "class", "src", "onclick"],
        "has_onclick": True,
        "text": "",
        "classes": ["btn", "toolbar", "extra", "quarta", "quinta", "sexta", "setima"],
        "visible": True,
        "ancestors": [
            {
                "tag": "a",
                "id": "wrapper",
                "role": "button",
                "classes": ["scButton", "usuario-secreto"],
            }
        ],
        "innerHTML": "SEGREDO",
        "row_text": "DADO_OPERACIONAL",
    }

    result = dom_probe._sanitize_generate_element(raw)
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["alt"] == "Gerar relatorio"
    assert result["src"] == "btn_gerar.png"
    assert result["matched_attributes"] == ["id", "class", "src", "onclick"]
    assert result["has_onclick"] is True
    assert result["visible"] is True
    assert len(result["classes"]) == 6
    assert "innerHTML" not in result
    assert "SEGREDO" not in serialized
    assert "SEGREDO_QUERY" not in serialized
    assert "DADO_OPERACIONAL" not in serialized


def test_sonda_gerar_busca_atributos_estruturais_sem_expor_onclick():
    selector = dom_probe.GENERATE_PROBE_SELECTOR.casefold()

    assert '[alt*="gerar" i]' in selector
    assert '[id*="gerar" i]' in selector
    assert '[name*="gerar" i]' in selector
    assert '[class*="gerar" i]' in selector
    assert '[src*="gerar" i]' in selector
    assert '[onclick*="gerar" i]' in selector


def test_sonda_gerar_inventario_limita_controles_e_remove_dados_sensiveis():
    selector = dom_probe.GENERATE_INVENTORY_SELECTOR.casefold()
    assert "button" in selector
    assert "input" in selector
    assert '[role="button"]' in selector
    assert "[onclick]" in selector

    raw = {
        "tag": "a",
        "id": "sc_b_new_t",
        "name": "nm_botao",
        "type": "",
        "role": "button",
        "title": "Novo",
        "aria-label": "",
        "alt": "",
        "src": "/img/sc_btn_new.png?token=SEGREDO_QUERY",
        "href": "https://router.invalid/app/form.php?session=SEGREDO_HREF",
        "classes": ["scButton_default", "css_toolbar_obj"],
        "visible": True,
        "onclick": "executar('SEGREDO_JS')",
        "value": "DADO_OPERACIONAL",
        "text": "DADO_OPERACIONAL",
    }

    result = dom_probe._sanitize_inventory_element(raw)
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["src"] == "sc_btn_new.png"
    assert result["href"] == "https://router.invalid/app/form.php"
    assert result["has_onclick"] is True
    assert all(
        secret not in serialized
        for secret in (
            "SEGREDO_QUERY",
            "SEGREDO_HREF",
            "SEGREDO_JS",
            "DADO_OPERACIONAL",
        )
    )


def test_sonda_gerar_inventario_nao_serializa_href_javascript():
    result = dom_probe._sanitize_inventory_element(
        {
            "tag": "a",
            "visible": True,
            "href": "javascript:executar('SEGREDO_JS')",
        }
    )
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["has_href"] is True
    assert "href" not in result
    assert "SEGREDO_JS" not in serialized


def test_sonda_gerar_estrutura_detecta_cursor_pointer_sem_texto():
    raw = {
        "ready_state": "complete",
        "total_elements": 128,
        "clickables": [
            {
                "tag": "span",
                "id": "sc_b_new_t",
                "classes": ["scButton_default"],
                "visible": True,
                "clickable_reasons": ["cursor", "class"],
                "text": "DADO_OPERACIONAL",
                "value": "DADO_OPERACIONAL",
            }
        ],
    }
    scope = ProbePage(ProbeElements([ProbeElement(raw=raw)]))

    result = dom_probe._snapshot_scope_structure(scope)
    serialized = json.dumps(result, ensure_ascii=False)

    assert result == {
        "ready_state": "complete",
        "total_elements": 128,
        "clickables": [
            {
                "tag": "span",
                "visible": True,
                "id": "sc_b_new_t",
                "classes": ["scButton_default"],
                "clickable_reasons": ["cursor", "class"],
            }
        ],
    }
    assert "DADO_OPERACIONAL" not in serialized


def test_snapshot_gerar_inclui_inventario_interativo_sanitizado():
    raw = {
        "tag": "button",
        "id": "sc_b_new_t",
        "role": "button",
        "classes": ["scButton_default"],
        "visible": True,
        "onclick": "SEGREDO_JS",
        "text": "DADO_OPERACIONAL",
    }
    structure_raw = {
        "ready_state": "complete",
        "total_elements": 1,
        "clickables": [raw],
    }

    class InventoryPage:
        url = "https://router.invalid/app?token=SEGREDO_QUERY"
        frames = ()
        main_frame = None

        def locator(self, selector):
            if selector == dom_probe.GENERATE_PROBE_SELECTOR:
                return ProbeElements([])
            if selector == dom_probe.GENERATE_INVENTORY_SELECTOR:
                return ProbeElements([ProbeElement(raw=raw)])
            if selector == "body":
                return ProbeElements([ProbeElement(raw=structure_raw)])
            raise AssertionError(selector)

    result = dom_probe.snapshot_generate_page(InventoryPage())
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["frames"][0]["inventory"] == [
        {
            "tag": "button",
            "visible": True,
            "id": "sc_b_new_t",
            "role": "button",

            "has_onclick": True,
            "classes": ["scButton_default"],
        }
    ]
    assert result["frames"][0]["document"]["ready_state"] == "complete"
    assert result["frames"][0]["document"]["total_elements"] == 1
    assert result["frames"][0]["document"]["clickables"][0]["id"] == "sc_b_new_t"
    assert "SEGREDO" not in serialized
    assert "DADO_OPERACIONAL" not in serialized

def test_sonda_gerar_grava_json_0600_sem_query_ou_credenciais(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "evidence" / "probe" / "loga-gerar.json"
    payload = {
        "url": dom_probe.sanitize_url(
            "https://router.invalid/app?token=SEGREDO_QUERY"
        ),
        "frames": [],
    }

    modes = []
    original_open = os.open

    def record_open(path, flags, mode=0o777, **kwargs):
        modes.append(mode)
        return original_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(os, "open", record_open)

    dom_probe._write_private_json(target, payload)

    serialized = target.read_text(encoding="utf-8")
    assert "SEGREDO_QUERY" not in serialized
    assert modes == [0o600]


def test_sonda_gerar_aguarda_renderizacao_sem_ultrapassar_deadline(monkeypatch):
    waits = []

    class WaitingPage:
        def wait_for_timeout(self, milliseconds):
            waits.append(milliseconds)

    monkeypatch.setattr(dom_probe.time, "monotonic", lambda: 10.0)

    dom_probe._wait_for_probe_render(
        WaitingPage(),
        monotonic_deadline=12.0,
    )

    assert waits == [1999]


def test_sonda_gerar_expira_durante_count_pos_navegacao(
    monkeypatch,
):
    clock = {"now": 10.0}

    class SlowCandidates:
        def count(self):
            clock["now"] = 12.1
            return 1

    class SlowPage:
        url = "https://router.invalid/app"
        frames = ()
        main_frame = None

        def locator(self, _selector):
            return SlowCandidates()

    monkeypatch.setattr(dom_probe.time, "monotonic", lambda: clock["now"])

    with pytest.raises(CollectionError, match="PROBE_TIMEOUT"):
        dom_probe.snapshot_generate_page(
            SlowPage(),
            monotonic_deadline=12.0,
        )


def test_supervisor_mata_worker_com_cleanup_bloqueado_sem_timer_parent():
    timer_before = (
        signal.getitimer(signal.ITIMER_REAL)
        if hasattr(signal, "getitimer")
        else None
    )
    started = time.monotonic()

    code, _stdout, stderr = dom_probe._run_supervised_command(
        [
            sys.executable,
            "-c",
            (
                "import signal,time;"
                "signal.signal(signal.SIGTERM,"
                "lambda *_: time.sleep(60)) "
                "if hasattr(signal,'SIGTERM') else None;"
                "time.sleep(60)"
            ),
        ],
        timeout_seconds=0.1,
        grace_seconds=0.1,
    )

    assert code == 2
    assert stderr == "PROBE_TIMEOUT"
    assert time.monotonic() - started < 2
    if timer_before is not None:
        assert signal.getitimer(signal.ITIMER_REAL) == timer_before


def test_timeout_preserva_output_valido_preexistente_e_remove_apenas_tmp(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "loga-gerar.json"
    target.write_text('{"status": "anterior"}', encoding="utf-8")
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text('{"status": "parcial"}', encoding="utf-8")

    monkeypatch.setattr(
        dom_probe,
        "_run_supervised_command",
        lambda *_args, **_kwargs: (2, "", "PROBE_TIMEOUT"),
    )

    code = dom_probe.main(
        [
            "--company",
            "LOGA",
            "--step",
            "gerar",
            "--timeout-seconds",
            "60",
            "--output",
            str(target),
        ]
    )

    assert code == 2
    assert target.read_text(encoding="utf-8") == '{"status": "anterior"}'
    assert not temporary.exists()


def test_fixture_reproduz_apenas_contrato_sintetico():
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "financeiro_hoje_personalizados.html"
    )

    result = snapshot_html(fixture.read_text(encoding="utf-8"))
    serialized = json.dumps(result, ensure_ascii=False)

    for expected in (
        "Empresa",
        "Relatórios Personalizados",
        "Gerar",
        "Agendamento de atendimentos",
        "Início",
        "Fim",
        "Atualizar/Recarregar",
        "Visualizar relatório personalizado",
        "Avançado",
        "Excel",
        "Baixar",
    ):
        assert expected in serialized
    assert '"value"' not in serialized


def test_probe_cli_roda_a_partir_da_raiz_sem_expor_query():
    root = Path(__file__).parents[1]
    fixture = (
        root / "tests" / "fixtures" / "financeiro_hoje_personalizados.html"
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "financeiro_hoje_dom_probe.py"),
            "--company",
            "LOGA",
            "--step",
            "personalizados",
            "--html",
            str(fixture),
            "--source-url",
            "https://example.invalid/app?token=SEGREDO_QUERY",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "SEGREDO_QUERY" not in completed.stdout
