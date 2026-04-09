from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any

import pyautogui
import win32gui
from mss import mss
from PIL import Image as PILImage
from pywinauto import Desktop
from pywinauto.findwindows import ElementAmbiguousError, ElementNotFoundError

from .helpers import ensure_windows, wait_until


def desktop() -> Desktop:
    ensure_windows()
    return Desktop(backend="uia")


def desktop_win32() -> Desktop:
    ensure_windows()
    return Desktop(backend="win32")


def window_info(window: Any) -> dict[str, Any]:
    try:
        rect = window.rectangle()
        bounds = {
            "left": rect.left,
            "top": rect.top,
            "right": rect.right,
            "bottom": rect.bottom,
            "width": rect.width(),
            "height": rect.height(),
        }
    except Exception:
        bounds = None

    try:
        handle = int(window.handle)
    except Exception:
        handle = None

    try:
        title = window.window_text()
    except Exception:
        title = ""

    try:
        class_name = window.class_name()
    except Exception:
        class_name = ""

    try:
        control_type = window.element_info.control_type
    except Exception:
        control_type = ""

    try:
        automation_id = window.element_info.automation_id
    except Exception:
        automation_id = ""

    return {
        "handle": handle,
        "title": title,
        "class_name": class_name,
        "control_type": control_type,
        "automation_id": automation_id,
        "bounds": bounds,
    }


def find_window(
    title_regex: str | None = None,
    handle: int | None = None,
    visible_only: bool = True,
):
    desktop_obj = desktop()
    if handle is not None:
        for _ in range(3):
            try:
                return desktop_obj.window(handle=handle).wrapper_object()
            except ElementNotFoundError:
                time.sleep(0.15)
            except Exception:
                time.sleep(0.2)
        raise ValueError(f"Window handle not found: {handle}")

    if not title_regex:
        raise ValueError("Provide either handle or title_regex.")

    for _ in range(3):
        try:
            return desktop_obj.window(title_re=title_regex, visible_only=visible_only).wrapper_object()
        except ElementAmbiguousError:
            try:
                matches = desktop_obj.windows(title_re=title_regex, visible_only=visible_only)
            except Exception:
                matches = []
            if matches:
                return matches[0].wrapper_object() if hasattr(matches[0], "wrapper_object") else matches[0]
            time.sleep(0.15)
        except ElementNotFoundError:
            time.sleep(0.15)
        except Exception:
            time.sleep(0.2)
    raise ValueError(f"No window matched title_regex={title_regex!r}")


def grab_png_bytes(monitor: dict[str, int] | None = None) -> tuple[bytes, dict[str, int]]:
    ensure_windows()
    with mss() as sct:
        target = monitor or sct.monitors[0]
        shot = sct.grab(target)
        image = PILImage.frombytes("RGB", shot.size, shot.rgb)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue(), {
            "left": target["left"],
            "top": target["top"],
            "width": target["width"],
            "height": target["height"],
        }


def virtual_screen_bounds() -> dict[str, int]:
    ensure_windows()
    with mss() as sct:
        monitor = sct.monitors[0]
        return {
            "left": monitor["left"],
            "top": monitor["top"],
            "right": monitor["left"] + monitor["width"],
            "bottom": monitor["top"] + monitor["height"],
            "width": monitor["width"],
            "height": monitor["height"],
        }


def save_png_bytes(png_bytes: bytes, path: str | None) -> str | None:
    if not path:
        return None
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(png_bytes)
    return str(target)


def window_capture_bounds(bounds: dict[str, int], padding: int = 0) -> dict[str, int]:
    ensure_windows()
    screen = virtual_screen_bounds()
    left = max(bounds["left"] - padding, screen["left"])
    top = max(bounds["top"] - padding, screen["top"])
    right = min(bounds["right"] + padding, screen["right"])
    bottom = min(bounds["bottom"] + padding, screen["bottom"])
    return {
        "left": left,
        "top": top,
        "width": max(1, right - left),
        "height": max(1, bottom - top),
    }


def validate_screen_point(x: int, y: int) -> None:
    ensure_windows()
    screen = virtual_screen_bounds()
    if not (screen["left"] <= x < screen["right"] and screen["top"] <= y < screen["bottom"]):
        raise ValueError(
            f"Point out of bounds: ({x}, {y}) for virtual screen "
            f"{screen['left']},{screen['top']} to {screen['right']},{screen['bottom']}."
        )


def list_windows_data(title_filter: str = "", visible_only: bool = True) -> list[dict[str, Any]]:
    windows = []
    filter_text = title_filter.lower().strip()
    try:
        raw_windows = desktop().windows()
    except Exception:
        raw_windows = desktop_win32().windows()
    for window in raw_windows:
        info = window_info(window)
        if visible_only and not info["title"]:
            continue
        searchable = " ".join(
            [str(info["title"]), str(info["class_name"]), str(info["automation_id"])]
        ).lower()
        if filter_text and filter_text not in searchable:
            continue
        windows.append(info)
    windows.sort(key=lambda item: (item["title"] or "", item["handle"] or 0))
    return windows


def wait_for_window_data(
    title_regex: str | None = None,
    title_filter: str = "",
    timeout_seconds: float = 10.0,
    interval_seconds: float = 0.2,
    visible_only: bool = True,
) -> dict[str, Any]:
    deadline = time.time() + max(timeout_seconds, 0.1)
    filter_text = title_filter.lower().strip()

    def predicate():
        if title_regex:
            try:
                return window_info(find_window(title_regex=title_regex, visible_only=visible_only))
            except Exception:
                return None
        for info in list_windows_data(title_filter=title_filter, visible_only=visible_only):
            if not filter_text or filter_text in (info["title"] or "").lower():
                return info
        return None

    result = wait_until(deadline, max(interval_seconds, 0.05), predicate)
    if not result:
        raise ValueError("Timed out waiting for a matching window.")
    return result


def focus_window_data(
    title_regex: str | None = None,
    handle: int | None = None,
    wait_seconds: float = 0.3,
) -> dict[str, Any]:
    window = find_window(title_regex=title_regex, handle=handle)
    for attempt in range(3):
        try:
            window.set_focus()
            break
        except Exception:
            try:
                window.restore()
            except Exception:
                pass
            if attempt == 2:
                raise
            time.sleep(0.2)
    time.sleep(max(wait_seconds, 0.0))
    return window_info(window)


def move_resize_window_data(handle: int, x: int, y: int, width: int, height: int) -> dict[str, Any]:
    window = find_window(handle=handle)
    try:
        window.move_window(x=x, y=y, width=width, height=height, repaint=True)
    except AttributeError:
        win32gui.MoveWindow(int(handle), int(x), int(y), int(width), int(height), True)
    return window_info(window)


def find_matching_elements(
    window: Any,
    automation_id: str | None = None,
    title: str | None = None,
    title_contains: str | None = None,
    control_type: str | None = None,
    class_name: str | None = None,
) -> list[tuple[Any, dict[str, Any]]]:
    for attempt in range(3):
        try:
            descendants = window.descendants()
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(0.2)
    matches = []
    for element in descendants:
        info = window_info(element)
        if automation_id and info["automation_id"] != automation_id:
            continue
        if title and info["title"] != title:
            continue
        if title_contains and title_contains.lower() not in info["title"].lower():
            continue
        if control_type and info["control_type"] != control_type:
            continue
        if class_name and info["class_name"] != class_name:
            continue
        matches.append((element, info))
    return matches


def focused_window_data() -> dict[str, Any]:
    ensure_windows()
    handle = win32gui.GetForegroundWindow()
    if not handle:
        raise ValueError("Could not determine the foreground window.")
    try:
        window = desktop().window(handle=handle)
    except Exception:
        window = desktop_win32().window(handle=handle)
    return window_info(window)
