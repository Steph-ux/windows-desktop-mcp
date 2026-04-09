from __future__ import annotations

import json
import importlib.util
import logging
import time
from functools import wraps
from typing import Any

from .browser_core import browser_availability
from .ocr_core import ocr_availability
from .paths import BROWSER_CAPTURE_ROOT, BROWSER_PROFILE_ROOT, MCP_LOG_PATH, RUNTIME_EVENT_LOG, ensure_runtime_dirs
from .state import ACTION_EVENT_LOG, DESKTOP_WATCH_SESSIONS, PLAYWRIGHT_SESSIONS

ensure_runtime_dirs()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(funcName)s: %(message)s",
    handlers=[],
)
logger = logging.getLogger("windows_mcp")
if not logger.handlers:
    try:
        logger.addHandler(logging.FileHandler(MCP_LOG_PATH, encoding="utf-8"))
    except OSError:
        logger.addHandler(logging.NullHandler())
logger.setLevel(logging.INFO)


def record_event(event_type: str, **payload: Any) -> dict[str, Any]:
    ensure_runtime_dirs()
    event = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "epoch": round(time.time(), 3),
        "type": event_type,
        **payload,
    }
    ACTION_EVENT_LOG.append(event)
    with RUNTIME_EVENT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=True) + "\n")
    logger.info("EVENT %s %s", event_type, json.dumps(payload, ensure_ascii=True, default=str))
    return event


def tool_log(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        logger.info("CALL %s args=%s", fn.__name__, json.dumps(kwargs, ensure_ascii=True, default=str))
        try:
            result = fn(*args, **kwargs)
            logger.info("OK %s", fn.__name__)
            return result
        except Exception as exc:
            logger.error("FAIL %s: %s", fn.__name__, exc, exc_info=True)
            raise

    return wrapper


def recent_events(limit: int = 25) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 250))
    return list(ACTION_EVENT_LOG)[-limit:]


def clear_events() -> dict[str, Any]:
    cleared = len(ACTION_EVENT_LOG)
    ACTION_EVENT_LOG.clear()
    return {"cleared": cleared, "log_path": str(RUNTIME_EVENT_LOG)}


def runtime_status() -> dict[str, Any]:
    ensure_runtime_dirs()
    return {
        "active_playwright_sessions": len(PLAYWRIGHT_SESSIONS),
        "active_desktop_watch_sessions": len(DESKTOP_WATCH_SESSIONS),
        "recent_event_count": len(ACTION_EVENT_LOG),
        "event_log_path": str(RUNTIME_EVENT_LOG),
    }


def runtime_health_check() -> dict[str, Any]:
    ensure_runtime_dirs()
    availability = browser_availability(include_firefox=True)
    ocr = ocr_availability()
    playwright_installed = importlib.util.find_spec("playwright") is not None
    diagnostics: dict[str, Any] = {
        "status": "ok",
        "checks": {
            "event_log_dir_exists": RUNTIME_EVENT_LOG.parent.exists(),
            "browser_capture_dir_exists": BROWSER_CAPTURE_ROOT.exists(),
            "browser_profile_dir_exists": BROWSER_PROFILE_ROOT.exists(),
            **availability,
            "playwright_installed": playwright_installed,
            **ocr,
            "playwright_sessions": len(PLAYWRIGHT_SESSIONS),
            "desktop_watch_sessions": len(DESKTOP_WATCH_SESSIONS),
        },
    }
    if not diagnostics["checks"]["any_browser_available"] or not diagnostics["checks"]["playwright_installed"]:
        diagnostics["status"] = "degraded"
    return diagnostics
