from __future__ import annotations

import json
import time
from typing import Any
import urllib.request
from urllib.parse import quote, urlparse


class CdpSession:
    def __init__(self, ws_url: str, timeout: float = 10.0):
        self.ws_url = ws_url
        self.timeout = timeout
        self._ws = None
        self._message_id = 0

    def __enter__(self):
        try:
            import websocket
        except ImportError as exc:
            raise RuntimeError("websocket-client is required for direct CDP automation.") from exc
        self._ws = websocket.create_connection(self.ws_url, timeout=self.timeout, suppress_origin=True)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._ws is not None:
            try:
                self._ws.close()
            finally:
                self._ws = None
        return False

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        if self._ws is None:
            raise RuntimeError("CDP session is not connected.")
        self._message_id += 1
        message_id = self._message_id
        resolved_timeout = max(float(timeout if timeout is not None else self.timeout), 0.1)
        if hasattr(self._ws, "settimeout"):
            self._ws.settimeout(resolved_timeout)
        self._ws.send(json.dumps({
            "id": message_id,
            "method": method,
            "params": params or {},
        }))
        deadline = time.monotonic() + resolved_timeout
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError(f"Timed out waiting for CDP {method} response.")
            payload = json.loads(self._ws.recv())
            if payload.get("id") != message_id:
                continue
            if payload.get("error"):
                error = payload["error"]
                raise RuntimeError(f"CDP {method} failed: {error.get('message') or error}")
            return payload.get("result")

    def evaluate(self, expression: str, timeout: float | None = None) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
            },
            timeout=timeout,
        ) or {}
        if result.get("exceptionDetails"):
            text = result["exceptionDetails"].get("text") or "JavaScript exception"
            raise RuntimeError(f"CDP evaluate exception: {text}")
        return (result.get("result") or {}).get("value")


def open_cdp_session(ws_url: str, timeout: float = 10.0) -> CdpSession:
    return CdpSession(ws_url=ws_url, timeout=timeout)


def cdp_targets(endpoint: str, timeout: float = 5.0) -> list[dict[str, Any]]:
    url = f"{endpoint.rstrip('/')}/json/list"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def select_cdp_page_target(endpoint: str, preferred_url: str = "", page_id: str | None = None) -> dict[str, Any]:
    pages = [
        target for target in cdp_targets(endpoint)
        if target.get("type") == "page"
        and target.get("webSocketDebuggerUrl")
        and not str(target.get("url") or "").startswith(("devtools://", "chrome://", "edge://"))
    ]
    if not pages:
        raise RuntimeError(f"No debuggable page target found at {endpoint!r}.")
    return min(pages, key=lambda target: _target_priority(target, preferred_url=preferred_url, page_id=page_id))


def cdp_create_page_target(endpoint: str, url: str = "about:blank", timeout: float = 5.0) -> dict[str, Any]:
    target_url = str(url or "about:blank")
    request_url = f"{endpoint.rstrip('/')}/json/new?{quote(target_url, safe='')}"
    request = urllib.request.Request(request_url, method="PUT")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"CDP create target at {endpoint!r} returned an unexpected payload.")
    if not payload.get("id") or not payload.get("webSocketDebuggerUrl"):
        raise RuntimeError(f"CDP create target at {endpoint!r} did not return a debuggable page target.")
    return payload


def cdp_close_page_target(endpoint: str, target_id: str, timeout: float = 5.0) -> dict[str, Any]:
    resolved_target_id = str(target_id or "").strip()
    if not resolved_target_id:
        raise ValueError("target_id is required.")
    request_url = f"{endpoint.rstrip('/')}/json/close/{quote(resolved_target_id, safe='')}"
    with urllib.request.urlopen(request_url, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
    return {
        "ok": True,
        "target_id": resolved_target_id,
        "response": body,
    }


def js_call(function_source: str, *args: Any) -> str:
    encoded_args = ", ".join(json.dumps(arg) for arg in args)
    return f"({function_source})({encoded_args})"


def cdp_page_info(cdp: Any) -> dict[str, Any]:
    value = cdp.evaluate("(() => ({ href: String(location.href), title: String(document.title || ''), ready: String(document.readyState || '') }))()")
    return value if isinstance(value, dict) else {}


def cdp_navigate(
    endpoint: str,
    url: str,
    preferred_url: str = "",
    page_id: str | None = None,
    wait_ms: int = 10000,
    new_tab_if_needed: bool = False,
    force_new_tab: bool = False,
) -> dict[str, Any]:
    created_target = False
    if force_new_tab and url:
        selected = cdp_create_page_target(endpoint, url=url, timeout=max(int(wait_ms), 1000) / 1000)
        created_target = True
    else:
        try:
            selected = select_cdp_page_target(endpoint, preferred_url=preferred_url or url, page_id=page_id)
        except RuntimeError:
            if not new_tab_if_needed or not url:
                raise
            selected = cdp_create_page_target(endpoint, url=url, timeout=max(int(wait_ms), 1000) / 1000)
            created_target = True
        if new_tab_if_needed and url and not created_target and not _target_matches_url_context(selected, url):
            selected = cdp_create_page_target(endpoint, url=url, timeout=max(int(wait_ms), 1000) / 1000)
            created_target = True
    target_id = str(selected.get("id") or page_id or "")
    ws_url = str(selected.get("webSocketDebuggerUrl") or "")
    if not ws_url:
        raise RuntimeError(f"Selected CDP target at {endpoint!r} has no webSocketDebuggerUrl.")
    started_at = time.monotonic()
    with open_cdp_session(ws_url, timeout=max(int(wait_ms), 1000) / 1000) as cdp:
        if not created_target and url and _normalize_url_for_match(str(selected.get("url") or "")) != _normalize_url_for_match(url):
            cdp.call("Page.navigate", {"url": url})
        page_info = _wait_for_page(cdp, url, wait_ms=wait_ms)
    return {
        "ok": True,
        "url": page_info.get("href") or url or selected.get("url"),
        "title": page_info.get("title") or selected.get("title"),
        "ready": page_info.get("ready"),
        "page_id": target_id,
        "cdp_target_id": target_id,
        "cdp_endpoint": endpoint.rstrip("/"),
        "cdp_direct": True,
        "navigated": _normalize_url_for_match(page_info.get("href") or "") == _normalize_url_for_match(url) if url else True,
        "created_target": created_target,
        "force_new_tab": bool(force_new_tab),
        "navigation_waited_ms": int((time.monotonic() - started_at) * 1000),
    }


def _wait_for_page(cdp: Any, url: str, wait_ms: int) -> dict[str, Any]:
    deadline = time.monotonic() + max(int(wait_ms), 0) / 1000
    expected = _normalize_url_for_match(url)
    last_info: dict[str, Any] = {}
    while True:
        last_info = cdp_page_info(cdp)
        current = _normalize_url_for_match(str(last_info.get("href") or ""))
        ready = str(last_info.get("ready") or "")
        if (not expected or current == expected) and ready in {"interactive", "complete"}:
            return last_info
        if time.monotonic() >= deadline:
            return last_info
        time.sleep(0.2)


def _target_priority(target: dict[str, Any], preferred_url: str = "", page_id: str | None = None) -> int:
    target_id = str(target.get("id") or "")
    target_url = str(target.get("url") or "")
    if page_id and target_id == str(page_id):
        return 0
    if not preferred_url:
        return 10
    normalized_target = _normalize_url_for_match(target_url)
    normalized_preferred = _normalize_url_for_match(preferred_url)
    if normalized_target and normalized_target == normalized_preferred:
        return 1
    target_parts = urlparse(target_url)
    preferred_parts = urlparse(preferred_url)
    if same_social_host(target_parts.hostname, preferred_parts.hostname):
        if target_parts.path == preferred_parts.path:
            return 2
        return 3
    return 20


def _target_matches_url_context(target: dict[str, Any], url: str) -> bool:
    target_url = str(target.get("url") or "")
    if not target_url or target_url == "about:blank":
        return True
    normalized_target = _normalize_url_for_match(target_url)
    normalized_url = _normalize_url_for_match(url)
    if normalized_target and normalized_target == normalized_url:
        return True
    target_parts = urlparse(target_url)
    url_parts = urlparse(url)
    return same_social_host(target_parts.hostname, url_parts.hostname)


def _normalize_url_for_match(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    query = parsed.query
    return f"{parsed.scheme.lower()}://{host}{path}?{query}" if query else f"{parsed.scheme.lower()}://{host}{path}"


def same_social_host(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    aliases = {
        "twitter.com": "x.com",
        "www.twitter.com": "x.com",
        "www.x.com": "x.com",
        "m.x.com": "x.com",
        "youtu.be": "youtube.com",
        "www.youtube.com": "youtube.com",
        "m.youtube.com": "youtube.com",
        "studio.youtube.com": "studio.youtube.com",
        "www.tiktok.com": "tiktok.com",
        "m.tiktok.com": "tiktok.com",
        "www.instagram.com": "instagram.com",
    }
    left_key = aliases.get(left.lower(), left.lower())
    right_key = aliases.get(right.lower(), right.lower())
    return left_key == right_key


__all__ = [
    "CdpSession",
    "cdp_close_page_target",
    "cdp_create_page_target",
    "cdp_navigate",
    "cdp_page_info",
    "cdp_targets",
    "js_call",
    "open_cdp_session",
    "same_social_host",
    "select_cdp_page_target",
]
