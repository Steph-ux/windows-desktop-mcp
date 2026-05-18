"""Persistent Playwright session MCP tools."""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import os
import re
import subprocess
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any

import anyio
from mcp.server.fastmcp import Image
import pyautogui

from ..app import mcp
from ..browser_core import (
    apply_playwright_intercepts,
    cleanup_stale_browser_profiles,
    cleanup_stale_playwright_sessions,
    close_playwright_session,
    detect_cdp_endpoints,
    get_playwright_page,
    get_playwright_page_event_buffers,
    get_playwright_session,
    launch_browser_process,
    launch_debug_browser_process,
    list_playwright_sessions,
    new_browser_window_info,
    open_playwright_cdp_runtime,
    open_playwright_runtime,
    page_title,
    playwright_launch,
    playwright_page_info,
    playwright_session_age_seconds,
    probe_cdp_endpoint,
    refresh_playwright_pages,
    register_playwright_page,
    store_playwright_session,
    wait_for_cdp_endpoint,
    wait_for_url_pattern,
)
from ..helpers import now_stamp
from ..paths import BROWSER_CAPTURE_ROOT, BROWSER_INSTANCE_ROOT, BROWSER_PRESET_ROOT, BROWSER_PROFILE_ROOT
from ..runtime import record_event, tool_log
from ..shared.playwright_utils import ensure_dom_revision_tracker, validate_js_expression, visual_signature
from ..state import SESSION_MAX_AGE_MINUTES
from ..desktop_core import find_window
from .capture import capture_window, save_window_screenshot
from .windows import focus_window, list_windows, move_resize_window, wait_for_window

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
except Exception:  # pragma: no cover
    PlaywrightTimeoutError = TimeoutError


def threaded_tool(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))

    return wrapper


def _safe_profile_name(profile_name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", (profile_name or "").strip()).strip("-._")
    if not cleaned:
        raise ValueError("profile_name must contain at least one alphanumeric character.")
    return cleaned[:64]


def _named_profile_path(profile_name: str) -> Path:
    return BROWSER_PROFILE_ROOT / "named" / _safe_profile_name(profile_name)


def _profile_manifest_path(profile_name: str) -> Path:
    return _named_profile_path(profile_name) / ".pm-profile.json"


def _read_profile_manifest(profile_name: str) -> dict[str, Any] | None:
    path = _profile_manifest_path(profile_name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_profile_manifest(profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    safe_name = _safe_profile_name(profile_name)
    path = _profile_manifest_path(safe_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "profile_name": safe_name,
        "updated_at": time.time(),
        **payload,
    }
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def _profile_payload(profile_name: str, path: Path, browser: str = "auto") -> dict[str, Any]:
    manifest = _read_profile_manifest(profile_name) or {}
    return {
        "name": profile_name,
        "path": str(path),
        "browser": manifest.get("browser", browser),
        "exists": True,
        "size_bytes": sum(item.stat().st_size for item in path.rglob("*") if item.is_file()),
        "description": manifest.get("description"),
        "tags": manifest.get("tags", []),
        "preferred_url": manifest.get("preferred_url"),
        "metadata": manifest,
    }


def _safe_instance_name(instance_name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", (instance_name or "").strip()).strip("-._")
    if not cleaned:
        raise ValueError("instance_name must contain at least one alphanumeric character.")
    return cleaned[:64]


def _safe_preset_name(preset_name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", (preset_name or "").strip()).strip("-._")
    if not cleaned:
        raise ValueError("preset_name must contain at least one alphanumeric character.")
    return cleaned[:64]


def _preset_path(preset_name: str) -> Path:
    return BROWSER_PRESET_ROOT / f"{_safe_preset_name(preset_name)}.json"


def _read_preset(preset_name: str) -> dict[str, Any] | None:
    path = _preset_path(preset_name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_preset(preset_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    safe_name = _safe_preset_name(preset_name)
    path = _preset_path(safe_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"preset_name": safe_name, "updated_at": time.time(), **payload}
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return data


def _list_presets() -> list[dict[str, Any]]:
    presets: list[dict[str, Any]] = []
    if not BROWSER_PRESET_ROOT.exists():
        return presets
    for path in sorted(BROWSER_PRESET_ROOT.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        data.setdefault("preset_name", path.stem)
        data.setdefault("path", str(path))
        presets.append(data)
    return presets


def _merge_browser_preset(preset_name: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    if not preset_name:
        return payload
    preset = _read_preset(preset_name)
    if not preset:
        raise ValueError(f"Unknown browser preset: {_safe_preset_name(preset_name)}")
    merged = dict(payload)
    for key, value in preset.items():
        if key in {"preset_name", "updated_at", "path"}:
            continue
        current = merged.get(key)
        if current is None:
            merged[key] = value
    merged["preset_name"] = preset["preset_name"]
    return merged


def _instance_manifest_path(instance_name: str) -> Path:
    return BROWSER_INSTANCE_ROOT / f"{_safe_instance_name(instance_name)}.json"


def _write_instance_manifest(instance_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    manifest = {
        "instance_name": _safe_instance_name(instance_name),
        "updated_at": time.time(),
        **payload,
    }
    path = _instance_manifest_path(instance_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def _read_instance_manifest(instance_name: str) -> dict[str, Any] | None:
    path = _instance_manifest_path(instance_name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _list_instance_manifests() -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    if not BROWSER_INSTANCE_ROOT.exists():
        return manifests
    for path in sorted(BROWSER_INSTANCE_ROOT.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        data.setdefault("instance_name", path.stem)
        data.setdefault("manifest_path", str(path))
        manifests.append(data)
    return manifests


def _running_instance_session(instance_name: str) -> tuple[str, dict[str, Any]] | None:
    target = _safe_instance_name(instance_name)
    for session_id, session in list_playwright_sessions():
        if session.get("instance_name") == target:
            return session_id, session
    return None


def _score_cdp_endpoint(
    endpoint: dict[str, Any],
    *,
    browser: str = "chrome",
    profile_name: str | None = None,
    expected_title: str | None = None,
    expected_url_contains: str | None = None,
) -> tuple[int, int, int, str]:
    score = 0
    browser_label = str(endpoint.get("browser") or "").lower()
    endpoint_url = str(endpoint.get("endpoint") or "")
    port = int(endpoint.get("port") or 0)
    targets = endpoint.get("targets") or []
    if browser == "edge" and "edge" in browser_label:
        score += 40
    elif browser in {"chrome", "auto"} and "chrome" in browser_label:
        score += 40
    if profile_name:
        manifest = _read_profile_manifest(profile_name) or {}
        if manifest.get("cdp_endpoint") == endpoint_url:
            score += 120
        if manifest.get("debug_port") == port:
            score += 80
    if port in {9222, 9223}:
        score += 10
    page_targets = [item for item in targets if str(item.get("type") or "").lower() == "page"]
    if page_targets:
        score += 15
    non_internal_pages = [
        item for item in page_targets
        if not str(item.get("url") or "").startswith("chrome://") and not str(item.get("url") or "").startswith("edge://")
    ]
    if non_internal_pages:
        score += 30
    expected_title_l = (expected_title or "").strip().lower()
    expected_url_l = (expected_url_contains or "").strip().lower()
    for item in page_targets:
        title = str(item.get("title") or "").lower()
        url = str(item.get("url") or "").lower()
        if expected_title_l and expected_title_l in title:
            score += 120
        if expected_url_l and expected_url_l in url:
            score += 120
    return (-score, port, len(endpoint_url), endpoint_url)


def _resolve_attach_existing_endpoint(
    *,
    browser: str = "chrome",
    profile_name: str | None = None,
    ports: list[int] | None = None,
    host: str = "127.0.0.1",
    expected_title: str | None = None,
    expected_url_contains: str | None = None,
) -> dict[str, Any]:
    endpoints = detect_cdp_endpoints(ports=ports, host=host)
    if not endpoints:
        raise ValueError("No existing debug-enabled browser detected. Start Chrome/Edge with remote debugging first.")
    ranked = sorted(
        endpoints,
        key=lambda item: _score_cdp_endpoint(
            item,
            browser=browser,
            profile_name=profile_name,
            expected_title=expected_title,
            expected_url_contains=expected_url_contains,
        ),
    )
    return ranked[0]


def _session_payload(session_id: str, session: dict[str, Any]) -> dict[str, Any]:
    refresh_playwright_pages(session)
    active_page_id = session.get("active_page_id")
    active_page = session["pages"].get(active_page_id) if active_page_id else None
    return {
        "session_id": session_id,
        "active_page_id": active_page_id,
        "page_count": len(session["pages"]),
        "url": active_page.url if active_page else "",
        "title": page_title(active_page) if active_page else "",
        "browser": session["browser_name"],
        "headless": session["headless"],
        "profile_name": session.get("profile_name"),
        "profile_path": session.get("profile_path"),
        "persistent_profile": bool(session.get("persistent_profile")),
        "instance_name": session.get("instance_name"),
        "attached": bool(session.get("attached")),
        "cdp_endpoint": session.get("cdp_endpoint"),
        "granted_permissions": session.get("granted_permissions", []),
        "age_seconds": round(playwright_session_age_seconds(session), 2),
    }


def _launch_persistent_context(
    browser: str,
    profile_name: str,
    headless: bool,
    stealth: bool = False,
    humanize: bool = False,
    proxy: str | None = None,
    geoip: bool = False,
    fingerprint_seed: int | None = None,
    timezone: str | None = None,
    locale: str | None = None,
    fingerprint_platform: str | None = None,
    webrtc_ip: str | None = None,
    human_preset: str = "default",
) -> tuple[Any, Any, Any, Any, str, Path]:
    browser_key = (browser or "auto").lower()
    profile_path = _named_profile_path(profile_name)
    profile_path.mkdir(parents=True, exist_ok=True)

    # Stealth persistent context via CloakBrowser
    if stealth and browser_key in {"auto", "chrome"}:
        try:
            from cloakbrowser import launch_persistent_context as cloak_persistent
            from ..browser_core import _build_stealth_args
            extra_args = _build_stealth_args(fingerprint_seed, fingerprint_platform, webrtc_ip)
            context = cloak_persistent(
                str(profile_path),
                headless=headless,
                humanize=humanize,
                human_preset=human_preset,
                proxy=proxy,
                geoip=geoip,
                timezone=timezone,
                locale=locale,
                viewport=None,
                accept_downloads=True,
                args=extra_args or None,
            )
            browser_instance = getattr(context, "browser", None)
            return None, None, browser_instance, context, "chrome-stealth", profile_path
        except ImportError:
            pass

    # Standard Playwright persistent context
    playwright = playwright_launch(browser)
    runtime = playwright.__enter__()
    if browser_key in {"auto", "chrome"}:
        context = runtime.chromium.launch_persistent_context(
            str(profile_path),
            channel="chrome",
            headless=headless,
            viewport=None,
            accept_downloads=True,
        )
        actual_browser = "chrome"
    elif browser_key == "edge":
        context = runtime.chromium.launch_persistent_context(
            str(profile_path),
            channel="msedge",
            headless=headless,
            viewport=None,
            accept_downloads=True,
        )
        actual_browser = "edge"
    else:
        context = runtime.firefox.launch_persistent_context(
            str(profile_path),
            headless=headless,
            viewport=None,
            accept_downloads=True,
        )
        actual_browser = "firefox"
    browser_instance = getattr(context, "browser", None)
    return playwright, runtime, browser_instance, context, actual_browser, profile_path


def _browser_open_session_impl(
    url: str,
    width: int | str = 1440,
    height: int | str = 960,
    browser: str = "auto",
    headless: bool = True,
    profile_name: str | None = None,
    persistent_profile: bool = False,
    instance_name: str | None = None,
    storage_state_path: str | None = None,
    init_script_paths: list[str] | None = None,
    grant_permissions: list[str] | None = None,
    preset_name: str | None = None,
    stealth: bool = False,
    humanize: bool = False,
    proxy: str | None = None,
    geoip: bool = False,
    fingerprint_seed: int | None = None,
    timezone: str | None = None,
    locale: str | None = None,
    fingerprint_platform: str | None = None,
    webrtc_ip: str | None = None,
    human_preset: str = "default",
    user_agent: str | None = None,
    color_scheme: str | None = None,
) -> dict[str, Any]:
    merged = _merge_browser_preset(
        preset_name,
        {
            "browser": browser,
            "headless": headless,
            "width": width,
            "height": height,
            "storage_state_path": storage_state_path,
            "init_script_paths": init_script_paths,
            "grant_permissions": grant_permissions,
        },
    )
    browser = merged["browser"]
    headless = merged["headless"]
    width = merged["width"]
    height = merged["height"]
    storage_state_path = merged.get("storage_state_path")
    init_script_paths = merged.get("init_script_paths")
    grant_permissions = merged.get("grant_permissions")
    cleanup_stale_playwright_sessions()
    session_id: str | None = None
    profile_path: Path | None = None
    if persistent_profile or profile_name:
        resolved_profile_name = profile_name or "default"
        playwright_cm, runtime, engine, context, actual_browser, profile_path = _launch_persistent_context(
            browser,
            resolved_profile_name,
            headless=headless,
            stealth=stealth,
            humanize=humanize,
            proxy=proxy,
            geoip=geoip,
            fingerprint_seed=fingerprint_seed,
            timezone=timezone,
            locale=locale,
            fingerprint_platform=fingerprint_platform,
            webrtc_ip=webrtc_ip,
            human_preset=human_preset,
        )
    else:
        playwright_cm, runtime, engine, actual_browser = open_playwright_runtime(
            browser, headless=headless, stealth=stealth, humanize=humanize,
            proxy=proxy, geoip=geoip, fingerprint_seed=fingerprint_seed,
            timezone=timezone, locale=locale,
            fingerprint_platform=fingerprint_platform, webrtc_ip=webrtc_ip,
            human_preset=human_preset,
        )
        context = None
    try:
        if isinstance(width, str) and width.lower() == "auto":
            width = pyautogui.size().width
        if isinstance(height, str) and height.lower() == "auto":
            height = pyautogui.size().height
        resolved_width = max(int(width), 320)
        resolved_height = max(int(height), 240)
        resolved_init_scripts = [str(Path(path)) for path in (init_script_paths or []) if str(path).strip()]
        resolved_permissions = [str(item) for item in (grant_permissions or []) if str(item).strip()]
        if context is None:
            context_options: dict[str, Any] = {
                "viewport": {"width": resolved_width, "height": resolved_height},
                "device_scale_factor": 1,
                "accept_downloads": True,
            }
            if storage_state_path:
                context_options["storage_state"] = str(Path(storage_state_path))
            if user_agent:
                context_options["user_agent"] = user_agent
            if color_scheme:
                context_options["color_scheme"] = color_scheme
            context = engine.new_context(**context_options)
            page = context.new_page()
        else:
            page = context.pages[0] if getattr(context, "pages", None) else context.new_page()
            try:
                page.set_viewport_size({"width": resolved_width, "height": resolved_height})
            except Exception:
                pass
        for script_path in resolved_init_scripts:
            context.add_init_script(path=script_path)
        if resolved_permissions:
            context.grant_permissions(resolved_permissions)
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
            "profile_name": _safe_profile_name(profile_name or "default") if (persistent_profile or profile_name) else None,
            "profile_path": str(profile_path) if profile_path else None,
            "persistent_profile": bool(persistent_profile or profile_name),
            "instance_name": _safe_instance_name(instance_name) if instance_name else None,
            "storage_state_path": str(Path(storage_state_path)) if storage_state_path else None,
            "init_script_paths": resolved_init_scripts,
            "granted_permissions": resolved_permissions,
            "preset_name": merged.get("preset_name"),
            "created_at": time.time(),
            "last_used_at": time.time(),
        }
        store_playwright_session(session)
        page_id = register_playwright_page(session, page, make_active=True)
        page.goto(url, wait_until="networkidle")
        if session["instance_name"]:
            _write_instance_manifest(
                session["instance_name"],
                {
                    "manifest_path": str(_instance_manifest_path(session["instance_name"])),
                    "status": "running",
                    "browser": actual_browser,
                    "headless": headless,
                    "profile_name": session["profile_name"],
                    "profile_path": session["profile_path"],
                    "persistent_profile": session["persistent_profile"],
                    "storage_state_path": session["storage_state_path"],
                    "init_script_paths": session["init_script_paths"],
                    "granted_permissions": session["granted_permissions"],
                    "preset_name": session.get("preset_name"),
                    "session_id": session_id,
                    "page_id": page_id,
                    "url": page.url,
                    "title": page.title(),
                },
            )
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
            "profile_name": session["profile_name"],
            "profile_path": session["profile_path"],
            "persistent_profile": session["persistent_profile"],
            "instance_name": session["instance_name"],
            "storage_state_path": session["storage_state_path"],
            "init_script_paths": session["init_script_paths"],
            "granted_permissions": session["granted_permissions"],
            "preset_name": session.get("preset_name"),
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


def browser_open_session(
    url: str,
    width: int | str = 1440,
    height: int | str = 960,
    browser: str = "auto",
    headless: bool = True,
    profile_name: str | None = None,
    persistent_profile: bool = False,
    instance_name: str | None = None,
    storage_state_path: str | None = None,
    init_script_paths: list[str] | None = None,
    grant_permissions: list[str] | None = None,
    preset_name: str | None = None,
    stealth: bool = False,
    humanize: bool = False,
    proxy: str | None = None,
    geoip: bool = False,
    fingerprint_seed: int | None = None,
    timezone: str | None = None,
    locale: str | None = None,
    fingerprint_platform: str | None = None,
    webrtc_ip: str | None = None,
    human_preset: str = "default",
    user_agent: str | None = None,
    color_scheme: str | None = None,
) -> dict[str, Any]:
    return _browser_open_session_impl(
        url=url,
        width=width,
        height=height,
        browser=browser,
        headless=headless,
        profile_name=profile_name,
        persistent_profile=persistent_profile,
        instance_name=instance_name,
        storage_state_path=storage_state_path,
        init_script_paths=init_script_paths,
        grant_permissions=grant_permissions,
        preset_name=preset_name,
        stealth=stealth,
        humanize=humanize,
        proxy=proxy,
        geoip=geoip,
        fingerprint_seed=fingerprint_seed,
        timezone=timezone,
        locale=locale,
        fingerprint_platform=fingerprint_platform,
        webrtc_ip=webrtc_ip,
        human_preset=human_preset,
        user_agent=user_agent,
        color_scheme=color_scheme,
    )


def browser_user_open(
    url: str,
    wait_title_contains: str = "",
    wait_title_regex: str = "",
    timeout_seconds: float = 10.0,
    focus: bool = True,
) -> dict[str, Any]:
    """Open a URL in the user's default browser/profile for logged-in sites."""
    launched = _open_url_in_default_browser(url)
    window = None
    verified = False
    verification_error = None
    if wait_title_contains or wait_title_regex:
        try:
            window = wait_for_window(
                title_regex=wait_title_regex or None,
                title_filter=wait_title_contains,
                timeout_seconds=timeout_seconds,
            )
            verified = True
            if focus and window.get("handle"):
                window = focus_window(handle=int(window["handle"]))
        except Exception as exc:
            verification_error = str(exc)

    result = {
        "ok": bool(launched),
        "url": url,
        "browser_context": "user_default",
        "automation": "desktop",
        "verified": verified,
        "window": window,
        "verification_error": verification_error,
        "note": "Uses the user's default browser/profile, not an isolated Playwright context.",
    }
    record_event("browser_user_open", url=url, verified=verified, launched=bool(launched))
    return result


def _open_url_in_default_browser(url: str) -> bool:
    if os.name == "nt":
        os.startfile(url)  # type: ignore[attr-defined]
        return True
    return bool(webbrowser.open(url, new=2, autoraise=True))


def browser_attach_cdp(
    endpoint: str,
    browser: str = "chrome",
    instance_name: str | None = None,
    profile_name: str | None = None,
    browser_pid: int | None = None,
    launched_debug_browser: bool = False,
    init_script_paths: list[str] | None = None,
    grant_permissions: list[str] | None = None,
    width: int | str = "auto",
    height: int | str = "auto",
    page_index: int = 0,
    timeout_ms: int = 30000,
) -> dict[str, Any]:
    return _browser_attach_cdp_impl(
        endpoint=endpoint,
        browser=browser,
        instance_name=instance_name,
        profile_name=profile_name,
        browser_pid=browser_pid,
        launched_debug_browser=launched_debug_browser,
        init_script_paths=init_script_paths,
        grant_permissions=grant_permissions,
        width=width,
        height=height,
        page_index=page_index,
        timeout_ms=timeout_ms,
    )
def browser_list_endpoints(
    ports: list[int] | None = None,
    host: str = "127.0.0.1",
) -> dict[str, Any]:
    endpoints = detect_cdp_endpoints(ports=ports, host=host)
    return {"count": len(endpoints), "endpoints": endpoints}
def browser_attach_existing(
    browser: str = "chrome",
    instance_name: str | None = None,
    profile_name: str | None = None,
    ports: list[int] | None = None,
    host: str = "127.0.0.1",
    expected_title: str | None = None,
    expected_url_contains: str | None = None,
    expected_tab_title: str | None = None,
    expected_tab_url_contains: str | None = None,
    width: int | str = "auto",
    height: int | str = "auto",
    init_script_paths: list[str] | None = None,
    grant_permissions: list[str] | None = None,
    preset_name: str | None = None,
    page_index: int | None = None,
    timeout_ms: int = 30000,
) -> dict[str, Any]:
    merged = _merge_browser_preset(
        preset_name,
        {
            "browser": browser,
            "width": width,
            "height": height,
            "init_script_paths": init_script_paths,
            "grant_permissions": grant_permissions,
            "expected_title": expected_title,
            "expected_url_contains": expected_url_contains,
        },
    )
    browser = merged["browser"]
    width = merged["width"]
    height = merged["height"]
    init_script_paths = merged.get("init_script_paths")
    grant_permissions = merged.get("grant_permissions")
    expected_title = merged.get("expected_title")
    expected_url_contains = merged.get("expected_url_contains")
    expected_tab_title_l = (expected_tab_title or "").strip().lower()
    expected_tab_url_l = (expected_tab_url_contains or "").strip().lower()
    endpoints = detect_cdp_endpoints(ports=ports, host=host)
    chosen = _resolve_attach_existing_endpoint(
        browser=browser,
        profile_name=profile_name,
        ports=ports,
        host=host,
        expected_title=expected_title,
        expected_url_contains=expected_url_contains,
    )
    browser_pid = None
    launched_debug_browser = False
    if profile_name:
        profile_manifest = _read_profile_manifest(profile_name) or {}
        if profile_manifest.get("cdp_endpoint") == chosen["endpoint"]:
            browser_pid = profile_manifest.get("browser_pid")
            launched_debug_browser = bool(profile_manifest.get("debug_browser_running"))
    result = _browser_attach_cdp_impl(
        endpoint=chosen["endpoint"],
        browser=browser,
        instance_name=instance_name,
        profile_name=profile_name,
        browser_pid=browser_pid,
        launched_debug_browser=launched_debug_browser,
        width=width,
        height=height,
        page_index=page_index,
        init_script_paths=init_script_paths,
        grant_permissions=grant_permissions,
        timeout_ms=timeout_ms,
    )
    result["discovered_endpoint"] = chosen
    page_targets = [item for item in (chosen.get("targets") or []) if str(item.get("type") or "").lower() == "page"]
    selected_target = None
    if expected_tab_title_l or expected_tab_url_l:
        for item in page_targets:
            title_l = str(item.get("title") or "").lower()
            url_l = str(item.get("url") or "").lower()
            if expected_tab_title_l and expected_tab_title_l not in title_l:
                continue
            if expected_tab_url_l and expected_tab_url_l not in url_l:
                continue
            selected_target = item
            break
    if selected_target is None and page_index is not None and 0 <= int(page_index) < len(page_targets):
        selected_target = page_targets[int(page_index)]
    if selected_target is not None:
        result["discovered_target"] = selected_target
    result["preset_name"] = merged.get("preset_name")
    record_event("browser_attach_existing", endpoint=chosen["endpoint"], browser=browser, session_id=result["session_id"])
    return result
def browser_list_sessions() -> list[dict[str, Any]]:
    cleanup_stale_playwright_sessions()
    return [_session_payload(session_id, session) for session_id, session in list_playwright_sessions()]
def browser_storage_state_export(
    session_id: str,
    path: str | None = None,
) -> dict[str, Any]:
    session = get_playwright_session(session_id)
    target_path = Path(path) if path else (BROWSER_CAPTURE_ROOT / f"storage-state-{session_id}-{now_stamp()}.json")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    session["context"].storage_state(path=str(target_path))
    result = {"session_id": session_id, "path": str(target_path), "exists": target_path.exists()}
    record_event("browser_storage_state_export", **result)
    return result
def browser_grant_permissions(session_id: str, permissions: list[str]) -> dict[str, Any]:
    session = get_playwright_session(session_id)
    resolved = [str(item) for item in permissions if str(item).strip()]
    existing = list(session.get("granted_permissions", []))
    merged = existing[:]
    for item in resolved:
        if item not in merged:
            merged.append(item)
    session["context"].grant_permissions(merged)
    session["granted_permissions"] = merged
    result = {"session_id": session_id, "granted_permissions": merged}
    record_event("browser_grant_permissions", **result)
    return result
def browser_clear_permissions(session_id: str) -> dict[str, Any]:
    session = get_playwright_session(session_id)
    try:
        session["context"].clear_permissions()
    except Exception:
        pass
    session["granted_permissions"] = []
    result = {"session_id": session_id, "granted_permissions": []}
    record_event("browser_clear_permissions", **result)
    return result
def browser_revoke_permissions(session_id: str, permissions: list[str]) -> dict[str, Any]:
    session = get_playwright_session(session_id)
    current = list(session.get("granted_permissions", []))
    revoked = {str(item) for item in permissions if str(item).strip()}
    remaining = [item for item in current if item not in revoked]
    try:
        session["context"].clear_permissions()
    except Exception:
        pass
    if remaining:
        session["context"].grant_permissions(remaining)
    session["granted_permissions"] = remaining
    result = {"session_id": session_id, "granted_permissions": remaining, "revoked_permissions": sorted(revoked)}
    record_event("browser_revoke_permissions", **result)
    return result


def _browser_launch_debug_browser_impl(
    browser: str = "chrome",
    port: int = 9222,
    url: str = "about:blank",
    profile_name: str | None = None,
    startup_wait_ms: int = 5000,
) -> dict[str, Any]:
    profile_path = str(_named_profile_path(profile_name)) if profile_name else None
    if profile_path:
        Path(profile_path).mkdir(parents=True, exist_ok=True)
    browser_name, process, endpoint = launch_debug_browser_process(
        browser=browser,
        port=port,
        url=url,
        profile_path=profile_path,
    )
    endpoint_info = wait_for_cdp_endpoint(endpoint, timeout_seconds=max(int(startup_wait_ms), 1) / 1000)
    result = {
        "browser": browser_name,
        "pid": process.pid,
        "port": max(int(port), 1),
        "endpoint": endpoint,
        "url": url,
        "profile_name": _safe_profile_name(profile_name) if profile_name else None,
        "profile_path": profile_path,
        "endpoint_ready": True,
        "endpoint_info": endpoint_info,
    }
    if profile_name:
        _write_profile_manifest(
            profile_name,
            {
                **(_read_profile_manifest(profile_name) or {}),
                "browser": browser_name,
                "debug_port": result["port"],
                "cdp_endpoint": endpoint,
                "browser_pid": process.pid,
                "debug_browser_running": True,
            },
        )
    record_event("browser_launch_debug_browser", **result)
    return result
def browser_launch_debug_browser(
    browser: str = "chrome",
    port: int = 9222,
    url: str = "about:blank",
    profile_name: str | None = None,
    startup_wait_ms: int = 5000,
) -> dict[str, Any]:
    return _browser_launch_debug_browser_impl(browser=browser, port=port, url=url, profile_name=profile_name, startup_wait_ms=startup_wait_ms)
def browser_launch_and_attach(
    browser: str = "chrome",
    port: int = 9222,
    url: str = "about:blank",
    profile_name: str | None = None,
    instance_name: str | None = None,
    width: int | str = "auto",
    height: int | str = "auto",
    startup_wait_ms: int = 2500,
    init_script_paths: list[str] | None = None,
    grant_permissions: list[str] | None = None,
) -> dict[str, Any]:
    launched = _browser_launch_debug_browser_impl(
        browser=browser,
        port=port,
        url=url,
        profile_name=profile_name,
        startup_wait_ms=startup_wait_ms,
    )
    attached = _browser_attach_cdp_impl(
        endpoint=launched["endpoint"],
        browser=browser,
        instance_name=instance_name,
        profile_name=profile_name,
        browser_pid=launched["pid"],
        launched_debug_browser=True,
        init_script_paths=init_script_paths,
        grant_permissions=grant_permissions,
        width=width,
        height=height,
    )
    return {**attached, "browser_pid": launched["pid"], "launched_debug_browser": True}
def browser_list_profiles(browser: str = "auto") -> dict[str, Any]:
    named_root = BROWSER_PROFILE_ROOT / "named"
    profiles: list[dict[str, Any]] = []
    if named_root.exists():
        for path in sorted(named_root.iterdir()):
            if not path.is_dir():
                continue
            profiles.append(_profile_payload(path.name, path, browser=browser))
    return {"count": len(profiles), "profiles": profiles}
def browser_get_profile(profile_name: str) -> dict[str, Any]:
    safe_name = _safe_profile_name(profile_name)
    path = _named_profile_path(safe_name)
    if not path.exists():
        raise ValueError(f"Unknown browser profile: {safe_name}")
    result = _profile_payload(safe_name, path)
    record_event("browser_get_profile", name=safe_name)
    return result
def browser_create_profile(
    profile_name: str,
    description: str | None = None,
    tags: list[str] | None = None,
    preferred_url: str | None = None,
    browser: str = "auto",
) -> dict[str, Any]:
    safe_name = _safe_profile_name(profile_name)
    path = _named_profile_path(safe_name)
    path.mkdir(parents=True, exist_ok=True)
    manifest = _write_profile_manifest(
        safe_name,
        {
            "browser": browser,
            "description": description,
            "tags": [str(tag) for tag in (tags or [])],
            "preferred_url": preferred_url,
        },
    )
    result = {"created": True, **_profile_payload(safe_name, path, browser=browser), "metadata": manifest}
    record_event("browser_create_profile", **result)
    return result
def browser_update_profile(
    profile_name: str,
    description: str | None = None,
    tags: list[str] | None = None,
    preferred_url: str | None = None,
    browser: str | None = None,
) -> dict[str, Any]:
    safe_name = _safe_profile_name(profile_name)
    path = _named_profile_path(safe_name)
    if not path.exists():
        raise ValueError(f"Unknown browser profile: {safe_name}")
    current = _read_profile_manifest(safe_name) or {}
    updated = _write_profile_manifest(
        safe_name,
        {
            "browser": browser if browser is not None else current.get("browser", "auto"),
            "description": description if description is not None else current.get("description"),
            "tags": [str(tag) for tag in tags] if tags is not None else current.get("tags", []),
            "preferred_url": preferred_url if preferred_url is not None else current.get("preferred_url"),
        },
    )
    result = {**_profile_payload(safe_name, path, browser=updated.get("browser", "auto")), "updated": True}
    record_event("browser_update_profile", name=safe_name)
    return result
def browser_save_preset(
    preset_name: str,
    browser: str | None = None,
    headless: bool | None = None,
    width: int | str | None = None,
    height: int | str | None = None,
    storage_state_path: str | None = None,
    init_script_paths: list[str] | None = None,
    grant_permissions: list[str] | None = None,
    expected_title: str | None = None,
    expected_url_contains: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    data = _write_preset(
        preset_name,
        {
            "browser": browser,
            "headless": headless,
            "width": width,
            "height": height,
            "storage_state_path": storage_state_path,
            "init_script_paths": [str(item) for item in (init_script_paths or [])],
            "grant_permissions": [str(item) for item in (grant_permissions or [])],
            "expected_title": expected_title,
            "expected_url_contains": expected_url_contains,
            "description": description,
        },
    )
    result = {"saved": True, "preset": data, "path": str(_preset_path(preset_name))}
    record_event("browser_save_preset", preset_name=data["preset_name"])
    return result
def browser_get_preset(preset_name: str) -> dict[str, Any]:
    preset = _read_preset(preset_name)
    if not preset:
        raise ValueError(f"Unknown browser preset: {_safe_preset_name(preset_name)}")
    return {"preset": preset, "path": str(_preset_path(preset_name))}
def browser_list_presets() -> dict[str, Any]:
    presets = _list_presets()
    return {"count": len(presets), "presets": presets}
def browser_delete_preset(preset_name: str) -> dict[str, Any]:
    path = _preset_path(preset_name)
    existed = path.exists()
    if existed:
        path.unlink()
    result = {"preset_name": _safe_preset_name(preset_name), "deleted": existed and (not path.exists()), "path": str(path)}
    record_event("browser_delete_preset", **result)
    return result
def browser_export_profile_config(profile_name: str, path: str | None = None) -> dict[str, Any]:
    safe_name = _safe_profile_name(profile_name)
    profile_path = _named_profile_path(safe_name)
    if not profile_path.exists():
        raise ValueError(f"Unknown browser profile: {safe_name}")
    manifest = _read_profile_manifest(safe_name) or {}
    payload = {
        "profile_name": safe_name,
        "browser": manifest.get("browser", "auto"),
        "description": manifest.get("description"),
        "tags": manifest.get("tags", []),
        "preferred_url": manifest.get("preferred_url"),
        "debug_port": manifest.get("debug_port"),
        "cdp_endpoint": manifest.get("cdp_endpoint"),
        "debug_browser_running": bool(manifest.get("debug_browser_running")),
        "browser_pid": manifest.get("browser_pid"),
    }
    target_path = Path(path) if path else (BROWSER_CAPTURE_ROOT / f"profile-config-{safe_name}-{now_stamp()}.json")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    result = {"profile_name": safe_name, "path": str(target_path), "exists": target_path.exists()}
    record_event("browser_export_profile_config", **result)
    return result
def browser_import_profile_config(path: str, profile_name_override: str | None = None) -> dict[str, Any]:
    source_path = Path(path)
    data = json.loads(source_path.read_text(encoding="utf-8"))
    profile_name = _safe_profile_name(profile_name_override or data["profile_name"])
    profile_path = _named_profile_path(profile_name)
    profile_path.mkdir(parents=True, exist_ok=True)
    manifest = _write_profile_manifest(
        profile_name,
        {
            "browser": data.get("browser", "auto"),
            "description": data.get("description"),
            "tags": [str(tag) for tag in data.get("tags", [])],
            "preferred_url": data.get("preferred_url"),
            "debug_port": data.get("debug_port"),
            "cdp_endpoint": data.get("cdp_endpoint"),
            "debug_browser_running": bool(data.get("debug_browser_running")),
            "browser_pid": data.get("browser_pid"),
        },
    )
    result = {"profile_name": profile_name, "path": str(profile_path), "imported": True, "metadata": manifest}
    record_event("browser_import_profile_config", profile_name=profile_name, source_path=str(source_path))
    return result
def browser_delete_profile(profile_name: str, force: bool = False) -> dict[str, Any]:
    import shutil

    safe_name = _safe_profile_name(profile_name)
    path = _named_profile_path(safe_name)
    if not path.exists():
        return {"deleted": False, "name": safe_name, "path": str(path), "reason": "missing"}
    active_sessions = [
        session_id for session_id, session in list_playwright_sessions() if session.get("profile_name") == safe_name
    ]
    if active_sessions and not force:
        raise ValueError(f"Profile {safe_name!r} is still in use by sessions: {', '.join(active_sessions)}")
    for session_id in active_sessions:
        close_playwright_session(session_id)
    for attempt in range(5):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            break
        time.sleep(0.4 * (attempt + 1))
    result = {"deleted": not path.exists(), "name": safe_name, "path": str(path), "closed_sessions": active_sessions}
    record_event("browser_delete_profile", **result)
    return result


def _browser_start_instance_impl(
    instance_name: str,
    url: str = "about:blank",
    profile_name: str | None = None,
    browser: str = "auto",
    headless: bool = True,
    width: int | str = "auto",
    height: int | str = "auto",
    storage_state_path: str | None = None,
    init_script_paths: list[str] | None = None,
    grant_permissions: list[str] | None = None,
    preset_name: str | None = None,
) -> dict[str, Any]:
    safe_name = _safe_instance_name(instance_name)
    running = _running_instance_session(safe_name)
    if running:
        session_id, session = running
        result = {"reused": True, **_session_payload(session_id, session)}
        record_event("browser_start_instance", instance_name=safe_name, reused=True, session_id=session_id)
        return result
    result = _browser_open_session_impl(
        url=url,
        width=width,
        height=height,
        browser=browser,
        headless=headless,
        profile_name=profile_name,
        persistent_profile=bool(profile_name),
        instance_name=safe_name,
        storage_state_path=storage_state_path,
        init_script_paths=init_script_paths,
        grant_permissions=grant_permissions,
        preset_name=preset_name,
    )
    result["reused"] = False
    record_event("browser_start_instance", instance_name=safe_name, reused=False, session_id=result["session_id"])
    return result
def browser_start_instance(
    instance_name: str,
    url: str = "about:blank",
    profile_name: str | None = None,
    browser: str = "auto",
    headless: bool = True,
    width: int | str = "auto",
    height: int | str = "auto",
    storage_state_path: str | None = None,
    init_script_paths: list[str] | None = None,
    grant_permissions: list[str] | None = None,
    preset_name: str | None = None,
) -> dict[str, Any]:
    return _browser_start_instance_impl(
        instance_name=instance_name,
        url=url,
        profile_name=profile_name,
        browser=browser,
        headless=headless,
        width=width,
        height=height,
        storage_state_path=storage_state_path,
        init_script_paths=init_script_paths,
        grant_permissions=grant_permissions,
        preset_name=preset_name,
    )
def browser_resume_instance(instance_name: str, url: str | None = None) -> dict[str, Any]:
    safe_name = _safe_instance_name(instance_name)
    manifest = _read_instance_manifest(safe_name)
    if not manifest:
        raise ValueError(f"Unknown browser instance: {safe_name}")
    if manifest.get("attached") and manifest.get("cdp_endpoint"):
        return _browser_attach_cdp_impl(
            endpoint=manifest["cdp_endpoint"],
            browser=manifest.get("browser", "chrome"),
            instance_name=safe_name,
            profile_name=manifest.get("profile_name"),
            init_script_paths=manifest.get("init_script_paths"),
            grant_permissions=manifest.get("granted_permissions"),
        )
    return _browser_start_instance_impl(
        instance_name=safe_name,
        url=url or manifest.get("last_url") or manifest.get("url") or "about:blank",
        profile_name=manifest.get("profile_name"),
        browser=manifest.get("browser", "auto"),
        headless=bool(manifest.get("headless", True)),
        storage_state_path=manifest.get("storage_state_path"),
        init_script_paths=manifest.get("init_script_paths"),
        grant_permissions=manifest.get("granted_permissions"),
        preset_name=manifest.get("preset_name"),
    )
def browser_get_instance(instance_name: str) -> dict[str, Any]:
    safe_name = _safe_instance_name(instance_name)
    running = _running_instance_session(safe_name)
    manifest = _read_instance_manifest(safe_name)
    if running:
        session_id, session = running
        return {"running": True, **_session_payload(session_id, session), "manifest": manifest}
    if manifest:
        return {"running": False, "instance_name": safe_name, "manifest": manifest}
    raise ValueError(f"Unknown browser instance: {safe_name}")
def browser_list_instances() -> dict[str, Any]:
    manifests = {item["instance_name"]: item for item in _list_instance_manifests()}
    instances: list[dict[str, Any]] = []
    seen: set[str] = set()
    for session_id, session in list_playwright_sessions():
        instance_name = session.get("instance_name")
        if not instance_name:
            continue
        seen.add(instance_name)
        instances.append(
            {
                "instance_name": instance_name,
                "running": True,
                **_session_payload(session_id, session),
                "manifest": manifests.get(instance_name),
            }
        )
    for instance_name, manifest in manifests.items():
        if instance_name in seen:
            continue
        instances.append({"instance_name": instance_name, "running": False, "manifest": manifest})
    return {"count": len(instances), "instances": sorted(instances, key=lambda item: item["instance_name"])}


def _browser_stop_instance_impl(instance_name: str) -> dict[str, Any]:
    safe_name = _safe_instance_name(instance_name)
    running = _running_instance_session(safe_name)
    manifest = _read_instance_manifest(safe_name) or {"instance_name": safe_name}
    if not running:
        return {"instance_name": safe_name, "closed": False, "reason": "not_running", "manifest": manifest}
    session_id, session = running
    payload = _session_payload(session_id, session)
    closed = close_playwright_session(session_id)
    browser_pid = payload.get("browser_pid") or manifest.get("browser_pid")
    launched_debug_browser = bool(payload.get("launched_debug_browser") or manifest.get("launched_debug_browser"))
    _write_instance_manifest(
        safe_name,
        {
            **manifest,
            "status": "stopped",
            "session_id": None,
            "last_url": payload.get("url"),
            "last_title": payload.get("title"),
            "browser": payload.get("browser"),
            "headless": payload.get("headless"),
            "profile_name": payload.get("profile_name"),
            "profile_path": payload.get("profile_path"),
            "persistent_profile": payload.get("persistent_profile"),
            "browser_pid": browser_pid,
            "launched_debug_browser": launched_debug_browser,
        },
    )
    result = {"instance_name": safe_name, "closed": closed, "session_id": session_id, "browser_pid": browser_pid}
    record_event("browser_stop_instance", **result)
    return result
def browser_stop_instance(instance_name: str) -> dict[str, Any]:
    return _browser_stop_instance_impl(instance_name)
def browser_stop_instance_and_browser(instance_name: str) -> dict[str, Any]:
    safe_name = _safe_instance_name(instance_name)
    manifest = _read_instance_manifest(safe_name) or {"instance_name": safe_name}
    result = _browser_stop_instance_impl(safe_name)
    browser_pid = result.get("browser_pid") or manifest.get("browser_pid")
    launched_debug_browser = bool(manifest.get("launched_debug_browser"))
    browser_closed = False
    if browser_pid and launched_debug_browser:
        try:
            subprocess.run(["taskkill", "/PID", str(browser_pid), "/T", "/F"], check=False, capture_output=True, text=True, timeout=10)
            browser_closed = True
        except Exception:
            browser_closed = False
    if browser_closed and manifest.get("profile_name"):
        _write_profile_manifest(
            manifest["profile_name"],
            {
                **(_read_profile_manifest(manifest["profile_name"]) or {}),
                "debug_browser_running": False,
                "browser_pid": None,
            },
        )
    payload = {**result, "browser_closed": browser_closed}
    record_event("browser_stop_instance_and_browser", instance_name=safe_name, browser_closed=browser_closed, browser_pid=browser_pid)
    return payload
def browser_delete_instance(instance_name: str, force: bool = False) -> dict[str, Any]:
    safe_name = _safe_instance_name(instance_name)
    running = _running_instance_session(safe_name)
    if running and not force:
        session_id, _ = running
        raise ValueError(f"Instance {safe_name!r} is still running in session {session_id}.")
    if running:
        session_id, _ = running
        close_playwright_session(session_id)
    path = _instance_manifest_path(safe_name)
    existed = path.exists()
    if existed:
        path.unlink()
    result = {"instance_name": safe_name, "deleted": existed and (not path.exists()), "manifest_path": str(path)}
    record_event("browser_delete_instance", **result)
    return result
def browser_cleanup_sessions(max_age_minutes: float = SESSION_MAX_AGE_MINUTES) -> dict[str, Any]:
    result = cleanup_stale_playwright_sessions(max_age_minutes=max_age_minutes)
    record_event("browser_cleanup_sessions", **result)
    return result
def browser_cleanup_profiles(max_age_hours: float = 12) -> dict[str, Any]:
    result = cleanup_stale_browser_profiles(max_age_hours=max_age_hours)
    record_event("browser_cleanup_profiles", **result)
    return result
def browser_list_pages(session_id: str) -> dict[str, Any]:
    session = get_playwright_session(session_id)
    refresh_playwright_pages(session)
    return {"session_id": session_id, "active_page_id": session.get("active_page_id"), "pages": [playwright_page_info(page_id, page) for page_id, page in session["pages"].items()]}
def browser_new_page(session_id: str, url: str = "about:blank", wait_until: str = "load", make_active: bool = True) -> dict[str, Any]:
    session = get_playwright_session(session_id)
    page = session["context"].new_page()
    page.goto(url, wait_until=wait_until)
    page_id = register_playwright_page(session, page, make_active=make_active)
    result = {"session_id": session_id, "page_id": page_id, "active_page_id": session.get("active_page_id"), "url": page.url, "title": page_title(page)}
    record_event("browser_new_page", **result)
    return result
def browser_switch_page(session_id: str, page_id: str) -> dict[str, Any]:
    session, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    page.bring_to_front()
    session["active_page_id"] = resolved_page_id
    result = {"session_id": session_id, "page_id": resolved_page_id, "url": page.url, "title": page_title(page)}
    record_event("browser_switch_page", **result)
    return result
def browser_close_page(session_id: str, page_id: str) -> dict[str, Any]:
    session, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    page.close()
    refresh_playwright_pages(session)
    result = {"session_id": session_id, "closed_page_id": resolved_page_id, "active_page_id": session.get("active_page_id"), "remaining_pages": len(session["pages"])}
    record_event("browser_close_page", **result)
    return result
def browser_close_session(session_id: str) -> dict[str, Any]:
    instance_name = None
    try:
        session = get_playwright_session(session_id)
        instance_name = session.get("instance_name")
    except Exception:
        session = None
    result = {"session_id": session_id, "closed": close_playwright_session(session_id)}
    if instance_name:
        manifest = _read_instance_manifest(instance_name) or {"instance_name": instance_name}
        _write_instance_manifest(
            instance_name,
            {
                **manifest,
                "status": "stopped",
                "session_id": None,
            },
        )
        result["instance_name"] = instance_name
    record_event("browser_close_session", **result)
    return result
def browser_navigate(session_id: str, url: str, wait_until: str = "networkidle", page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    page.goto(url, wait_until=wait_until)
    result = {"session_id": session_id, "page_id": resolved_page_id, "url": page.url, "title": page_title(page)}
    record_event("browser_navigate", **result)
    return result
def browser_capture_session(session_id: str, path: str | None = None, full_page: bool = False, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    target_path = Path(path) if path else BROWSER_CAPTURE_ROOT / f"session-{session_id}-{resolved_page_id}-{now_stamp()}.png"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(target_path), full_page=full_page)
    result = {"session_id": session_id, "page_id": resolved_page_id, "path": str(target_path), "url": page.url, "title": page_title(page), "full_page": full_page}
    record_event("browser_capture_session", **result)
    return result
def browser_get_dom(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    html = page.content()
    return {"session_id": session_id, "page_id": resolved_page_id, "url": page.url, "title": page_title(page), "html": html, "length": len(html)}
def browser_get_viewport_state(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    state = page.evaluate(
        """() => ({
            url: window.location.href,
            title: document.title,
            scrollX: window.scrollX,
            scrollY: window.scrollY,
            innerWidth: window.innerWidth,
            innerHeight: window.innerHeight,
            outerWidth: window.outerWidth,
            outerHeight: window.outerHeight,
            devicePixelRatio: window.devicePixelRatio,
            documentWidth: document.documentElement.scrollWidth,
            documentHeight: document.documentElement.scrollHeight,
            readyState: document.readyState,
        })"""
    )
    return {"session_id": session_id, "page_id": resolved_page_id, **state}
def browser_reload(session_id: str, wait_until: str = "networkidle", page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    page.reload(wait_until=wait_until)
    result = {"session_id": session_id, "page_id": resolved_page_id, "url": page.url, "title": page_title(page)}
    record_event("browser_reload", **result)
    return result
def browser_go_back(session_id: str, wait_until: str = "networkidle", page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    response = page.go_back(wait_until=wait_until)
    result = {"session_id": session_id, "page_id": resolved_page_id, "url": page.url, "title": page_title(page), "navigated": response is not None}
    record_event("browser_go_back", **result)
    return result
def browser_go_forward(session_id: str, wait_until: str = "networkidle", page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    response = page.go_forward(wait_until=wait_until)
    result = {"session_id": session_id, "page_id": resolved_page_id, "url": page.url, "title": page_title(page), "navigated": response is not None}
    record_event("browser_go_forward", **result)
    return result
def browser_wait_for_url(session_id: str, pattern: str, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    matched_url = wait_for_url_pattern(page, pattern, timeout_ms=timeout_ms)
    result = {"session_id": session_id, "page_id": resolved_page_id, "matched": True, "url": matched_url}
    record_event("browser_wait_for_url", **result)
    return result
def browser_count_selectors(session_id: str, selector: str, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    return {"session_id": session_id, "page_id": resolved_page_id, "selector": selector, "count": page.locator(selector).count()}
def browser_get_text(session_id: str, selector: str, timeout_ms: int = 10000, all_matches: bool = False, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    locator = page.locator(selector)
    locator.first.wait_for(timeout=max(timeout_ms, 1), state="attached")
    if all_matches:
        texts = [text.strip() for text in locator.all_inner_texts()]
        return {"session_id": session_id, "page_id": resolved_page_id, "selector": selector, "count": len(texts), "texts": texts}
    return {"session_id": session_id, "page_id": resolved_page_id, "selector": selector, "text": locator.first.inner_text(timeout=max(timeout_ms, 1)).strip()}
def browser_list_form_fields(session_id: str, page_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    fields = page.evaluate(
        """() => {
            const textOf = (el) => (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 180);
            return Array.from(document.querySelectorAll('input, select, textarea')).map((el, index) => {
                const label =
                    el.labels && el.labels.length
                        ? Array.from(el.labels).map((label) => textOf(label)).join(' ')
                        : null;
                const rect = el.getBoundingClientRect();
                return {
                    index,
                    tag: el.tagName.toLowerCase(),
                    id: el.id || null,
                    name: el.getAttribute('name'),
                    type: el.getAttribute('type') || null,
                    value: 'value' in el ? String(el.value ?? '') : null,
                    placeholder: el.getAttribute('placeholder'),
                    label,
                    required: el.required === true || el.getAttribute('aria-required') === 'true',
                    disabled: Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true'),
                    visible: rect.width > 0 && rect.height > 0,
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                };
            });
        }"""
    )
    bounded = max(1, min(int(limit), 200))
    return {"session_id": session_id, "page_id": resolved_page_id, "count": min(len(fields), bounded), "fields": fields[:bounded]}
def browser_fill_form_field(session_id: str, index: int, value: str, clear_first: bool = True, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    js_index = int(index)
    details = page.evaluate(
        """(targetIndex) => {
            const fields = Array.from(document.querySelectorAll('input, select, textarea'));
            const el = fields[targetIndex];
            if (!el) return null;
            const label =
                el.labels && el.labels.length
                    ? Array.from(el.labels).map((label) => (label.innerText || label.textContent || '').trim()).join(' ')
                    : null;
            return {
                tag: el.tagName.toLowerCase(),
                id: el.id || null,
                name: el.getAttribute('name'),
                type: el.getAttribute('type') || null,
                label,
            };
        }""",
        js_index,
    )
    if details is None:
        raise ValueError(f"Form field index {js_index} is out of range.")
    locator = page.locator("input, select, textarea").nth(js_index)
    tag = details["tag"]
    field_type = (details["type"] or "").lower()
    if tag == "select":
        locator.select_option(value=value, timeout=max(timeout_ms, 1))
    elif field_type in {"checkbox", "radio"}:
        should_check = value.strip().lower() in {"1", "true", "yes", "on", "checked"}
        if should_check:
            locator.check(timeout=max(timeout_ms, 1))
        else:
            locator.uncheck(timeout=max(timeout_ms, 1))
    else:
        if clear_first:
            locator.fill("", timeout=max(timeout_ms, 1))
        locator.fill(value, timeout=max(timeout_ms, 1))
    return {"session_id": session_id, "page_id": resolved_page_id, "index": js_index, "value_length": len(value), "clear_first": clear_first, **details}
def browser_toggle_form_field(session_id: str, index: int, checked: bool = True, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    js_index = int(index)
    details = page.evaluate(
        """(targetIndex) => {
            const fields = Array.from(document.querySelectorAll('input, select, textarea'));
            const el = fields[targetIndex];
            if (!el) return null;
            return {
                tag: el.tagName.toLowerCase(),
                id: el.id || null,
                name: el.getAttribute('name'),
                type: el.getAttribute('type') || null,
            };
        }""",
        js_index,
    )
    if details is None:
        raise ValueError(f"Form field index {js_index} is out of range.")
    field_type = (details["type"] or "").lower()
    if field_type not in {"checkbox", "radio"}:
        raise ValueError(f"Form field index {js_index} is not a checkbox or radio field.")
    locator = page.locator("input, select, textarea").nth(js_index)
    if checked:
        locator.check(timeout=max(timeout_ms, 1))
    else:
        locator.uncheck(timeout=max(timeout_ms, 1))
    return {"session_id": session_id, "page_id": resolved_page_id, "index": js_index, "checked": checked, **details}
def browser_wait_for_selector(session_id: str, selector: str, timeout_ms: int = 10000, state: str = "visible", page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    locator = page.wait_for_selector(selector, timeout=max(timeout_ms, 1), state=state)
    box = locator.bounding_box() if locator else None
    return {"session_id": session_id, "page_id": resolved_page_id, "selector": selector, "found": locator is not None, "box": box}
def browser_wait_for_text(session_id: str, text: str, timeout_ms: int = 10000, exact: bool = False, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    locator = page.get_by_text(text, exact=exact).first
    locator.wait_for(timeout=max(timeout_ms, 1), state="visible")
    result = {"session_id": session_id, "page_id": resolved_page_id, "text": text, "exact": exact, "found": True}
    record_event("browser_wait_for_text", **result)
    return result
def browser_wait_for_dom_change(session_id: str, timeout_ms: int = 10000, poll_ms: int = 250, baseline_hash: str | None = None, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    before_hash = baseline_hash or hashlib.sha256(page.content().encode("utf-8", errors="replace")).hexdigest()
    baseline_revision = ensure_dom_revision_tracker(page)
    try:
        page.wait_for_function("(baseline) => (window.__pmMcpDomRevision || 0) > baseline", arg=baseline_revision, timeout=max(timeout_ms, 1))
        after_hash = hashlib.sha256(page.content().encode("utf-8", errors="replace")).hexdigest()
        result = {"session_id": session_id, "page_id": resolved_page_id, "changed": True, "before_hash": before_hash, "after_hash": after_hash}
        record_event("browser_wait_for_dom_change", **result)
        return result
    except PlaywrightTimeoutError:
        result = {"session_id": session_id, "page_id": resolved_page_id, "changed": False, "before_hash": before_hash, "after_hash": before_hash}
        record_event("browser_wait_for_dom_change", **result)
        return result
def browser_click_text(session_id: str, text: str, timeout_ms: int = 10000, exact: bool = False, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    locator = page.get_by_text(text, exact=exact).first
    locator.click(timeout=max(timeout_ms, 1))
    result = {"session_id": session_id, "page_id": resolved_page_id, "text": text, "exact": exact, "clicked": True}
    record_event("browser_click_text", **result)
    return result


def _save_download_artifact(download: Any, session_id: str) -> dict[str, Any]:
    from ..paths import DOWNLOAD_ROOT

    target_dir = DOWNLOAD_ROOT / str(session_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = getattr(download, "suggested_filename", "download.bin")
    target_path = target_dir / filename
    entry = {"suggested_filename": filename, "path": str(target_path), "saved": False, "url": getattr(download, "url", "")}
    try:
        download.save_as(str(target_path))
        entry["saved"] = target_path.exists()
    except Exception as exc:
        entry["error"] = str(exc)
    return entry
def browser_click_text_and_wait_download(session_id: str, text: str, timeout_ms: int = 10000, exact: bool = False, page_id: str | None = None) -> dict[str, Any]:
    session, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    locator = page.get_by_text(text, exact=exact).first
    with page.expect_download(timeout=max(timeout_ms, 1)) as download_info:
        locator.click(timeout=max(timeout_ms, 1))
    entry = _save_download_artifact(download_info.value, session_id)
    buffers = get_playwright_page_event_buffers(session, resolved_page_id)
    buffers["downloads"].append(entry)
    result = {"session_id": session_id, "page_id": resolved_page_id, "text": text, "exact": exact, "clicked": True, "download": entry}
    record_event("browser_click_text_and_wait_download", session_id=session_id, page_id=resolved_page_id, text=text, exact=exact, download_path=entry.get("path"), saved=entry.get("saved"))
    return result
def browser_click_interactive(session_id: str, index: int, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    js_index = int(index)
    details = page.evaluate(
        """(targetIndex) => {
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
            const matches = Array.from(document.querySelectorAll(selector));
            const el = matches[targetIndex];
            if (!el) return null;
            return { tag: el.tagName.toLowerCase(), text: textOf(el), id: el.id || null, role: el.getAttribute('role') };
        }""",
        js_index,
    )
    if details is None:
        raise ValueError(f"Interactive element index {js_index} is out of range.")
    selector = "a[href], button, input, select, textarea, [role='button'], [role='link'], [role='tab'], [role='menuitem'], [tabindex]:not([tabindex='-1'])"
    page.locator(selector).nth(js_index).click(timeout=max(timeout_ms, 1))
    result = {"session_id": session_id, "page_id": resolved_page_id, "index": js_index, "clicked": True, **details}
    record_event("browser_click_interactive", session_id=session_id, page_id=resolved_page_id, index=js_index, tag=details.get("tag"), role=details.get("role"))
    return result
def browser_click_selector(session_id: str, selector: str, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    page.locator(selector).first.click(timeout=max(timeout_ms, 1))
    result = {"session_id": session_id, "page_id": resolved_page_id, "selector": selector, "clicked": True}
    record_event("browser_click_selector", **result)
    return result
def browser_click_selector_and_wait_download(session_id: str, selector: str, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    session, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    with page.expect_download(timeout=max(timeout_ms, 1)) as download_info:
        page.locator(selector).first.click(timeout=max(timeout_ms, 1))
    entry = _save_download_artifact(download_info.value, session_id)
    buffers = get_playwright_page_event_buffers(session, resolved_page_id)
    buffers["downloads"].append(entry)
    result = {"session_id": session_id, "page_id": resolved_page_id, "selector": selector, "clicked": True, "download": entry}
    record_event("browser_click_selector_and_wait_download", session_id=session_id, page_id=resolved_page_id, selector=selector, download_path=entry.get("path"), saved=entry.get("saved"))
    return result
def browser_type_selector(session_id: str, selector: str, text: str, clear_first: bool = True, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    locator = page.locator(selector).first
    if clear_first:
        locator.fill("", timeout=max(timeout_ms, 1))
    locator.type(text, timeout=max(timeout_ms, 1))
    result = {"session_id": session_id, "page_id": resolved_page_id, "selector": selector, "length": len(text)}
    record_event("browser_type_selector", **result)
    return result
def browser_focus_selector(session_id: str, selector: str, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    locator = page.locator(selector).first
    locator.focus(timeout=max(timeout_ms, 1))
    result = {"session_id": session_id, "page_id": resolved_page_id, "selector": selector, "focused": True}
    record_event("browser_focus_selector", **result)
    return result
def browser_set_input_files(session_id: str, selector: str, paths: list[str], timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    locator = page.locator(selector).first
    locator.set_input_files(paths, timeout=max(timeout_ms, 1))
    result = {"session_id": session_id, "page_id": resolved_page_id, "selector": selector, "file_count": len(paths), "paths": paths}
    record_event("browser_set_input_files", session_id=session_id, page_id=resolved_page_id, selector=selector, file_count=len(paths))
    return result
def browser_press_key(session_id: str, key: str, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    page.keyboard.press(key)
    result = {"session_id": session_id, "page_id": resolved_page_id, "key": key}
    record_event("browser_press_key", **result)
    return result
def browser_scroll_page(session_id: str, delta_y: int = 800, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    before = page.evaluate("() => window.scrollY")
    after = page.evaluate("(dy) => { window.scrollBy(0, dy); return window.scrollY; }", delta_y)
    result = {"session_id": session_id, "page_id": resolved_page_id, "before": before, "after": after, "delta_y": delta_y}
    record_event("browser_scroll_page", **result)
    return result


def browser_scroll_extract(
    session_id: str = "",
    max_scrolls: int = 5,
    extract_mode: str = "text",
    page_id: str | None = None,
) -> dict[str, Any]:
    """Scroll the page incrementally and extract content at each step.
    
    extract_mode: 'text' (body text), 'links' (all links), 'all' (text + links + headings).
    Returns full page content up to ~8000 chars.
    """
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    
    content_parts: list[str] = []
    links: list[dict[str, str]] = []
    
    for i in range(max_scrolls + 1):
        if extract_mode in ("text", "all"):
            text = page.evaluate("""() => {
                const sel = window.getSelection();
                sel.removeAllRanges();
                const visible = Array.from(document.querySelectorAll('p, h1, h2, h3, h4, li, td, span, a, label, button'))
                    .filter(el => { const r = el.getBoundingClientRect(); return r.top >= 0 && r.top < window.innerHeight; })
                    .map(el => el.innerText.trim())
                    .filter(t => t.length > 0);
                return [...new Set(visible)].join('\\n');
            }""")
            if text:
                content_parts.append(text)
        
        if extract_mode in ("links", "all"):
            page_links = page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href]'))
                    .filter(el => { const r = el.getBoundingClientRect(); return r.top >= 0 && r.top < window.innerHeight; })
                    .map(a => ({text: a.innerText.trim(), href: a.href}))
                    .filter(l => l.text.length > 0);
            }""")
            links.extend(page_links)
        
        if i < max_scrolls:
            old_y = page.evaluate("() => window.scrollY")
            page.evaluate("() => window.scrollBy(0, window.innerHeight * 0.8)")
            import time as _time
            _time.sleep(0.3)
            new_y = page.evaluate("() => window.scrollY")
            if new_y == old_y:
                break
    
    full_text = "\n".join(dict.fromkeys(content_parts))[:8000]
    unique_links = {l["href"]: l for l in links}
    
    result: dict[str, Any] = {
        "ok": True,
        "session_id": session_id,
        "page_id": resolved_page_id,
        "scrolls_done": i + 1 if 'i' in dir() else 1,
        "url": page.url,
    }
    if extract_mode in ("text", "all"):
        result["text"] = full_text
        result["text_length"] = len(full_text)
    if extract_mode in ("links", "all"):
        result["links"] = list(unique_links.values())[:100]
        result["link_count"] = len(unique_links)
    
    record_event("browser_scroll_extract", session_id=session_id, scrolls=result.get("scrolls_done"))
    return result


def browser_eval(session_id: str, expression: str, page_id: str | None = None) -> dict[str, Any]:
    validate_js_expression(expression)
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    result = page.evaluate(expression)
    payload = {"session_id": session_id, "page_id": resolved_page_id, "result": result}
    record_event("browser_eval", session_id=session_id, page_id=resolved_page_id)
    return payload
def browser_check_actionable(
    session_id: str,
    selector: str,
    page_id: str | None = None,
    timeout_ms: int = 5000,
) -> dict[str, Any]:
    """Check if an element is visible, stable, and clickable before interacting.

    Returns actionability info: visible, enabled, stable, in_viewport,
    pointer_events, bounding_box. Use before clicking to avoid detection
    or errors from interacting with hidden/moving elements.
    """
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    locator = page.locator(selector).first
    checks: dict[str, Any] = {
        "session_id": session_id,
        "page_id": resolved_page_id,
        "selector": selector,
    }
    try:
        checks["visible"] = locator.is_visible(timeout=timeout_ms)
    except Exception:
        checks["visible"] = False
    try:
        checks["enabled"] = locator.is_enabled(timeout=timeout_ms)
    except Exception:
        checks["enabled"] = False
    try:
        box = locator.bounding_box(timeout=timeout_ms)
        checks["bounding_box"] = box
        checks["in_viewport"] = box is not None
    except Exception:
        checks["bounding_box"] = None
        checks["in_viewport"] = False
    # Check pointer-events and stability via JS
    try:
        info = locator.evaluate("""(el) => {
            const style = window.getComputedStyle(el);
            const rect1 = el.getBoundingClientRect();
            return {
                pointer_events: style.pointerEvents,
                opacity: parseFloat(style.opacity),
                display: style.display,
                visibility: style.visibility,
                rect: { x: rect1.x, y: rect1.y, w: rect1.width, h: rect1.height },
            };
        }""")
        checks["pointer_events"] = info.get("pointer_events", "unknown")
        checks["opacity"] = info.get("opacity", 1.0)
        checks["display"] = info.get("display", "unknown")
        checks["css_visibility"] = info.get("visibility", "unknown")
        checks["interactable"] = (
            checks["visible"]
            and checks["enabled"]
            and checks.get("pointer_events") != "none"
            and checks.get("opacity", 0) > 0
            and checks.get("display") != "none"
            and checks.get("css_visibility") != "hidden"
        )
    except Exception:
        checks["interactable"] = checks["visible"] and checks["enabled"]
    # Stability check — bounding box doesn't move over 100ms
    try:
        box1 = locator.bounding_box(timeout=1000)
        page.wait_for_timeout(100)
        box2 = locator.bounding_box(timeout=1000)
        if box1 and box2:
            dx = abs(box2["x"] - box1["x"])
            dy = abs(box2["y"] - box1["y"])
            checks["stable"] = dx < 2 and dy < 2
        else:
            checks["stable"] = False
    except Exception:
        checks["stable"] = False
    checks["actionable"] = checks.get("interactable", False) and checks.get("stable", False)
    record_event("browser_check_actionable", session_id=session_id, selector=selector, actionable=checks["actionable"])
    return checks
def browser_stealth_eval(session_id: str, expression: str, page_id: str | None = None) -> dict[str, Any]:
    """Evaluate JS in a CDP Isolated World — invisible to page scripts.

    Uses Runtime.evaluate with a unique contextId so the page's own JS
    cannot observe the call (no MutationObserver, no Proxy trap, etc.).
    Falls back to standard page.evaluate if CDP is unavailable.
    """
    validate_js_expression(expression)
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    try:
        cdp = page.context.new_cdp_session(page)
        try:
            # Create an isolated world on the main frame
            frame_tree = cdp.send("Page.getFrameTree")
            frame_id = frame_tree["frameTree"]["frame"]["id"]
            world = cdp.send("Page.createIsolatedWorld", {
                "frameId": frame_id,
                "worldName": "__stealth_eval__",
                "grantUniveralAccess": True,
            })
            context_id = world["executionContextId"]
            # Evaluate in the isolated context
            resp = cdp.send("Runtime.evaluate", {
                "expression": expression,
                "contextId": context_id,
                "returnByValue": True,
                "awaitPromise": True,
            })
            if resp.get("exceptionDetails"):
                raise RuntimeError(resp["exceptionDetails"].get("text", "CDP eval error"))
            result = resp.get("result", {}).get("value")
            method = "cdp_isolated_world"
        finally:
            cdp.detach()
    except Exception:
        # Fallback to standard eval
        result = page.evaluate(expression)
        method = "standard_fallback"
    payload = {
        "session_id": session_id,
        "page_id": resolved_page_id,
        "result": result,
        "method": method,
    }
    record_event("browser_stealth_eval", session_id=session_id, page_id=resolved_page_id, method=method)
    return payload
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
def browser_smart_fill(
    session_id: str = "",
    fields: dict[str, str] | None = None,
    submit: bool = False,
    page_id: str | None = None,
) -> dict[str, Any]:
    """Detect form fields and fill them by matching label/placeholder/name/id.
    
    fields: dict mapping human-readable name to value, e.g. {"email": "user@x.com", "password": "***"}.
    The function fuzzy-matches keys against field labels, placeholders, names, and aria-labels.
    """
    if not fields:
        return {"ok": False, "error": "fields dict required, e.g. {\"email\": \"...\", \"password\": \"...\"}"}
    
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    
    # Get all form fields with their attributes
    form_fields = page.evaluate("""() => {
        const inputs = Array.from(document.querySelectorAll('input, textarea, select'));
        return inputs.map((el, idx) => {
            const label = el.labels?.[0]?.innerText?.trim() || '';
            return {
                idx,
                tag: el.tagName.toLowerCase(),
                type: el.type || 'text',
                name: el.name || '',
                id: el.id || '',
                placeholder: el.placeholder || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                label,
                visible: el.offsetParent !== null,
            };
        }).filter(f => f.visible && f.type !== 'hidden');
    }""")
    
    filled = []
    not_found = []
    
    for key, value in fields.items():
        key_lower = key.lower().strip()
        best_field = None
        best_score = 0
        
        for f in form_fields:
            score = 0
            searchable = f"{f['name']} {f['id']} {f['placeholder']} {f['ariaLabel']} {f['label']}".lower()
            if key_lower == f['name'].lower() or key_lower == f['id'].lower():
                score = 100
            elif key_lower in searchable:
                score = 50
            elif any(w in searchable for w in key_lower.split()):
                score = 25
            
            if score > best_score:
                best_score = score
                best_field = f
        
        if best_field:
            idx = best_field['idx']
            if best_field['tag'] == 'select':
                page.evaluate(f"""(v) => {{
                    const el = document.querySelectorAll('input, textarea, select')[{idx}];
                    el.value = v;
                    el.dispatchEvent(new Event('change', {{bubbles: true}}));
                }}""", value)
            else:
                page.evaluate(f"""() => {{
                    const el = document.querySelectorAll('input, textarea, select')[{idx}];
                    el.focus();
                    el.value = '';
                    el.dispatchEvent(new Event('input', {{bubbles: true}}));
                }}""")
                locator = page.locator(f"input, textarea, select").nth(idx)
                locator.fill(str(value))
            filled.append({"key": key, "matched": f"{best_field['tag']}[name={best_field['name']}]", "score": best_score})
            # Mark as used
            form_fields = [f for f in form_fields if f['idx'] != idx]
        else:
            not_found.append(key)
    
    submitted = False
    if submit and filled:
        try:
            btn = page.locator("button[type=submit], input[type=submit], button:has-text('Submit'), button:has-text('Log'), button:has-text('Sign')").first
            btn.click(timeout=3000)
            submitted = True
        except Exception:
            pass
    
    result = {
        "ok": len(filled) > 0,
        "session_id": session_id,
        "page_id": resolved_page_id,
        "filled": filled,
        "not_found": not_found,
        "submitted": submitted,
    }
    record_event("browser_smart_fill", session_id=session_id, filled_count=len(filled))
    return result


def browser_tab_summary(session_id: str = "") -> dict[str, Any]:
    """Return a summary of all open tabs/pages in the session."""
    from ..browser_sessions import get_playwright_session, list_playwright_sessions
    
    if not session_id:
        sessions = list_playwright_sessions()
        if not sessions:
            return {"ok": False, "error": "No active sessions"}
        session_id = sessions[0][0]
    
    session = get_playwright_session(session_id)
    context = session.get("context")
    if not context:
        return {"ok": False, "error": "Session has no browser context"}
    
    pages = context.pages
    tabs = []
    for i, page in enumerate(pages):
        try:
            tabs.append({
                "index": i,
                "url": page.url,
                "title": page.title() if page.url != "about:blank" else "(blank)",
                "active": page == session.get("page"),
            })
        except Exception:
            tabs.append({"index": i, "url": "?", "title": "?", "active": False})
    
    return {"ok": True, "session_id": session_id, "tabs": tabs, "count": len(tabs)}


def browser_wait_for_load_state(session_id: str, state: str = "networkidle", timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    page.wait_for_load_state(state=state, timeout=max(timeout_ms, 1))
    result = {"session_id": session_id, "page_id": resolved_page_id, "state": state, "ready": True, "url": page.url}
    record_event("browser_wait_for_load_state", **result)
    return result
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
        result = {"session_id": session_id, "page_id": resolved_page_id, "selector": selector, "changed": True, "before_hash": before_hash, "after_hash": after_hash}
        record_event("browser_wait_for_visual_change", **result)
        return result
    except PlaywrightTimeoutError:
        result = {"session_id": session_id, "page_id": resolved_page_id, "selector": selector, "changed": False, "before_hash": before_hash, "after_hash": before_hash}
        record_event("browser_wait_for_visual_change", **result)
        return result
def browser_get_console_logs(session_id: str, page_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    session, resolved_page_id, _ = get_playwright_page(session_id, page_id=page_id)
    buffers = get_playwright_page_event_buffers(session, resolved_page_id)
    entries = buffers["console"][-max(1, min(int(limit), 100)) :]
    return {"session_id": session_id, "page_id": resolved_page_id, "count": len(entries), "entries": entries}
def browser_get_network_errors(session_id: str, page_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    session, resolved_page_id, _ = get_playwright_page(session_id, page_id=page_id)
    buffers = get_playwright_page_event_buffers(session, resolved_page_id)
    request_failures = buffers["request_failures"][-max(1, min(int(limit), 100)) :]
    page_errors = buffers["page_errors"][-max(1, min(int(limit), 100)) :]
    return {"session_id": session_id, "page_id": resolved_page_id, "request_failure_count": len(request_failures), "page_error_count": len(page_errors), "request_failures": request_failures, "page_errors": page_errors}
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
    session, resolved_page_id, _ = get_playwright_page(session_id, page_id=page_id)
    buffers = get_playwright_page_event_buffers(session, resolved_page_id)
    request_items = buffers["requests"][-max(1, min(int(limit), 200)) :]
    response_map = {item.get("request_id"): item for item in buffers["responses"]}
    failure_map = {item.get("request_id"): item for item in buffers["request_failures"]}
    method_filter = (method or "").upper().strip()
    status_filter = int(status) if status is not None else None
    status_min_filter = int(status_min) if status_min is not None else None
    status_max_filter = int(status_max) if status_max is not None else None
    resource_filter = (resource_type or "").strip().lower()
    url_filter = (url_contains or "").strip().lower()
    mime_filter = (mime_contains or "").strip().lower()
    entries: list[dict[str, Any]] = []
    for item in request_items:
        if method_filter and str(item.get("method", "")).upper() != method_filter:
            continue
        if resource_filter and str(item.get("resource_type", "")).lower() != resource_filter:
            continue
        if url_filter and url_filter not in str(item.get("url", "")).lower():
            continue
        merged = {
            "request_id": item.get("request_id"),
            "url": item.get("url"),
            "method": item.get("method"),
            "resource_type": item.get("resource_type"),
            "timestamp": item.get("timestamp"),
        }
        if include_headers:
            merged["request_headers"] = item.get("headers", {})
        if item.get("post_data") is not None:
            merged["post_data"] = item.get("post_data")
        response = response_map.get(item.get("request_id"))
        if response:
            merged.update(
                {
                    "status": response.get("status"),
                    "status_text": response.get("status_text"),
                    "ok": response.get("ok"),
                    "response_timestamp": response.get("timestamp"),
                    "duration_ms": (
                        max(0, int((float(response.get("timestamp", item.get("timestamp", time.time()))) - float(item.get("timestamp", time.time()))) * 1000))
                        if item.get("timestamp") is not None
                        else None
                    ),
                }
            )
            if include_headers:
                merged["response_headers"] = response.get("headers", {})
        failure = failure_map.get(item.get("request_id"))
        if failure:
            merged["failure"] = failure
        if status_filter is not None and merged.get("status") != status_filter:
            continue
        if status_min_filter is not None and (merged.get("status") is None or int(merged.get("status")) < status_min_filter):
            continue
        if status_max_filter is not None and (merged.get("status") is None or int(merged.get("status")) > status_max_filter):
            continue
        if mime_filter:
            response_headers = merged.get("response_headers", response.get("headers", {}) if response else {})
            content_type = str((response_headers or {}).get("content-type", "")).lower()
            if mime_filter not in content_type:
                continue
        if has_body is True:
            body_value = merged.get("post_data")
            if body_value in (None, "", b""):
                continue
        if has_body is False:
            body_value = merged.get("post_data")
            if body_value not in (None, "", b""):
                continue
        if failed_only and not failure:
            continue
        entries.append(merged)
    reverse = (sort_order or "desc").lower() != "asc"
    sort_key = (sort_by or "timestamp").strip().lower()
    if sort_key not in {"timestamp", "status", "method", "url", "duration_ms"}:
        raise ValueError("sort_by must be one of: timestamp, status, method, url, duration_ms")
    entries = sorted(entries, key=lambda item: (item.get(sort_key) is None, item.get(sort_key)), reverse=reverse)
    total_count = len(entries)
    resolved_offset = max(0, int(offset))
    resolved_limit = max(1, min(int(limit), 200))
    paged_entries = entries[resolved_offset : resolved_offset + resolved_limit]
    return {
        "session_id": session_id,
        "page_id": resolved_page_id,
        "count": len(paged_entries),
        "total_count": total_count,
        "offset": resolved_offset,
        "limit": resolved_limit,
        "sort_by": sort_key,
        "sort_order": "desc" if reverse else "asc",
        "filters": {
            "method": method,
            "status": status,
            "resource_type": resource_type,
            "url_contains": url_contains,
            "failed_only": failed_only,
            "status_min": status_min,
            "status_max": status_max,
            "mime_contains": mime_contains,
            "has_body": has_body,
        },
        "entries": paged_entries,
    }
def browser_get_network_request(session_id: str, request_id: str, page_id: str | None = None) -> dict[str, Any]:
    session, resolved_page_id, _ = get_playwright_page(session_id, page_id=page_id)
    buffers = get_playwright_page_event_buffers(session, resolved_page_id)
    request = next((item for item in reversed(buffers["requests"]) if item.get("request_id") == request_id), None)
    if not request:
        raise ValueError(f"Unknown network request id: {request_id}")
    response = next((item for item in reversed(buffers["responses"]) if item.get("request_id") == request_id), None)
    failure = next((item for item in reversed(buffers["request_failures"]) if item.get("request_id") == request_id), None)
    return {
        "session_id": session_id,
        "page_id": resolved_page_id,
        "request": request,
        "response": response,
        "failure": failure,
    }
def browser_export_network_har(
    session_id: str,
    page_id: str | None = None,
    path: str | None = None,
    include_headers: bool = True,
) -> dict[str, Any]:
    session, resolved_page_id, _ = get_playwright_page(session_id, page_id=page_id)
    buffers = get_playwright_page_event_buffers(session, resolved_page_id)
    response_map = {item.get("request_id"): item for item in buffers["responses"]}
    failure_map = {item.get("request_id"): item for item in buffers["request_failures"]}
    entries: list[dict[str, Any]] = []
    for request in buffers["requests"]:
        response = response_map.get(request.get("request_id"))
        failure = failure_map.get(request.get("request_id"))
        request_headers = request.get("headers", {}) if include_headers else {}
        response_headers = response.get("headers", {}) if include_headers and response else {}
        started_ts = float(request.get("timestamp", time.time()))
        ended_ts = float(response.get("timestamp", started_ts)) if response else started_ts
        entries.append(
            {
                "startedDateTime": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(started_ts)),
                "time": max(0, int((ended_ts - started_ts) * 1000)),
                "pageref": resolved_page_id,
                "request": {
                    "method": request.get("method"),
                    "url": request.get("url"),
                    "headers": [{"name": k, "value": str(v)} for k, v in request_headers.items()],
                    "queryString": [
                        {"name": part.split("=", 1)[0], "value": part.split("=", 1)[1] if "=" in part else ""}
                        for part in str(request.get("url", "")).split("?", 1)[1].split("&")
                    ] if "?" in str(request.get("url", "")) else [],
                    "headersSize": -1,
                    "bodySize": len(str(request.get("post_data", ""))) if request.get("post_data") is not None else 0,
                    "postData": {"text": request.get("post_data")} if request.get("post_data") is not None else None,
                },
                "response": {
                    "status": response.get("status") if response else 0,
                    "statusText": response.get("status_text") if response else "",
                    "headers": [{"name": k, "value": str(v)} for k, v in response_headers.items()],
                    "content": {
                        "size": int(response_headers.get("content-length", 0) or 0),
                        "mimeType": response_headers.get("content-type", ""),
                    },
                    "redirectURL": response_headers.get("location", ""),
                    "headersSize": -1,
                    "bodySize": int(response_headers.get("content-length", 0) or 0),
                    "ok": response.get("ok") if response else False,
                },
                "cache": {},
                "timings": {"send": 0, "wait": max(0, int((ended_ts - started_ts) * 1000)), "receive": 0},
                "comment": failure.get("error_text") if failure else "",
                "_requestId": request.get("request_id"),
                "_resourceType": request.get("resource_type"),
            }
        )
    target_path = Path(path) if path else BROWSER_CAPTURE_ROOT / f"har-{session_id}-{resolved_page_id}-{now_stamp()}.json"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "log": {
            "version": "1.2",
            "creator": {"name": "windows-desktop-mcp", "version": "1"},
            "pages": [{"id": resolved_page_id, "title": resolved_page_id, "startedDateTime": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())}],
            "entries": entries,
        }
    }
    target_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    result = {
        "session_id": session_id,
        "page_id": resolved_page_id,
        "path": str(target_path),
        "entry_count": len(entries),
        "pseudo_har": True,
    }
    record_event("browser_export_network_har", **result)
    return result
def browser_get_dialogs(session_id: str, page_id: str | None = None, limit: int = 25) -> dict[str, Any]:
    session, resolved_page_id, _ = get_playwright_page(session_id, page_id=page_id)
    buffers = get_playwright_page_event_buffers(session, resolved_page_id)
    dialogs = buffers["dialogs"][-max(1, min(int(limit), 50)) :]
    return {"session_id": session_id, "page_id": resolved_page_id, "count": len(dialogs), "dialogs": dialogs}
def browser_list_downloads(session_id: str, page_id: str | None = None, limit: int = 25) -> dict[str, Any]:
    session, resolved_page_id, _ = get_playwright_page(session_id, page_id=page_id)
    buffers = get_playwright_page_event_buffers(session, resolved_page_id)
    downloads = buffers["downloads"][-max(1, min(int(limit), 50)) :]
    return {"session_id": session_id, "page_id": resolved_page_id, "count": len(downloads), "downloads": downloads}
def browser_wait_for_download(session_id: str, page_id: str | None = None, timeout_ms: int = 10000, minimum_count: int = 1) -> dict[str, Any]:
    session, resolved_page_id, _ = get_playwright_page(session_id, page_id=page_id)
    deadline = time.time() + (max(timeout_ms, 1) / 1000)
    target_count = max(1, int(minimum_count))
    while time.time() < deadline:
        buffers = get_playwright_page_event_buffers(session, resolved_page_id)
        downloads = buffers["downloads"]
        if len(downloads) >= target_count:
            return {"session_id": session_id, "page_id": resolved_page_id, "count": len(downloads), "downloads": downloads}
        time.sleep(0.1)
    buffers = get_playwright_page_event_buffers(session, resolved_page_id)
    return {"session_id": session_id, "page_id": resolved_page_id, "count": len(buffers["downloads"]), "downloads": buffers["downloads"], "timed_out": True}
def browser_get_page_summary(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    summary = page.evaluate(
        """() => {
            const text = (document.body?.innerText || '').trim().replace(/\\s+/g, ' ');
            const headings = Array.from(document.querySelectorAll('h1,h2,h3')).map((el) => ({
                level: el.tagName.toLowerCase(),
                text: (el.innerText || el.textContent || '').trim().slice(0, 200),
            }));
            return {
                title: document.title,
                url: window.location.href,
                readyState: document.readyState,
                body_text_excerpt: text.slice(0, 1000),
                heading_count: headings.length,
                headings: headings.slice(0, 20),
                link_count: document.querySelectorAll('a[href]').length,
                button_count: document.querySelectorAll('button, [role=\"button\"]').length,
                form_field_count: document.querySelectorAll('input, select, textarea').length,
            };
        }"""
    )
    return {"session_id": session_id, "page_id": resolved_page_id, **summary}
def browser_get_performance_metrics(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    metrics = page.evaluate(
        """() => {
            const nav = performance.getEntriesByType('navigation')[0];
            const paints = performance.getEntriesByType('paint');
            const resources = performance.getEntriesByType('resource');
            const memory = performance.memory || null;
            const paintMap = Object.fromEntries(paints.map((entry) => [entry.name, entry.startTime]));
            const byType = {};
            for (const entry of resources) {
                const key = entry.initiatorType || 'other';
                if (!byType[key]) {
                    byType[key] = {count: 0, transferSize: 0, encodedBodySize: 0, decodedBodySize: 0, durationMs: 0};
                }
                byType[key].count += 1;
                byType[key].transferSize += entry.transferSize || 0;
                byType[key].encodedBodySize += entry.encodedBodySize || 0;
                byType[key].decodedBodySize += entry.decodedBodySize || 0;
                byType[key].durationMs += entry.duration || 0;
            }
            const slowestResources = resources
                .slice()
                .sort((a, b) => (b.duration || 0) - (a.duration || 0))
                .slice(0, 10)
                .map((entry) => ({
                    name: entry.name,
                    initiatorType: entry.initiatorType || 'other',
                    durationMs: entry.duration || 0,
                    transferSize: entry.transferSize || 0,
                }));
            const navigationTimings = nav ? {
                startTime: nav.startTime,
                fetchStart: nav.fetchStart,
                domainLookupStart: nav.domainLookupStart,
                domainLookupEnd: nav.domainLookupEnd,
                connectStart: nav.connectStart,
                secureConnectionStart: nav.secureConnectionStart,
                connectEnd: nav.connectEnd,
                requestStart: nav.requestStart,
                responseStart: nav.responseStart,
                responseEnd: nav.responseEnd,
                domInteractive: nav.domInteractive,
                domContentLoadedEventStart: nav.domContentLoadedEventStart,
                domContentLoadedEventEnd: nav.domContentLoadedEventEnd,
                loadEventStart: nav.loadEventStart,
                loadEventEnd: nav.loadEventEnd,
            } : null;
            const longTaskCount = performance.getEntriesByType('longtask').length;
            return {
                url: location.href,
                title: document.title,
                domContentLoadedMs: nav ? nav.domContentLoadedEventEnd : null,
                loadEventMs: nav ? nav.loadEventEnd : null,
                responseEndMs: nav ? nav.responseEnd : null,
                domInteractiveMs: nav ? nav.domInteractive : null,
                firstPaintMs: paintMap['first-paint'] ?? null,
                firstContentfulPaintMs: paintMap['first-contentful-paint'] ?? null,
                resourceCount: resources.length,
                transferSize: resources.reduce((sum, entry) => sum + (entry.transferSize || 0), 0),
                encodedBodySize: resources.reduce((sum, entry) => sum + (entry.encodedBodySize || 0), 0),
                decodedBodySize: resources.reduce((sum, entry) => sum + (entry.decodedBodySize || 0), 0),
                jsHeapUsedSize: memory ? memory.usedJSHeapSize : null,
                jsHeapTotalSize: memory ? memory.totalJSHeapSize : null,
                resourceCategories: byType,
                slowestResources,
                navigationTimings,
                longTaskCount,
            };
        }"""
    )
    return {"session_id": session_id, "page_id": resolved_page_id, **metrics}
def browser_get_network_summary(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    session, resolved_page_id, _ = get_playwright_page(session_id, page_id=page_id)
    buffers = get_playwright_page_event_buffers(session, resolved_page_id)
    responses = {item.get("request_id"): item for item in buffers["responses"]}
    failures = {item.get("request_id"): item for item in buffers["request_failures"]}
    by_status: dict[str, int] = {}
    by_resource_type: dict[str, int] = {}
    total_transfer_size = 0
    slowest: list[dict[str, Any]] = []
    for request in buffers["requests"]:
        response = responses.get(request.get("request_id"))
        failure = failures.get(request.get("request_id"))
        resource_key = str(request.get("resource_type") or "other")
        by_resource_type[resource_key] = by_resource_type.get(resource_key, 0) + 1
        if response:
            status_key = str(response.get("status"))
            by_status[status_key] = by_status.get(status_key, 0) + 1
            headers = response.get("headers", {}) or {}
            total_transfer_size += int(headers.get("content-length", 0) or 0)
            duration_ms = max(0, int((float(response.get("timestamp", request.get("timestamp", time.time()))) - float(request.get("timestamp", time.time()))) * 1000))
            slowest.append(
                {
                    "request_id": request.get("request_id"),
                    "url": request.get("url"),
                    "status": response.get("status"),
                    "resource_type": resource_key,
                    "duration_ms": duration_ms,
                }
            )
        elif failure:
            by_status["failed"] = by_status.get("failed", 0) + 1
    slowest = sorted(slowest, key=lambda item: item.get("duration_ms", 0), reverse=True)[:10]
    return {
        "session_id": session_id,
        "page_id": resolved_page_id,
        "request_count": len(buffers["requests"]),
        "response_count": len(buffers["responses"]),
        "failure_count": len(buffers["request_failures"]),
        "by_status": by_status,
        "by_resource_type": by_resource_type,
        "total_transfer_size": total_transfer_size,
        "slowest_requests": slowest,
    }
def browser_start_trace(
    session_id: str,
    screenshots: bool = True,
    snapshots: bool = True,
    sources: bool = False,
    trace_name: str | None = None,
) -> dict[str, Any]:
    session = get_playwright_session(session_id)
    trace_state = session.setdefault("trace_state", {})
    session["context"].tracing.start(
        screenshots=bool(screenshots),
        snapshots=bool(snapshots),
        sources=bool(sources),
    )
    trace_state.update(
        {
            "active": True,
            "started_at": time.time(),
            "screenshots": bool(screenshots),
            "snapshots": bool(snapshots),
            "sources": bool(sources),
            "trace_name": trace_name or f"trace-{session_id}",
        }
    )
    result = {"session_id": session_id, **trace_state}
    record_event("browser_start_trace", **result)
    return result
def browser_stop_trace(session_id: str, path: str | None = None) -> dict[str, Any]:
    session = get_playwright_session(session_id)
    trace_state = session.setdefault("trace_state", {})
    target_path = Path(path) if path else BROWSER_CAPTURE_ROOT / f"trace-{session_id}-{now_stamp()}.zip"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    session["context"].tracing.stop(path=str(target_path))
    started_at = trace_state.get("started_at")
    trace_state.clear()
    result = {
        "session_id": session_id,
        "path": str(target_path),
        "active": False,
        "duration_seconds": max(0.0, time.time() - float(started_at)) if started_at else None,
    }
    record_event("browser_stop_trace", **result)
    return result
def browser_start_coverage(
    session_id: str,
    page_id: str | None = None,
    include_js: bool = True,
    include_css: bool = True,
    call_count: bool = False,
) -> dict[str, Any]:
    session, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    browser_name = str(session.get("browser_name") or "").lower()
    if browser_name not in {"chrome", "edge", "chromium", "auto"}:
        raise ValueError("Coverage is currently only supported for Chromium-based browser sessions.")
    coverage_state = session.setdefault("coverage_state", {})
    page_state = coverage_state.setdefault(resolved_page_id, {})
    cdp_session = page.context.new_cdp_session(page)
    page_state["cdp_session"] = cdp_session
    page_state["include_js"] = bool(include_js)
    page_state["include_css"] = bool(include_css)
    page_state["call_count"] = bool(call_count)
    page_state["started_at"] = time.time()
    if include_js:
        cdp_session.send("Profiler.enable")
        cdp_session.send("Profiler.startPreciseCoverage", {"callCount": bool(call_count), "detailed": True})
    if include_css:
        cdp_session.send("DOM.enable")
        cdp_session.send("CSS.enable")
        cdp_session.send("CSS.startRuleUsageTracking")
    result = {
        "session_id": session_id,
        "page_id": resolved_page_id,
        "active": True,
        "include_js": bool(include_js),
        "include_css": bool(include_css),
        "call_count": bool(call_count),
    }
    record_event("browser_start_coverage", **result)
    return result
def browser_stop_coverage(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    session, resolved_page_id, _ = get_playwright_page(session_id, page_id=page_id)
    coverage_state = session.setdefault("coverage_state", {})
    page_state = coverage_state.get(resolved_page_id)
    if not page_state or "cdp_session" not in page_state:
        raise ValueError(f"No active coverage session for page: {resolved_page_id}")
    cdp_session = page_state["cdp_session"]
    js_entries: list[dict[str, Any]] = []
    css_entries: list[dict[str, Any]] = []
    if page_state.get("include_js"):
        js_result = cdp_session.send("Profiler.takePreciseCoverage")
        for item in js_result.get("result", []):
            functions = item.get("functions", [])
            total_bytes = 0
            used_bytes = 0
            for fn in functions:
                for rng in fn.get("ranges", []):
                    count = int(rng.get("count", 0) or 0)
                    start = int(rng.get("startOffset", 0) or 0)
                    end = int(rng.get("endOffset", start) or start)
                    size = max(0, end - start)
                    total_bytes += size
                    if count > 0:
                        used_bytes += size
            js_entries.append(
                {
                    "url": item.get("url"),
                    "script_id": item.get("scriptId"),
                    "function_count": len(functions),
                    "used_bytes": used_bytes,
                    "total_bytes": total_bytes,
                    "used_ratio": (used_bytes / total_bytes) if total_bytes else None,
                }
            )
        cdp_session.send("Profiler.stopPreciseCoverage")
        cdp_session.send("Profiler.disable")
    if page_state.get("include_css"):
        css_result = cdp_session.send("CSS.stopRuleUsageTracking")
        for item in css_result.get("ruleUsage", []):
            css_entries.append(
                {
                    "style_sheet_id": item.get("styleSheetId"),
                    "start_offset": item.get("startOffset"),
                    "end_offset": item.get("endOffset"),
                    "used": bool(item.get("used")),
                }
            )
        cdp_session.send("CSS.disable")
        cdp_session.send("DOM.disable")
    started_at = page_state.get("started_at")
    coverage_state.pop(resolved_page_id, None)
    js_summary = {
        "script_count": len(js_entries),
        "total_bytes": sum(int(item.get("total_bytes", 0) or 0) for item in js_entries),
        "used_bytes": sum(int(item.get("used_bytes", 0) or 0) for item in js_entries),
    }
    js_summary["used_ratio"] = (js_summary["used_bytes"] / js_summary["total_bytes"]) if js_summary["total_bytes"] else None
    css_summary = {
        "rule_count": len(css_entries),
        "used_rule_count": sum(1 for item in css_entries if item.get("used")),
    }
    css_summary["used_ratio"] = (css_summary["used_rule_count"] / css_summary["rule_count"]) if css_summary["rule_count"] else None
    result = {
        "session_id": session_id,
        "page_id": resolved_page_id,
        "active": False,
        "duration_seconds": max(0.0, time.time() - float(started_at)) if started_at else None,
        "js_summary": js_summary,
        "css_summary": css_summary,
        "js_entries": js_entries,
        "css_entries": css_entries,
    }
    record_event("browser_stop_coverage", session_id=session_id, page_id=resolved_page_id, js_script_count=js_summary["script_count"], css_rule_count=css_summary["rule_count"])
    return result
def browser_export_coverage_json(
    session_id: str,
    page_id: str | None = None,
    path: str | None = None,
) -> dict[str, Any]:
    return _browser_export_coverage_json_impl(session_id=session_id, page_id=page_id, path=path)


def _call_sync_tool(fn: Any, *args: Any, **kwargs: Any) -> Any:
    target = getattr(fn, "__wrapped__", fn)
    return target(*args, **kwargs)
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
def browser_list_intercepts(session_id: str) -> dict[str, Any]:
    session = get_playwright_session(session_id)
    rules = []
    for rule in session.get("intercept_rules", []):
        rules.append({key: value for key, value in rule.items() if key not in {"body"}} | {"body_length": len(rule["body"]) if rule.get("body") else 0})
    return {"session_id": session_id, "count": len(rules), "rules": rules}
def browser_clear_intercepts(session_id: str) -> dict[str, Any]:
    session = get_playwright_session(session_id)
    cleared = len(session.get("intercept_rules", []))
    session["intercept_rules"] = []
    applied = apply_playwright_intercepts(session)
    result = {"session_id": session_id, "cleared": cleared, "applied": applied["applied"]}
    record_event("browser_clear_intercepts", **result)
    return result
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
def browser_hover_selector(session_id: str, selector: str, timeout_ms: int = 10000, page_id: str | None = None) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    page.locator(selector).first.hover(timeout=max(timeout_ms, 1))
    result = {"session_id": session_id, "page_id": resolved_page_id, "selector": selector, "hovered": True}
    record_event("browser_hover_selector", **result)
    return result
def browser_capture_live_page(
    url: str,
    width: int = 1440,
    height: int = 960,
    wait_seconds: float = 2.5,
    browser: str = "auto",
    path: str | None = None,
    close_after: bool = False,
) -> Image:
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
def browser_save_live_page_screenshot(
    url: str,
    width: int = 1440,
    height: int = 960,
    wait_seconds: float = 2.5,
    browser: str = "auto",
    prefix: str = "browser-live",
    close_after: bool = False,
) -> dict[str, Any]:
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


def _browser_export_coverage_json_impl(
    session_id: str,
    page_id: str | None = None,
    path: str | None = None,
) -> dict[str, Any]:
    coverage = _call_sync_tool(browser_stop_coverage, session_id, page_id=page_id)
    target_path = Path(path) if path else BROWSER_CAPTURE_ROOT / f"coverage-{session_id}-{coverage['page_id']}-{now_stamp()}.json"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(coverage, indent=2, ensure_ascii=False), encoding="utf-8")
    result = {
        "session_id": session_id,
        "page_id": coverage["page_id"],
        "path": str(target_path),
        "js_script_count": coverage["js_summary"]["script_count"],
        "css_rule_count": coverage["css_summary"]["rule_count"],
    }
    record_event("browser_export_coverage_json", **result)
    return result
def browser_debug_snapshot(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    return _browser_debug_snapshot_impl(session_id=session_id, page_id=page_id)


def _browser_debug_snapshot_impl(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    session, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    page_summary = _call_sync_tool(browser_get_page_summary, session_id, page_id=resolved_page_id)
    perf_metrics = _call_sync_tool(browser_get_performance_metrics, session_id, page_id=resolved_page_id)
    network_summary = _call_sync_tool(browser_get_network_summary, session_id, page_id=resolved_page_id)
    console_logs = _call_sync_tool(browser_get_console_logs, session_id, page_id=resolved_page_id, limit=20)
    network_errors = _call_sync_tool(browser_get_network_errors, session_id, page_id=resolved_page_id, limit=20)
    viewport_state = _call_sync_tool(browser_get_viewport_state, session_id, page_id=resolved_page_id)
    coverage_state = session.get("coverage_state", {}).get(resolved_page_id, {})
    trace_state = session.get("trace_state", {})
    return {
        "session_id": session_id,
        "page_id": resolved_page_id,
        "url": page.url,
        "title": page_title(page),
        "page_summary": page_summary,
        "performance": perf_metrics,
        "network": network_summary,
        "console": console_logs,
        "errors": network_errors,
        "viewport": viewport_state,
        "coverage_active": bool(coverage_state),
        "trace_active": bool(trace_state.get("active")),
    }
def browser_debug_report(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    return _browser_debug_report_impl(session_id=session_id, page_id=page_id)


def _browser_debug_report_impl(session_id: str, page_id: str | None = None) -> dict[str, Any]:
    snapshot = _call_sync_tool(browser_debug_snapshot, session_id, page_id=page_id)
    perf = snapshot["performance"]
    network = snapshot["network"]
    console = snapshot["console"]
    errors = snapshot["errors"]
    issues: list[str] = []
    if errors.get("page_error_count", 0):
        issues.append(f"{errors['page_error_count']} page error(s)")
    if errors.get("request_failure_count", 0):
        issues.append(f"{errors['request_failure_count']} request failure(s)")
    if perf.get("loadEventMs") and perf["loadEventMs"] > 3000:
        issues.append(f"slow load event: {perf['loadEventMs']} ms")
    if perf.get("firstContentfulPaintMs") and perf["firstContentfulPaintMs"] > 1800:
        issues.append(f"slow first contentful paint: {perf['firstContentfulPaintMs']} ms")
    if perf.get("longTaskCount", 0):
        issues.append(f"{perf['longTaskCount']} long task(s)")
    if network.get("failure_count", 0):
        issues.append(f"network failures present: {network['failure_count']}")
    report_lines = [
        f"Page: {snapshot.get('title', '')}",
        f"URL: {snapshot.get('url', '')}",
        f"Ready state: {snapshot['page_summary'].get('readyState', '')}",
        f"Requests: {network.get('request_count', 0)} total, {network.get('failure_count', 0)} failed",
        f"Load: FCP={perf.get('firstContentfulPaintMs')} ms, DCL={perf.get('domContentLoadedMs')} ms, Load={perf.get('loadEventMs')} ms",
        f"Console entries: {console.get('count', 0)}",
        f"Coverage active: {snapshot.get('coverage_active')}, Trace active: {snapshot.get('trace_active')}",
    ]
    if issues:
        report_lines.append("Issues: " + "; ".join(issues))
    result = {
        "session_id": session_id,
        "page_id": snapshot["page_id"],
        "title": snapshot.get("title"),
        "url": snapshot.get("url"),
        "issues": issues,
        "report": "\n".join(report_lines),
        "snapshot": snapshot,
    }
    record_event("browser_debug_report", session_id=session_id, page_id=snapshot["page_id"], issue_count=len(issues))
    return result
def browser_debug_bundle(
    session_id: str,
    page_id: str | None = None,
    bundle_dir: str | None = None,
    include_har: bool = True,
    include_trace: bool = True,
    include_coverage: bool = True,
) -> dict[str, Any]:
    session, resolved_page_id, _ = get_playwright_page(session_id, page_id=page_id)
    target_dir = Path(bundle_dir) if bundle_dir else BROWSER_CAPTURE_ROOT / f"debug-bundle-{session_id}-{resolved_page_id}-{now_stamp()}"
    target_dir.mkdir(parents=True, exist_ok=True)
    report = _call_sync_tool(browser_debug_report, session_id, page_id=resolved_page_id)
    report_path = target_dir / "debug-report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    artifacts: dict[str, Any] = {"report": str(report_path)}
    if include_har:
        har = _call_sync_tool(
            browser_export_network_har,
            session_id,
            page_id=resolved_page_id,
            path=str(target_dir / "network.har.json"),
        )
        artifacts["har"] = har["path"]
    if include_trace and session.get("trace_state", {}).get("active"):
        trace = _call_sync_tool(browser_stop_trace, session_id, path=str(target_dir / "trace.zip"))
        artifacts["trace"] = trace["path"]
    if include_coverage and session.get("coverage_state", {}).get(resolved_page_id):
        coverage = _call_sync_tool(
            browser_export_coverage_json,
            session_id,
            page_id=resolved_page_id,
            path=str(target_dir / "coverage.json"),
        )
        artifacts["coverage"] = coverage["path"]
    result = {
        "session_id": session_id,
        "page_id": resolved_page_id,
        "bundle_dir": str(target_dir),
        "artifacts": artifacts,
    }
    record_event("browser_debug_bundle", session_id=session_id, page_id=resolved_page_id, artifact_count=len(artifacts))
    return result
def browser_get_accessibility_snapshot(session_id: str, page_id: str | None = None, interesting_only: bool = True) -> dict[str, Any]:
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    snapshot = page.evaluate(
        """(interestingOnly) => {
            const isInteresting = (el) => {
                const role = el.getAttribute('role');
                const tag = el.tagName.toLowerCase();
                return Boolean(
                    role ||
                    ['a', 'button', 'input', 'select', 'textarea', 'summary', 'label'].includes(tag) ||
                    el.getAttribute('aria-label') ||
                    el.getAttribute('aria-labelledby')
                );
            };
            const textOf = (el) => (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 240);
            const walk = (el) => {
                if (!(el instanceof Element)) return null;
                const include = !interestingOnly || isInteresting(el);
                const children = Array.from(el.children).map(walk).filter(Boolean);
                if (!include && children.length === 0) return null;
                const role =
                    el.getAttribute('role') ||
                    ({
                        A: 'link', BUTTON: 'button', INPUT: 'input', SELECT: 'select', TEXTAREA: 'textarea',
                        NAV: 'navigation', MAIN: 'main', ASIDE: 'complementary', HEADER: 'banner',
                        FOOTER: 'contentinfo', H1: 'heading', H2: 'heading', H3: 'heading',
                        H4: 'heading', H5: 'heading', H6: 'heading',
                    }[el.tagName] || 'generic');
                return { role, name: el.getAttribute('aria-label') || textOf(el), tag: el.tagName.toLowerCase(), id: el.id || null, children };
            };
            return walk(document.body);
        }""",
        interesting_only,
    )
    return {"session_id": session_id, "page_id": resolved_page_id, "interesting_only": interesting_only, "snapshot": snapshot}


# ---------------------------------------------------------------------------
# Cookie management
# ---------------------------------------------------------------------------
def browser_get_cookies(
    session_id: str,
    urls: list[str] | None = None,
) -> dict[str, Any]:
    """Get cookies from the browser context. Optionally filter by URL(s)."""
    session = get_playwright_session(session_id)
    ctx = session["context"]
    cookies = ctx.cookies(urls) if urls else ctx.cookies()
    serialized = [
        {
            "name": c.get("name"),
            "value": c.get("value"),
            "domain": c.get("domain"),
            "path": c.get("path"),
            "expires": c.get("expires"),
            "httpOnly": c.get("httpOnly"),
            "secure": c.get("secure"),
            "sameSite": c.get("sameSite"),
        }
        for c in cookies
    ]
    result = {"session_id": session_id, "count": len(serialized), "cookies": serialized}
    record_event("browser_get_cookies", session_id=session_id, count=len(serialized))
    return result
def browser_set_cookies(
    session_id: str,
    cookies: list[dict[str, Any]],
) -> dict[str, Any]:
    """Add cookies to the browser context.

    Each cookie dict must have 'name' and 'value'.
    Optional keys: 'url', 'domain', 'path', 'expires', 'httpOnly', 'secure', 'sameSite'.
    Either 'url' or 'domain'+'path' must be provided.
    """
    session = get_playwright_session(session_id)
    ctx = session["context"]
    for c in cookies:
        if "name" not in c or "value" not in c:
            raise ValueError("Each cookie must have 'name' and 'value'.")
        if "url" not in c and "domain" not in c:
            raise ValueError("Each cookie must have either 'url' or 'domain'.")
    ctx.add_cookies(cookies)
    result = {"session_id": session_id, "added": len(cookies)}
    record_event("browser_set_cookies", session_id=session_id, count=len(cookies))
    return result
def browser_delete_cookies(
    session_id: str,
) -> dict[str, Any]:
    """Clear all cookies from the browser context."""
    session = get_playwright_session(session_id)
    ctx = session["context"]
    ctx.clear_cookies()
    result = {"session_id": session_id, "cleared": True}
    record_event("browser_delete_cookies", session_id=session_id)
    return result


# ---------------------------------------------------------------------------
# Iframe / frame navigation
# ---------------------------------------------------------------------------
def browser_list_frames(
    session_id: str,
    page_id: str | None = None,
) -> dict[str, Any]:
    """List all frames (including iframes) in the current page."""
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    frames_info = []
    for f in page.frames:
        frames_info.append({
            "name": f.name or "",
            "url": f.url,
            "is_main": f == page.main_frame,
            "is_detached": f.is_detached(),
        })
    result = {
        "session_id": session_id,
        "page_id": resolved_page_id,
        "count": len(frames_info),
        "frames": frames_info,
    }
    record_event("browser_list_frames", session_id=session_id, page_id=resolved_page_id, count=len(frames_info))
    return result
def browser_frame_eval(
    session_id: str,
    expression: str,
    frame_name: str | None = None,
    frame_url: str | None = None,
    page_id: str | None = None,
) -> dict[str, Any]:
    """Execute JavaScript in a specific frame/iframe.

    Identify the frame by name or URL substring.
    Use browser_list_frames to discover available frames first.
    """
    validate_js_expression(expression)
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    target_frame = None
    if frame_name:
        target_frame = page.frame(name=frame_name)
    elif frame_url:
        target_frame = page.frame(url=re.compile(re.escape(frame_url)))
    if target_frame is None:
        raise ValueError(
            f"Frame not found (name={frame_name!r}, url={frame_url!r}). "
            "Use browser_list_frames to see available frames."
        )
    result_value = target_frame.evaluate(expression)
    result = {
        "session_id": session_id,
        "page_id": resolved_page_id,
        "frame_name": target_frame.name,
        "frame_url": target_frame.url,
        "result": result_value,
    }
    record_event("browser_frame_eval", session_id=session_id, page_id=resolved_page_id, frame=target_frame.name)
    return result
def browser_frame_click(
    session_id: str,
    selector: str,
    frame_name: str | None = None,
    frame_url: str | None = None,
    page_id: str | None = None,
    timeout: int = 5000,
) -> dict[str, Any]:
    """Click an element inside a specific frame/iframe.

    Identify the frame by name or URL substring.
    """
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    target_frame = None
    if frame_name:
        target_frame = page.frame(name=frame_name)
    elif frame_url:
        target_frame = page.frame(url=re.compile(re.escape(frame_url)))
    if target_frame is None:
        raise ValueError(
            f"Frame not found (name={frame_name!r}, url={frame_url!r}). "
            "Use browser_list_frames to see available frames."
        )
    target_frame.locator(selector).first.click(timeout=timeout)
    result = {
        "session_id": session_id,
        "page_id": resolved_page_id,
        "frame_name": target_frame.name,
        "selector": selector,
        "clicked": True,
    }
    record_event("browser_frame_click", session_id=session_id, page_id=resolved_page_id, frame=target_frame.name, selector=selector)
    return result
def browser_frame_fill(
    session_id: str,
    selector: str,
    value: str,
    frame_name: str | None = None,
    frame_url: str | None = None,
    page_id: str | None = None,
    timeout: int = 5000,
) -> dict[str, Any]:
    """Fill a text input inside a specific frame/iframe.

    Identify the frame by name or URL substring.
    """
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    target_frame = None
    if frame_name:
        target_frame = page.frame(name=frame_name)
    elif frame_url:
        target_frame = page.frame(url=re.compile(re.escape(frame_url)))
    if target_frame is None:
        raise ValueError(
            f"Frame not found (name={frame_name!r}, url={frame_url!r}). "
            "Use browser_list_frames to see available frames."
        )
    target_frame.locator(selector).first.fill(value, timeout=timeout)
    result = {
        "session_id": session_id,
        "page_id": resolved_page_id,
        "frame_name": target_frame.name,
        "selector": selector,
        "filled": True,
    }
    record_event("browser_frame_fill", session_id=session_id, page_id=resolved_page_id, frame=target_frame.name, selector=selector)
    return result


# ---------------------------------------------------------------------------
# Shadow DOM
# ---------------------------------------------------------------------------
def browser_shadow_query(
    session_id: str,
    host_selector: str,
    inner_selector: str,
    action: str = "text",
    fill_value: str = "",
    page_id: str | None = None,
    timeout: int = 5000,
) -> dict[str, Any]:
    """Interact with elements inside Shadow DOM.

    Args:
        host_selector: CSS selector for the shadow host element.
        inner_selector: CSS selector for the target element inside the shadow root.
        action: One of 'text', 'click', 'fill', 'html', 'visible', 'count'.
        fill_value: Value to fill (only used when action='fill').
    """
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    # Playwright's >> syntax pierces shadow DOM
    combined = f"{host_selector} >> {inner_selector}"
    locator = page.locator(combined)
    action = action.lower().strip()
    if action == "text":
        result_value = locator.first.text_content(timeout=timeout)
    elif action == "click":
        locator.first.click(timeout=timeout)
        result_value = True
    elif action == "fill":
        locator.first.fill(fill_value, timeout=timeout)
        result_value = True
    elif action == "html":
        result_value = locator.first.inner_html(timeout=timeout)
    elif action == "visible":
        result_value = locator.first.is_visible(timeout=timeout)
    elif action == "count":
        result_value = locator.count()
    else:
        raise ValueError(f"Unknown action: {action!r}. Use text, click, fill, html, visible, or count.")

    result = {
        "session_id": session_id,
        "page_id": resolved_page_id,
        "host_selector": host_selector,
        "inner_selector": inner_selector,
        "action": action,
        "result": result_value,
    }
    record_event("browser_shadow_query", session_id=session_id, page_id=resolved_page_id, action=action)
    return result


class _AwaitableDict(dict):
    def __await__(self):
        async def _value():
            return self

        return _value().__await__()


class _AwaitableList(list):
    def __await__(self):
        async def _value():
            return self

        return _value().__await__()


def _legacy_awaitable(value: Any) -> Any:
    if isinstance(value, dict) and not isinstance(value, _AwaitableDict):
        return _AwaitableDict(value)
    if isinstance(value, list) and not isinstance(value, _AwaitableList):
        return _AwaitableList(value)
    return value


# ═══ PHASE 10-21: ADVANCED BROWSER FEATURES ═══════════════════════

def browser_smart_wait(
    session_id: str = "",
    timeout_ms: int = 15000,
    checks: list[str] | None = None,
    page_id: str | None = None,
) -> dict[str, Any]:
    """Smart wait: network idle + DOM stable + no visual change.
    
    checks: list of checks to run. Default: ['network', 'dom', 'visual'].
    Waits for ALL checks to pass within timeout.
    """
    import time as _time
    checks = checks or ["network", "dom", "visual"]
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    t0 = _time.monotonic()
    deadline = t0 + timeout_ms / 1000
    results: dict[str, bool] = {}

    if "network" in checks:
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 8000))
            results["network_idle"] = True
        except Exception:
            results["network_idle"] = False

    if "dom" in checks:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=3000)
            dom_hash_1 = page.evaluate("() => document.body?.innerHTML?.length || 0")
            _time.sleep(0.3)
            dom_hash_2 = page.evaluate("() => document.body?.innerHTML?.length || 0")
            results["dom_stable"] = dom_hash_1 == dom_hash_2
        except Exception:
            results["dom_stable"] = False

    if "visual" in checks:
        try:
            snap1 = page.evaluate("() => document.body?.scrollHeight || 0")
            _time.sleep(0.4)
            snap2 = page.evaluate("() => document.body?.scrollHeight || 0")
            results["visual_stable"] = snap1 == snap2
        except Exception:
            results["visual_stable"] = False

    elapsed = round((_time.monotonic() - t0) * 1000)
    all_ok = all(results.values())
    return {
        "ok": all_ok,
        "session_id": session_id,
        "page_id": resolved_page_id,
        "checks": results,
        "elapsed_ms": elapsed,
        "url": page.url,
    }


def browser_network_intercept(
    session_id: str = "",
    action: str = "list_rules",
    pattern: str = "",
    response_body: str = "",
    response_status: int = 200,
    block: bool = False,
    page_id: str | None = None,
) -> dict[str, Any]:
    """Intercept network requests: block, mock, or capture.
    
    action: 'add_rule', 'remove_rule', 'list_rules', 'capture_start', 'capture_stop', 'capture_get'.
    pattern: URL glob pattern (e.g. '**/*.png', '**/api/users*').
    """
    session, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)

    # Store intercept rules in session
    if "intercept_rules" not in session:
        session["intercept_rules"] = []
    if "captured_requests" not in session:
        session["captured_requests"] = []
    if "capturing" not in session:
        session["capturing"] = False

    if action == "list_rules":
        return {"ok": True, "rules": session["intercept_rules"], "capturing": session["capturing"]}

    elif action == "add_rule":
        if not pattern:
            return {"ok": False, "error": "pattern required for add_rule"}
        rule = {"pattern": pattern, "block": block, "mock_body": response_body, "mock_status": response_status}
        session["intercept_rules"].append(rule)

        def _route_handler(route):
            url = route.request.url
            for r in session.get("intercept_rules", []):
                import fnmatch
                if fnmatch.fnmatch(url, r["pattern"]):
                    if r["block"]:
                        route.abort()
                        return
                    if r["mock_body"]:
                        route.fulfill(status=r["mock_status"], body=r["mock_body"])
                        return
            route.continue_()

        try:
            page.route(pattern, _route_handler)
        except Exception:
            pass
        return {"ok": True, "added": rule, "total_rules": len(session["intercept_rules"])}

    elif action == "remove_rule":
        before = len(session["intercept_rules"])
        session["intercept_rules"] = [r for r in session["intercept_rules"] if r["pattern"] != pattern]
        try:
            page.unroute(pattern)
        except Exception:
            pass
        return {"ok": True, "removed": before - len(session["intercept_rules"]), "remaining": len(session["intercept_rules"])}

    elif action == "capture_start":
        session["capturing"] = True
        session["captured_requests"] = []

        def _capture_handler(request):
            if session.get("capturing"):
                session["captured_requests"].append({
                    "url": request.url,
                    "method": request.method,
                    "resource_type": request.resource_type,
                    "headers": dict(request.headers) if len(request.headers) < 20 else {"count": len(request.headers)},
                })

        page.on("request", _capture_handler)
        return {"ok": True, "capturing": True}

    elif action == "capture_stop":
        session["capturing"] = False
        return {"ok": True, "capturing": False, "captured_count": len(session.get("captured_requests", []))}

    elif action == "capture_get":
        reqs = session.get("captured_requests", [])
        return {"ok": True, "requests": reqs[-100:], "total": len(reqs)}

    return {"ok": False, "error": f"Unknown intercept action: {action}"}


def browser_save_session(
    session_id: str = "",
    path: str = "",
    page_id: str | None = None,
) -> dict[str, Any]:
    """Save browser session state (cookies, storage, URL) to a JSON file."""
    import json as _json
    import os
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    session = get_playwright_session(session_id)
    context = session.get("context")

    if not path:
        path = os.path.join(os.path.expanduser("~"), ".mcp_sessions", f"{session_id}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    state = {
        "session_id": session_id,
        "url": page.url,
        "title": page.title(),
        "cookies": context.cookies() if context else [],
        "local_storage": page.evaluate("() => { try { return JSON.parse(JSON.stringify(localStorage)); } catch { return {}; } }"),
        "session_storage": page.evaluate("() => { try { return JSON.parse(JSON.stringify(sessionStorage)); } catch { return {}; } }"),
    }

    with open(path, "w", encoding="utf-8") as f:
        _json.dump(state, f, indent=2, default=str)

    record_event("browser_save_session", session_id=session_id, path=path)
    return {"ok": True, "path": path, "cookies": len(state["cookies"]), "url": state["url"]}


def browser_restore_session(
    session_id: str = "",
    path: str = "",
    page_id: str | None = None,
) -> dict[str, Any]:
    """Restore browser session state (cookies, storage, URL) from a JSON file."""
    import json as _json
    import os
    if not path:
        path = os.path.join(os.path.expanduser("~"), ".mcp_sessions", f"{session_id}.json")

    if not os.path.exists(path):
        return {"ok": False, "error": f"Session file not found: {path}"}

    with open(path, "r", encoding="utf-8") as f:
        state = _json.load(f)

    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    session = get_playwright_session(session_id)
    context = session.get("context")

    # Restore cookies
    if context and state.get("cookies"):
        context.add_cookies(state["cookies"])

    # Navigate to saved URL
    if state.get("url") and state["url"] != "about:blank":
        page.goto(state["url"], wait_until="domcontentloaded", timeout=15000)

    # Restore storage
    if state.get("local_storage"):
        for k, v in state["local_storage"].items():
            page.evaluate(f"() => localStorage.setItem({_json.dumps(k)}, {_json.dumps(v)})")
    if state.get("session_storage"):
        for k, v in state["session_storage"].items():
            page.evaluate(f"() => sessionStorage.setItem({_json.dumps(k)}, {_json.dumps(v)})")

    record_event("browser_restore_session", session_id=session_id, path=path)
    return {"ok": True, "path": path, "url": state.get("url"), "cookies_restored": len(state.get("cookies", []))}


def browser_page_diff(
    session_id: str = "",
    mode: str = "dom",
    selector: str = "body",
    page_id: str | None = None,
) -> dict[str, Any]:
    """Capture a page snapshot for diffing. Call twice to compare before/after.
    
    mode: 'dom' (text content), 'visual' (screenshot hash), 'full' (both).
    First call stores baseline, second call returns diff.
    """
    import time as _time
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    session = get_playwright_session(session_id)

    current: dict[str, Any] = {}
    if mode in ("dom", "full"):
        current["text"] = page.evaluate(f"""() => {{
            const el = document.querySelector('{selector}');
            return el ? el.innerText.trim().slice(0, 5000) : '';
        }}""")
        current["html_length"] = page.evaluate(f"() => document.querySelector('{selector}')?.innerHTML?.length || 0")
    if mode in ("visual", "full"):
        try:
            screenshot = page.locator(selector).first.screenshot()
            current["visual_hash"] = hashlib.sha256(screenshot).hexdigest()
        except Exception:
            current["visual_hash"] = ""
    current["url"] = page.url
    current["timestamp"] = _time.time()

    baseline = session.get("_diff_baseline")
    if not baseline:
        session["_diff_baseline"] = current
        return {"ok": True, "phase": "baseline_stored", "hint": "Perform actions, then call page_diff again to see changes."}

    # Compare
    diff_result: dict[str, Any] = {"ok": True, "phase": "diff"}
    if "text" in baseline and "text" in current:
        b_lines = set(baseline["text"].split("\n"))
        c_lines = set(current["text"].split("\n"))
        diff_result["text_added"] = list(c_lines - b_lines)[:30]
        diff_result["text_removed"] = list(b_lines - c_lines)[:30]
        diff_result["text_changed"] = baseline["text"] != current["text"]
    if "visual_hash" in baseline and "visual_hash" in current:
        diff_result["visual_changed"] = baseline["visual_hash"] != current["visual_hash"]
    diff_result["url_changed"] = baseline["url"] != current["url"]
    diff_result["before_url"] = baseline["url"]
    diff_result["after_url"] = current["url"]

    # Clear baseline for next use
    session.pop("_diff_baseline", None)
    return diff_result


def browser_auto_login(
    session_id: str = "",
    credentials: dict[str, str] | None = None,
    page_id: str | None = None,
) -> dict[str, Any]:
    """Auto-detect login form and fill credentials.
    
    credentials: {"username": "...", "password": "..."} or {"email": "...", "password": "..."}.
    Detects login forms by looking for password fields.
    """
    if not credentials:
        return {"ok": False, "error": "credentials dict required: {\"username\": \"...\", \"password\": \"...\"}"}

    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)

    # Detect login form
    form_info = page.evaluate("""() => {
        const pwdFields = Array.from(document.querySelectorAll('input[type=password]'));
        if (pwdFields.length === 0) return null;
        const pwdField = pwdFields[0];
        const form = pwdField.closest('form') || document.body;
        const inputs = Array.from(form.querySelectorAll('input:not([type=hidden]):not([type=submit])'));
        const submitBtn = form.querySelector('button[type=submit], input[type=submit], button') 
            || document.querySelector('button[type=submit]');
        return {
            has_form: true,
            fields: inputs.map(el => ({
                type: el.type,
                name: el.name || '',
                id: el.id || '',
                placeholder: el.placeholder || '',
            })),
            submit_text: submitBtn?.innerText?.trim() || submitBtn?.value || '',
        };
    }""")

    if not form_info:
        return {"ok": False, "error": "No login form detected (no password field found)"}

    # Fill fields
    filled = []
    password = credentials.get("password", "")
    username = credentials.get("username", credentials.get("email", credentials.get("user", "")))

    # Fill non-password field first (username/email)
    for field in form_info["fields"]:
        if field["type"] != "password" and field["type"] in ("text", "email", "tel"):
            selector = f"input#{field['id']}" if field["id"] else f"input[name='{field['name']}']" if field["name"] else None
            if selector and username:
                try:
                    page.locator(selector).first.fill(username)
                    filled.append({"field": field["name"] or field["id"], "type": "username"})
                except Exception:
                    pass
                break

    # Fill password
    try:
        page.locator("input[type=password]").first.fill(password)
        filled.append({"field": "password", "type": "password"})
    except Exception:
        pass

    # Submit
    submitted = False
    try:
        page.locator("button[type=submit], input[type=submit]").first.click(timeout=3000)
        submitted = True
    except Exception:
        try:
            page.keyboard.press("Enter")
            submitted = True
        except Exception:
            pass

    return {
        "ok": len(filled) > 0,
        "session_id": session_id,
        "form_detected": True,
        "fields_count": len(form_info["fields"]),
        "filled": filled,
        "submitted": submitted,
        "submit_button": form_info.get("submit_text", ""),
    }


def browser_cookie_editor(
    session_id: str = "",
    action: str = "list",
    name: str = "",
    value: str = "",
    domain: str = "",
    url: str = "",
    page_id: str | None = None,
) -> dict[str, Any]:
    """CRUD operations on browser cookies.
    
    action: 'list', 'get', 'set', 'delete', 'clear'.
    """
    session = get_playwright_session(session_id)
    context = session.get("context")
    if not context:
        return {"ok": False, "error": "No browser context"}

    if action == "list":
        cookies = context.cookies()
        return {"ok": True, "cookies": cookies, "count": len(cookies)}

    elif action == "get":
        cookies = context.cookies()
        matched = [c for c in cookies if c.get("name") == name]
        return {"ok": True, "cookies": matched, "count": len(matched)}

    elif action == "set":
        if not name or not value:
            return {"ok": False, "error": "name and value required"}
        cookie = {"name": name, "value": value}
        if domain:
            cookie["domain"] = domain
            cookie["url"] = f"https://{domain}"
        elif url:
            cookie["url"] = url
        else:
            _, _, page = get_playwright_page(session_id, page_id=page_id)
            cookie["url"] = page.url
        context.add_cookies([cookie])
        return {"ok": True, "set": cookie}

    elif action == "delete":
        cookies = context.cookies()
        remaining = [c for c in cookies if c.get("name") != name]
        context.clear_cookies()
        if remaining:
            context.add_cookies(remaining)
        return {"ok": True, "deleted": name, "remaining": len(remaining)}

    elif action == "clear":
        context.clear_cookies()
        return {"ok": True, "cleared": True}

    return {"ok": False, "error": f"Unknown cookie action: {action}"}


def browser_pdf_export(
    session_id: str = "",
    path: str = "",
    full_page: bool = True,
    format_type: str = "pdf",
    page_id: str | None = None,
) -> dict[str, Any]:
    """Export current page as PDF or full-page screenshot.
    
    format_type: 'pdf' or 'image'.
    """
    import os
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)

    if not path:
        ext = "pdf" if format_type == "pdf" else "png"
        path = os.path.join(os.path.expanduser("~"), "Downloads", f"page_export.{ext}")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if format_type == "pdf":
        try:
            pdf_bytes = page.pdf(path=path, format="A4", print_background=True)
            return {"ok": True, "path": path, "format": "pdf", "size_bytes": os.path.getsize(path)}
        except Exception as e:
            return {"ok": False, "error": f"PDF export failed (requires headless Chromium): {e}"}
    else:
        page.screenshot(path=path, full_page=full_page)
        return {"ok": True, "path": path, "format": "png", "full_page": full_page, "size_bytes": os.path.getsize(path)}


def browser_captcha_detect(
    session_id: str = "",
    page_id: str | None = None,
) -> dict[str, Any]:
    """Detect if a captcha is present on the page."""
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)

    detection = page.evaluate("""() => {
        const html = document.documentElement.innerHTML.toLowerCase();
        const signals = [];
        
        // reCAPTCHA
        if (html.includes('recaptcha') || document.querySelector('.g-recaptcha, #recaptcha'))
            signals.push('recaptcha');
        // hCaptcha
        if (html.includes('hcaptcha') || document.querySelector('.h-captcha'))
            signals.push('hcaptcha');
        // Cloudflare Turnstile
        if (html.includes('turnstile') || html.includes('cf-challenge') || document.querySelector('.cf-turnstile'))
            signals.push('cloudflare_turnstile');
        // Cloudflare challenge page
        if (html.includes('checking your browser') || html.includes('just a moment'))
            signals.push('cloudflare_challenge');
        // Generic captcha
        if (document.querySelector('img[src*=captcha], iframe[src*=captcha]'))
            signals.push('generic_captcha');
        // FunCaptcha / Arkose
        if (html.includes('funcaptcha') || html.includes('arkoselabs'))
            signals.push('funcaptcha');
            
        return {
            detected: signals.length > 0,
            types: signals,
            url: window.location.href,
        };
    }""")

    return {
        "ok": True,
        "session_id": session_id,
        "captcha_detected": detection["detected"],
        "captcha_types": detection["types"],
        "url": detection["url"],
        "hint": "Captcha detected! Manual intervention or captcha service may be required." if detection["detected"] else "No captcha detected.",
    }


def browser_perf_profile(
    session_id: str = "",
    page_id: str | None = None,
) -> dict[str, Any]:
    """Measure page performance: load time, resources, memory."""
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)

    perf = page.evaluate("""() => {
        const timing = performance.timing || {};
        const nav = performance.getEntriesByType('navigation')[0] || {};
        const resources = performance.getEntriesByType('resource');
        
        return {
            load_time_ms: timing.loadEventEnd ? timing.loadEventEnd - timing.navigationStart : null,
            dom_ready_ms: timing.domContentLoadedEventEnd ? timing.domContentLoadedEventEnd - timing.navigationStart : null,
            first_paint_ms: (() => { const fp = performance.getEntriesByName('first-paint')[0]; return fp ? Math.round(fp.startTime) : null; })(),
            first_contentful_paint_ms: (() => { const fcp = performance.getEntriesByName('first-contentful-paint')[0]; return fcp ? Math.round(fcp.startTime) : null; })(),
            resource_count: resources.length,
            total_transfer_kb: Math.round(resources.reduce((s, r) => s + (r.transferSize || 0), 0) / 1024),
            largest_resources: resources
                .sort((a, b) => (b.transferSize || 0) - (a.transferSize || 0))
                .slice(0, 5)
                .map(r => ({name: r.name.split('/').pop()?.slice(0, 60), type: r.initiatorType, size_kb: Math.round((r.transferSize || 0) / 1024)})),
            dom_elements: document.querySelectorAll('*').length,
            url: window.location.href,
        };
    }""")

    return {"ok": True, "session_id": session_id, **perf}


# ═══ END PHASE 10-21 ══════════════════════════════════════════════


def browser_intent_click(
    session_id: str,
    intent: str,
    page_id: str | None = None,
    button: str = "left",
    timeout_ms: int = 5000,
) -> dict[str, Any]:
    """Click an element by natural language intent description.

    Searches through multiple strategies: accessibility tree text match,
    visible text match, role+name match, then OCR fallback on screenshot.
    Example intents: "login button", "search input", "accept cookies".
    """
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    query = " ".join((intent or "").strip().split())
    if not query:
        raise ValueError("Provide non-empty intent.")

    # Strategy 1: getByRole with name match
    for role in ("button", "link", "textbox", "checkbox", "menuitem", "tab", "option"):
        try:
            locator = page.get_by_role(role, name=re.compile(re.escape(query), re.IGNORECASE))
            if locator.count() > 0:
                locator.first.click(button=button, timeout=timeout_ms)
                box = locator.first.bounding_box()
                result = {
                    "ok": True, "session_id": session_id, "page_id": resolved_page_id,
                    "intent": query, "source": f"role_{role}", "x": int(box["x"] + box["width"] / 2) if box else None,
                    "y": int(box["y"] + box["height"] / 2) if box else None,
                }
                record_event("browser_intent_click", intent=query, source=f"role_{role}")
                return result
        except Exception:
            continue

    # Strategy 2: getByText exact then partial
    for exact in (True, False):
        try:
            locator = page.get_by_text(query, exact=exact)
            if locator.count() > 0:
                locator.first.click(button=button, timeout=timeout_ms)
                box = locator.first.bounding_box()
                source = "text_exact" if exact else "text_partial"
                result = {
                    "ok": True, "session_id": session_id, "page_id": resolved_page_id,
                    "intent": query, "source": source, "x": int(box["x"] + box["width"] / 2) if box else None,
                    "y": int(box["y"] + box["height"] / 2) if box else None,
                }
                record_event("browser_intent_click", intent=query, source=source)
                return result
        except Exception:
            continue

    # Strategy 3: getByLabel (for form inputs)
    try:
        locator = page.get_by_label(query)
        if locator.count() > 0:
            locator.first.click(button=button, timeout=timeout_ms)
            box = locator.first.bounding_box()
            result = {
                "ok": True, "session_id": session_id, "page_id": resolved_page_id,
                "intent": query, "source": "label", "x": int(box["x"] + box["width"] / 2) if box else None,
                "y": int(box["y"] + box["height"] / 2) if box else None,
            }
            record_event("browser_intent_click", intent=query, source="label")
            return result
    except Exception:
        pass

    # Strategy 4: getByPlaceholder
    try:
        locator = page.get_by_placeholder(re.compile(re.escape(query), re.IGNORECASE))
        if locator.count() > 0:
            locator.first.click(button=button, timeout=timeout_ms)
            box = locator.first.bounding_box()
            result = {
                "ok": True, "session_id": session_id, "page_id": resolved_page_id,
                "intent": query, "source": "placeholder", "x": int(box["x"] + box["width"] / 2) if box else None,
                "y": int(box["y"] + box["height"] / 2) if box else None,
            }
            record_event("browser_intent_click", intent=query, source="placeholder")
            return result
    except Exception:
        pass

    # Strategy 5: CSS selector fallback
    for sel in (f"[aria-label*='{query}' i]", f"[title*='{query}' i]", f"[alt*='{query}' i]"):
        try:
            locator = page.locator(sel)
            if locator.count() > 0:
                locator.first.click(button=button, timeout=timeout_ms)
                box = locator.first.bounding_box()
                result = {
                    "ok": True, "session_id": session_id, "page_id": resolved_page_id,
                    "intent": query, "source": "aria_attr", "x": int(box["x"] + box["width"] / 2) if box else None,
                    "y": int(box["y"] + box["height"] / 2) if box else None,
                }
                record_event("browser_intent_click", intent=query, source="aria_attr")
                return result
        except Exception:
            continue

    raise ValueError(f"No element found for intent={intent!r}. Try browser_suggest_actions() or browser_content(action='interactive') first.")


def browser_suggest_actions(
    session_id: str,
    page_id: str | None = None,
    max_items: int = 30,
) -> dict[str, Any]:
    """Analyze the page and suggest possible actions.

    Returns interactive elements (buttons, links, inputs) with
    their text, role, position, and a ready-to-use action string.
    """
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    suggestions: list[dict[str, Any]] = []
    safe_max = max(1, min(int(max_items), 100))

    # Scan for interactive elements
    interactive_roles = {
        "button": "click", "link": "click", "textbox": "fill",
        "checkbox": "toggle", "radio": "toggle", "combobox": "select",
        "menuitem": "click", "tab": "click", "switch": "toggle",
    }
    for role, action_type in interactive_roles.items():
        try:
            locator = page.get_by_role(role)
            count = min(locator.count(), safe_max - len(suggestions))
            for i in range(count):
                if len(suggestions) >= safe_max:
                    break
                try:
                    el = locator.nth(i)
                    text = (el.text_content() or "").strip()[:80]
                    box = el.bounding_box()
                    if not box or box["width"] < 1 or box["height"] < 1:
                        continue
                    visible = el.is_visible()
                    if not visible:
                        continue
                    suggestions.append({
                        "role": role,
                        "text": text,
                        "action_type": action_type,
                        "x": int(box["x"] + box["width"] / 2),
                        "y": int(box["y"] + box["height"] / 2),
                        "width": int(box["width"]),
                        "height": int(box["height"]),
                        "suggested_action": f'browser_intent_click(intent="{text}")' if action_type == "click" else f'browser_type_selector(selector="[role={role}]", text="...")',
                    })
                except Exception:
                    continue
        except Exception:
            continue

    result = {
        "ok": True,
        "session_id": session_id,
        "page_id": resolved_page_id,
        "suggestions": suggestions,
        "count": len(suggestions),
        "url": page.url,
        "title": page.title(),
    }
    record_event("browser_suggest_actions", session_id=session_id, count=len(suggestions))
    return result


def browser_observe(
    session_id: str,
    page_id: str | None = None,
    include_interactive: bool = True,
    include_text: bool = True,
    max_interactive: int = 30,
) -> dict[str, Any]:
    """Rich browser observation: page state + interactive elements + visible text.

    Combines page summary, interactive element scan, and text extraction
    into a single observation for model-side planning.
    """
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    observation: dict[str, Any] = {
        "ok": True,
        "session_id": session_id,
        "page_id": resolved_page_id,
        "url": page.url,
        "title": page.title(),
        "viewport": page.viewport_size,
        "scroll_y": page.evaluate("() => window.scrollY"),
        "scroll_height": page.evaluate("() => document.body.scrollHeight"),
        "ready_state": page.evaluate("() => document.readyState"),
    }
    if include_interactive:
        try:
            suggestions = browser_suggest_actions(session_id=session_id, page_id=page_id, max_items=max_interactive)
            observation["interactive"] = suggestions.get("suggestions", [])
            observation["interactive_count"] = suggestions.get("count", 0)
        except Exception as e:
            observation["interactive"] = []
            observation["interactive_error"] = str(e)
    if include_text:
        try:
            text = (page.inner_text("body") or "")[:3000]
            observation["visible_text"] = text
        except Exception:
            observation["visible_text"] = ""
    record_event("browser_observe", session_id=session_id, page_id=resolved_page_id)
    return observation


def browser_human_idle(session_id: str, page_id: str | None = None, duration_ms: int = 2000) -> dict[str, Any]:
    """Simulate human idle behavior between browser actions.

    Adds small random mouse micro-movements and wait time to appear
    more human-like between page interactions.
    """
    _, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
    import random
    safe_duration = max(200, min(int(duration_ms), 10000))
    viewport = page.viewport_size or {"width": 1280, "height": 900}
    steps = random.randint(1, 3)
    for _ in range(steps):
        x = random.randint(int(viewport["width"] * 0.2), int(viewport["width"] * 0.8))
        y = random.randint(int(viewport["height"] * 0.2), int(viewport["height"] * 0.8))
        try:
            page.mouse.move(x, y, steps=random.randint(5, 15))
        except Exception:
            pass
        page.wait_for_timeout(random.randint(100, safe_duration // steps))
    result = {
        "ok": True,
        "session_id": session_id,
        "page_id": resolved_page_id,
        "idle_ms": safe_duration,
        "mouse_steps": steps,
    }
    record_event("browser_human_idle", session_id=session_id, idle_ms=safe_duration)
    return result


def _enable_legacy_awaitable_browser_tools() -> None:
    for name, fn in list(globals().items()):
        if not name.startswith("browser_") or not callable(fn) or inspect.iscoroutinefunction(fn):
            continue

        @functools.wraps(fn)
        def wrapper(*args, __fn=fn, **kwargs):
            return _legacy_awaitable(__fn(*args, **kwargs))

        globals()[name] = wrapper


_enable_legacy_awaitable_browser_tools()
__all__ = [name for name in globals() if name.startswith("browser_")]
