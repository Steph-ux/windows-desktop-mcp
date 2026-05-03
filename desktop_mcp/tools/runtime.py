"""Runtime and observability MCP tools."""

from __future__ import annotations

import importlib.util
import threading
import shutil
import subprocess
import sys
import time
from typing import Any

import psutil
import pyautogui
import win32clipboard

from ..app import mcp
from ..browser_core import browser_availability
from ..paths import SCREENSHOT_DIR
from ..runtime import tool_log
from ..state import PLAYWRIGHT_SESSIONS, SESSION_MAX_AGE_MINUTES
from ..tools_runtime import runtime_clear_events, runtime_get_recent_events, runtime_get_status, runtime_healthcheck
from ..desktop_core import list_windows_data, focused_window_data

_MACROS: dict[str, list[dict[str, Any]]] = {}
_MACROS_LOCK = threading.RLock()
@tool_log
def ping() -> dict[str, Any]:
    """Health check complet du serveur MCP desktop."""
    width, height = pyautogui.size()
    try:
        availability = browser_availability(include_firefox=True)
    except Exception:
        availability = {
            "chrome_available": False,
            "edge_available": False,
            "firefox_available": False,
            "any_browser_available": False,
        }
    try:
        window_count = len([item for item in list_windows_data(visible_only=True) if item.get("title")])
    except Exception:
        window_count = -1
    mem = psutil.virtual_memory()
    return {
        "status": "ok",
        "platform": sys.platform,
        "screen": {"width": width, "height": height},
        "screenshot_dir": str(SCREENSHOT_DIR),
        "capabilities": {
            "tesseract": bool(shutil.which("tesseract")),
            "playwright": importlib.util.find_spec("playwright") is not None,
            "chrome": availability["chrome_available"],
            "edge": availability["edge_available"],
            "firefox": availability["firefox_available"],
        },
        "state": {
            "playwright_sessions": len(PLAYWRIGHT_SESSIONS),
            "visible_windows": window_count,
            "session_max_age_minutes": SESSION_MAX_AGE_MINUTES,
            "focused_window": focused_window_data(),
        },
        "system": {
            "ram_total_gb": round(mem.total / 1e9, 1),
            "ram_available_gb": round(mem.available / 1e9, 1),
            "ram_used_pct": mem.percent,
        },
        "config": {
            "pyautogui_pause": pyautogui.PAUSE,
            "failsafe": pyautogui.FAILSAFE,
        },
    }
@tool_log
def clipboard_get() -> dict[str, Any]:
    """Lit le contenu texte du presse-papier Windows."""
    win32clipboard.OpenClipboard()
    try:
        try:
            text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        except Exception:
            text = ""
    finally:
        win32clipboard.CloseClipboard()
    return {"text": text, "length": len(text)}
@tool_log
def clipboard_set(text: str) -> dict[str, Any]:
    """Ecrit du texte dans le presse-papier Windows."""
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()
    return {"ok": True, "length": len(text)}
@tool_log
def run_command(
    command: list[str],
    cwd: str | None = None,
    wait: bool = False,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Lance un processus local. Si wait=True, retourne stdout/stderr."""
    if not command:
        raise ValueError("Provide at least one command part.")
    if wait:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=max(timeout_seconds, 0.1),
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[:4096],
            "stderr": result.stderr[:4096],
            "command": command,
        }
    proc = subprocess.Popen(command, cwd=cwd)
    return {"pid": proc.pid, "command": command}
@tool_log
def macro_record_action(macro_id: str, action: dict[str, Any]) -> dict[str, Any]:
    """Ajoute une action a un macro en memoire."""
    if not macro_id.strip():
        raise ValueError("macro_id cannot be empty.")
    if not action or "action" not in action:
        raise ValueError("Action must include an 'action' field.")
    entry = {**action, "timestamp": round(time.time(), 3)}
    with _MACROS_LOCK:
        _MACROS.setdefault(macro_id, []).append(entry)
        step_count = len(_MACROS[macro_id])
    return {"macro_id": macro_id, "step_count": step_count}
@tool_log
def macro_list() -> dict[str, Any]:
    """Liste les macros en memoire et leur nombre d'etapes."""
    with _MACROS_LOCK:
        return {"macros": {macro_id: len(steps) for macro_id, steps in _MACROS.items()}}
@tool_log
def macro_replay(macro_id: str, speed: float = 1.0) -> dict[str, Any]:
    """Rejoue un macro en memoire."""
    with _MACROS_LOCK:
        steps = list(_MACROS.get(macro_id, []))
    if not steps:
        raise ValueError(f"Macro '{macro_id}' introuvable.")
    effective_speed = max(float(speed), 0.01)
    executed = 0
    for step in steps:
        action = str(step.get("action", "")).lower()
        if action == "click":
            pyautogui.click(
                x=int(step["x"]),
                y=int(step["y"]),
                button=str(step.get("button", "left")),
                clicks=max(int(step.get("clicks", 1)), 1),
                interval=max(float(step.get("interval", 0.0)), 0.0),
            )
        elif action == "type":
            pyautogui.write(str(step.get("text", "")), interval=max(float(step.get("interval", 0.0)), 0.0))
        elif action == "hotkey":
            keys = [str(key) for key in step.get("keys", [])]
            if not keys:
                raise ValueError(f"Macro '{macro_id}' contains a hotkey action without keys.")
            pyautogui.hotkey(*keys, interval=max(float(step.get("interval", 0.0)), 0.0))
        elif action == "wait":
            time.sleep(max(float(step.get("seconds", 0.0)), 0.0) / effective_speed)
        else:
            raise ValueError(f"Unsupported macro action: {action}")
        executed += 1
    return {"macro_id": macro_id, "steps_executed": executed}
@tool_log
def macro_clear(macro_id: str | None = None) -> dict[str, Any]:
    """Supprime une macro cible ou toutes les macros."""
    with _MACROS_LOCK:
        if macro_id is None:
            cleared = len(_MACROS)
            _MACROS.clear()
            return {"cleared": cleared, "scope": "all"}
        removed = 1 if _MACROS.pop(macro_id, None) is not None else 0
    return {"cleared": removed, "macro_id": macro_id}


__all__ = [
    "clipboard_get",
    "clipboard_set",
    "macro_clear",
    "macro_list",
    "macro_record_action",
    "macro_replay",
    "ping",
    "run_command",
    "runtime_clear_events",
    "runtime_get_recent_events",
    "runtime_get_status",
    "runtime_healthcheck",
]
