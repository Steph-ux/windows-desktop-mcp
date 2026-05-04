"""Dedicated browser profile/instance helpers for model-controlled browsing."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import re
from typing import Any

from ..cdp_client import cdp_navigate
from ..runtime import record_event
from . import browser_sessions as _bs

_DEFAULT_PLATFORM_URLS = {
    "x": "https://x.com/",
    "twitter": "https://x.com/",
    "youtube": "https://www.youtube.com/",
    "youtube_studio": "https://studio.youtube.com/",
    "tiktok": "https://www.tiktok.com/",
    "instagram": "https://www.instagram.com/",
}


def _safe_name(value: str, fallback: str = "agent") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", (value or "").strip().lower()).strip("-._")
    return (cleaned or fallback)[:64]


def _platform_key(platform: str = "") -> str:
    key = _safe_name(platform, fallback="general")
    return "x" if key == "twitter" else key


def default_agent_profile_name(platform: str = "", name: str = "social") -> str:
    """Return the stable profile name used for isolated agent browser sessions."""
    base = _safe_name(name, fallback="social")
    platform_part = _platform_key(platform)
    if platform_part in {"", "general"}:
        return f"agent-{base}"
    return f"agent-{base}-{platform_part}"


def default_agent_instance_name(platform: str = "", name: str = "social") -> str:
    """Return the stable browser instance name for a dedicated agent profile."""
    return default_agent_profile_name(platform=platform, name=name)


def _run_browser_call(fn, /, *args, **kwargs):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return fn(*args, **kwargs)
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: fn(*args, **kwargs)).result()


def _navigate_started_browser(started: dict[str, Any], target_url: str, wait_until: str) -> tuple[dict[str, Any], bool, str | None]:
    if not target_url or not started.get("session_id") or started.get("url") == target_url:
        return started, bool(target_url and started.get("url") == target_url), None
    try:
        navigation = _run_browser_call(
            _bs.browser_navigate,
            session_id=started["session_id"],
            page_id=started.get("page_id"),
            url=target_url,
            wait_until=wait_until,
        )
        started.update({key: value for key, value in navigation.items() if value is not None})
        return started, True, None
    except Exception as exc:
        navigation_error = str(exc)
        try:
            instance = _run_browser_call(_bs.browser_get_instance, str(started.get("instance_name") or ""))
            if instance.get("url"):
                started["url"] = instance["url"]
            if instance.get("title"):
                started["title"] = instance["title"]
            if instance.get("active_page_id"):
                started["page_id"] = instance["active_page_id"]
        except Exception:
            pass
        started["navigation_error"] = navigation_error
        started["navigation_timed_out"] = "timeout" in navigation_error.lower()
        return started, started.get("url") == target_url, navigation_error


def _cdp_endpoint(started: dict[str, Any]) -> str:
    manifest = started.get("manifest") if isinstance(started.get("manifest"), dict) else {}
    return str(started.get("cdp_endpoint") or manifest.get("cdp_endpoint") or "").strip().rstrip("/")


def _navigate_started_cdp(
    started: dict[str, Any],
    target_url: str,
    wait_until: str,
    new_tab_if_needed: bool = False,
) -> tuple[dict[str, Any], bool, str | None]:
    endpoint = _cdp_endpoint(started)
    if not target_url or not endpoint:
        return started, bool(target_url and started.get("url") == target_url), None
    if started.get("url") == target_url:
        started["cdp_direct"] = True
        return started, True, None
    try:
        navigate_kwargs = {
            "endpoint": endpoint,
            "url": target_url,
            "preferred_url": str(started.get("url") or target_url),
            "page_id": started.get("cdp_target_id"),
            "wait_ms": 10000 if wait_until else 5000,
        }
        if new_tab_if_needed:
            navigate_kwargs["new_tab_if_needed"] = True
        navigation = cdp_navigate(**navigate_kwargs)
        started.update({key: value for key, value in navigation.items() if value is not None})
        started["cdp_direct"] = True
        return started, bool(navigation.get("navigated")), None
    except Exception as exc:
        navigation_error = str(exc)
        try:
            instance = _run_browser_call(_bs.browser_get_instance, str(started.get("instance_name") or ""))
            if instance.get("url"):
                started["url"] = instance["url"]
            if instance.get("title"):
                started["title"] = instance["title"]
            if instance.get("active_page_id"):
                started["page_id"] = instance["active_page_id"]
        except Exception:
            pass
        started["navigation_error"] = navigation_error
        started["navigation_timed_out"] = "timeout" in navigation_error.lower()
        started["cdp_direct"] = True
        return started, started.get("url") == target_url, navigation_error


def _attach_known_cdp_instance(
    browser: str,
    resolved_instance: str,
    resolved_profile: str,
    width: int | str,
    height: int | str,
    init_script_paths: list[str] | None,
    grant_permissions: list[str] | None,
) -> dict[str, Any] | None:
    try:
        instance = _bs.browser_get_instance(resolved_instance)
    except Exception:
        instance = None
    if not isinstance(instance, dict):
        return None
    endpoint = str(instance.get("cdp_endpoint") or instance.get("manifest", {}).get("cdp_endpoint") or "").strip()
    if not endpoint:
        return None
    browser_pid = instance.get("browser_pid") or instance.get("manifest", {}).get("browser_pid")
    launched_debug_browser = bool(instance.get("launched_debug_browser") or instance.get("manifest", {}).get("launched_debug_browser"))
    return _run_browser_call(
        _bs.browser_attach_cdp,
        endpoint=endpoint,
        browser=browser,
        instance_name=resolved_instance,
        profile_name=resolved_profile,
        browser_pid=browser_pid,
        launched_debug_browser=launched_debug_browser,
        width=width,
        height=height,
        init_script_paths=init_script_paths,
        grant_permissions=grant_permissions,
    )


def agent_browser_ensure_profile(
    profile_name: str = "",
    platform: str = "",
    name: str = "social",
    browser: str = "chrome",
    preferred_url: str = "",
    description: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Create/update a named persistent browser profile dedicated to model automation."""
    resolved_profile = _safe_name(profile_name) if profile_name else default_agent_profile_name(platform, name)
    resolved_platform = _platform_key(platform)
    url = preferred_url or _DEFAULT_PLATFORM_URLS.get(resolved_platform, "about:blank")
    profile_tags = ["agent", "dedicated", "non-host-interactive"]
    if resolved_platform:
        profile_tags.append(resolved_platform)
    profile_tags.extend(str(item) for item in (tags or []) if str(item).strip())
    payload = _bs.browser_create_profile(
        profile_name=resolved_profile,
        description=description or "Dedicated Playwright browser profile for model-controlled read-only workflows.",
        tags=profile_tags,
        preferred_url=url,
        browser=browser,
    )
    record_event("agent_browser_ensure_profile", profile_name=resolved_profile, platform=resolved_platform)
    return {
        "ok": True,
        "profile_name": resolved_profile,
        "platform": resolved_platform,
        "browser_context": "agent_dedicated",
        "automation": "playwright",
        "host_interactive": False,
        "profile": payload,
    }


def agent_browser_start(
    platform: str = "",
    url: str = "",
    profile_name: str = "",
    instance_name: str = "",
    name: str = "social",
    browser: str = "chrome",
    browser_engine: str = "playwright",
    debug_port: int = 9333,
    new_tab_if_needed: bool = False,
    startup_wait_ms: int = 4000,
    headless: bool = True,
    width: int | str = "auto",
    height: int | str = "auto",
    wait_until: str = "domcontentloaded",
    storage_state_path: str | None = None,
    init_script_paths: list[str] | None = None,
    grant_permissions: list[str] | None = None,
    preset_name: str | None = None,
) -> dict[str, Any]:
    """Start a dedicated Playwright browser instance that does not touch host input."""
    resolved_platform = _platform_key(platform)
    profile = agent_browser_ensure_profile(
        profile_name=profile_name,
        platform=resolved_platform,
        name=name,
        browser=browser,
        preferred_url=url or _DEFAULT_PLATFORM_URLS.get(resolved_platform, "about:blank"),
    )
    resolved_profile = profile["profile_name"]
    resolved_instance = _safe_name(instance_name) if instance_name else default_agent_instance_name(resolved_platform, name)
    target_url = url or _DEFAULT_PLATFORM_URLS.get(resolved_platform, "about:blank")
    engine = (browser_engine or "playwright").strip().lower()
    if engine not in {"playwright", "cdp"}:
        raise ValueError("browser_engine must be 'playwright' or 'cdp'.")
    if engine == "cdp":
        cdp_port = int(debug_port)
        started: dict[str, Any] | None = None
        cdp_reattached = False
        try:
            started = _attach_known_cdp_instance(
                browser=browser,
                resolved_instance=resolved_instance,
                resolved_profile=resolved_profile,
                width=width,
                height=height,
                init_script_paths=init_script_paths,
                grant_permissions=grant_permissions,
            )
            cdp_reattached = started is not None
        except Exception:
            started = None
        endpoints = {"count": 0, "endpoints": []}
        if started is None:
            try:
                endpoints = _bs.browser_list_endpoints(ports=[cdp_port])
            except Exception:
                endpoints = {"count": 0, "endpoints": []}
            if int(endpoints.get("count") or 0) > 0:
                try:
                    started = _run_browser_call(
                        _bs.browser_attach_existing,
                        browser=browser,
                        instance_name=resolved_instance,
                        profile_name=resolved_profile,
                        ports=[cdp_port],
                        width=width,
                        height=height,
                        init_script_paths=init_script_paths,
                        grant_permissions=grant_permissions,
                    )
                    cdp_reattached = True
                except Exception:
                    started = None
        if started is None:
            started = _run_browser_call(
                _bs.browser_launch_and_attach,
                browser=browser,
                port=cdp_port,
                url=target_url,
                profile_name=resolved_profile,
                instance_name=resolved_instance,
                width=width,
                height=height,
                startup_wait_ms=startup_wait_ms,
                init_script_paths=init_script_paths,
                grant_permissions=grant_permissions,
            )
        started, navigated, navigation_error = _navigate_started_cdp(
            started,
            target_url,
            wait_until,
            new_tab_if_needed=new_tab_if_needed,
        )
        record_event(
            "agent_browser_start",
            profile_name=resolved_profile,
            instance_name=resolved_instance,
            platform=resolved_platform,
            url=target_url,
            browser_engine="cdp",
            cdp_reattached=cdp_reattached,
            new_tab_if_needed=bool(new_tab_if_needed),
            navigation_error=navigation_error,
            headless=False,
        )
        return {
            **started,
            "ok": bool(started.get("ok", True)),
            "profile_name": resolved_profile,
            "instance_name": resolved_instance,
            "platform": resolved_platform,
            "url": started.get("url") or target_url,
            "navigated": navigated or started.get("url") == target_url,
            "navigation_error": navigation_error,
            "browser_context": "agent_dedicated",
            "automation": "cdp",
            "browser_engine": "cdp",
            "cdp_direct": True,
            "cdp_reattached": cdp_reattached,
            "new_tab_if_needed": bool(new_tab_if_needed),
            "created_target": bool(started.get("created_target")),
            "host_interactive": False,
            "uses_host_mouse": False,
            "uses_host_keyboard": False,
        }

    startup_url = "about:blank" if target_url and target_url != "about:blank" else target_url
    started = _run_browser_call(
        _bs.browser_start_instance,
        instance_name=resolved_instance,
        url=startup_url,
        profile_name=resolved_profile,
        browser=browser,
        headless=headless,
        width=width,
        height=height,
        storage_state_path=storage_state_path,
        init_script_paths=init_script_paths,
        grant_permissions=grant_permissions,
        preset_name=preset_name,
    )
    started, navigated, navigation_error = _navigate_started_browser(started, target_url, wait_until)
    record_event(
        "agent_browser_start",
        profile_name=resolved_profile,
        instance_name=resolved_instance,
        platform=resolved_platform,
        url=target_url,
        navigation_error=navigation_error,
        headless=headless,
    )
    return {
        **started,
        "ok": bool(started.get("ok", True)),
        "profile_name": resolved_profile,
        "instance_name": resolved_instance,
        "platform": resolved_platform,
        "url": started.get("url") or target_url,
        "navigated": navigated,
        "navigation_error": navigation_error,
        "browser_context": "agent_dedicated",
        "automation": "playwright",
        "browser_engine": "playwright",
        "host_interactive": False,
        "uses_host_mouse": False,
        "uses_host_keyboard": False,
    }


def agent_browser_status(profile_name: str = "", instance_name: str = "", platform: str = "", name: str = "social") -> dict[str, Any]:
    """Return profile and instance metadata for the dedicated agent browser."""
    resolved_profile = _safe_name(profile_name) if profile_name else default_agent_profile_name(platform, name)
    resolved_instance = _safe_name(instance_name) if instance_name else default_agent_instance_name(platform, name)
    profile: dict[str, Any] | None
    instance: dict[str, Any] | None
    try:
        profile = _run_browser_call(_bs.browser_get_profile, resolved_profile)
    except Exception as exc:
        profile = {"ok": False, "error": str(exc)}
    try:
        instance = _run_browser_call(_bs.browser_get_instance, resolved_instance)
    except Exception as exc:
        instance = {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "profile_name": resolved_profile,
        "instance_name": resolved_instance,
        "browser_context": "agent_dedicated",
        "automation": "playwright",
        "host_interactive": False,
        "profile": profile,
        "instance": instance,
    }


def agent_browser_stop(instance_name: str = "", platform: str = "", name: str = "social") -> dict[str, Any]:
    """Stop a dedicated agent browser instance by name."""
    resolved_instance = _safe_name(instance_name) if instance_name else default_agent_instance_name(platform, name)
    result = _run_browser_call(_bs.browser_stop_instance, resolved_instance)
    record_event("agent_browser_stop", instance_name=resolved_instance)
    return {
        **result,
        "instance_name": resolved_instance,
        "browser_context": "agent_dedicated",
        "automation": "playwright",
        "host_interactive": False,
    }


__all__ = [
    "agent_browser_ensure_profile",
    "agent_browser_start",
    "agent_browser_status",
    "agent_browser_stop",
    "default_agent_instance_name",
    "default_agent_profile_name",
]
