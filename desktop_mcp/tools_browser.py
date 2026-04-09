"""Legacy browser compatibility shim.

This module is intentionally not imported by the MCP server. The active server
surface lives under desktop_mcp.tools.*. Keep this module only for
backwards-compatible direct Python imports during the transition.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Image

from .app import mcp
from .browser_core import (
    apply_playwright_intercepts,
    browser_candidates,
    build_browser_screenshot_args,
    cleanup_stale_browser_profiles,
    cleanup_stale_playwright_sessions,
    close_playwright_session,
    dump_dom_fallback,
    get_playwright_page,
    get_playwright_page_event_buffers,
    get_playwright_session,
    launch_browser_process,
    list_playwright_sessions,
    new_browser_window_info,
    open_exact_playwright_page,
    open_playwright_runtime,
    page_title,
    playwright_page_info,
    playwright_session_age_seconds,
    refresh_playwright_pages,
    register_playwright_page,
    run_browser_command,
    store_playwright_session,
    wait_for_url_pattern,
)
from .helpers import now_stamp
from .paths import BROWSER_CAPTURE_ROOT
from .shared.playwright_utils import ensure_dom_revision_tracker, validate_js_expression, visual_signature
from .state import SESSION_MAX_AGE_MINUTES
from .tools.capture import capture_window, save_window_screenshot
from .tools.windows import focus_window, list_windows, move_resize_window
from .desktop_core import find_window
from .runtime import record_event, tool_log

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
except Exception:  # pragma: no cover - optional dependency at import time
    PlaywrightTimeoutError = TimeoutError


def _headless_capture(
    url: str,
    width: int,
    height: int,
    wait_ms: int,
    browser: str,
    output_path: Path,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = build_browser_screenshot_args(browser, url, width, height, wait_ms, output_path)
    result = run_browser_command(browser, args, include_firefox=True)
    return {
        "path": str(output_path),
        "url": url,
        "browser": result["browser"],
        "executable": result["executable"],
        "width": max(width, 320),
        "height": max(height, 240),
        "wait_ms": max(wait_ms, 0),
    }


def browser_capture_page(url: str, width: int = 1440, height: int = 960, wait_ms: int = 1200, browser: str = "auto", path: str | None = None) -> Image:
    from .tools.browser_headless import browser_capture_page as headless_browser_capture_page

    return headless_browser_capture_page(url=url, width=width, height=height, wait_ms=wait_ms, browser=browser, path=path)


def browser_save_screenshot(url: str, width: int = 1440, height: int = 960, wait_ms: int = 1200, browser: str = "auto", prefix: str = "browser") -> dict[str, Any]:
    from .tools.browser_headless import browser_save_screenshot as headless_browser_save_screenshot

    return headless_browser_save_screenshot(url=url, width=width, height=height, wait_ms=wait_ms, browser=browser, prefix=prefix)


def browser_dump_dom(url: str, wait_ms: int = 1200, browser: str = "auto") -> dict[str, Any]:
    from .tools.browser_headless import browser_dump_dom as headless_browser_dump_dom

    return headless_browser_dump_dom(url=url, wait_ms=wait_ms, browser=browser)


def browser_capture_page_exact(url: str, width: int = 1440, height: int = 960, browser: str = "auto", path: str | None = None, full_page: bool = True) -> dict[str, Any]:
    from .tools.browser_headless import browser_capture_page_exact as headless_browser_capture_page_exact

    return headless_browser_capture_page_exact(url=url, width=width, height=height, browser=browser, path=path, full_page=full_page)


def browser_dump_dom_exact(url: str, width: int = 1440, height: int = 960, browser: str = "auto") -> dict[str, Any]:
    from .tools.browser_headless import browser_dump_dom_exact as headless_browser_dump_dom_exact

    return headless_browser_dump_dom_exact(url=url, width=width, height=height, browser=browser)


def browser_open_session(
    url: str,
    width: int | str = 1440,
    height: int | str = 960,
    browser: str = "auto",
    headless: bool = True,
) -> dict[str, Any]:
    cleanup_stale_playwright_sessions()
    session_id: str | None = None
    playwright_cm, runtime, engine, actual_browser = open_playwright_runtime(browser, headless=headless)
    try:
        if isinstance(width, str) and width.lower() == "auto":
            width = pyautogui.size().width
        if isinstance(height, str) and height.lower() == "auto":
            height = pyautogui.size().height
        resolved_width = max(int(width), 320)
        resolved_height = max(int(height), 240)
        context = engine.new_context(
            viewport={"width": resolved_width, "height": resolved_height},
            device_scale_factor=1,
            accept_downloads=True,
        )
        page = context.new_page()
        session_id = uuid.uuid4().hex[:12]
        session = {
            "session_id": session_id,
            "playwright_cm": playwright_cm,
            "runtime": runtime,
            "browser": engine,
            "context": context,
            "browser_name": actual_browser,
            "headless": headless,
            "pages": {},
            "intercept_rules": [],
            "route_handlers": [],
            "active_page_id": None,
            "created_at": time.time(),
            "last_used_at": time.time(),
        }
        store_playwright_session(session)
        page_id = register_playwright_page(session, page, make_active=True)
        page.goto(url, wait_until="networkidle")
        record_event("browser_open_session", session_id=session_id, page_id=page_id, url=page.url, browser=actual_browser, headless=headless)
        return {
            "session_id": session_id,
            "page_id": page_id,
            "url": page.url,
            "title": page.title(),
            "browser": actual_browser,
            "headless": headless,
            "width": resolved_width,
            "height": resolved_height,
        }
    except Exception as exc:
        record_event("browser_open_session_error", url=url, browser=browser, headless=headless, error=str(exc))
        if session_id:
            close_playwright_session(session_id)
        else:
            try:
                engine.close()
            finally:
                playwright_cm.__exit__(None, None, None)
        raise


def browser_list_sessions() -> list[dict[str, Any]]:
    from .tools.browser_sessions import browser_list_sessions as sessions_browser_list_sessions

    return sessions_browser_list_sessions()


def browser_cleanup_sessions(max_age_minutes: float = SESSION_MAX_AGE_MINUTES) -> dict[str, Any]:
    from .tools.browser_sessions import browser_cleanup_sessions as sessions_browser_cleanup_sessions

    return sessions_browser_cleanup_sessions(max_age_minutes=max_age_minutes)


def browser_cleanup_profiles(max_age_hours: float = 12) -> dict[str, Any]:
    from .tools.browser_sessions import browser_cleanup_profiles as sessions_browser_cleanup_profiles

    return sessions_browser_cleanup_profiles(max_age_hours=max_age_hours)


@mcp.tool()
def browser_intercept_requests(
    session_id: str,
    pattern: str = "**/*",
    action: str = "continue",
    resource_types: list[str] | None = None,
    methods: list[str] | None = None,
    status: int = 200,
    body: str | None = None,
    headers: dict[str, str] | None = None,
    content_type: str | None = None,
    abort_error_code: str = "failed",
    url_override: str | None = None,
    method_override: str | None = None,
    post_data: str | None = None,
) -> dict[str, Any]:
    session = get_playwright_session(session_id)
    resolved_action = (action or "continue").lower()
    if resolved_action not in {"continue", "abort", "fulfill"}:
        raise ValueError("Use action='continue', 'abort', or 'fulfill'.")
    rule = {
        "rule_id": uuid.uuid4().hex[:10],
        "pattern": pattern or "**/*",
        "action": resolved_action,
        "resource_types": [item.strip().lower() for item in (resource_types or []) if str(item).strip()],
        "methods": [item.strip().upper() for item in (methods or []) if str(item).strip()],
        "status": max(int(status), 100),
        "body": body,
        "headers": headers or {},
        "content_type": content_type,
        "abort_error_code": abort_error_code,
        "url_override": url_override,
        "method_override": method_override.upper() if method_override else None,
        "post_data": post_data,
    }
    session.setdefault("intercept_rules", []).append(rule)
    applied = apply_playwright_intercepts(session)
    result = {
        "session_id": session_id,
        "rule_id": rule["rule_id"],
        "pattern": rule["pattern"],
        "action": rule["action"],
        "applied": applied["applied"],
    }
    record_event("browser_intercept_requests", **result)
    return result


@mcp.tool()
def browser_list_intercepts(session_id: str) -> dict[str, Any]:
    session = get_playwright_session(session_id)
    rules = []
    for rule in session.get("intercept_rules", []):
        rules.append({key: value for key, value in rule.items() if key not in {"body"}} | {"body_length": len(rule["body"]) if rule.get("body") else 0})
    return {"session_id": session_id, "count": len(rules), "rules": rules}


@mcp.tool()
def browser_clear_intercepts(session_id: str) -> dict[str, Any]:
    session = get_playwright_session(session_id)
    cleared = len(session.get("intercept_rules", []))
    session["intercept_rules"] = []
    applied = apply_playwright_intercepts(session)
    result = {"session_id": session_id, "cleared": cleared, "applied": applied["applied"]}
    record_event("browser_clear_intercepts", **result)
    return result


def browser_list_pages(session_id: str) -> dict[str, Any]:
    from .tools.browser_sessions import browser_list_pages as sessions_browser_list_pages

    return sessions_browser_list_pages(session_id=session_id)


def browser_new_page(session_id: str, url: str = "about:blank", wait_until: str = "load", make_active: bool = True) -> dict[str, Any]:
    from .tools.browser_sessions import browser_new_page as sessions_browser_new_page

    return sessions_browser_new_page(session_id=session_id, url=url, wait_until=wait_until, make_active=make_active)


def browser_switch_page(session_id: str, page_id: str) -> dict[str, Any]:
    from .tools.browser_sessions import browser_switch_page as sessions_browser_switch_page

    return sessions_browser_switch_page(session_id=session_id, page_id=page_id)


def browser_close_page(session_id: str, page_id: str) -> dict[str, Any]:
    from .tools.browser_sessions import browser_close_page as sessions_browser_close_page

    return sessions_browser_close_page(session_id=session_id, page_id=page_id)


def browser_close_session(session_id: str) -> dict[str, Any]:
    from .tools.browser_sessions import browser_close_session as sessions_browser_close_session

    return sessions_browser_close_session(session_id=session_id)


def browser_navigate(session_id: str, url: str, wait_until: str = "networkidle", page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_navigate as sessions_browser_navigate

    return sessions_browser_navigate(session_id=session_id, url=url, wait_until=wait_until, page_id=page_id)


def browser_capture_session(session_id: str, path: str | None = None, full_page: bool = False, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_capture_session as sessions_browser_capture_session

    return sessions_browser_capture_session(session_id=session_id, path=path, full_page=full_page, page_id=page_id)


@mcp.tool()
def browser_capture_element(session_id: str, selector: str, path: str | None = None, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    target_path = Path(path) if path else BROWSER_CAPTURE_ROOT / f"element-{session_id}-{resolved_page_id}-{now_stamp()}.png"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    locator = page.locator(selector).first
    locator.wait_for(timeout=max(timeout_ms, 1), state="visible")
    locator.screenshot(path=str(target_path))
    result = {"session_id": session_id, "page_id": resolved_page_id, "selector": selector, "path": str(target_path), "url": page.url}
    record_event("browser_capture_element", **result)
    return result


def browser_get_dom(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_get_dom as sessions_browser_get_dom

    return sessions_browser_get_dom(session_id=session_id, page_id=page_id)


def browser_get_viewport_state(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_get_viewport_state as sessions_browser_get_viewport_state

    return sessions_browser_get_viewport_state(session_id=session_id, page_id=page_id)


@mcp.tool()
def browser_snapshot_state(session_id: str, page_id: str | None = None, selector: str = "body", path: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    html = page.content()
    dom_hash = hashlib.sha256(html.encode("utf-8", errors="replace")).hexdigest()
    viewport_state = page.evaluate(
        """() => ({
            url: window.location.href,
            title: document.title,
            scrollX: window.scrollX,
            scrollY: window.scrollY,
            innerWidth: window.innerWidth,
            innerHeight: window.innerHeight,
            documentWidth: document.documentElement.scrollWidth,
            documentHeight: document.documentElement.scrollHeight,
            readyState: document.readyState,
        })"""
    )
    target_path = Path(path) if path else BROWSER_CAPTURE_ROOT / f"snapshot-{session_id}-{resolved_page_id}-{now_stamp()}.png"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    locator = page.locator(selector).first
    locator.wait_for(timeout=10000, state="visible")
    locator.screenshot(path=str(target_path))
    image_hash = hashlib.sha256(target_path.read_bytes()).hexdigest()
    return {
        "session_id": session_id,
        "page_id": resolved_page_id,
        "selector": selector,
        "path": str(target_path),
        "dom_hash": dom_hash,
        "image_hash": image_hash,
        **viewport_state,
    }


@mcp.tool()
def browser_list_interactive_elements(session_id: str, page_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    elements = page.evaluate(
        """() => {
            const selector = [
                'a[href]',
                'button',
                'input',
                'select',
                'textarea',
                '[role="button"]',
                '[role="link"]',
                '[role="tab"]',
                '[role="menuitem"]',
                '[tabindex]:not([tabindex="-1"])'
            ].join(',');

            const textOf = (el) => (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 180);
            const cssPath = (el) => {
                if (el.id) return `#${el.id}`;
                const parts = [];
                let current = el;
                while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 4) {
                    let part = current.tagName.toLowerCase();
                    if (current.classList.length) {
                        part += '.' + Array.from(current.classList).slice(0, 2).join('.');
                    }
                    const parent = current.parentElement;
                    if (parent) {
                        const siblings = Array.from(parent.children).filter((child) => child.tagName === current.tagName);
                        if (siblings.length > 1) {
                            part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
                        }
                    }
                    parts.unshift(part);
                    current = parent;
                }
                return parts.join(' > ');
            };

            return Array.from(document.querySelectorAll(selector)).map((el, index) => {
                const rect = el.getBoundingClientRect();
                return {
                    index,
                    tag: el.tagName.toLowerCase(),
                    id: el.id || null,
                    role: el.getAttribute('role'),
                    name: el.getAttribute('aria-label') || textOf(el),
                    text: textOf(el),
                    href: el.getAttribute('href'),
                    type: el.getAttribute('type'),
                    disabled: Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true'),
                    visible: rect.width > 0 && rect.height > 0,
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                    selector_hint: cssPath(el),
                };
            });
        }"""
    )
    bounded = max(1, min(int(limit), 200))
    return {
        "session_id": session_id,
        "page_id": resolved_page_id,
        "count": min(len(elements), bounded),
        "elements": elements[:bounded],
    }


def browser_list_form_fields(session_id: str, page_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    from .tools.browser_sessions import browser_list_form_fields as sessions_browser_list_form_fields

    return sessions_browser_list_form_fields(session_id=session_id, page_id=page_id, limit=limit)


def browser_fill_form_field(session_id: str, index: int, value: str, clear_first: bool = True, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_fill_form_field as sessions_browser_fill_form_field

    return sessions_browser_fill_form_field(session_id=session_id, index=index, value=value, clear_first=clear_first, timeout_ms=timeout_ms, page_id=page_id)


def browser_toggle_form_field(session_id: str, index: int, checked: bool = True, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_toggle_form_field as sessions_browser_toggle_form_field

    return sessions_browser_toggle_form_field(session_id=session_id, index=index, checked=checked, timeout_ms=timeout_ms, page_id=page_id)


def browser_wait_for_selector(session_id: str, selector: str, timeout_ms: int = 10000, state: str = "visible", page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_wait_for_selector as sessions_browser_wait_for_selector

    return sessions_browser_wait_for_selector(session_id=session_id, selector=selector, timeout_ms=timeout_ms, state=state, page_id=page_id)


def browser_wait_for_text(session_id: str, text: str, timeout_ms: int = 10000, exact: bool = False, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_wait_for_text as sessions_browser_wait_for_text

    return sessions_browser_wait_for_text(session_id=session_id, text=text, timeout_ms=timeout_ms, exact=exact, page_id=page_id)


def browser_wait_for_dom_change(session_id: str, timeout_ms: int = 10000, poll_ms: int = 250, baseline_hash: str | None = None, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    before_hash = baseline_hash or hashlib.sha256(page.content().encode("utf-8", errors="replace")).hexdigest()
    baseline_revision = ensure_dom_revision_tracker(page)
    try:
        page.wait_for_function(
            "(baseline) => (window.__pmMcpDomRevision || 0) > baseline",
            arg=baseline_revision,
            timeout=max(timeout_ms, 1),
        )
        after_hash = hashlib.sha256(page.content().encode("utf-8", errors="replace")).hexdigest()
        result = {
            "session_id": session_id,
            "page_id": resolved_page_id,
            "changed": True,
            "before_hash": before_hash,
            "after_hash": after_hash,
        }
        record_event("browser_wait_for_dom_change", **result)
        return result
    except PlaywrightTimeoutError:
        result = {
            "session_id": session_id,
            "page_id": resolved_page_id,
            "changed": False,
            "before_hash": before_hash,
            "after_hash": before_hash,
        }
        record_event("browser_wait_for_dom_change", **result)
        return result


def browser_click_text(session_id: str, text: str, timeout_ms: int = 10000, exact: bool = False, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_click_text as sessions_browser_click_text

    return sessions_browser_click_text(session_id=session_id, text=text, timeout_ms=timeout_ms, exact=exact, page_id=page_id)


def browser_click_text_and_wait_download(session_id: str, text: str, timeout_ms: int = 10000, exact: bool = False, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_click_text_and_wait_download as sessions_browser_click_text_and_wait_download

    return sessions_browser_click_text_and_wait_download(session_id=session_id, text=text, timeout_ms=timeout_ms, exact=exact, page_id=page_id)


def browser_click_interactive(session_id: str, index: int, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_click_interactive as sessions_browser_click_interactive

    return sessions_browser_click_interactive(session_id=session_id, index=index, timeout_ms=timeout_ms, page_id=page_id)


def browser_click_selector(session_id: str, selector: str, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_click_selector as sessions_browser_click_selector

    return sessions_browser_click_selector(session_id=session_id, selector=selector, timeout_ms=timeout_ms, page_id=page_id)


def browser_click_selector_and_wait_download(session_id: str, selector: str, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_click_selector_and_wait_download as sessions_browser_click_selector_and_wait_download

    return sessions_browser_click_selector_and_wait_download(session_id=session_id, selector=selector, timeout_ms=timeout_ms, page_id=page_id)


def browser_type_selector(session_id: str, selector: str, text: str, clear_first: bool = True, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_type_selector as sessions_browser_type_selector

    return sessions_browser_type_selector(session_id=session_id, selector=selector, text=text, clear_first=clear_first, timeout_ms=timeout_ms, page_id=page_id)


def browser_focus_selector(session_id: str, selector: str, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_focus_selector as sessions_browser_focus_selector

    return sessions_browser_focus_selector(session_id=session_id, selector=selector, timeout_ms=timeout_ms, page_id=page_id)


def browser_set_input_files(session_id: str, selector: str, paths: list[str], timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_set_input_files as sessions_browser_set_input_files

    return sessions_browser_set_input_files(session_id=session_id, selector=selector, paths=paths, timeout_ms=timeout_ms, page_id=page_id)


def browser_press_key(session_id: str, key: str, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_press_key as sessions_browser_press_key

    return sessions_browser_press_key(session_id=session_id, key=key, page_id=page_id)


def browser_scroll_page(session_id: str, delta_y: int = 800, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_scroll_page as sessions_browser_scroll_page

    return sessions_browser_scroll_page(session_id=session_id, delta_y=delta_y, page_id=page_id)


def browser_eval(session_id: str, expression: str, page_id: str | None = None) -> dict[str, Any]:
    validate_js_expression(expression)
    from .tools.browser_sessions import browser_eval as sessions_browser_eval

    return sessions_browser_eval(session_id=session_id, expression=expression, page_id=page_id)


def browser_fill_form(session_id: str, fields: list[dict[str, Any]], submit_selector: str | None = None, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    filled: list[dict[str, Any]] = []
    for field in fields:
        selector = str(field["selector"])
        value = field.get("value", "")
        field_type = str(field.get("type", "text")).lower()
        locator = page.locator(selector).first
        if field_type == "text":
            locator.fill(str(value))
        elif field_type == "select":
            locator.select_option(value=str(value))
        elif field_type in {"checkbox", "radio"}:
            desired = bool(field.get("checked", True))
            if locator.is_checked() != desired:
                locator.click()
        else:
            raise ValueError(f"Unsupported field type: {field_type}")
        filled.append({"selector": selector, "type": field_type})
    if submit_selector:
        page.locator(submit_selector).first.click()
    result = {"session_id": session_id, "page_id": resolved_page_id, "filled": filled, "submitted": submit_selector is not None}
    record_event("browser_fill_form", session_id=session_id, page_id=resolved_page_id, field_count=len(filled), submitted=submit_selector is not None)
    return result


def browser_wait_for_load_state(session_id: str, state: str = "networkidle", timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_wait_for_load_state as sessions_browser_wait_for_load_state

    return sessions_browser_wait_for_load_state(session_id=session_id, state=state, timeout_ms=timeout_ms, page_id=page_id)


def browser_wait_for_visual_change(session_id: str, selector: str = "body", timeout_ms: int = 10000, poll_ms: int = 250, baseline_hash: str | None = None, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    locator = page.locator(selector).first
    locator.wait_for(timeout=max(timeout_ms, 1), state="visible")
    before_hash = baseline_hash or hashlib.sha256(locator.screenshot()).hexdigest()
    signature = visual_signature(page, selector)
    try:
        page.wait_for_function(
            """([sel, baseline]) => {
                const el = document.querySelector(sel);
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                const current = JSON.stringify({
                    text: (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 4000),
                    html: (el.innerHTML || '').slice(0, 4000),
                    className: el.className || '',
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    display: style.display,
                    visibility: style.visibility,
                    opacity: style.opacity,
                    color: style.color,
                    backgroundColor: style.backgroundColor,
                    transform: style.transform,
                });
                return current !== baseline;
            }""",
            arg=[selector, signature],
            timeout=max(timeout_ms, 1),
        )
        after_hash = hashlib.sha256(locator.screenshot()).hexdigest()
        result = {
            "session_id": session_id,
            "page_id": resolved_page_id,
            "selector": selector,
            "changed": True,
            "before_hash": before_hash,
            "after_hash": after_hash,
        }
        record_event("browser_wait_for_visual_change", **result)
        return result
    except PlaywrightTimeoutError:
        result = {
            "session_id": session_id,
            "page_id": resolved_page_id,
            "selector": selector,
            "changed": False,
            "before_hash": before_hash,
            "after_hash": before_hash,
        }
        record_event("browser_wait_for_visual_change", **result)
        return result


def browser_reload(session_id: str, wait_until: str = "networkidle", page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_reload as sessions_browser_reload

    return sessions_browser_reload(session_id=session_id, wait_until=wait_until, page_id=page_id)


def browser_go_back(session_id: str, wait_until: str = "networkidle", page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_go_back as sessions_browser_go_back

    return sessions_browser_go_back(session_id=session_id, wait_until=wait_until, page_id=page_id)


def browser_go_forward(session_id: str, wait_until: str = "networkidle", page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_go_forward as sessions_browser_go_forward

    return sessions_browser_go_forward(session_id=session_id, wait_until=wait_until, page_id=page_id)


def browser_wait_for_url(session_id: str, pattern: str, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_wait_for_url as sessions_browser_wait_for_url

    return sessions_browser_wait_for_url(session_id=session_id, pattern=pattern, timeout_ms=timeout_ms, page_id=page_id)


@mcp.tool()
def browser_hover_selector(session_id: str, selector: str, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    page.locator(selector).first.hover(timeout=max(timeout_ms, 1))
    result = {"session_id": session_id, "page_id": resolved_page_id, "selector": selector, "hovered": True}
    record_event("browser_hover_selector", **result)
    return result


def browser_count_selectors(session_id: str, selector: str, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_count_selectors as sessions_browser_count_selectors

    return sessions_browser_count_selectors(session_id=session_id, selector=selector, page_id=page_id)


def browser_get_text(session_id: str, selector: str, timeout_ms: int = 10000, all_matches: bool = False, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_get_text as sessions_browser_get_text

    return sessions_browser_get_text(
        session_id=session_id,
        selector=selector,
        timeout_ms=timeout_ms,
        all_matches=all_matches,
        page_id=page_id,
    )


def browser_get_console_logs(session_id: str, page_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    from .tools.browser_sessions import browser_get_console_logs as sessions_browser_get_console_logs

    return sessions_browser_get_console_logs(session_id=session_id, page_id=page_id, limit=limit)


def browser_get_network_errors(session_id: str, page_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    from .tools.browser_sessions import browser_get_network_errors as sessions_browser_get_network_errors

    return sessions_browser_get_network_errors(session_id=session_id, page_id=page_id, limit=limit)


def browser_list_network_requests(
    session_id: str,
    page_id: str | None = None,
    limit: int = 50,
    include_headers: bool = False,
    method: str | None = None,
    status: int | None = None,
    resource_type: str | None = None,
    url_contains: str | None = None,
    failed_only: bool = False,
    status_min: int | None = None,
    status_max: int | None = None,
    mime_contains: str | None = None,
    has_body: bool | None = None,
    offset: int = 0,
    sort_by: str = "timestamp",
    sort_order: str = "desc",
) -> dict[str, Any]:
    from .tools.browser_sessions import browser_list_network_requests as sessions_browser_list_network_requests

    return sessions_browser_list_network_requests(
        session_id=session_id,
        page_id=page_id,
        limit=limit,
        include_headers=include_headers,
        method=method,
        status=status,
        resource_type=resource_type,
        url_contains=url_contains,
        failed_only=failed_only,
        status_min=status_min,
        status_max=status_max,
        mime_contains=mime_contains,
        has_body=has_body,
        offset=offset,
        sort_by=sort_by,
        sort_order=sort_order,
    )


def browser_get_network_request(session_id: str, request_id: str, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_get_network_request as sessions_browser_get_network_request

    return sessions_browser_get_network_request(session_id=session_id, request_id=request_id, page_id=page_id)


def browser_get_network_summary(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_get_network_summary as sessions_browser_get_network_summary

    return sessions_browser_get_network_summary(session_id=session_id, page_id=page_id)


def browser_export_network_har(
    session_id: str,
    page_id: str | None = None,
    path: str | None = None,
    include_headers: bool = True,
) -> dict[str, Any]:
    from .tools.browser_sessions import browser_export_network_har as sessions_browser_export_network_har

    return sessions_browser_export_network_har(
        session_id=session_id,
        page_id=page_id,
        path=path,
        include_headers=include_headers,
    )


def browser_get_dialogs(session_id: str, page_id: str | None = None, limit: int = 25) -> dict[str, Any]:
    from .tools.browser_sessions import browser_get_dialogs as sessions_browser_get_dialogs

    return sessions_browser_get_dialogs(session_id=session_id, page_id=page_id, limit=limit)


def browser_list_downloads(session_id: str, page_id: str | None = None, limit: int = 25) -> dict[str, Any]:
    from .tools.browser_sessions import browser_list_downloads as sessions_browser_list_downloads

    return sessions_browser_list_downloads(session_id=session_id, page_id=page_id, limit=limit)


def browser_wait_for_download(session_id: str, page_id: str | None = None, timeout_ms: int = 10000, minimum_count: int = 1) -> dict[str, Any]:
    from .tools.browser_sessions import browser_wait_for_download as sessions_browser_wait_for_download

    return sessions_browser_wait_for_download(session_id=session_id, page_id=page_id, timeout_ms=timeout_ms, minimum_count=minimum_count)


def browser_get_page_summary(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_get_page_summary as sessions_browser_get_page_summary

    return sessions_browser_get_page_summary(session_id=session_id, page_id=page_id)


def browser_get_accessibility_snapshot(session_id: str, page_id: str | None = None, interesting_only: bool = True) -> dict[str, Any]:
    from .tools.browser_sessions import browser_get_accessibility_snapshot as sessions_browser_get_accessibility_snapshot

    return sessions_browser_get_accessibility_snapshot(session_id=session_id, page_id=page_id, interesting_only=interesting_only)


def browser_get_performance_metrics(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_get_performance_metrics as sessions_browser_get_performance_metrics

    return sessions_browser_get_performance_metrics(session_id=session_id, page_id=page_id)


def browser_start_trace(
    session_id: str,
    screenshots: bool = True,
    snapshots: bool = True,
    sources: bool = False,
    trace_name: str | None = None,
) -> dict[str, Any]:
    from .tools.browser_sessions import browser_start_trace as sessions_browser_start_trace

    return sessions_browser_start_trace(
        session_id=session_id,
        screenshots=screenshots,
        snapshots=snapshots,
        sources=sources,
        trace_name=trace_name,
    )


def browser_stop_trace(session_id: str, path: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_stop_trace as sessions_browser_stop_trace

    return sessions_browser_stop_trace(session_id=session_id, path=path)


def browser_start_coverage(
    session_id: str,
    page_id: str | None = None,
    include_js: bool = True,
    include_css: bool = True,
    call_count: bool = False,
) -> dict[str, Any]:
    from .tools.browser_sessions import browser_start_coverage as sessions_browser_start_coverage

    return sessions_browser_start_coverage(
        session_id=session_id,
        page_id=page_id,
        include_js=include_js,
        include_css=include_css,
        call_count=call_count,
    )


def browser_stop_coverage(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_stop_coverage as sessions_browser_stop_coverage

    return sessions_browser_stop_coverage(session_id=session_id, page_id=page_id)


def browser_export_coverage_json(
    session_id: str,
    page_id: str | None = None,
    path: str | None = None,
) -> dict[str, Any]:
    from .tools.browser_sessions import browser_export_coverage_json as sessions_browser_export_coverage_json

    return sessions_browser_export_coverage_json(session_id=session_id, page_id=page_id, path=path)


def browser_debug_snapshot(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_debug_snapshot as sessions_browser_debug_snapshot

    return sessions_browser_debug_snapshot(session_id=session_id, page_id=page_id)


def browser_debug_report(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    from .tools.browser_sessions import browser_debug_report as sessions_browser_debug_report

    return sessions_browser_debug_report(session_id=session_id, page_id=page_id)


def browser_debug_bundle(
    session_id: str,
    page_id: str | None = None,
    bundle_dir: str | None = None,
    include_har: bool = True,
    include_trace: bool = True,
    include_coverage: bool = True,
) -> dict[str, Any]:
    from .tools.browser_sessions import browser_debug_bundle as sessions_browser_debug_bundle

    return sessions_browser_debug_bundle(
        session_id=session_id,
        page_id=page_id,
        bundle_dir=bundle_dir,
        include_har=include_har,
        include_trace=include_trace,
        include_coverage=include_coverage,
    )


@mcp.tool()
def browser_capture_live_page(url: str, width: int = 1440, height: int = 960, wait_seconds: float = 2.5, browser: str = "auto", path: str | None = None, close_after: bool = False) -> Image:
    before_handles = {info.get("handle") for info in list_windows(visible_only=True) if info.get("handle")}
    browser_name, _ = launch_browser_process(browser, url)
    info = new_browser_window_info(browser_name, before_handles, wait_seconds, list_windows)
    handle = info.get("handle")
    if not handle:
        raise ValueError("Could not resolve launched browser window handle.")
    focus_window(handle=handle, wait_seconds=0.4)
    move_resize_window(handle=handle, x=60, y=40, width=max(width, 640), height=max(height, 480))
    time.sleep(max(wait_seconds, 0.2))
    image = capture_window(handle=handle, path=path, padding=0)
    if close_after:
        try:
            find_window(handle=handle).close()
        except Exception:
            pass
    return image


@mcp.tool()
def browser_save_live_page_screenshot(url: str, width: int = 1440, height: int = 960, wait_seconds: float = 2.5, browser: str = "auto", prefix: str = "browser-live", close_after: bool = False) -> dict[str, Any]:
    before_handles = {info.get("handle") for info in list_windows(visible_only=True) if info.get("handle")}
    browser_name, _ = launch_browser_process(browser, url)
    info = new_browser_window_info(browser_name, before_handles, wait_seconds, list_windows)
    handle = info.get("handle")
    if not handle:
        raise ValueError("Could not resolve launched browser window handle.")
    focus_window(handle=handle, wait_seconds=0.4)
    move_resize_window(handle=handle, x=60, y=40, width=max(width, 640), height=max(height, 480))
    time.sleep(max(wait_seconds, 0.2))
    screenshot = save_window_screenshot(prefix=prefix, handle=handle, padding=0)
    screenshot["url"] = url
    screenshot["browser"] = browser_name
    if close_after:
        try:
            find_window(handle=handle).close()
        except Exception:
            pass
    return screenshot
