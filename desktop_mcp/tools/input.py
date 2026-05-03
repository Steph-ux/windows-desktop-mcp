"""Input-oriented MCP tools."""

from __future__ import annotations

import pyautogui
from typing import Any
import win32clipboard

from ..app import mcp
from ..desktop_core import focused_window_data, validate_screen_point
from ..runtime import record_event, tool_log
def get_cursor_position() -> dict[str, int]:
    x, y = pyautogui.position()
    return {"x": x, "y": y}
def move_mouse(x: int, y: int, duration: float = 0.0) -> dict[str, int]:
    validate_screen_point(x, y)
    pyautogui.moveTo(x, y, duration=max(duration, 0.0))
    pos = pyautogui.position()
    result = {"x": pos.x, "y": pos.y}
    record_event("move_mouse", **result)
    return result
def click(x: int, y: int, button: str = "left", clicks: int = 1, interval: float = 0.1) -> dict[str, Any]:
    validate_screen_point(x, y)
    pyautogui.click(x=x, y=y, button=button, clicks=clicks, interval=interval)
    result = {"ok": True, "x": x, "y": y, "button": button, "clicks": clicks}
    record_event("click", **result)
    return result
def double_click(x: int, y: int, button: str = "left") -> dict[str, Any]:
    validate_screen_point(x, y)
    pyautogui.doubleClick(x=x, y=y, button=button)
    result = {"ok": True, "x": x, "y": y, "button": button}
    record_event("double_click", **result)
    return result
def right_click(x: int, y: int) -> dict[str, Any]:
    validate_screen_point(x, y)
    pyautogui.click(x=x, y=y, button="right")
    result = {"ok": True, "x": x, "y": y, "button": "right"}
    record_event("right_click", **result)
    return result
def drag_mouse(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration: float = 0.2,
    button: str = "left",
) -> dict[str, Any]:
    validate_screen_point(start_x, start_y)
    validate_screen_point(end_x, end_y)
    pyautogui.moveTo(start_x, start_y, duration=0)
    pyautogui.dragTo(end_x, end_y, duration=max(duration, 0.0), button=button)
    result = {"ok": True, "start": {"x": start_x, "y": start_y}, "end": {"x": end_x, "y": end_y}, "button": button}
    record_event("drag_mouse", start_x=start_x, start_y=start_y, end_x=end_x, end_y=end_y, button=button)
    return result
def scroll(clicks: int) -> dict[str, int]:
    pyautogui.scroll(clicks)
    result = {"clicks": clicks}
    record_event("scroll", **result)
    return result
@tool_log
def type_text(text: str, interval: float = 0.01, require_handle: int | None = None) -> dict[str, Any]:
    """Tape du texte. Si require_handle est fourni, verifie la fenetre focus."""
    if require_handle is not None:
        active = focused_window_data().get("handle")
        if active != require_handle:
            raise RuntimeError(f"Fenetre active ({active}) != attendue ({require_handle})")
    pyautogui.write(text, interval=max(interval, 0.0))
    result = {"ok": True, "length": len(text)}
    record_event("type_text", length=len(text))
    return result
@tool_log
def type_text_unicode(text: str, require_handle: int | None = None) -> dict[str, Any]:
    """Tape du texte Unicode via le presse-papier Windows puis Ctrl+V."""
    if require_handle is not None:
        active = focused_window_data().get("handle")
        if active != require_handle:
            raise RuntimeError(f"Fenetre active ({active}) != attendue ({require_handle})")
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()
    pyautogui.hotkey("ctrl", "v")
    result = {"ok": True, "length": len(text), "method": "clipboard"}
    record_event("type_text_unicode", length=len(text), method="clipboard")
    return result
def press_key(key: str, presses: int = 1, interval: float = 0.05) -> dict[str, Any]:
    pyautogui.press(key, presses=max(presses, 1), interval=max(interval, 0.0))
    result = {"ok": True, "key": key, "presses": max(presses, 1)}
    record_event("press_key", **result)
    return result
def hotkey(keys: list[str], interval: float = 0.05) -> dict[str, Any]:
    if not keys:
        raise ValueError("Provide at least one key.")
    pyautogui.hotkey(*keys, interval=max(interval, 0.0))
    result = {"ok": True, "keys": keys}
    record_event("hotkey", keys=keys)
    return result


__all__ = [
    "click",
    "double_click",
    "drag_mouse",
    "get_cursor_position",
    "hotkey",
    "move_mouse",
    "press_key",
    "right_click",
    "scroll",
    "type_text",
    "type_text_unicode",
]
