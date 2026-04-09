from __future__ import annotations

import re
import time
import uuid
from collections import deque
from typing import Any

from .helpers import wait_until
from .state import OBSERVED_PLAYWRIGHT_PAGES, PLAYWRIGHT_SESSIONS, PLAYWRIGHT_SESSIONS_LOCK, SESSION_MAX_AGE_MINUTES


def store_playwright_session(session: dict[str, Any]) -> None:
    session_id = str(session["session_id"])
    with PLAYWRIGHT_SESSIONS_LOCK:
        PLAYWRIGHT_SESSIONS[session_id] = session


def list_playwright_sessions() -> list[tuple[str, dict[str, Any]]]:
    with PLAYWRIGHT_SESSIONS_LOCK:
        return list(PLAYWRIGHT_SESSIONS.items())


def close_playwright_session(session_id: str) -> bool:
    with PLAYWRIGHT_SESSIONS_LOCK:
        session = PLAYWRIGHT_SESSIONS.pop(session_id, None)
    if not session:
        return False
    try:
        session["context"].close()
    except Exception:
        pass
    try:
        session["browser"].close()
    except Exception:
        pass
    try:
        session["playwright_cm"].__exit__(None, None, None)
    except Exception:
        pass
    return True


def touch_playwright_session(session: dict[str, Any]) -> None:
    with PLAYWRIGHT_SESSIONS_LOCK:
        session["last_used_at"] = time.time()


def playwright_session_age_seconds(session: dict[str, Any]) -> float:
    last_used_at = session.get("last_used_at", session.get("created_at", time.time()))
    return max(0.0, time.time() - float(last_used_at))


def cleanup_stale_playwright_sessions(max_age_minutes: float = SESSION_MAX_AGE_MINUTES) -> dict[str, Any]:
    max_age_seconds = max(float(max_age_minutes), 0.0) * 60
    items = list_playwright_sessions()
    checked = len(items)
    closed_sessions: list[str] = []
    for session_id, session in items:
        if playwright_session_age_seconds(session) <= max_age_seconds:
            continue
        if close_playwright_session(session_id):
            closed_sessions.append(session_id)
    return {
        "checked": checked,
        "closed": len(closed_sessions),
        "closed_session_ids": closed_sessions,
        "max_age_minutes": max(float(max_age_minutes), 0.0),
    }


def close_all_playwright_sessions() -> dict[str, Any]:
    session_ids = [session_id for session_id, _ in list_playwright_sessions()]
    closed_session_ids: list[str] = []
    for session_id in session_ids:
        if close_playwright_session(session_id):
            closed_session_ids.append(session_id)
    return {
        "checked": len(session_ids),
        "closed": len(closed_session_ids),
        "closed_session_ids": closed_session_ids,
    }


def get_playwright_session(session_id: str) -> dict[str, Any]:
    cleanup_stale_playwright_sessions()
    with PLAYWRIGHT_SESSIONS_LOCK:
        session = PLAYWRIGHT_SESSIONS.get(session_id)
        if not session:
            raise ValueError(
                f"Unknown browser session: {session_id}. "
                "The session may have expired, been cleaned up, or never existed."
            )
        session["last_used_at"] = time.time()
        return session


def page_title(page: Any) -> str:
    try:
        return page.title()
    except Exception:
        return ""


def _page_event_buffers(session: dict[str, Any], page_id: str) -> dict[str, Any]:
    with PLAYWRIGHT_SESSIONS_LOCK:
        buffers = session.setdefault("page_event_buffers", {})
        if page_id not in buffers:
            buffers[page_id] = {
                "console": deque(maxlen=100),
                "page_errors": deque(maxlen=50),
                "request_failures": deque(maxlen=50),
                "requests": deque(maxlen=200),
                "responses": deque(maxlen=200),
                "dialogs": deque(maxlen=25),
                "downloads": deque(maxlen=25),
                "request_ids": {},
            }
        return buffers[page_id]


def attach_playwright_page_observers(session: dict[str, Any], page_id: str, page: Any) -> None:
    with PLAYWRIGHT_SESSIONS_LOCK:
        if page in OBSERVED_PLAYWRIGHT_PAGES:
            return
        OBSERVED_PLAYWRIGHT_PAGES.add(page)
        buffers = _page_event_buffers(session, page_id)

    def on_console(message: Any) -> None:
        try:
            entry = {"type": message.type, "text": message.text, "location": message.location}
        except Exception:
            entry = {"type": "unknown", "text": str(message), "location": {}}
        buffers["console"].append(entry)

    def on_page_error(error: Any) -> None:
        buffers["page_errors"].append({"message": str(error)})

    def on_request_failed(request: Any) -> None:
        failure = getattr(request, "failure", None)
        error_text = None
        if callable(failure):
            try:
                failure_info = failure()
                error_text = failure_info.get("errorText") if isinstance(failure_info, dict) else str(failure_info)
            except Exception:
                error_text = None
        request_id = buffers["request_ids"].get(id(request))
        buffers["request_failures"].append(
            {
                "request_id": request_id,
                "url": getattr(request, "url", ""),
                "method": getattr(request, "method", ""),
                "resource_type": getattr(request, "resource_type", None),
                "error_text": error_text,
            }
        )

    def on_request(request: Any) -> None:
        request_id = uuid.uuid4().hex[:12]
        buffers["request_ids"][id(request)] = request_id
        try:
            headers = dict(request.headers)
        except Exception:
            headers = {}
        buffers["requests"].append(
            {
                "request_id": request_id,
                "url": getattr(request, "url", ""),
                "method": getattr(request, "method", ""),
                "resource_type": getattr(request, "resource_type", None),
                "headers": headers,
                "post_data": getattr(request, "post_data", None),
                "timestamp": time.time(),
            }
        )

    def on_response(response: Any) -> None:
        try:
            request = response.request
        except Exception:
            request = None
        request_id = buffers["request_ids"].get(id(request)) if request is not None else None
        try:
            headers = dict(response.headers)
        except Exception:
            headers = {}
        buffers["responses"].append(
            {
                "request_id": request_id,
                "url": getattr(response, "url", ""),
                "status": getattr(response, "status", None),
                "status_text": getattr(response, "status_text", None),
                "ok": bool(response.ok) if hasattr(response, "ok") else None,
                "headers": headers,
                "timestamp": time.time(),
            }
        )

    def on_dialog(dialog: Any) -> None:
        entry = {
            "type": getattr(dialog, "type", "unknown"),
            "message": getattr(dialog, "message", ""),
            "default_value": getattr(dialog, "default_value", ""),
            "handled": "dismissed",
        }
        buffers["dialogs"].append(entry)
        try:
            dialog.dismiss()
        except Exception:
            entry["handled"] = "failed"

    def on_download(download: Any) -> None:
        from .paths import DOWNLOAD_ROOT

        session_id = session.get("session_id", "session")
        target_dir = DOWNLOAD_ROOT / str(session_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = getattr(download, "suggested_filename", "download.bin")
        target_path = target_dir / filename
        entry = {
            "url": getattr(download, "url", ""),
            "suggested_filename": filename,
            "path": str(target_path),
            "saved": False,
        }
        try:
            download.save_as(str(target_path))
            entry["saved"] = target_path.exists()
        except Exception as exc:
            entry["error"] = str(exc)
        buffers["downloads"].append(entry)

    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    page.on("request", on_request)
    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)
    page.on("dialog", on_dialog)
    page.on("download", on_download)


def get_playwright_page_event_buffers(session: dict[str, Any], page_id: str) -> dict[str, list[dict[str, Any]]]:
    buffers = _page_event_buffers(session, page_id)
    return {
        "console": list(buffers["console"]),
        "page_errors": list(buffers["page_errors"]),
        "request_failures": list(buffers["request_failures"]),
        "requests": list(buffers["requests"]),
        "responses": list(buffers["responses"]),
        "dialogs": list(buffers["dialogs"]),
        "downloads": list(buffers["downloads"]),
    }


def register_playwright_page(session: dict[str, Any], page: Any, make_active: bool = True) -> str:
    with PLAYWRIGHT_SESSIONS_LOCK:
        existing_pages = session.setdefault("pages", {})
        for page_id, existing in existing_pages.items():
            if existing is page:
                attach_playwright_page_observers(session, page_id, page)
                if make_active:
                    session["active_page_id"] = page_id
                session["last_used_at"] = time.time()
                return page_id
        page_id = uuid.uuid4().hex[:10]
        existing_pages[page_id] = page
        attach_playwright_page_observers(session, page_id, page)
        if make_active or not session.get("active_page_id"):
            session["active_page_id"] = page_id
        session["last_used_at"] = time.time()
        return page_id


def refresh_playwright_pages(session: dict[str, Any]) -> None:
    current_pages = [page for page in session["context"].pages if not page.is_closed()]
    with PLAYWRIGHT_SESSIONS_LOCK:
        pages = session.setdefault("pages", {})
    for page in current_pages:
        register_playwright_page(session, page, make_active=False)
    with PLAYWRIGHT_SESSIONS_LOCK:
        for page_id, page in list(pages.items()):
            if page.is_closed() or page not in current_pages:
                pages.pop(page_id, None)
        if session.get("active_page_id") not in pages:
            session["active_page_id"] = next(iter(pages), None)
        session["last_used_at"] = time.time()


def playwright_page_info(page_id: str, page: Any) -> dict[str, Any]:
    return {"page_id": page_id, "url": page.url, "title": page_title(page), "closed": page.is_closed()}


def get_playwright_page(session_id: str, page_id: str | None = None) -> tuple[dict[str, Any], str, Any]:
    session = get_playwright_session(session_id)
    refresh_playwright_pages(session)
    with PLAYWRIGHT_SESSIONS_LOCK:
        resolved_page_id = page_id or session.get("active_page_id")
        if not resolved_page_id:
            raise ValueError(f"Browser session {session_id} has no open pages.")
        page = session["pages"].get(resolved_page_id)
        if not page or page.is_closed():
            raise ValueError(f"Unknown or closed page: {resolved_page_id}")
        session["active_page_id"] = resolved_page_id
    return session, resolved_page_id, page


def _route_handler_for_rule(rule: dict[str, Any]):
    action = rule["action"]
    resource_types = set(rule.get("resource_types") or [])
    methods = {method.upper() for method in (rule.get("methods") or [])}

    def handler(route, request) -> None:
        if resource_types and request.resource_type not in resource_types:
            route.continue_()
            return
        if methods and request.method.upper() not in methods:
            route.continue_()
            return
        if action == "abort":
            route.abort(rule.get("abort_error_code") or "failed")
            return
        if action == "fulfill":
            route.fulfill(
                status=int(rule.get("status", 200)),
                headers=rule.get("headers") or {},
                body=rule.get("body") or "",
                content_type=rule.get("content_type"),
            )
            return
        overrides = {}
        if rule.get("url_override"):
            overrides["url"] = rule["url_override"]
        if rule.get("method_override"):
            overrides["method"] = rule["method_override"]
        if rule.get("headers"):
            merged_headers = dict(request.headers)
            merged_headers.update(rule["headers"])
            overrides["headers"] = merged_headers
        if rule.get("post_data") is not None:
            overrides["post_data"] = rule["post_data"]
        route.continue_(**overrides)

    return handler


def apply_playwright_intercepts(session: dict[str, Any]) -> dict[str, Any]:
    context = session["context"]
    with PLAYWRIGHT_SESSIONS_LOCK:
        existing = list(session.get("route_handlers", []))
    for item in existing:
        try:
            context.unroute(item["pattern"], item["handler"])
        except Exception:
            pass
    applied: list[dict[str, Any]] = []
    for rule in session.get("intercept_rules", []):
        handler = _route_handler_for_rule(rule)
        context.route(rule["pattern"], handler)
        applied.append({"rule_id": rule["rule_id"], "pattern": rule["pattern"], "handler": handler})
    with PLAYWRIGHT_SESSIONS_LOCK:
        session["route_handlers"] = applied
    return {
        "session_id": session.get("session_id"),
        "applied": len(applied),
        "rule_ids": [item["rule_id"] for item in applied],
    }


def wait_for_url_pattern(page: Any, pattern: str, timeout_ms: int = 10000) -> str:
    regex = re.compile(pattern)
    deadline = time.time() + (max(timeout_ms, 1) / 1000)

    def predicate():
        return page.url if regex.search(page.url) else None

    matched_url = wait_until(deadline, 0.1, predicate, description=f"URL pattern {pattern!r}")
    if not matched_url:
        raise ValueError(f"Timed out waiting for URL pattern {pattern!r}; current URL is {page.url!r}.")
    return matched_url
