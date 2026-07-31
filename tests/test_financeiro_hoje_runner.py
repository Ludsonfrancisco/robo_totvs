from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import pytest
from openpyxl import Workbook

import flows.financeiro_hoje.runner as runner
import flows.financeiro_hoje.publication as publication
from flows.financeiro_hoje.browser import CollectionError
from flows.financeiro_hoje.config import Instance, Settings
from flows.financeiro_hoje.runner import (
    append_history,
    exclusive_run_lock,
    run_once as _run_once,
    write_done_atomic,
)


SCHEDULED = datetime(2026, 7, 28, 8, 20, tzinfo=timezone(timedelta(hours=-3)))


def run_once(*args, **kwargs):
    kwargs.setdefault("now", lambda: SCHEDULED)
    return _run_once(*args, **kwargs)


class Clock:
    def __init__(self, value):
        self.value = value

    def now(self):
        return self.value

    def advance(self, **delta):
        self.value += timedelta(**delta)


class FakeDownload:
    def __init__(self):
        self.failed: dict[str, str] = {}
        self.clock = None
        self.advance_by = None

    def fail_for(self, company, code):
        self.failed[company] = code

    def advance_clock(self, clock, **delta):
        self.clock = clock
        self.advance_by = delta

    def __call__(self, _page, instance, destination, _start, _end, deadline, **_kwargs):
        if self.clock is not None:
            self.clock.advance(**self.advance_by)
        if instance.name in self.failed:
            raise CollectionError(self.failed[instance.name])
        write_workbook(Path(destination))
        os.utime(destination, (deadline.timestamp() - 1, deadline.timestamp() - 1))
        return Path(destination)


@pytest.fixture
def settings(tmp_path):
    root = tmp_path / "financeiro_hoje"
    root.mkdir()
    for name in ("runs", "published", "evidence", "logs"):
        (root / name).mkdir()
    return Settings(
        root=root,
        schedule_enabled=False,
        timezone="America/Sao_Paulo",
        deadline_seconds=8 * 60,
        period_days=10,
        poll_seconds=5,
        instances=(
            Instance("LOGA", "https://loga.invalid", "user", "pass"),
            Instance("ACERTA", "https://acerta.invalid", "user", "pass"),
        ),
    )


@pytest.fixture
def fake_download():
    return FakeDownload()


@pytest.fixture
def clock():
    return Clock(SCHEDULED)


def test_falha_acerta_preserva_current_anterior(settings, fake_download):
    previous = seed_current(settings.root, "20260727T075000-a001")
    fake_download.fail_for("ACERTA", code="DOWNLOAD_TIMEOUT")

    result = run_once(settings=settings, scheduled_for=SCHEDULED, downloader=fake_download)

    assert result["success"] is False
    assert result["company"] == "ACERTA"
    assert (settings.root / "current.json").read_bytes() == previous
    assert result["alert_active"] is True
    assert result["code"] == "DOWNLOAD_TIMEOUT"


def test_falha_loga_nao_tenta_acerta_e_sanitiza_erro(settings, fake_download):
    fake_download.fail_for("LOGA", code="bad secret=senha")

    result = run_once(settings=settings, scheduled_for=SCHEDULED, downloader=fake_download)

    assert result["success"] is False
    assert result["company"] == "LOGA"
    assert result["code"] == "RUN_FAILED"
    assert "senha" not in result["message"]
    assert not (settings.root / "current.json").exists()


def test_sucesso_publica_as_duas_e_resolve_alerta(settings, fake_download):
    seed_failed_done(settings.root)

    result = run_once(settings=settings, scheduled_for=SCHEDULED, downloader=fake_download)

    manifest = json.loads(resolve_current(settings.root).read_text(encoding="utf-8"))
    assert result["success"] is True
    assert set(manifest["sources"]) == {"LOGA", "ACERTA"}
    assert manifest["period_start"] == "2026-07-28"
    assert manifest["period_end"] == "2026-08-07"
    assert result["alert_active"] is False
    assert result["recovered_at"] is not None
    assert re.fullmatch(r"20260728T082000-[A-Za-z0-9]+", result["run_id"])


def test_deadline_de_oito_minutos_impede_publicacao(settings, fake_download, clock):
    fake_download.advance_clock(clock, minutes=8, seconds=1)

    result = run_once(
        settings=settings,
        scheduled_for=SCHEDULED,
        downloader=fake_download,
        now=clock.now,
    )

    assert result["success"] is False
    assert result["stage"] == "deadline"
    assert not (settings.root / "current.json").exists()


def test_slot_recebido_apos_deadline_registra_falha_sem_iniciar_download(
    settings,
):
    now = SCHEDULED + timedelta(minutes=8, seconds=1)
    calls = []

    def forbidden_download(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("download nao deve iniciar")

    result = run_once(
        settings=settings,
        scheduled_for=SCHEDULED,
        downloader=forbidden_download,
        now=lambda: now,
    )

    assert result["success"] is False
    assert result["scheduled_for"] == SCHEDULED.isoformat()
    assert result["code"] == "DEADLINE_EXCEEDED"
    assert result["stage"] == "deadline"
    assert calls == []
    assert not (settings.root / "current.json").exists()
    persisted = json.loads((settings.root / "done.json").read_text(encoding="utf-8"))
    assert persisted["code"] == "DEADLINE_EXCEEDED"


def test_deadline_antes_do_current_interrompe_promocao(settings, fake_download, clock, monkeypatch):
    def publish_after_deadline(*_args, before_promote):
        clock.advance(minutes=8, seconds=1)
        before_promote()

    monkeypatch.setattr(runner, "publish", publish_after_deadline)

    result = run_once(
        settings=settings,
        scheduled_for=SCHEDULED,
        downloader=fake_download,
        now=clock.now,
    )

    assert result["stage"] == "deadline"
    assert not (settings.root / "current.json").exists()


def test_payload_marca_fim_apos_publicacao_e_proxima_execucao_futura(settings, fake_download, clock, monkeypatch):
    original_publish = runner.publish

    def publish_then_advance(*args, **kwargs):
        result = original_publish(*args, **kwargs)
        clock.advance(seconds=3)
        return result

    monkeypatch.setattr(runner, "publish", publish_then_advance)

    result = run_once(
        settings=settings,
        scheduled_for=SCHEDULED,
        downloader=fake_download,
        now=clock.now,
    )

    finished = datetime.fromisoformat(result["finished_at"])
    assert finished == SCHEDULED + timedelta(seconds=3)
    assert datetime.fromisoformat(result["next_scheduled_for"]) > finished


def test_falhas_pos_publicacao_nao_convertem_sucesso_em_retry(settings, fake_download, monkeypatch):
    monkeypatch.setattr(
        runner,
        "write_done_atomic",
        lambda *_args: (_ for _ in ()).throw(OSError("disk full secret")),
    )
    monkeypatch.setattr(
        runner,
        "append_history",
        lambda *_args: (_ for _ in ()).throw(OSError("history secret")),
    )
    monkeypatch.setattr(
        runner,
        "apply_retention",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("retention secret")),
    )

    result = run_once(settings=settings, scheduled_for=SCHEDULED, downloader=fake_download)

    assert result["success"] is True
    assert (settings.root / "current.json").exists()
    assert result["state_persisted"] is False
    assert result["state_error"] == "STATE_PERSIST_FAILED"
    assert result["housekeeping"] == "HOUSEKEEPING_FAILED"


def test_erro_de_durabilidade_apos_current_promovido_e_sucesso(settings, fake_download, monkeypatch):
    original_sync = publication._sync_directory

    def fail_root_sync(path):
        if Path(path) == settings.root:
            raise OSError("fsync after current")
        return original_sync(path)

    monkeypatch.setattr(publication, "_sync_directory", fail_root_sync)

    result = run_once(settings=settings, scheduled_for=SCHEDULED, downloader=fake_download)

    assert result["success"] is True
    assert result["publication_state"] == "PUBLICATION_DURABILITY_UNCERTAIN"
    pointer = json.loads((settings.root / "current.json").read_text(encoding="utf-8"))
    assert pointer["run_id"] == result["run_id"]


def test_erro_de_publicacao_antes_de_current_permanece_falha(settings, fake_download, monkeypatch):
    monkeypatch.setattr(
        runner,
        "publish",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("copy failed")),
    )

    result = run_once(settings=settings, scheduled_for=SCHEDULED, downloader=fake_download)

    assert result["success"] is False
    assert result["stage"] == "publication"
    assert not (settings.root / "current.json").exists()


def test_validacao_invalida_nao_publica_e_identifica_fonte(settings):
    def invalid_acerta(_page, instance, destination, _start, _end, deadline, **_kwargs):
        destination = Path(destination)
        if instance.name == "ACERTA":
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"nao-e-xlsx")
            os.utime(destination, (deadline.timestamp() - 1, deadline.timestamp() - 1))
            return destination
        fake = FakeDownload()
        return fake(_page, instance, destination, _start, _end, deadline)

    result = run_once(
        settings=settings,
        scheduled_for=SCHEDULED,
        downloader=invalid_acerta,
    )

    assert result["success"] is False
    assert result["stage"] == "validation"
    assert result["company"] == "ACERTA"
    assert not (settings.root / "current.json").exists()


def test_falha_preserva_ultimo_sucesso_e_grava_done_e_history_atomicos(settings, fake_download):
    seed_failed_done(settings.root, last_success="2026-07-27T08:20:00-03:00")
    fake_download.fail_for("ACERTA", code="DOWNLOAD_TIMEOUT")

    result = run_once(settings=settings, scheduled_for=SCHEDULED, downloader=fake_download)

    assert result["last_success"] == "2026-07-27T08:20:00-03:00"
    persisted = {
        key: value for key, value in result.items()
        if key not in {"state_persisted", "state_error", "housekeeping"}
    }
    assert json.loads((settings.root / "done.json").read_text(encoding="utf-8")) == persisted
    history = (settings.root / "logs" / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(history[-1]) == persisted
    assert not (settings.root / "done.json.tmp").exists()
    assert not (settings.root / "logs" / "history.jsonl.tmp").exists()


def test_temporarios_antigos_nao_bloqueiam_estado_atomico(settings):
    (settings.root / "done.json.tmp").write_text("stale", encoding="utf-8")
    (settings.root / "logs" / "history.jsonl.tmp").write_text("stale", encoding="utf-8")
    payload = {"success": True, "finished_at": SCHEDULED.isoformat()}

    write_done_atomic(settings.root, payload)
    append_history(settings.root, payload)

    assert json.loads((settings.root / "done.json").read_text(encoding="utf-8")) == payload
    assert json.loads((settings.root / "logs" / "history.jsonl").read_text(encoding="utf-8")) == payload


def test_lock_exclusivo_nao_remove_lock_de_outra_execucao(settings, fake_download):
    seed_failed_done(settings.root, last_success="2026-07-27T08:20:00-03:00")
    done_before = (settings.root / "done.json").read_bytes()
    history = settings.root / "logs" / "history.jsonl"
    history.write_text('{"event":"owner"}\n', encoding="utf-8")
    history_before = history.read_bytes()
    lock = settings.root / "financeiro_hoje.lock"
    lock.write_text('{"token":"other"}', encoding="utf-8")

    result = run_once(settings=settings, scheduled_for=SCHEDULED, downloader=fake_download)

    assert result["success"] is False
    assert result["stage"] == "lock"
    assert result["last_success"] == "2026-07-27T08:20:00-03:00"
    assert result["alert_active"] is True
    assert result["state_skipped"] == "ACTIVE_RUN"
    assert (settings.root / "done.json").read_bytes() == done_before
    assert history.read_bytes() == history_before
    assert lock.exists()


def test_lock_corrompido_na_limpeza_nao_suprime_erro_do_ciclo(settings):
    lock = settings.root / "financeiro_hoje.lock"

    with pytest.raises(RuntimeError, match="erro original"):
        with exclusive_run_lock(lock):
            lock.write_text("{corrompido", encoding="utf-8")
            raise RuntimeError("erro original")

    assert lock.read_text(encoding="utf-8") == "{corrompido"


def test_falha_na_remocao_do_lock_nao_suprime_erro_do_ciclo(settings, monkeypatch):
    lock = settings.root / "financeiro_hoje.lock"
    original_unlink = Path.unlink

    def fail_owner_unlink(path, *args, **kwargs):
        if path == lock:
            raise OSError("cleanup failed")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_owner_unlink)

    with pytest.raises(RuntimeError, match="erro original"):
        with exclusive_run_lock(lock):
            raise RuntimeError("erro original")


def test_contexto_injetado_e_fechado_em_falha(settings, fake_download):
    events = []

    class Context:
        def __enter__(self):
            events.append("enter")
            return object()

        def __exit__(self, *_args):
            events.append("exit")

    fake_download.fail_for("LOGA", code="DOWNLOAD_TIMEOUT")
    run_once(
        settings=settings,
        scheduled_for=SCHEDULED,
        downloader=fake_download,
        context_factory=lambda _instance: Context(),
    )

    assert events == ["enter", "exit"]


def test_deadline_unsupported_fecha_contexto_e_libera_locks(settings):
    events = []

    class Context:
        def __enter__(self):
            events.append("enter")
            return object()

        def __exit__(self, *_args):
            events.append("exit")

    def unsupported(*_args, **_kwargs):
        raise CollectionError("DOWNLOAD_DEADLINE_UNSUPPORTED")

    result = run_once(
        settings=settings,
        scheduled_for=SCHEDULED,
        downloader=unsupported,
        context_factory=lambda _instance: Context(),
    )

    assert result["code"] == "DOWNLOAD_DEADLINE_UNSUPPORTED"
    assert events == ["enter", "exit"]
    assert not (settings.root / "financeiro_hoje.lock").exists()
    assert not (settings.root.parent / "routerbox-site.lock").exists()


def test_downloader_recebe_deadline_local_compativel_com_browser(settings, fake_download):
    deadlines = []

    def record_deadline(*args, **kwargs):
        deadlines.append(args[5])
        return fake_download(*args, **kwargs)

    result = run_once(
        settings=settings,
        scheduled_for=SCHEDULED,
        downloader=record_deadline,
    )

    assert result["success"] is True
    assert all(value.tzinfo is None for value in deadlines)


def test_usa_timezone_configurada_na_fronteira_e_deadline_do_host(settings, fake_download):
    scheduled_utc = datetime(2026, 7, 28, 1, 30, tzinfo=timezone.utc)
    settings = replace(settings, timezone="Pacific/Kiritimati")
    seen_deadlines = []

    def record_deadline(*args, **kwargs):
        seen_deadlines.append(args[5])
        return fake_download(*args, **kwargs)

    result = run_once(
        settings=settings,
        scheduled_for=scheduled_utc,
        downloader=record_deadline,
        now=lambda: scheduled_utc,
    )

    configured = scheduled_utc.astimezone(ZoneInfo("Pacific/Kiritimati"))
    expected_browser_deadline = (
        (configured + timedelta(minutes=8)).astimezone().replace(tzinfo=None)
    )
    assert result["run_id"].startswith(configured.strftime("%Y%m%dT%H%M%S"))
    assert result["scheduled_for"] == configured.isoformat()
    assert json.loads(resolve_current(settings.root).read_text(encoding="utf-8"))["period_start"] == "2026-07-28"
    assert seen_deadlines == [expected_browser_deadline, expected_browser_deadline]


def test_now_naive_e_wall_clock_do_host_antes_de_converter_timezone(settings, fake_download, monkeypatch):
    settings = replace(settings, timezone="Pacific/Kiritimati")
    host_wall_clock = datetime(2026, 7, 27, 10, 0)
    host_zone = timezone(timedelta(hours=-3))
    monkeypatch.setattr(
        runner,
        "_host_aware",
        lambda value: value.replace(tzinfo=host_zone),
    )

    result = run_once(
        settings=settings,
        downloader=fake_download,
        now=lambda: host_wall_clock,
    )

    expected = host_wall_clock.replace(tzinfo=host_zone).astimezone(ZoneInfo(settings.timezone))
    assert result["scheduled_for"] == expected.isoformat()
    assert result["run_id"].startswith(expected.strftime("%Y%m%dT%H%M%S"))


def test_scheduled_for_naive_e_wall_time_da_timezone_configurada(settings):
    configured = ZoneInfo("Pacific/Kiritimati")

    scheduled = runner._scheduled_in_zone(datetime(2026, 7, 28, 9, 0), configured)

    assert scheduled == datetime(2026, 7, 28, 9, 0, tzinfo=configured)


def write_workbook(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    book = Workbook()
    sheet = book.active
    sheet.title = "Consulta"
    sheet.append(("Numero", "SituacaoOS", "Fluxo"))
    sheet.append(("1", "Aberta", "Financeiro"))
    book.save(path)
    book.close()


def seed_current(root, run_id):
    payload = json.dumps({
        "schema_version": 1,
        "run_id": run_id,
        "manifest": f"published/{run_id}/manifest.json",
    }).encode("utf-8")
    (root / "current.json").write_bytes(payload)
    return payload


def seed_failed_done(root, last_success=None):
    payload = {
        "success": False,
        "alert_active": True,
        "last_success": last_success,
        "message": "Falha anterior.",
    }
    (root / "done.json").write_text(json.dumps(payload), encoding="utf-8")


def resolve_current(root):
    pointer = json.loads((root / "current.json").read_text(encoding="utf-8"))
    return root / pointer["manifest"]
