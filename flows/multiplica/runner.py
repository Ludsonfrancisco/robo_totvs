from datetime import date, datetime
import json
import os
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .browser import authenticated_page
from .config import Settings
from .cycles import collection_windows
from .loga import CollectionError, collect_window


RETRYABLE_ERRORS = {
    "NAVIGATION_TIMEOUT",
    "DOWNLOAD_TIMEOUT",
    "DOWNLOAD_FAILED",
}


class AlreadyRunning(RuntimeError):
    pass


def _write_done(settings, payload):
    target = settings.runtime_root / "run_multiplica.done"
    temporary = settings.runtime_root / "runtime" / "run_multiplica.done.tmp"
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, target)


def run_once(
    *,
    settings: Settings | None = None,
    day: date | None = None,
    page_factory=None,
    collector=None,
):
    settings = settings or Settings.from_mapping(os.environ)
    day = day or date.today()
    page_factory = page_factory or authenticated_page
    collector = collector or collect_window
    lock_path = settings.runtime_root / "runtime" / "run_multiplica.lock"
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )
    except FileExistsError as exc:
        raise AlreadyRunning("MULTIPLICA_ALREADY_RUNNING") from exc
    os.close(descriptor)

    started_at = datetime.now().astimezone().isoformat()
    completed = []
    error_code = ""
    try:
        with page_factory(settings) as page:
            for window in collection_windows(day):
                for attempt in range(1, 4):
                    try:
                        bundle = collector(page, window, settings)
                        error_code = ""
                        completed.append(
                            {
                                "cycle_start": window.cycle_start.isoformat(),
                                "bundle_id": Path(bundle).name,
                            }
                        )
                        break
                    except CollectionError as exc:
                        error_code = exc.code
                        if exc.code not in RETRYABLE_ERRORS or attempt == 3:
                            break
                    except PlaywrightTimeoutError:
                        error_code = "NAVIGATION_TIMEOUT"
                        if attempt == 3:
                            break
                if error_code and error_code not in RETRYABLE_ERRORS:
                    break
        payload = {
            "success": not error_code,
            "error_code": error_code,
            "started_at": started_at,
            "finished_at": datetime.now().astimezone().isoformat(),
            "bundles": completed,
        }
    except CollectionError as exc:
        payload = {
            "success": False,
            "error_code": exc.code,
            "started_at": started_at,
            "finished_at": datetime.now().astimezone().isoformat(),
            "bundles": completed,
        }
    except Exception:
        payload = {
            "success": False,
            "error_code": "UNEXPECTED_ERROR",
            "started_at": started_at,
            "finished_at": datetime.now().astimezone().isoformat(),
            "bundles": completed,
        }
    finally:
        lock_path.unlink(missing_ok=True)

    _write_done(settings, payload)
    return payload


if __name__ == "__main__":
    raise SystemExit(0 if run_once()["success"] else 1)
