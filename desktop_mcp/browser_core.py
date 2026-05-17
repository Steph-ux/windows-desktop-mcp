from __future__ import annotations

from contextlib import contextmanager
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from .paths import BROWSER_CAPTURE_ROOT, BROWSER_PROFILE_ROOT, DOWNLOAD_ROOT
from .state import BROWSER_PROFILE_MAX_AGE_HOURS, PLAYWRIGHT_SESSIONS, PLAYWRIGHT_SESSIONS_LOCK
from .browser_sessions import (
    apply_playwright_intercepts,
    attach_playwright_page_observers,
    cleanup_stale_playwright_sessions,
    close_all_playwright_sessions,
    close_playwright_session,
    get_playwright_page,
    get_playwright_page_event_buffers,
    get_playwright_session,
    list_playwright_sessions,
    page_title,
    playwright_page_info,
    playwright_session_age_seconds,
    refresh_playwright_pages,
    register_playwright_page,
    store_playwright_session,
    touch_playwright_session,
    wait_for_url_pattern,
)


def browser_candidates(browser: str = "auto", include_firefox: bool = False) -> list[tuple[str, str]]:
    browser_key = (browser or "auto").lower()
    candidates: list[tuple[str, str, list[str]]] = [
        (
            "chrome",
            "Google Chrome",
            [
                shutil.which("chrome") or "",
                shutil.which("chrome.exe") or "",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ],
        ),
        (
            "edge",
            "Microsoft Edge",
            [
                shutil.which("msedge") or "",
                shutil.which("msedge.exe") or "",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            ],
        ),
    ]
    if include_firefox:
        candidates.append(
            (
                "firefox",
                "Mozilla Firefox",
                [
                    shutil.which("firefox") or "",
                    shutil.which("firefox.exe") or "",
                    r"C:\Program Files\Mozilla Firefox\firefox.exe",
                    r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
                ],
            )
        )
    if browser_key != "auto":
        candidates = [item for item in candidates if item[0] == browser_key]
        supported = ["auto", "chrome", "edge"]
        if include_firefox:
            supported.append("firefox")
        if not candidates:
            raise ValueError(f"Unsupported browser={browser!r}. Use one of: {', '.join(supported)}.")
    matches: list[tuple[str, str]] = []
    for key, _, paths in candidates:
        for path in paths:
            if path and Path(path).exists():
                matches.append((key, str(Path(path))))
                break
    if not matches:
        raise RuntimeError("No supported Chromium browser executable found. Install Google Chrome or Microsoft Edge.")
    return matches


def browser_availability(include_firefox: bool = True) -> dict[str, Any]:
    available: dict[str, Any] = {
        "chrome_available": False,
        "chrome_executable": None,
        "edge_available": False,
        "edge_executable": None,
        "firefox_available": False,
        "firefox_executable": None,
        "any_browser_available": False,
    }
    for browser_name, executable in browser_candidates("auto", include_firefox=include_firefox):
        available[f"{browser_name}_available"] = True
        available[f"{browser_name}_executable"] = executable
        available["any_browser_available"] = True
    return available


def browser_headless_args(browser_name: str, executable: str, extra_args: list[str]) -> tuple[list[str], Path]:
    temp_profile = Path(tempfile.mkdtemp(prefix="pm-browser-mcp-", dir=str(BROWSER_PROFILE_ROOT)))
    if browser_name == "firefox":
        command = [executable, "--headless", *extra_args]
    else:
        command = [
            executable,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--user-data-dir={temp_profile}",
            *extra_args,
        ]
    return command, temp_profile


def run_browser_command(browser: str, extra_args: list[str], include_firefox: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    for browser_name, executable in browser_candidates(browser, include_firefox=include_firefox):
        command, temp_profile = browser_headless_args(browser_name, executable, extra_args)
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            shutil.rmtree(temp_profile, ignore_errors=True)
        if result.returncode == 0:
            return {
                "browser": browser_name,
                "executable": executable,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        errors.append(f"{browser_name} failed with code {result.returncode}: {result.stderr.strip() or result.stdout.strip()}")
    raise RuntimeError(
        "Browser command failed for all candidates. "
        f"browser={browser!r}, include_firefox={include_firefox}, errors={' | '.join(errors)}"
    )


def build_browser_screenshot_args(
    browser: str,
    url: str,
    width: int,
    height: int,
    wait_ms: int,
    target_path: Path,
) -> list[str]:
    browser_key = (browser or "auto").lower()
    width = max(width, 320)
    height = max(height, 240)
    wait_ms = max(wait_ms, 0)
    if browser_key == "firefox":
        return [f"--window-size={width},{height}", "--screenshot", str(target_path), url]
    return [
        f"--window-size={width},{height}",
        f"--virtual-time-budget={wait_ms}",
        f"--screenshot={target_path}",
        url,
    ]


def playwright_launch(browser: str = "auto"):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Install the Python package 'playwright' first."
        ) from exc
    browser_key = (browser or "auto").lower()
    if browser_key not in {"auto", "chrome", "edge", "firefox"}:
        raise ValueError("Use browser='auto', 'chrome', 'edge', or 'firefox'.")
    return sync_playwright()


def open_playwright_runtime(browser: str = "auto", headless: bool = True, stealth: bool = False, humanize: bool = False):
    browser_key = (browser or "auto").lower()
    if stealth and browser_key in {"auto", "chrome"}:
        try:
            from cloakbrowser import launch as cloak_launch
            engine = cloak_launch(headless=headless, humanize=humanize)
            return None, None, engine, "chrome-stealth"
        except ImportError:
            pass
    playwright = playwright_launch(browser)
    runtime = playwright.__enter__()
    if browser_key in {"auto", "chrome"}:
        engine = runtime.chromium.launch(channel="chrome", headless=headless)
        actual_browser = "chrome"
    elif browser_key == "edge":
        engine = runtime.chromium.launch(channel="msedge", headless=headless)
        actual_browser = "edge"
    else:
        engine = runtime.firefox.launch(headless=headless)
        actual_browser = "firefox"
    return playwright, runtime, engine, actual_browser


def open_playwright_cdp_runtime(endpoint: str, browser: str = "chrome", timeout_ms: int = 30000):
    playwright = playwright_launch(browser)
    runtime = playwright.__enter__()
    engine = runtime.chromium.connect_over_cdp(endpoint, timeout=max(int(timeout_ms), 1))
    actual_browser = "chrome" if (browser or "chrome").lower() == "auto" else (browser or "chrome").lower()
    return playwright, runtime, engine, actual_browser


def probe_cdp_endpoint(endpoint: str, timeout_seconds: float = 1.5) -> dict[str, Any] | None:
    candidate = endpoint.rstrip("/")
    version_url = f"{candidate}/json/version"
    try:
        with urllib.request.urlopen(version_url, timeout=max(float(timeout_seconds), 0.1)) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return None
    return {
        "endpoint": candidate,
        "web_socket_debugger_url": payload.get("webSocketDebuggerUrl"),
        "browser": payload.get("Browser"),
        "protocol_version": payload.get("Protocol-Version"),
        "user_agent": payload.get("User-Agent"),
        "v8_version": payload.get("V8-Version"),
    }


def probe_cdp_targets(endpoint: str, timeout_seconds: float = 1.5) -> list[dict[str, Any]]:
    candidate = endpoint.rstrip("/")
    targets_url = f"{candidate}/json/list"
    try:
        with urllib.request.urlopen(targets_url, timeout=max(float(timeout_seconds), 0.1)) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return []
    targets: list[dict[str, Any]] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        targets.append(
            {
                "id": item.get("id"),
                "type": item.get("type"),
                "title": item.get("title"),
                "url": item.get("url"),
                "attached": item.get("attached"),
            }
        )
    return targets


def detect_cdp_endpoints(ports: list[int] | None = None, host: str = "127.0.0.1") -> list[dict[str, Any]]:
    resolved_ports = ports or [9222, 9223, 9333, 9444, 9555]
    found: list[dict[str, Any]] = []
    for port in resolved_ports:
        try:
            endpoint = f"http://{host}:{max(int(port), 1)}"
        except Exception:
            continue
        payload = probe_cdp_endpoint(endpoint)
        if payload:
            payload["port"] = max(int(port), 1)
            payload["targets"] = probe_cdp_targets(endpoint)
            found.append(payload)
    return found


def cleanup_stale_browser_profiles(max_age_hours: float = BROWSER_PROFILE_MAX_AGE_HOURS) -> dict[str, Any]:
    max_age_seconds = max(float(max_age_hours), 0.0) * 3600
    now = time.time()
    checked = 0
    removed: list[str] = []
    if not BROWSER_PROFILE_ROOT.exists():
        return {"checked": 0, "removed": 0, "removed_paths": [], "max_age_hours": max(float(max_age_hours), 0.0)}

    for path in BROWSER_PROFILE_ROOT.iterdir():
        if not path.is_dir() or not path.name.startswith("pm-browser-mcp-"):
            continue
        checked += 1
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if now - mtime <= max_age_seconds:
            continue
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            removed.append(str(path))

    return {
        "checked": checked,
        "removed": len(removed),
        "removed_paths": removed,
        "max_age_hours": max(float(max_age_hours), 0.0),
    }


def runtime_housekeeping() -> dict[str, Any]:
    return {
        "profiles": cleanup_stale_browser_profiles(),
        "sessions": cleanup_stale_playwright_sessions(),
    }


def launch_browser_process(browser: str, url: str) -> tuple[str, subprocess.Popen[Any]]:
    browser_name, executable = browser_candidates(browser, include_firefox=True)[0]
    command = [executable, "-new-window", url] if browser_name == "firefox" else [executable, "--new-window", url]
    process = subprocess.Popen(command)
    return browser_name, process


def launch_debug_browser_process(
    browser: str,
    port: int,
    url: str = "about:blank",
    profile_path: str | None = None,
) -> tuple[str, subprocess.Popen[Any], str]:
    browser_name, executable = browser_candidates(browser, include_firefox=False)[0]
    resolved_port = max(int(port), 1)
    resolved_profile_path = profile_path or tempfile.mkdtemp(prefix="pm-browser-debug-", dir=str(BROWSER_PROFILE_ROOT))
    command = [
        executable,
        f"--remote-debugging-port={resolved_port}",
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={resolved_profile_path}",
    ]
    command.extend(["--new-window", url])
    process = subprocess.Popen(command)
    endpoint = f"http://127.0.0.1:{resolved_port}"
    return browser_name, process, endpoint


def wait_for_cdp_endpoint(endpoint: str, timeout_seconds: float = 10.0, poll_seconds: float = 0.2) -> dict[str, Any]:
    deadline = time.time() + max(float(timeout_seconds), 0.1)
    last_error: str | None = None
    while time.time() < deadline:
        payload = probe_cdp_endpoint(endpoint, timeout_seconds=max(poll_seconds, 0.1))
        if payload:
            return payload
        last_error = f"Endpoint not ready: {endpoint}"
        time.sleep(max(float(poll_seconds), 0.05))
    raise RuntimeError(last_error or f"Timed out waiting for CDP endpoint: {endpoint}")


def browser_window_class_names(browser_name: str) -> list[str]:
    if browser_name == "firefox":
        return ["MozillaWindowClass"]
    return ["Chrome_WidgetWin_1"]


def new_browser_window_info(
    browser_name: str,
    before_handles: set[int | None],
    timeout_seconds: float,
    list_windows_func,
) -> dict[str, Any]:
    deadline = time.time() + max(timeout_seconds, 0.1)
    class_names = set(browser_window_class_names(browser_name))
    latest_match = None
    while time.time() < deadline:
        for info in list_windows_func(visible_only=True):
            if info.get("class_name") not in class_names:
                continue
            handle = info.get("handle")
            if handle and handle not in before_handles and info.get("bounds"):
                return info
            latest_match = info
        time.sleep(0.2)
    if latest_match:
        return latest_match
    raise ValueError(f"Timed out waiting for a {browser_name} browser window.")


def dump_dom_fallback(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=10) as response:
        html = response.read().decode("utf-8", errors="replace")
    return {"browser": "http-fetch", "executable": None, "html": html, "length": len(html), "rendered": False}


@contextmanager
def open_exact_playwright_page(url: str, width: int = 1440, height: int = 960, browser: str = "auto"):
    playwright_cm, runtime, engine, actual_browser = open_playwright_runtime(browser, headless=True)
    try:
        page = engine.new_page(viewport={"width": max(width, 320), "height": max(height, 240)}, device_scale_factor=1)
        page.goto(url, wait_until="networkidle")
        yield actual_browser, page
    finally:
        try:
            engine.close()
        finally:
            playwright_cm.__exit__(None, None, None)
