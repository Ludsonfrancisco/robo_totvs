from __future__ import annotations

from datetime import datetime, timedelta, timezone
import errno
import json
import os
import stat
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

import pytest

from flows.financeiro_hoje import publication
from flows.financeiro_hoje.publication import (
    AUTOMATION_VERSION,
    apply_retention,
    build_manifest,
    publish,
)


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
RUN_ID = "20260727T082000-03a1"


def valid_run(root: Path) -> Path:
    run_dir = root / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    (run_dir / "consolidado.xlsx").write_bytes(b"xlsx-content")
    return run_dir


def valid_manifest() -> dict:
    return {
        "schema_version": 1,
        "automation_version": "2026.07.27",
        "status": "success",
        "scheduled_for": "2026-07-27T08:20:00-03:00",
        "started_at": "2026-07-27T08:20:01-03:00",
        "finished_at": "2026-07-27T08:20:14-03:00",
        "period_start": "2026-07-27",
        "period_end": "2026-08-06",
        "sources": {
            "LOGA": {"path": "original_loga.xlsx", "rows": 1, "size": 11, "sha256": "a" * 64},
            "ACERTA": {"path": "original_acerta.xlsx", "rows": 1, "size": 12, "sha256": "b" * 64},
        },
        "consolidated": {
            "path": "consolidado.xlsx",
            "rows": 2,
            "size": 12,
            "sha256": "c" * 64,
        },
    }


def test_publish_troca_current_somente_no_final(tmp_path):
    seen = []

    published = publish(
        root=tmp_path,
        run_id=RUN_ID,
        run_dir=valid_run(tmp_path),
        manifest=valid_manifest(),
        before_promote=lambda: seen.append((tmp_path / "current.json").exists()),
    )

    assert seen == [False]
    pointer = json.loads((tmp_path / "current.json").read_text(encoding="utf-8"))
    assert pointer == {
        "schema_version": 1,
        "run_id": RUN_ID,
        "manifest": f"published/{RUN_ID}/manifest.json",
    }
    assert published.name == RUN_ID
    assert (published / "consolidado.xlsx").read_bytes() == b"xlsx-content"
    assert json.loads((published / "manifest.json").read_text(encoding="utf-8")) == valid_manifest()
    assert not (tmp_path / "published" / f"{RUN_ID}.tmp").exists()


def test_publish_sincroniza_diretorios_em_cada_ponto_de_promocao(tmp_path):
    with patch("flows.financeiro_hoje.publication._sync_directory") as sync_directory:
        publish(tmp_path, RUN_ID, valid_run(tmp_path), valid_manifest())

    assert sync_directory.call_args_list == [
        ((tmp_path / "published" / f"{RUN_ID}.tmp",), {}),
        ((tmp_path / "published",), {}),
        ((tmp_path,), {}),
    ]


def test_sync_directory_tolera_windows_e_erros_de_suporte(tmp_path, monkeypatch):
    open_directory = patch.object(publication.os, "open")
    with open_directory as open_mock:
        monkeypatch.setattr(publication.os, "name", "nt")
        publication._sync_directory(tmp_path)
    open_mock.assert_not_called()

    monkeypatch.setattr(publication.os, "name", "posix")
    with patch.object(publication.os, "open", side_effect=OSError(errno.EINVAL, "unsupported")):
        publication._sync_directory(tmp_path)


@pytest.mark.parametrize("run_id", (
    "../escape",
    "/absolute",
    r"C:\absolute",
    ".",
    "..",
    r"20260727T082000-03a1\escape",
    "not-a-canonical-run-id",
))
def test_publish_rejeita_run_id_inseguro_antes_de_tocar_no_destino(tmp_path, run_id):
    destination_root = tmp_path / "destination"
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "consolidado.xlsx").write_bytes(b"xlsx-content")

    with pytest.raises(ValueError, match="run_id"):
        publish(destination_root, run_id, source_dir, valid_manifest())

    assert not destination_root.exists()


def test_build_manifest_rejeita_run_id_que_nao_pode_ser_publicado(tmp_path):
    artifact = SimpleNamespace(
        path=valid_run(tmp_path) / "consolidado.xlsx", rows=1, size=10, sha256="a" * 64,
    )

    with pytest.raises(ValueError, match="run_id"):
        build_manifest(artifact, artifact, artifact, run_id="../escape")


def test_build_manifest_registra_auditoria_sem_dados_da_planilha(tmp_path):
    run_dir = valid_run(tmp_path)
    loga = SimpleNamespace(path=run_dir / "loga.xlsx", rows=1, size=10, sha256="a" * 64)
    acerta = SimpleNamespace(path=run_dir / "acerta.xlsx", rows=2, size=20, sha256="b" * 64)
    consolidated = SimpleNamespace(
        path=run_dir / "consolidado.xlsx", rows=3, size=30, sha256="c" * 64,
    )

    manifest = build_manifest(
        loga,
        acerta,
        consolidated,
        scheduled_for=datetime(2026, 7, 27, 8, 20, tzinfo=timezone(timedelta(hours=-3))),
        started_at=datetime(2026, 7, 27, 8, 20, 1, tzinfo=timezone(timedelta(hours=-3))),
        finished_at=datetime(2026, 7, 27, 8, 20, 14, tzinfo=timezone(timedelta(hours=-3))),
    )

    assert manifest["schema_version"] == 1
    assert manifest["run_id"] == RUN_ID
    assert manifest["automation_version"] == AUTOMATION_VERSION
    assert manifest["status"] == "success"
    assert manifest["scheduled_for"].endswith("-03:00")
    assert manifest["period_start"] == "2026-07-27"
    assert manifest["period_end"] == "2026-08-06"
    assert manifest["sources"] == {
        "LOGA": {"name": "LOGA", "path": "loga.xlsx", "rows": 1, "size": 10, "sha256": "a" * 64},
        "ACERTA": {"name": "ACERTA", "path": "acerta.xlsx", "rows": 2, "size": 20, "sha256": "b" * 64},
    }
    assert manifest["consolidated"] == {
        "name": "consolidated",
        "path": "consolidado.xlsx",
        "rows": 3,
        "size": 30,
        "sha256": "c" * 64,
    }


def test_publish_nao_promove_current_quando_falha_antes_do_ponteiro(tmp_path):
    previous = b'{"run_id":"anterior"}'
    (tmp_path / "current.json").write_bytes(previous)

    with pytest.raises(RuntimeError, match="interrompido"):
        publish(
            root=tmp_path,
            run_id=RUN_ID,
            run_dir=valid_run(tmp_path),
            manifest=valid_manifest(),
            before_promote=lambda: (_ for _ in ()).throw(RuntimeError("interrompido")),
        )

    assert (tmp_path / "current.json").read_bytes() == previous
    assert (tmp_path / "published" / RUN_ID).exists()


def test_publish_remove_current_tmp_se_a_troca_do_ponteiro_falhar(tmp_path, monkeypatch):
    previous = b'{"run_id":"anterior"}'
    (tmp_path / "current.json").write_bytes(previous)
    original_replace = publication.os.replace

    def fail_only_current(source, target):
        if Path(target).name == "current.json":
            raise OSError("falha simulada")
        return original_replace(source, target)

    monkeypatch.setattr(publication.os, "replace", fail_only_current)

    with pytest.raises(OSError, match="falha simulada"):
        publish(tmp_path, RUN_ID, valid_run(tmp_path), valid_manifest())

    assert (tmp_path / "current.json").read_bytes() == previous
    assert not (tmp_path / "current.json.tmp").exists()


def test_publish_recupera_so_tmp_antigo_e_nunca_substitui_pacote_final(tmp_path):
    stale = tmp_path / "published" / f"{RUN_ID}.tmp"
    stale.mkdir(parents=True)
    _set_mtime(stale, datetime.now(timezone.utc) - timedelta(days=2))

    published = publish(tmp_path, RUN_ID, valid_run(tmp_path), valid_manifest())

    assert published.exists()
    assert not stale.exists()
    active = tmp_path / "published" / "20260727T082100-03a2"
    active.mkdir()
    (active / "marker").write_text("ativo", encoding="utf-8")
    stale_active = tmp_path / "published" / f"{active.name}.tmp"
    stale_active.mkdir()
    _set_mtime(stale_active, datetime.now(timezone.utc) - timedelta(days=2))

    with pytest.raises(FileExistsError):
        publish(tmp_path, active.name, valid_run(tmp_path), valid_manifest())

    assert (active / "marker").read_text(encoding="utf-8") == "ativo"
    assert stale_active.exists()


def test_publish_recusa_tmp_recente_sem_apaga_lo(tmp_path):
    temporary = tmp_path / "published" / f"{RUN_ID}.tmp"
    temporary.mkdir(parents=True)

    with pytest.raises(FileExistsError):
        publish(tmp_path, RUN_ID, valid_run(tmp_path), valid_manifest())

    assert temporary.exists()


def test_publish_rejeita_published_root_redirecionado(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    published = tmp_path / "published"
    _make_directory_link(published, outside)

    with pytest.raises(ValueError, match="storage"):
        publish(tmp_path, RUN_ID, valid_run(tmp_path), valid_manifest())

    assert not (outside / RUN_ID).exists()


def test_publish_rejeita_runs_root_redirecionado(tmp_path):
    outside_runs = tmp_path / "outside-runs"
    outside_runs.mkdir()
    _make_directory_link(tmp_path / "runs", outside_runs)
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "consolidado.xlsx").write_bytes(b"xlsx-content")

    with pytest.raises(ValueError, match="storage"):
        publish(tmp_path, RUN_ID, source_dir, valid_manifest())

    assert not (tmp_path / "published" / RUN_ID).exists()


def test_publish_rejeita_root_redirecionado(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    _make_directory_link(root, outside)
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "consolidado.xlsx").write_bytes(b"xlsx-content")

    with pytest.raises(ValueError, match="storage"):
        publish(root, RUN_ID, source_dir, valid_manifest())

    assert not (outside / "published" / RUN_ID).exists()


def test_reparse_point_e_tratado_como_storage_redirecionado(tmp_path, monkeypatch):
    attributes = SimpleNamespace(
        st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )
    monkeypatch.setattr(Path, "is_symlink", lambda _path: False)
    monkeypatch.setattr(Path, "lstat", lambda _path: attributes)

    assert publication._is_storage_redirect(tmp_path)


def test_retention_nunca_remove_current_e_preserva_tres_publicados(tmp_path):
    current = _build_packages(tmp_path, count=5, current_index=0)
    old_run = tmp_path / "runs" / "old-run"
    old_run.mkdir(parents=True)
    old_evidence = tmp_path / "evidence" / "old-run"
    old_evidence.mkdir(parents=True)
    recent_evidence = tmp_path / "evidence" / "recent-run"
    recent_evidence.mkdir(parents=True)
    _set_mtime(old_run, NOW - timedelta(days=8))
    _set_mtime(old_evidence, NOW - timedelta(days=15))
    _set_mtime(recent_evidence, NOW - timedelta(days=13))
    history = tmp_path / "logs" / "history.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text(
        "\n".join((
            json.dumps({"finished_at": (NOW - timedelta(days=31)).isoformat()}),
            json.dumps({"finished_at": (NOW - timedelta(days=29)).isoformat()}),
        )) + "\n",
        encoding="utf-8",
    )

    apply_retention(tmp_path, now=NOW)

    assert current.exists()
    assert len(list((tmp_path / "published").iterdir())) >= 3
    assert not old_run.exists()
    assert not old_evidence.exists()
    assert recent_evidence.exists()
    assert history.read_text(encoding="utf-8") == json.dumps({
        "finished_at": (NOW - timedelta(days=29)).isoformat(),
    }) + "\n"


def test_retention_preserva_evidencia_da_execucao_corrente(tmp_path):
    (tmp_path / "current.json").write_text(json.dumps({"run_id": RUN_ID}), encoding="utf-8")
    evidence = tmp_path / "evidence" / RUN_ID
    evidence.mkdir(parents=True)
    _set_mtime(evidence, NOW - timedelta(days=15))

    apply_retention(tmp_path, now=NOW)

    assert evidence.exists()


def test_retention_ignora_symlink_em_published_sem_tocar_no_alvo(tmp_path):
    published_root = tmp_path / "published"
    published_root.mkdir()
    for index in range(4):
        package = published_root / f"2026072{index}T082000-a{index:03d}"
        package.mkdir()
        _set_mtime(package, NOW - timedelta(days=8 + index))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_text("nao remover", encoding="utf-8")
    link = published_root / "published-link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink indisponivel: {exc}")

    apply_retention(tmp_path, now=NOW)

    assert link.is_symlink()
    assert (outside / "sentinel").read_text(encoding="utf-8") == "nao remover"
    assert len([path for path in published_root.iterdir() if path.is_dir() and not path.is_symlink()]) == 3


@pytest.mark.parametrize("managed_name", ("runs", "evidence"))
def test_retention_ignora_root_gerenciado_redirecionado(tmp_path, managed_name):
    outside = tmp_path / "outside"
    victim = outside / "victim"
    victim.mkdir(parents=True)
    _set_mtime(victim, NOW - timedelta(days=31))
    _make_directory_link(tmp_path / managed_name, outside)

    apply_retention(tmp_path, now=NOW)

    assert victim.exists()


def test_retention_ignora_logs_root_redirecionado(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    history = outside / "history.jsonl"
    original = json.dumps({"finished_at": (NOW - timedelta(days=31)).isoformat()}) + "\n"
    history.write_text(original, encoding="utf-8")
    _make_directory_link(tmp_path / "logs", outside)

    apply_retention(tmp_path, now=NOW)

    assert history.read_text(encoding="utf-8") == original


def test_retention_ignora_candidate_reparse_simulado(tmp_path, monkeypatch):
    candidate = tmp_path / "runs" / RUN_ID
    candidate.mkdir(parents=True)
    _set_mtime(candidate, NOW - timedelta(days=31))
    original_state = publication._storage_path_state

    monkeypatch.setattr(
        publication,
        "_storage_path_state",
        lambda path: publication.StoragePathState(True, False, False, True)
        if path == candidate else original_state(path),
    )

    apply_retention(tmp_path, now=NOW)

    assert candidate.exists()


def test_publish_nao_chama_exists_para_pacote_redirecionado(tmp_path, monkeypatch):
    package = tmp_path / "published" / RUN_ID
    outside = tmp_path / "outside"
    outside.mkdir()
    package.parent.mkdir()
    _make_directory_link(package, outside)
    original_exists = Path.exists

    def reject_redirect_exists(path):
        if path == package:
            raise AssertionError("exists seguiu pacote redirecionado")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", reject_redirect_exists)

    with pytest.raises(FileExistsError):
        publish(tmp_path, RUN_ID, valid_run(tmp_path), valid_manifest())


def test_retention_nao_chama_is_dir_para_candidate_redirecionado(tmp_path, monkeypatch):
    candidate = tmp_path / "runs" / RUN_ID
    outside = tmp_path / "outside"
    outside.mkdir()
    candidate.parent.mkdir()
    _make_directory_link(candidate, outside)
    original_is_dir = Path.is_dir

    def reject_redirect_is_dir(path):
        if path == candidate:
            raise AssertionError("is_dir seguiu candidate redirecionado")
        return original_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", reject_redirect_is_dir)

    apply_retention(tmp_path, now=NOW)
    assert candidate.is_symlink()


def test_publish_nao_chama_exists_para_current_tmp_redirecionado(tmp_path, monkeypatch):
    temporary = tmp_path / "current.json.tmp"
    outside = tmp_path / "outside"
    outside.mkdir()
    _make_directory_link(temporary, outside)
    original_exists = Path.exists

    def reject_redirect_exists(path):
        if path == temporary:
            raise AssertionError("exists seguiu current tmp redirecionado")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", reject_redirect_exists)

    with pytest.raises(ValueError, match="storage"):
        publish(tmp_path, RUN_ID, valid_run(tmp_path), valid_manifest())


def test_publish_nao_chama_exists_ao_limpar_tmp_de_pacote(tmp_path, monkeypatch):
    temporary = tmp_path / "published" / f"{RUN_ID}.tmp"
    original_exists = Path.exists
    monkeypatch.setattr(publication, "_copy_synced", lambda _source, _target: (_ for _ in ()).throw(RuntimeError("copy")))

    def reject_temporary_exists(path):
        if path == temporary:
            raise AssertionError("exists seguiu tmp de pacote")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", reject_temporary_exists)

    with pytest.raises(RuntimeError, match="copy"):
        publish(tmp_path, RUN_ID, valid_run(tmp_path), valid_manifest())


def _build_packages(root: Path, count: int, current_index: int) -> Path:
    published_root = root / "published"
    published_root.mkdir()
    packages = []
    for index in range(count):
        package = published_root / f"202607{22 + index}T082000-a{index:03d}"
        package.mkdir()
        _set_mtime(package, NOW - timedelta(days=8 - index))
        packages.append(package)
    current = packages[current_index]
    (root / "current.json").write_text(json.dumps({
        "schema_version": 1,
        "run_id": current.name,
        "manifest": f"published/{current.name}/manifest.json",
    }), encoding="utf-8")
    return current


def _set_mtime(path: Path, when: datetime) -> None:
    timestamp = when.timestamp()
    path.touch()
    path.stat()
    os.utime(path, (timestamp, timestamp))


def _make_directory_link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink indisponivel: {exc}")
