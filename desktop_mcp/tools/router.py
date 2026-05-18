"""Global router — single 'do' tool for models that struggle with multiple tools.

Also includes lightweight utility functions: clipboard_bridge, replay_last.
"""
from __future__ import annotations

import re
from typing import Any


# Keyword → (tool_name, action) mapping
_ROUTE_RULES: list[tuple[list[str], str, str]] = [
    # Browser navigation
    (["scroll down", "scroll page", "page down"], "browser_navigate", "scroll"),
    (["scroll up", "page up"], "browser_navigate", "scroll"),
    (["go back", "back", "previous page"], "browser_navigate", "back"),
    (["go forward", "forward", "next page"], "browser_navigate", "forward"),
    (["reload", "refresh", "refresh page"], "browser_navigate", "reload"),
    (["new tab", "open tab", "new page"], "browser_navigate", "new_page"),
    (["close tab", "close page"], "browser_navigate", "close_page"),
    (["switch tab"], "browser_navigate", "switch_page"),
    (["navigate to", "go to", "open url", "open http", "open https", "visit"], "browser_navigate", "goto"),
    # Browser interaction
    (["click on", "click the", "click button", "press button", "tap"], "browser_interact", "click_text"),
    (["type in", "type text", "write text", "enter text", "input text"], "browser_interact", "type"),
    (["fill form", "fill the form", "fill fields"], "browser_interact", "fill_form"),
    (["press key", "press enter", "press escape", "hit enter"], "browser_interact", "press"),
    (["hover over", "hover on", "mouse over"], "browser_interact", "hover"),
    # Browser observation
    (["screenshot", "capture page", "take screenshot"], "browser_observe", "capture"),
    (["what do i see", "observe", "describe page", "page state"], "browser_observe", "observe_rich"),
    (["wait for", "wait until"], "browser_observe", "wait_text"),
    # Browser session
    (["open browser", "launch browser", "start browser", "open chrome"], "browser_session", "open"),
    (["close browser", "stop browser", "close session"], "browser_session", "close"),
    (["list sessions", "active sessions"], "browser_session", "list"),
    # Desktop interaction
    (["click at", "click x", "click position", "click coordinate"], "desktop_interact", "click"),
    (["double click"], "desktop_interact", "double_click"),
    (["right click"], "desktop_interact", "right_click"),
    (["type", "keyboard type", "send keys"], "desktop_interact", "kb_type"),
    (["press", "key press"], "desktop_interact", "kb_press"),
    (["hotkey", "shortcut", "ctrl+", "alt+"], "desktop_interact", "kb_hotkey"),
    (["scroll", "mouse scroll", "wheel"], "desktop_interact", "mouse_scroll"),
    (["move mouse", "mouse move"], "desktop_interact", "mouse_move"),
    (["drag", "drag and drop"], "desktop_interact", "mouse_drag"),
    (["copy", "clipboard get"], "desktop_interact", "clip_get"),
    (["paste", "clipboard set"], "desktop_interact", "clip_set"),
    (["suggest actions", "what can i click", "available actions"], "desktop_interact", "suggest_actions"),
    # Desktop observation
    (["desktop screenshot", "capture screen", "capture desktop"], "desktop_observe", "capture"),
    (["ocr", "read screen", "read text", "extract text"], "desktop_observe", "ocr_window"),
    (["desktop observe", "desktop state", "window state"], "desktop_observe", "observe_rich"),
    (["find text", "search text", "locate text"], "desktop_observe", "ocr_find"),
    # Desktop window
    (["list windows", "open windows", "active windows"], "desktop_window", "list"),
    (["focus window", "switch window", "activate window"], "desktop_window", "focus"),
    (["minimize window", "minimize"], "desktop_window", "minimize"),
    (["maximize window", "maximize"], "desktop_window", "maximize"),
    (["close window"], "desktop_window", "close"),
    # System
    (["system info", "computer info", "os info"], "system_info", "info"),
    (["run command", "execute", "shell"], "system_ops", "run"),
    (["list files", "directory", "ls"], "system_ops", "list"),
    (["read file", "cat file", "view file"], "system_ops", "read"),
]


def _extract_url(instruction: str) -> str | None:
    """Extract URL from instruction text."""
    match = re.search(r'https?://[^\s\'"]+', instruction)
    return match.group(0) if match else None


def _extract_number(instruction: str) -> int | None:
    """Extract first number from instruction."""
    match = re.search(r'\b(\d+)\b', instruction)
    return int(match.group(1)) if match else None


def _extract_quoted(instruction: str) -> str | None:
    """Extract quoted text from instruction."""
    match = re.search(r'["\']([^"\']+)["\']', instruction)
    return match.group(1) if match else None


def route_instruction(instruction: str) -> tuple[str, str, dict[str, Any]]:
    """Route a natural language instruction to tool + action + kwargs.
    
    Returns (tool_name, action, kwargs).
    Raises ValueError if no route found.
    """
    text = instruction.strip().lower()
    
    # Try exact/prefix matching against rules
    best_match: tuple[str, str] | None = None
    best_score = 0
    
    for keywords, tool, action in _ROUTE_RULES:
        for kw in keywords:
            if kw in text:
                score = len(kw)
                if score > best_score:
                    best_score = score
                    best_match = (tool, action)
    
    if not best_match:
        # Fallback: if it looks like a URL, navigate to it
        url = _extract_url(instruction)
        if url:
            return "browser_navigate", "goto", {"url": url}
        raise ValueError(
            f"Could not route instruction: {instruction!r}. "
            "Try being more specific or use tool(action='help') for available actions."
        )
    
    tool_name, action = best_match
    kwargs: dict[str, Any] = {}
    
    # Auto-extract kwargs based on action type
    if action == "goto":
        url = _extract_url(instruction)
        if url:
            kwargs["url"] = url
        else:
            quoted = _extract_quoted(instruction)
            if quoted:
                kwargs["url"] = quoted if quoted.startswith("http") else f"https://{quoted}"
    elif action == "scroll":
        if "up" in text:
            kwargs["delta_y"] = -800
        else:
            kwargs["delta_y"] = 800
        num = _extract_number(instruction)
        if num and num > 10:
            kwargs["delta_y"] = num if "up" not in text else -num
    elif action in ("type", "kb_type"):
        quoted = _extract_quoted(instruction)
        if quoted:
            kwargs["text"] = quoted
    elif action in ("click_text",):
        quoted = _extract_quoted(instruction)
        if quoted:
            kwargs["text"] = quoted
        else:
            # Try to extract what to click from natural language
            for prefix in ("click on ", "click the ", "click ", "press ", "tap "):
                if text.startswith(prefix):
                    kwargs["text"] = instruction[len(prefix):].strip().strip("'\"")
                    break
    elif action in ("press", "kb_press"):
        for key in ("enter", "escape", "tab", "space", "backspace", "delete"):
            if key in text:
                kwargs["key"] = key
                break
    elif action == "kb_hotkey":
        # Extract hotkey like ctrl+c, alt+tab
        match = re.search(r'(ctrl|alt|shift|win)\s*\+\s*(\w+)', text)
        if match:
            kwargs["keys"] = f"{match.group(1)}+{match.group(2)}"
    elif action == "mouse_scroll":
        if "up" in text:
            kwargs["clicks"] = -3
        else:
            kwargs["clicks"] = 3
    elif action == "focus":
        quoted = _extract_quoted(instruction)
        if quoted:
            kwargs["title_regex"] = quoted
    elif action == "open":
        url = _extract_url(instruction)
        if url:
            kwargs["url"] = url
        else:
            kwargs["url"] = "about:blank"
    
    return tool_name, action, kwargs


# ═══ UTILITY FUNCTIONS ═════════════════════════════════════════════

def clipboard_bridge(direction: str = "browser_to_desktop", session_id: str = "") -> dict[str, Any]:
    """Copy content between browser and desktop clipboard.
    
    direction: 'browser_to_desktop' or 'desktop_to_browser'.
    """
    import pyperclip
    from ..browser_sessions import get_playwright_session, get_playwright_page

    if direction == "browser_to_desktop":
        _, _, page = get_playwright_page(session_id)
        text = page.evaluate("() => window.getSelection().toString() || document.activeElement?.value || ''")
        if text:
            pyperclip.copy(text)
        return {"ok": True, "direction": direction, "text": text[:500], "length": len(text)}
    elif direction == "desktop_to_browser":
        text = pyperclip.paste() or ""
        if text:
            _, _, page = get_playwright_page(session_id)
            page.evaluate(f"""() => {{
                const el = document.activeElement;
                if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) {{
                    el.value = (el.value || '') + {repr(text)};
                    el.dispatchEvent(new Event('input', {{bubbles: true}}));
                }}
            }}""")
        return {"ok": True, "direction": direction, "text": text[:500], "length": len(text)}
    else:
        return {"ok": False, "error": f"direction must be 'browser_to_desktop' or 'desktop_to_browser', got {direction!r}"}


_PROXY_POOL: list[dict[str, Any]] = []


def proxy_manager(
    action: str = "list",
    proxy_url: str = "",
    label: str = "",
) -> dict[str, Any]:
    """Manage proxy pool for browser sessions.
    
    action: 'add', 'remove', 'list', 'next' (round-robin), 'health_check'.
    proxy_url: e.g. 'http://user:pass@proxy.com:8080' or 'socks5://proxy:1080'.
    """
    import urllib.request
    
    if action == "list":
        return {"ok": True, "proxies": _PROXY_POOL, "count": len(_PROXY_POOL)}
    
    elif action == "add":
        if not proxy_url:
            return {"ok": False, "error": "proxy_url required"}
        entry = {"url": proxy_url, "label": label or proxy_url[:30], "healthy": True, "uses": 0}
        _PROXY_POOL.append(entry)
        return {"ok": True, "added": entry, "total": len(_PROXY_POOL)}
    
    elif action == "remove":
        before = len(_PROXY_POOL)
        _PROXY_POOL[:] = [p for p in _PROXY_POOL if p["url"] != proxy_url and p["label"] != proxy_url]
        return {"ok": True, "removed": before - len(_PROXY_POOL), "remaining": len(_PROXY_POOL)}
    
    elif action == "next":
        healthy = [p for p in _PROXY_POOL if p.get("healthy", True)]
        if not healthy:
            return {"ok": False, "error": "No healthy proxies available"}
        # Round-robin: pick the one with fewest uses
        proxy = min(healthy, key=lambda p: p["uses"])
        proxy["uses"] += 1
        return {"ok": True, "proxy": proxy["url"], "label": proxy["label"], "uses": proxy["uses"]}
    
    elif action == "health_check":
        results = []
        for p in _PROXY_POOL:
            try:
                proxy_handler = urllib.request.ProxyHandler({"http": p["url"], "https": p["url"]})
                opener = urllib.request.build_opener(proxy_handler)
                opener.open("http://httpbin.org/ip", timeout=5)
                p["healthy"] = True
                results.append({"url": p["url"], "healthy": True})
            except Exception as e:
                p["healthy"] = False
                results.append({"url": p["url"], "healthy": False, "error": str(e)[:100]})
        return {"ok": True, "results": results, "healthy_count": sum(1 for r in results if r["healthy"])}
    
    return {"ok": False, "error": f"Unknown proxy action: {action}"}


def multi_browser_run(
    urls: list[str] | None = None,
    action_per_page: str = "text",
    max_parallel: int = 3,
) -> dict[str, Any]:
    """Open multiple URLs in parallel sessions and extract content.
    
    action_per_page: 'text' (get page text), 'screenshot' (capture), 'links' (extract links).
    Returns results for all URLs.
    """
    if not urls:
        return {"ok": False, "error": "urls list required"}
    
    from ..browser_sessions import get_playwright_session, list_playwright_sessions
    from ..tools.browser_sessions import (
        browser_open_session, browser_navigate as _nav,
        browser_get_text, browser_scroll_extract,
    )
    from ..tools.browser_helpers import browser_capture_page
    import concurrent.futures
    
    results = []
    
    def _process_url(url: str) -> dict[str, Any]:
        try:
            session = browser_open_session(url=url)
            sid = session.get("session_id", "")
            if not sid:
                return {"url": url, "ok": False, "error": "Failed to open session"}
            
            if action_per_page == "text":
                data = browser_scroll_extract(session_id=sid, max_scrolls=2, extract_mode="text")
            elif action_per_page == "screenshot":
                data = browser_capture_page(session_id=sid)
            elif action_per_page == "links":
                data = browser_scroll_extract(session_id=sid, max_scrolls=2, extract_mode="links")
            else:
                data = browser_get_text(session_id=sid)
            
            return {"url": url, "ok": True, "session_id": sid, **data}
        except Exception as e:
            return {"url": url, "ok": False, "error": str(e)}
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as executor:
        futures = {executor.submit(_process_url, url): url for url in urls[:10]}
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    
    return {
        "ok": all(r.get("ok", False) for r in results),
        "results": results,
        "total": len(results),
        "succeeded": sum(1 for r in results if r.get("ok")),
    }


def action_recorder(
    action: str = "status",
    name: str = "",
    step_tool: str = "",
    step_action: str = "",
    step_kwargs: dict | None = None,
) -> dict[str, Any]:
    """Record and replay action sequences.
    
    action: 'start', 'stop', 'add_step', 'replay', 'list', 'delete', 'status'.
    """
    import json as _json
    import os
    
    recordings_dir = os.path.join(os.path.expanduser("~"), ".mcp_recordings")
    os.makedirs(recordings_dir, exist_ok=True)
    
    # Use a module-level variable for current recording
    if not hasattr(action_recorder, "_current"):
        action_recorder._current = None
        action_recorder._steps = []
    
    if action == "start":
        if not name:
            return {"ok": False, "error": "name required to start recording"}
        action_recorder._current = name
        action_recorder._steps = []
        return {"ok": True, "recording": name, "status": "started"}
    
    elif action == "add_step":
        if not action_recorder._current:
            return {"ok": False, "error": "No recording in progress. Call start first."}
        action_recorder._steps.append({
            "tool": step_tool,
            "action": step_action,
            "kwargs": step_kwargs or {},
        })
        return {"ok": True, "recording": action_recorder._current, "steps": len(action_recorder._steps)}
    
    elif action == "stop":
        if not action_recorder._current:
            return {"ok": False, "error": "No recording in progress"}
        path = os.path.join(recordings_dir, f"{action_recorder._current}.json")
        with open(path, "w") as f:
            _json.dump({"name": action_recorder._current, "steps": action_recorder._steps}, f, indent=2)
        result = {"ok": True, "recording": action_recorder._current, "steps": len(action_recorder._steps), "path": path}
        action_recorder._current = None
        action_recorder._steps = []
        return result
    
    elif action == "list":
        files = [f.replace(".json", "") for f in os.listdir(recordings_dir) if f.endswith(".json")]
        return {"ok": True, "recordings": files, "count": len(files)}
    
    elif action == "delete":
        path = os.path.join(recordings_dir, f"{name}.json")
        if os.path.exists(path):
            os.remove(path)
            return {"ok": True, "deleted": name}
        return {"ok": False, "error": f"Recording '{name}' not found"}
    
    elif action == "replay":
        path = os.path.join(recordings_dir, f"{name}.json")
        if not os.path.exists(path):
            return {"ok": False, "error": f"Recording '{name}' not found"}
        with open(path) as f:
            data = _json.load(f)
        return {
            "ok": True,
            "name": data["name"],
            "steps": data["steps"],
            "step_count": len(data["steps"]),
            "hint": "Use batch to execute these steps: browser_interact(action='batch', kwargs='{\"actions\": [...]}')",
        }
    
    elif action == "status":
        return {
            "ok": True,
            "recording": action_recorder._current,
            "steps_recorded": len(action_recorder._steps) if action_recorder._current else 0,
        }
    
    return {"ok": False, "error": f"Unknown recorder action: {action}"}


def replay_last(count: int = 5) -> dict[str, Any]:
    """Replay the last N actions from the event log.
    
    Returns the actions that were replayed (read-only report, not actual re-execution).
    """
    from ..state import ACTION_EVENT_LOG
    
    recent = list(ACTION_EVENT_LOG)[-count:]
    return {
        "ok": True,
        "recent_actions": [
            {"event": e.get("event", ""), "timestamp": e.get("timestamp", ""), 
             "details": {k: v for k, v in e.items() if k not in ("event", "timestamp") and not k.startswith("_")}}
            for e in recent
        ],
        "count": len(recent),
        "hint": "These are the last actions executed. Use batch to re-run them.",
    }
