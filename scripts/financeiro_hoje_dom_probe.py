from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from flows.financeiro_hoje.browser import (
    CollectionError,
    prepare_custom_reports,
)
from flows.financeiro_hoje.config import Instance


CONTROL_TAGS = {"a", "button", "input", "label", "select", "textarea"}
CONTROL_SELECTOR = (
    "a, button, input, label, select, textarea, "
    "[role], [aria-label], [title]"
)
SAFE_ATTRIBUTES = ("id", "name", "role", "aria-label", "title")
CONTAINER_CHROMIUM_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]
GENERATE_PROBE_SELECTOR = (
    '*:text-is("Gerar"), [title*="Gerar" i], '
    '[aria-label*="Gerar" i], [value*="Gerar" i], '
    '[alt*="Gerar" i], [id*="Gerar" i], [name*="Gerar" i], '
    '[class*="Gerar" i], [src*="Gerar" i], [onclick*="Gerar" i]'
)
GENERATE_MATCH_ATTRIBUTES = ("id", "name", "class", "src", "onclick")
GENERATE_INVENTORY_SELECTOR = (
    'a, button, input, [role="button"], [onclick], img'
)
GENERATE_INVENTORY_LIMIT = 40


def _check_probe_deadline(monotonic_deadline: float | None) -> None:
    if (
        monotonic_deadline is not None
        and time.monotonic() >= monotonic_deadline
    ):
        raise CollectionError("PROBE_TIMEOUT")


def _remaining_probe_ms(monotonic_deadline: float) -> int:
    _check_probe_deadline(monotonic_deadline)
    return max(
        1,
        int((monotonic_deadline - time.monotonic()) * 1000),
    )


def _wait_for_probe_render(
    page,
    *,
    monotonic_deadline: float,
) -> None:
    wait_ms = min(3_000, max(1, _remaining_probe_ms(monotonic_deadline) - 1))
    page.wait_for_timeout(wait_ms)
    _check_probe_deadline(monotonic_deadline)


def _run_supervised_command(
    command: list[str],
    *,
    timeout_seconds: float,
    grace_seconds: float = 1.0,
) -> tuple[int, str, str]:
    options = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "posix":
        options["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(command, **options)
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return process.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.communicate(timeout=grace_seconds)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                pass
        return 2, "", "PROBE_TIMEOUT"


def sanitize_url(url: str) -> str:
    parts = urlsplit(url or "")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())[:80]


def _sanitize_control(raw: dict) -> dict:
    control = {"tag": str(raw.get("tag", "")).casefold()}
    for attribute in SAFE_ATTRIBUTES:
        value = _clean_text(raw.get(attribute))
        if value:
            control[attribute] = value
    if (
        control["tag"] not in {"input", "textarea"}
        and control.get("role", "").casefold() != "row"
        and not raw.get("in_report_row")
        and not raw.get("contains_report_row")
    ):
        text = _clean_text(raw.get("text"))
        if text:
            control["text"] = text
    return control


def _safe_classes(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        _clean_text(item)[:40]
        for item in value[:6]
        if _clean_text(item)
    ]


def _sanitize_generate_element(raw: dict) -> dict:
    result = {
        "tag": _clean_text(raw.get("tag")).casefold(),
        "visible": bool(raw.get("visible")),
    }
    for key in (
        "id",
        "name",
        "type",
        "role",
        "title",
        "aria-label",
        "value",
        "text",
        "alt",
    ):
        value = _clean_text(raw.get(key))
        if value:
            result[key] = value
    source_path = urlsplit(str(raw.get("src") or "")).path
    source_basename = _clean_text(source_path.rsplit("/", 1)[-1])
    if source_basename:
        result["src"] = source_basename
    matched_attributes = [
        attribute
        for attribute in list(raw.get("matched_attributes") or ())
        if attribute in GENERATE_MATCH_ATTRIBUTES
    ][:6]
    if matched_attributes:
        result["matched_attributes"] = matched_attributes
    if raw.get("has_onclick"):
        result["has_onclick"] = True
    classes = _safe_classes(raw.get("classes"))
    if classes:
        result["classes"] = classes
    ancestors = []
    for raw_ancestor in list(raw.get("ancestors") or ())[:4]:
        ancestor = {
            key: value
            for key, value in {
                "tag": _clean_text(raw_ancestor.get("tag")).casefold(),
                "id": _clean_text(raw_ancestor.get("id")),
                "role": _clean_text(raw_ancestor.get("role")),
            }.items()
            if value
        }
        ancestor_classes = _safe_classes(raw_ancestor.get("classes"))
        if ancestor_classes:
            ancestor["classes"] = ancestor_classes
        if ancestor:
            ancestors.append(ancestor)
    if ancestors:
        result["ancestors"] = ancestors
    return result

def _sanitize_inventory_element(raw: dict) -> dict:
    result = {
        "tag": _clean_text(raw.get("tag")).casefold(),
        "visible": bool(raw.get("visible")),
    }
    for key in (
        "id",
        "name",
        "type",
        "role",
        "title",
        "aria-label",
        "alt",
    ):
        value = _clean_text(raw.get(key))
        if value:
            result[key] = value
    source_path = urlsplit(str(raw.get("src") or "")).path
    source_basename = _clean_text(source_path.rsplit("/", 1)[-1])
    if source_basename:
        result["src"] = source_basename
    raw_href = str(raw.get("href") or "")
    if raw_href:
        result["has_href"] = True
        href_parts = urlsplit(raw_href)
        if href_parts.scheme.casefold() in {"", "http", "https"}:
            result["href"] = sanitize_url(raw_href)
    if raw.get("onclick") or raw.get("has_onclick"):
        result["has_onclick"] = True
    classes = _safe_classes(raw.get("classes"))
    if classes:
        result["classes"] = classes
    allowed_reasons = {"cursor", "tabindex", "event", "class", "tag", "role"}
    clickable_reasons = [
        reason
        for reason in list(raw.get("clickable_reasons") or ())
        if reason in allowed_reasons
    ][:6]
    if clickable_reasons:
        result["clickable_reasons"] = clickable_reasons
    return result

def _snapshot_scope_structure(scope) -> dict:
    bodies = scope.locator("body")
    if not bodies.count():
        return {"ready_state": "missing", "total_elements": 0, "clickables": []}
    raw = bodies.nth(0).evaluate(
        """body => {
            const elements = Array.from(body.querySelectorAll('*'));
            const interactiveTags = new Set(['A', 'BUTTON', 'INPUT', 'IMG']);
            const pattern = /(btn|button|botao|toolbar|sc_b_)/i;
            const clickables = [];
            for (const element of elements) {
                if (clickables.length >= 40) break;
                const style = getComputedStyle(element);
                const visible = Boolean(
                    element.isConnected
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && element.getClientRects().length
                );
                if (!visible) continue;
                const reasons = [];
                if (interactiveTags.has(element.tagName)) reasons.push('tag');
                if ((element.getAttribute('role') || '').toLowerCase() === 'button') reasons.push('role');
                if (element.hasAttribute('tabindex')) reasons.push('tabindex');
                const hasEvent = ['onclick', 'onmousedown', 'onmouseup', 'onpointerdown']
                    .some(attribute => element.hasAttribute(attribute));
                if (hasEvent) reasons.push('event');
                if (style.cursor === 'pointer') reasons.push('cursor');
                if (pattern.test(`${element.id || ''} ${element.className || ''}`)) reasons.push('class');
                if (!reasons.length) continue;
                clickables.push({
                    tag: element.tagName.toLowerCase(),
                    id: element.getAttribute('id'),
                    name: element.getAttribute('name'),
                    type: element.getAttribute('type'),
                    role: element.getAttribute('role'),
                    title: element.getAttribute('title'),
                    'aria-label': element.getAttribute('aria-label'),
                    alt: element.getAttribute('alt'),
                    src: element.getAttribute('src'),
                    href: element.getAttribute('href'),
                    has_onclick: hasEvent,
                    classes: Array.from(element.classList || []).slice(0, 6),
                    visible,
                    clickable_reasons: reasons
                });
            }
            return {
                ready_state: document.readyState,
                total_elements: elements.length,
                clickables
            };
        }"""
    )
    ready_state = str(raw.get("ready_state") or "unknown").casefold()
    if ready_state not in {"loading", "interactive", "complete"}:
        ready_state = "unknown"
    try:
        total_elements = max(0, min(int(raw.get("total_elements") or 0), 1_000_000))
    except (TypeError, ValueError):
        total_elements = 0
    return {
        "ready_state": ready_state,
        "total_elements": total_elements,
        "clickables": [
            _sanitize_inventory_element(item)
            for item in list(raw.get("clickables") or ())[:GENERATE_INVENTORY_LIMIT]
        ],
    }


def _write_private_json(
    target: Path,
    payload: dict,
    *,
    monotonic_deadline: float | None = None,
) -> Path:
    _check_probe_deadline(monotonic_deadline)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        _check_probe_deadline(monotonic_deadline)
        os.replace(temporary, target)
        target.chmod(0o600)
        _check_probe_deadline(monotonic_deadline)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return target


class _SyntheticParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.controls: list[dict] = []
        self.frames: list[dict] = []
        self.stack: list[tuple[str, int | None, bool]] = []
        self.row_depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        is_report_row = (
            tag == "tr"
            or str(attributes.get("role", "")).casefold() == "row"
        )
        if is_report_row:
            self.row_depth += 1
            for _open_tag, index, _is_row in self.stack:
                if index is not None:
                    self.controls[index]["contains_report_row"] = True
        if tag == "iframe":
            frame = {
                key: _clean_text(attributes.get(key))
                for key in ("id", "name", "title")
                if _clean_text(attributes.get(key))
            }
            self.frames.append(frame)
        is_control = (
            tag in CONTROL_TAGS
            or any(key in attributes for key in ("role", "aria-label", "title"))
        )
        index = None
        if is_control:
            raw = {
                "tag": tag,
                **{key: attributes.get(key) for key in SAFE_ATTRIBUTES},
                "text": "",
                "in_report_row": self.row_depth > 0,
            }
            self.controls.append(raw)
            index = len(self.controls) - 1
        self.stack.append((tag, index, is_report_row))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        for _tag, index, _is_row in self.stack:
            if index is not None:
                self.controls[index]["text"] += data

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                removed = self.stack[index:]
                del self.stack[index:]
                self.row_depth = max(
                    0,
                    self.row_depth
                    - sum(int(item[2]) for item in removed),
                )
                break


def snapshot_html(html: str, source_url: str = "") -> dict:
    parser = _SyntheticParser()
    parser.feed(html)
    return {
        "url": sanitize_url(source_url),
        "frames": parser.frames,
        "controls": [
            _sanitize_control(control)
            for control in parser.controls
        ],
    }


def _scopes(page):
    seen: set[int] = set()
    pending = [page, *tuple(page.frames)]
    while pending:
        scope = pending.pop(0)
        if id(scope) in seen:
            continue
        seen.add(id(scope))
        yield scope
        children = getattr(scope, "child_frames", ())
        pending.extend(tuple(children() if callable(children) else children))


def snapshot_page(page) -> dict:
    frames = []
    controls = []
    readable_scopes = 0
    discovered_elements = 0
    readable_elements = 0
    for scope in _scopes(page):
        if scope is not page:
            frames.append(
                {
                    key: value
                    for key, value in {
                        "name": _clean_text(getattr(scope, "name", "")),
                        "url": sanitize_url(getattr(scope, "url", "")),
                    }.items()
                    if value
                }
            )
        try:
            elements = scope.locator(CONTROL_SELECTOR)
            count = elements.count()
        except Exception:
            continue
        readable_scopes += 1
        discovered_elements += count
        for index in range(count):
            try:
                raw = elements.nth(index).evaluate(
                    """element => ({
                        tag: element.tagName.toLowerCase(),
                        id: element.getAttribute("id"),
                        name: element.getAttribute("name"),
                        role: element.getAttribute("role"),
                        "aria-label": element.getAttribute("aria-label"),
                        title: element.getAttribute("title"),
                        text: element.closest('tr, [role="row"]')
                            ? ""
                            : element.textContent,
                        in_report_row: Boolean(
                            element.closest('tr, [role="row"]')
                        ),
                        contains_report_row: Boolean(
                            element.querySelector('tr, [role="row"]')
                        )
                    })"""
                )
                controls.append(_sanitize_control(raw))
                readable_elements += 1
            except Exception:
                continue
    if (
        not readable_scopes
        or (discovered_elements and not readable_elements)
    ):
        raise CollectionError("PROBE_DOM_UNREADABLE")
    return {
        "url": sanitize_url(page.url),
        "frames": frames,
        "controls": controls,
    }


def snapshot_generate_page(
    page,
    *,
    monotonic_deadline: float | None = None,
) -> dict:
    _check_probe_deadline(monotonic_deadline)
    frame_results = []
    scopes = [page]
    main_frame = getattr(page, "main_frame", None)
    scopes.extend(
        frame
        for frame in tuple(getattr(page, "frames", ()))
        if frame is not main_frame
    )
    for index, scope in enumerate(scopes):
        _check_probe_deadline(monotonic_deadline)
        elements = []
        inventory = []
        candidates = scope.locator(GENERATE_PROBE_SELECTOR)
        candidate_count = candidates.count()
        _check_probe_deadline(monotonic_deadline)
        for element_index in range(candidate_count):
            _check_probe_deadline(monotonic_deadline)
            raw = candidates.nth(element_index).evaluate(
                """element => {
                    const clean = value =>
                        String(value || '').replace(/\\s+/g, ' ').trim();
                    const exact = value => clean(value) === 'Gerar';
                    const containsGenerate = value => /gerar/i.test(clean(value));
                    const text = element.closest('tr, [role="row"]')
                        ? ''
                        : clean(element.textContent);
                    const value = clean(element.getAttribute('value'));
                    const title = clean(element.getAttribute('title'));
                    const aria = clean(element.getAttribute('aria-label'));
                    const alt = clean(element.getAttribute('alt'));
                    const structural = {
                        id: element.getAttribute('id'),
                        name: element.getAttribute('name'),
                        class: element.getAttribute('class'),
                        src: element.getAttribute('src'),
                        onclick: element.getAttribute('onclick')
                    };
                    const matchedAttributes = Object.entries(structural)
                        .filter(([, attributeValue]) => containsGenerate(attributeValue))
                        .map(([attribute]) => attribute);
                    if (![text, value, title, aria].some(exact)
                        && !containsGenerate(alt)
                        && !matchedAttributes.length) return null;
                    const classes = node =>
                        Array.from(node.classList || []).slice(0, 6);
                    const ancestors = [];
                    let current = element.parentElement;
                    while (current && ancestors.length < 4) {
                        ancestors.push({
                            tag: current.tagName.toLowerCase(),
                            id: current.getAttribute('id'),
                            role: current.getAttribute('role'),
                            classes: classes(current)
                        });
                        current = current.parentElement;
                    }
                    const style = getComputedStyle(element);
                    return {
                        tag: element.tagName.toLowerCase(),
                        id: element.getAttribute('id'),
                        name: element.getAttribute('name'),
                        type: element.getAttribute('type'),
                        role: element.getAttribute('role'),
                        title,
                        'aria-label': aria,
                        value,
                        alt,
                        src: structural.src,
                        matched_attributes: matchedAttributes,
                        has_onclick: Boolean(structural.onclick),
                        text,
                        classes: classes(element),
                        visible: Boolean(
                            element.isConnected
                            && style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && element.getClientRects().length
                        ),
                        ancestors
                    };
                }"""
            )
            _check_probe_deadline(monotonic_deadline)
            if raw is not None:
                elements.append(_sanitize_generate_element(raw))
        inventory_candidates = scope.locator(GENERATE_INVENTORY_SELECTOR)
        inventory_count = min(
            inventory_candidates.count(),
            GENERATE_INVENTORY_LIMIT,
        )
        _check_probe_deadline(monotonic_deadline)
        for inventory_index in range(inventory_count):
            _check_probe_deadline(monotonic_deadline)
            raw_inventory = inventory_candidates.nth(inventory_index).evaluate(
                """element => {
                    const classes = node =>
                        Array.from(node.classList || []).slice(0, 6);
                    const style = getComputedStyle(element);
                    return {
                        tag: element.tagName.toLowerCase(),
                        id: element.getAttribute('id'),
                        name: element.getAttribute('name'),
                        type: element.getAttribute('type'),
                        role: element.getAttribute('role'),
                        title: element.getAttribute('title'),
                        'aria-label': element.getAttribute('aria-label'),
                        alt: element.getAttribute('alt'),
                        src: element.getAttribute('src'),
                        href: element.getAttribute('href'),
                        onclick: element.getAttribute('onclick'),
                        classes: classes(element),
                        visible: Boolean(
                            element.isConnected
                            && style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && element.getClientRects().length
                        )
                    };
                }"""
            )
            _check_probe_deadline(monotonic_deadline)
            if raw_inventory and raw_inventory.get("visible"):
                inventory.append(_sanitize_inventory_element(raw_inventory))
        frame_results.append(
            {
                "index": index,
                "name": (
                    "main"
                    if scope is page
                    else _clean_text(getattr(scope, "name", ""))
                ),
                "url": sanitize_url(getattr(scope, "url", page.url)),
                "elements": elements,
                "inventory": inventory,
                "document": _snapshot_scope_structure(scope),
            }
        )
    _check_probe_deadline(monotonic_deadline)
    return {
        "schema_version": 1,
        "captured_at": datetime.now().isoformat(),
        "url": sanitize_url(page.url),
        "frames": frame_results,
    }


def _instance_from_environment(company: str) -> Instance:
    upper = company.upper()
    url_key = f"ROUTERBOX_{upper}_URL"
    password_key = (
        "ROUTERBOX_LOGA_PASS"
        if upper == "LOGA"
        else "ROUTERBOX_PASS"
    )
    values = {
        "url": os.environ.get(url_key, ""),
        "user": os.environ.get("ROUTERBOX_USER", ""),
        "password": os.environ.get(password_key, ""),
    }
    if not all(values.values()):
        raise CollectionError("PROBE_CONFIG_MISSING")
    return Instance(name=upper, **values)


def _browser_launch_options() -> dict:
    return {
        "headless": True,
        "channel": "chrome",
        "args": list(CONTAINER_CHROMIUM_ARGS),
    }


def _live_snapshot(
    company: str,
    *,
    step: str = "personalizados",
    timeout_seconds: int = 75,
    monotonic_deadline: float | None = None,
) -> dict:
    from playwright.sync_api import sync_playwright

    instance = _instance_from_environment(company)
    monotonic_deadline = (
        monotonic_deadline
        if monotonic_deadline is not None
        else time.monotonic() + timeout_seconds
    )
    _check_probe_deadline(monotonic_deadline)
    deadline = datetime.now() + timedelta(
        milliseconds=_remaining_probe_ms(monotonic_deadline)
    )
    with sync_playwright() as playwright:
        _check_probe_deadline(monotonic_deadline)
        browser = playwright.chromium.launch(**_browser_launch_options())
        _check_probe_deadline(monotonic_deadline)
        context = browser.new_context()
        _check_probe_deadline(monotonic_deadline)
        page = context.new_page()
        try:
            page.set_default_timeout(
                _remaining_probe_ms(monotonic_deadline)
            )
            prepare_custom_reports(page, instance, deadline)
            _check_probe_deadline(monotonic_deadline)
            _wait_for_probe_render(page, monotonic_deadline=monotonic_deadline)
            page.set_default_timeout(
                _remaining_probe_ms(monotonic_deadline)
            )
            if step == "gerar":
                return snapshot_generate_page(
                    page,
                    monotonic_deadline=monotonic_deadline,
                )
            return snapshot_page(page)
        finally:
            context.close()
            browser.close()


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", required=True, choices=("LOGA", "ACERTA"))
    parser.add_argument(
        "--step",
        required=True,
        choices=("personalizados", "gerar"),
    )
    parser.add_argument("--html", type=Path)
    parser.add_argument("--source-url", default="")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=75)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(raw_argv)
    try:
        if not 60 <= args.timeout_seconds <= 90:
            raise CollectionError("PROBE_TIMEOUT_INVALID")
        if not args.worker:
            worker_argv = list(raw_argv)
            target = args.output
            if args.step == "gerar" and target is None:
                target = (
                    Path("evidence")
                    / "probe"
                    / (
                        f"{args.company.casefold()}-gerar-"
                        f"{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
                    )
                )
                worker_argv.extend(("--output", str(target)))
            worker_argv.append("--worker")
            code, stdout, stderr = _run_supervised_command(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    *worker_argv,
                ],
                timeout_seconds=args.timeout_seconds,
            )
            if code == 2 and stderr.strip() == "PROBE_TIMEOUT":
                if target is not None:
                    target.with_name(
                        f".{target.name}.tmp"
                    ).unlink(missing_ok=True)
            if stdout:
                print(stdout, end="")
            if stderr:
                print(stderr.strip(), file=sys.stderr)
            return code
        monotonic_deadline = time.monotonic() + args.timeout_seconds
        if args.html:
            result = snapshot_html(
                args.html.read_text(encoding="utf-8"),
                source_url=args.source_url,
            )
        else:
            result = _live_snapshot(
                args.company,
                step=args.step,
                timeout_seconds=args.timeout_seconds,
                monotonic_deadline=monotonic_deadline,
            )
        _check_probe_deadline(monotonic_deadline)
        if args.output or args.step == "gerar":
            target = args.output or (
                Path("evidence")
                / "probe"
                / (
                    f"{args.company.casefold()}-gerar-"
                    f"{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
                )
            )
            _write_private_json(
                target,
                result,
                monotonic_deadline=monotonic_deadline,
            )
            print(str(target))
            return 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except CollectionError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    except Exception:
        print("PROBE_FAILED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
