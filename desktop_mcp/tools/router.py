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
