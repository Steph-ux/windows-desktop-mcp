"""Legacy desktop compatibility shim.

This module is intentionally not imported by the MCP server. The active server
surface lives under desktop_mcp.tools.*. Keep this module only for
backwards-compatible direct Python imports during the transition.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import subprocess
import shutil
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

import pyautogui
import psutil
import win32clipboard
from mcp.server.fastmcp import Image
from PIL import Image as PILImage
from PIL import ImageChops
from PIL import ImageDraw
import numpy as np

from .app import mcp
from .desktop_core import (
    find_matching_elements,
    find_window,
    focused_window_data,
    focus_window_data,
    grab_png_bytes,
    list_windows_data,
    move_resize_window_data,
    save_png_bytes,
    validate_screen_point,
    wait_for_window_data,
    window_capture_bounds,
    window_info,
)
from .helpers import now_stamp, wait_until
from .browser_core import browser_availability
from .ocr_core import find_ocr_text_spans, ocr_image_object
from .paths import DESKTOP_WATCH_CAPTURE_ROOT, SCREENSHOT_DIR
from .runtime import record_event, tool_log
from .state import DESKTOP_WATCH_LOCK, DESKTOP_WATCH_SESSIONS, PLAYWRIGHT_SESSIONS, SESSION_MAX_AGE_MINUTES


def ping() -> dict[str, Any]:
    from .tools.runtime import ping as runtime_ping

    return runtime_ping()


def list_windows(title_filter: str = "", visible_only: bool = True) -> list[dict[str, Any]]:
    from .tools.windows import list_windows as windows_list_windows

    return windows_list_windows(title_filter=title_filter, visible_only=visible_only)


def wait_for_window(
    title_regex: str | None = None,
    title_filter: str = "",
    timeout_seconds: float = 10.0,
    interval_seconds: float = 0.2,
    visible_only: bool = True,
) -> dict[str, Any]:
    from .tools.windows import wait_for_window as windows_wait_for_window

    return windows_wait_for_window(
        title_regex=title_regex,
        title_filter=title_filter,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        visible_only=visible_only,
    )


def focus_window(title_regex: str | None = None, handle: int | None = None, wait_seconds: float = 0.3) -> dict[str, Any]:
    from .tools.windows import focus_window as windows_focus_window

    return windows_focus_window(title_regex=title_regex, handle=handle, wait_seconds=wait_seconds)


def get_focused_window() -> dict[str, Any]:
    from .tools.windows import get_focused_window as windows_get_focused_window

    return windows_get_focused_window()


def get_active_window() -> dict[str, Any]:
    from .tools.windows import get_active_window as windows_get_active_window

    return windows_get_active_window()


def move_resize_window(handle: int, x: int, y: int, width: int, height: int) -> dict[str, Any]:
    from .tools.windows import move_resize_window as windows_move_resize_window

    return windows_move_resize_window(handle=handle, x=x, y=y, width=width, height=height)


def capture_desktop(path: str | None = None) -> Image:
    from .tools.capture import capture_desktop as capture_capture_desktop

    return capture_capture_desktop(path=path)


def capture_region(left: int, top: int, width: int, height: int, path: str | None = None) -> Image:
    from .tools.capture import capture_region as capture_capture_region

    return capture_capture_region(left=left, top=top, width=width, height=height, path=path)


def capture_window(
    title_regex: str | None = None,
    handle: int | None = None,
    path: str | None = None,
    padding: int = 0,
) -> Image:
    from .tools.capture import capture_window as capture_capture_window

    return capture_capture_window(title_regex=title_regex, handle=handle, path=path, padding=padding)


def capture_focused_window(path: str | None = None, padding: int = 0) -> Image:
    from .tools.capture import capture_focused_window as capture_capture_focused_window

    return capture_capture_focused_window(path=path, padding=padding)


def desktop_snapshot_state(path: str | None = None, include_windows: bool = True, title_filter: str = "", max_windows: int = 50) -> dict[str, Any]:
    from .tools.capture import desktop_snapshot_state as capture_desktop_snapshot_state

    return capture_desktop_snapshot_state(
        path=path,
        include_windows=include_windows,
        title_filter=title_filter,
        max_windows=max_windows,
    )


def focused_window_summary(use_ocr: bool = True, uia_depth: int = 2, max_nodes: int = 120) -> dict[str, Any]:
    from .tools.windows import focused_window_summary as windows_focused_window_summary

    return windows_focused_window_summary(use_ocr=use_ocr, uia_depth=uia_depth, max_nodes=max_nodes)


def _window_summary_from_target(
    title_regex: str | None = None,
    handle: int | None = None,
    use_ocr: bool = True,
    uia_depth: int = 2,
    max_nodes: int = 120,
) -> dict[str, Any]:
    window = find_window(title_regex=title_regex, handle=handle)
    info = window_info(window)
    tree = inspect_ui_tree(handle=info.get("handle"), depth=max(0, int(uia_depth)), max_nodes=max(1, int(max_nodes)))
    titles: list[str] = []
    seen: set[str] = set()
    for node in tree["nodes"]:
        title = (node.get("title") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        titles.append(title)
        if len(titles) >= 40:
            break
    ocr_excerpt = ""
    ocr_word_count = 0
    if use_ocr:
        try:
            ocr = ocr_window(handle=info.get("handle"))
            ocr_excerpt = (ocr.get("text") or "").strip()[:1000]
            ocr_word_count = int(ocr.get("word_count") or 0)
        except Exception as exc:
            ocr_excerpt = f"[ocr unavailable: {exc}]"
    return {
        "window": info,
        "uia_node_count": len(tree["nodes"]),
        "uia_titles": titles,
        "ocr_excerpt": ocr_excerpt,
        "ocr_word_count": ocr_word_count,
    }


def _watch_change_summary(states: list[dict[str, Any]]) -> dict[str, Any]:
    if not states:
        return {"count": 0, "changes": []}
    changes: list[dict[str, Any]] = []
    previous = states[0]
    for current in states[1:]:
        if current.get("hash") == previous.get("hash"):
            previous = current
            continue
        previous_summary = previous.get("summary", {})
        current_summary = current.get("summary", {})
        previous_window = previous_summary.get("window") or previous_summary.get("focused_window") or {}
        current_window = current_summary.get("window") or current_summary.get("focused_window") or {}
        changes.append(
            {
                "from_ts": previous.get("ts"),
                "to_ts": current.get("ts"),
                "from_hash": previous.get("hash"),
                "to_hash": current.get("hash"),
                "from_title": previous_window.get("title"),
                "to_title": current_window.get("title"),
                "from_excerpt": (previous_summary.get("ocr_excerpt") or "")[:160],
                "to_excerpt": (current_summary.get("ocr_excerpt") or "")[:160],
                "from_capture_path": previous.get("capture_path"),
                "to_capture_path": current.get("capture_path"),
            }
        )
        previous = current
    return {"count": len(changes), "changes": changes}


def _compare_image_paths(before_path: str, after_path: str) -> dict[str, Any]:
    before = PILImage.open(before_path).convert("RGB")
    after = PILImage.open(after_path).convert("RGB")
    if before.size != after.size:
        return {
            "same_size": False,
            "before_size": {"width": before.width, "height": before.height},
            "after_size": {"width": after.width, "height": after.height},
            "changed_pixels": None,
            "changed_ratio": None,
            "diff_bbox": None,
        }
    diff = ImageChops.difference(before, after)
    bbox = diff.getbbox()
    if not bbox:
        return {
            "same_size": True,
            "before_size": {"width": before.width, "height": before.height},
            "after_size": {"width": after.width, "height": after.height},
            "changed_pixels": 0,
            "changed_ratio": 0.0,
            "diff_bbox": None,
        }
    gray = diff.convert("L")
    changed_pixels = sum(1 for value in gray.tobytes() if value != 0)
    total_pixels = before.width * before.height
    return {
        "same_size": True,
        "before_size": {"width": before.width, "height": before.height},
        "after_size": {"width": after.width, "height": after.height},
        "changed_pixels": changed_pixels,
        "changed_ratio": round(changed_pixels / max(total_pixels, 1), 6),
        "diff_bbox": {"left": bbox[0], "top": bbox[1], "right": bbox[2], "bottom": bbox[3]},
    }


def _normalize_intent_query(description: str) -> str:
    query = " ".join((description or "").strip().split())
    if not query:
        raise ValueError("Provide non-empty intent text.")
    lowered = query.lower()
    prefixes = [
        "click on ",
        "click ",
        "clique sur ",
        "clique ",
        "press ",
        "appuie sur ",
        "tap ",
        "ouvre ",
    ]
    for prefix in prefixes:
        if lowered.startswith(prefix):
            query = query[len(prefix) :].strip()
            lowered = query.lower()
            break
    for token in ("the ", "le ", "la ", "les ", "l'", "button ", "bouton ", "link ", "lien ", "menu ", "item "):
        if lowered.startswith(token):
            query = query[len(token) :].strip()
            lowered = query.lower()
    return query.strip(" '\"")


def _uia_click_by_text(
    text: str,
    title_regex: str | None = None,
    handle: int | None = None,
    found_index: int = 0,
    click_type: str = "single",
) -> dict[str, Any]:
    window = find_window(title_regex=title_regex, handle=handle)
    matches = find_matching_elements(window, title=text) or find_matching_elements(window, title_contains=text)
    if not matches:
        raise ValueError(f"No UI element matched text={text!r}")
    if found_index < 0 or found_index >= len(matches):
        raise ValueError(f"found_index={found_index} is out of range for {len(matches)} matches.")
    element, info = matches[found_index]
    if click_type == "double":
        element.double_click_input()
    elif click_type == "right":
        element.right_click_input()
    else:
        element.click_input()
    return info


def _window_target_info(title_regex: str | None = None, handle: int | None = None) -> tuple[dict[str, Any], dict[str, int]]:
    window = find_window(title_regex=title_regex, handle=handle)
    info = window_info(window)
    bounds = info.get("bounds")
    if not bounds:
        raise ValueError("Could not determine window bounds.")
    region = window_capture_bounds(bounds, padding=0)
    return info, region


def _uia_annotation_items(title_regex: str | None = None, handle: int | None = None, limit: int = 50) -> tuple[dict[str, Any], dict[str, int], list[dict[str, Any]]]:
    info, region = _window_target_info(title_regex=title_regex, handle=handle)
    tree = inspect_ui_tree(handle=info.get("handle"), depth=3, max_nodes=400)
    items: list[dict[str, Any]] = []
    for node in tree["nodes"]:
        bounds = node.get("bounds")
        title = (node.get("title") or "").strip()
        control_type = (node.get("control_type") or "").lower()
        if not bounds or not title:
            continue
        if control_type not in {"button", "hyperlink", "menuitem", "tabitem", "listitem", "edit", "checkbox", "radio button"}:
            continue
        if bounds["width"] <= 2 or bounds["height"] <= 2:
            continue
        items.append(
            {
                "label": title,
                "control_type": node.get("control_type"),
                "left": int(bounds["left"]) - region["left"],
                "top": int(bounds["top"]) - region["top"],
                "width": int(bounds["width"]),
                "height": int(bounds["height"]),
                "screen_left": int(bounds["left"]),
                "screen_top": int(bounds["top"]),
                "handle": info.get("handle"),
            }
        )
        if len(items) >= max(1, min(int(limit), 200)):
            break
    return info, region, items


def _ocr_annotation_items(
    title_regex: str | None = None,
    handle: int | None = None,
    left: int | None = None,
    top: int | None = None,
    width: int | None = None,
    height: int | None = None,
    query: str | None = None,
    exact: bool = False,
    language: str = "eng",
    limit: int = 50,
) -> tuple[dict[str, Any] | None, dict[str, int], list[dict[str, Any]]]:
    if handle is not None or title_regex is not None:
        result = ocr_window(title_regex=title_regex, handle=handle, language=language)
        info = result.get("window")
    else:
        if None in (left, top, width, height):
            raise ValueError("Provide either a window target or a full region.")
        result = ocr_region(left=int(left), top=int(top), width=int(width), height=int(height), language=language)
        info = None
    region = result["region"]
    if query:
        matches = find_ocr_text_spans(result["words"], text=query, exact=exact)
    else:
        matches = result["words"]
    items: list[dict[str, Any]] = []
    for match in matches[: max(1, min(int(limit), 200))]:
        items.append(
            {
                "label": match.get("text") or query or "",
                "confidence": match.get("confidence"),
                "left": int(match["left"]),
                "top": int(match["top"]),
                "width": int(match["width"]),
                "height": int(match["height"]),
                "screen_left": region["left"] + int(match["left"]),
                "screen_top": region["top"] + int(match["top"]),
                "handle": info.get("handle") if info else None,
            }
        )
    return info, region, items


def _annotate_capture(image_bytes: bytes, items: list[dict[str, Any]], target_path: str | None) -> str:
    image = PILImage.open(io.BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    palette = ["#e11d48", "#2563eb", "#16a34a", "#d97706", "#7c3aed", "#0f766e"]
    for idx, item in enumerate(items, start=1):
        left = int(item["left"])
        top = int(item["top"])
        width = int(item["width"])
        height = int(item["height"])
        color = palette[(idx - 1) % len(palette)]
        draw.rectangle((left, top, left + width, top + height), outline=color, width=3)
        badge_bottom = max(22, top + 22)
        draw.rectangle((left, max(0, top - 22), left + 28, badge_bottom), fill=color)
        draw.text((left + 7, max(1, top - 20)), str(idx), fill="white")
    output = Path(target_path) if target_path else SCREENSHOT_DIR / f"annotated-{now_stamp()}.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG")
    return str(output)


def _desktop_watch_sample(
    mode: str,
    handle: int | None,
    title_regex: str | None,
    region: dict[str, int] | None,
    use_ocr: bool,
    uia_depth: int,
    max_nodes: int,
    capture: bool,
    watch_id: str,
) -> dict[str, Any]:
    capture_path = None
    if mode == "focused":
        summary = focused_window_summary(use_ocr=use_ocr, uia_depth=uia_depth, max_nodes=max_nodes)
        if capture:
            capture_target = DESKTOP_WATCH_CAPTURE_ROOT / watch_id / f"{now_stamp()}.png"
            capture_target.parent.mkdir(parents=True, exist_ok=True)
            capture_focused_window(path=str(capture_target))
            capture_path = str(capture_target)
        payload = {
            "window_title": summary["window"].get("title"),
            "window_handle": summary["window"].get("handle"),
            "uia_titles": summary["uia_titles"],
            "ocr_excerpt": summary["ocr_excerpt"],
        }
    elif mode == "desktop":
        summary = desktop_overview(
            max_windows=8,
            include_window_summaries=False,
            use_ocr=False,
            uia_depth=uia_depth,
            max_nodes=max_nodes,
        )
        if capture:
            capture_target = DESKTOP_WATCH_CAPTURE_ROOT / watch_id / f"{now_stamp()}.png"
            capture_target.parent.mkdir(parents=True, exist_ok=True)
            capture_desktop(path=str(capture_target))
            capture_path = str(capture_target)
        payload = {
            "focused_window_title": summary["focused_window"].get("title"),
            "focused_window_handle": summary["focused_window"].get("handle"),
            "window_count": summary["window_count"],
            "image_hash": summary["image_hash"],
            "window_titles": [item["window"].get("title") for item in summary["windows"]],
        }
    elif mode == "region":
        if not region:
            raise ValueError("Region watch requires a region definition.")
        ocr_result = ocr_region(
            left=region["left"],
            top=region["top"],
            width=region["width"],
            height=region["height"],
            language="eng",
        ) if use_ocr else {"text": "", "word_count": 0, "words": [], "region": region}
        summary = {
            "region": region,
            "ocr_excerpt": (ocr_result.get("text") or "").strip()[:1000],
            "ocr_word_count": int(ocr_result.get("word_count") or 0),
        }
        if capture:
            capture_target = DESKTOP_WATCH_CAPTURE_ROOT / watch_id / f"{now_stamp()}.png"
            capture_target.parent.mkdir(parents=True, exist_ok=True)
            capture_region(
                left=region["left"],
                top=region["top"],
                width=region["width"],
                height=region["height"],
                path=str(capture_target),
            )
            capture_path = str(capture_target)
        png_bytes, _ = grab_png_bytes(region)
        payload = {
            "region": region,
            "image_hash": hashlib.sha256(png_bytes).hexdigest(),
            "ocr_excerpt": summary["ocr_excerpt"],
            "ocr_word_count": summary["ocr_word_count"],
        }
    else:
        summary = _window_summary_from_target(
            handle=handle,
            title_regex=title_regex,
            use_ocr=use_ocr,
            uia_depth=uia_depth,
            max_nodes=max_nodes,
        )
        if capture:
            capture_target = DESKTOP_WATCH_CAPTURE_ROOT / watch_id / f"{now_stamp()}.png"
            capture_target.parent.mkdir(parents=True, exist_ok=True)
            capture_window(handle=summary["window"].get("handle"), path=str(capture_target))
            capture_path = str(capture_target)
        payload = {
            "window_title": summary["window"].get("title"),
            "window_handle": summary["window"].get("handle"),
            "uia_titles": summary["uia_titles"],
            "ocr_excerpt": summary["ocr_excerpt"],
        }
    sample_hash = hashlib.sha256(repr(payload).encode("utf-8", errors="replace")).hexdigest()
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "epoch": round(time.time(), 3),
        "hash": sample_hash,
        "summary": summary,
        "capture_path": capture_path,
    }


def _desktop_watch_loop(watch_id: str) -> None:
    while True:
        with DESKTOP_WATCH_LOCK:
            watch = DESKTOP_WATCH_SESSIONS.get(watch_id)
            if not watch:
                return
            stop_event = watch["stop_event"]
            mode = watch["mode"]
            handle = watch.get("handle")
            title_regex = watch.get("title_regex")
            region = watch.get("region")
            use_ocr = watch["use_ocr"]
            uia_depth = watch["uia_depth"]
            max_nodes = watch["max_nodes"]
            interval_seconds = watch["interval_seconds"]
            capture = watch["capture"]
        if stop_event.is_set():
            return
        try:
            sample = _desktop_watch_sample(mode, handle, title_regex, region, use_ocr, uia_depth, max_nodes, capture, watch_id)
            with DESKTOP_WATCH_LOCK:
                current = DESKTOP_WATCH_SESSIONS.get(watch_id)
                if current:
                    last_hash = current.get("last_hash")
                    current["sample_count"] += 1
                    if sample["hash"] != last_hash:
                        current["change_count"] += 1
                        current["last_hash"] = sample["hash"]
                    current["history"].append(sample)
        except Exception as exc:
            with DESKTOP_WATCH_LOCK:
                current = DESKTOP_WATCH_SESSIONS.get(watch_id)
                if current:
                    current["last_error"] = str(exc)
        if stop_event.wait(max(interval_seconds, 0.1)):
            return


def _stop_desktop_watch_session(watch_id: str) -> dict[str, Any]:
    with DESKTOP_WATCH_LOCK:
        watch = DESKTOP_WATCH_SESSIONS.pop(watch_id, None)
    if not watch:
        return {"watch_id": watch_id, "stopped": False}
    watch["stop_event"].set()
    thread = watch.get("thread")
    if thread and thread.is_alive():
        thread.join(timeout=2.0)
    return {
        "watch_id": watch_id,
        "stopped": True,
        "sample_count": watch.get("sample_count", 0),
        "change_count": watch.get("change_count", 0),
    }


def stop_all_desktop_watch_sessions() -> dict[str, Any]:
    from .tools.capture import stop_all_desktop_watch_sessions as capture_stop_all_desktop_watch_sessions

    return capture_stop_all_desktop_watch_sessions()


def window_summary(
    title_regex: str | None = None,
    handle: int | None = None,
    use_ocr: bool = True,
    uia_depth: int = 2,
    max_nodes: int = 120,
) -> dict[str, Any]:
    from .tools.windows import window_summary as windows_window_summary

    return windows_window_summary(
        title_regex=title_regex,
        handle=handle,
        use_ocr=use_ocr,
        uia_depth=uia_depth,
        max_nodes=max_nodes,
    )


def desktop_overview(
    title_filter: str = "",
    max_windows: int = 10,
    include_window_summaries: bool = False,
    use_ocr: bool = False,
    uia_depth: int = 1,
    max_nodes: int = 60,
) -> dict[str, Any]:
    from .tools.capture import desktop_overview as capture_desktop_overview

    return capture_desktop_overview(
        title_filter=title_filter,
        max_windows=max_windows,
        include_window_summaries=include_window_summaries,
        use_ocr=use_ocr,
        uia_depth=uia_depth,
        max_nodes=max_nodes,
    )


def desktop_perception_snapshot(
    mode: str = "focused",
    title_regex: str | None = None,
    handle: int | None = None,
    left: int | None = None,
    top: int | None = None,
    width: int | None = None,
    height: int | None = None,
    capture: bool = True,
    use_ocr: bool = True,
    uia_depth: int = 2,
    max_nodes: int = 120,
) -> dict[str, Any]:
    from .tools.capture import desktop_perception_snapshot as capture_desktop_perception_snapshot

    return capture_desktop_perception_snapshot(
        mode=mode,
        title_regex=title_regex,
        handle=handle,
        left=left,
        top=top,
        width=width,
        height=height,
        capture=capture,
        use_ocr=use_ocr,
        uia_depth=uia_depth,
        max_nodes=max_nodes,
    )


def compare_capture_images(before_path: str, after_path: str) -> dict[str, Any]:
    from .tools.capture import compare_capture_images as capture_compare_capture_images

    return capture_compare_capture_images(before_path=before_path, after_path=after_path)


def desktop_watch_start(
    mode: str = "focused",
    title_regex: str | None = None,
    handle: int | None = None,
    left: int | None = None,
    top: int | None = None,
    width: int | None = None,
    height: int | None = None,
    interval_seconds: float = 0.5,
    history_limit: int = 40,
    capture: bool = False,
    use_ocr: bool = True,
    uia_depth: int = 2,
    max_nodes: int = 120,
) -> dict[str, Any]:
    from .tools.capture import desktop_watch_start as capture_desktop_watch_start

    return capture_desktop_watch_start(
        mode=mode,
        title_regex=title_regex,
        handle=handle,
        left=left,
        top=top,
        width=width,
        height=height,
        interval_seconds=interval_seconds,
        history_limit=history_limit,
        capture=capture,
        use_ocr=use_ocr,
        uia_depth=uia_depth,
        max_nodes=max_nodes,
    )


def desktop_watch_list() -> dict[str, Any]:
    from .tools.capture import desktop_watch_list as capture_desktop_watch_list

    return capture_desktop_watch_list()


def desktop_watch_get_states(watch_id: str, limit: int = 10) -> dict[str, Any]:
    from .tools.capture import desktop_watch_get_states as capture_desktop_watch_get_states

    return capture_desktop_watch_get_states(watch_id=watch_id, limit=limit)


def desktop_watch_get_change_summary(watch_id: str, limit: int = 20) -> dict[str, Any]:
    from .tools.capture import desktop_watch_get_change_summary as capture_desktop_watch_get_change_summary

    return capture_desktop_watch_get_change_summary(watch_id=watch_id, limit=limit)


def desktop_watch_compare_latest_frames(watch_id: str) -> dict[str, Any]:
    from .tools.capture import desktop_watch_compare_latest_frames as capture_desktop_watch_compare_latest_frames

    return capture_desktop_watch_compare_latest_frames(watch_id=watch_id)


def desktop_watch_get_latest_capture(watch_id: str) -> Image:
    from .tools.capture import desktop_watch_get_latest_capture as capture_desktop_watch_get_latest_capture

    return capture_desktop_watch_get_latest_capture(watch_id=watch_id)


def desktop_watch_wait_change(watch_id: str, baseline_change_count: int | None = None, timeout_seconds: float = 10.0, interval_seconds: float = 0.2) -> dict[str, Any]:
    from .tools.capture import desktop_watch_wait_change as capture_desktop_watch_wait_change

    return capture_desktop_watch_wait_change(
        watch_id=watch_id,
        baseline_change_count=baseline_change_count,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
    )


def desktop_watch_stop(watch_id: str) -> dict[str, Any]:
    from .tools.capture import desktop_watch_stop as capture_desktop_watch_stop

    return capture_desktop_watch_stop(watch_id=watch_id)


def save_desktop_screenshot(prefix: str = "desktop") -> dict[str, Any]:
    from .tools.capture import save_desktop_screenshot as capture_save_desktop_screenshot

    return capture_save_desktop_screenshot(prefix=prefix)


def save_window_screenshot(
    prefix: str = "window",
    title_regex: str | None = None,
    handle: int | None = None,
    padding: int = 0,
) -> dict[str, Any]:
    from .tools.capture import save_window_screenshot as capture_save_window_screenshot

    return capture_save_window_screenshot(prefix=prefix, title_regex=title_regex, handle=handle, padding=padding)


def get_cursor_position() -> dict[str, int]:
    from .tools.input import get_cursor_position as input_get_cursor_position

    return input_get_cursor_position()


def move_mouse(x: int, y: int, duration: float = 0.0) -> dict[str, int]:
    from .tools.input import move_mouse as input_move_mouse

    return input_move_mouse(x=x, y=y, duration=duration)


def click(x: int, y: int, button: str = "left", clicks: int = 1, interval: float = 0.1) -> dict[str, Any]:
    from .tools.input import click as input_click

    return input_click(x=x, y=y, button=button, clicks=clicks, interval=interval)


def double_click(x: int, y: int, button: str = "left") -> dict[str, Any]:
    from .tools.input import double_click as input_double_click

    return input_double_click(x=x, y=y, button=button)


def right_click(x: int, y: int) -> dict[str, Any]:
    from .tools.input import right_click as input_right_click

    return input_right_click(x=x, y=y)


def drag_mouse(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration: float = 0.2,
    button: str = "left",
) -> dict[str, Any]:
    from .tools.input import drag_mouse as input_drag_mouse

    return input_drag_mouse(
        start_x=start_x,
        start_y=start_y,
        end_x=end_x,
        end_y=end_y,
        duration=duration,
        button=button,
    )


@mcp.tool()
def click_relative_to_window(
    handle: int,
    offset_x: int,
    offset_y: int,
    button: str = "left",
    clicks: int = 1,
) -> dict[str, Any]:
    window = find_window(handle=handle)
    info = window_info(window)
    bounds = info["bounds"]
    if not bounds:
        raise ValueError("Could not determine window bounds.")
    x = bounds["left"] + offset_x
    y = bounds["top"] + offset_y
    validate_screen_point(x, y)
    pyautogui.click(x=x, y=y, button=button, clicks=clicks)
    result = {"ok": True, "window": info, "x": x, "y": y, "button": button, "clicks": clicks}
    record_event("click_relative_to_window", handle=handle, x=x, y=y, button=button, clicks=clicks)
    return result


def wait_for_focus_change(
    baseline_handle: int | None = None,
    timeout_seconds: float = 10.0,
    interval_seconds: float = 0.2,
) -> dict[str, Any]:
    from .tools.windows import wait_for_focus_change as windows_wait_for_focus_change

    return windows_wait_for_focus_change(
        baseline_handle=baseline_handle,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
    )


def wait_for_desktop_change(
    baseline_hash: str | None = None,
    timeout_seconds: float = 10.0,
    interval_seconds: float = 0.2,
    left: int | None = None,
    top: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    from .tools.capture import wait_for_desktop_change as capture_wait_for_desktop_change

    return capture_wait_for_desktop_change(
        baseline_hash=baseline_hash,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        left=left,
        top=top,
        width=width,
        height=height,
    )


@mcp.tool()
def wait_for_focused_window_content_change(
    baseline_hash: str | None = None,
    timeout_seconds: float = 10.0,
    interval_seconds: float = 0.3,
    use_ocr: bool = True,
    uia_depth: int = 2,
    max_nodes: int = 120,
) -> dict[str, Any]:
    baseline_summary = focused_window_summary(use_ocr=use_ocr, uia_depth=uia_depth, max_nodes=max_nodes)
    baseline_payload = {
        "uia_titles": baseline_summary["uia_titles"],
        "ocr_excerpt": baseline_summary["ocr_excerpt"],
        "window_title": baseline_summary["window"].get("title"),
    }
    before_hash = baseline_hash or hashlib.sha256(repr(baseline_payload).encode("utf-8", errors="replace")).hexdigest()
    deadline = time.time() + max(timeout_seconds, 0.1)

    def predicate():
        current = focused_window_summary(use_ocr=use_ocr, uia_depth=uia_depth, max_nodes=max_nodes)
        payload = {
            "uia_titles": current["uia_titles"],
            "ocr_excerpt": current["ocr_excerpt"],
            "window_title": current["window"].get("title"),
        }
        current_hash = hashlib.sha256(repr(payload).encode("utf-8", errors="replace")).hexdigest()
        if current_hash != before_hash:
            return {
                "changed": True,
                "before_hash": before_hash,
                "after_hash": current_hash,
                "summary": current,
            }
        return None

    result = wait_until(deadline, max(interval_seconds, 0.05), predicate)
    if not result:
        return {"changed": False, "before_hash": before_hash, "after_hash": before_hash, "summary": baseline_summary}
    return result


def wait_for_window_content_change(
    title_regex: str | None = None,
    handle: int | None = None,
    baseline_hash: str | None = None,
    timeout_seconds: float = 10.0,
    interval_seconds: float = 0.3,
    use_ocr: bool = True,
    uia_depth: int = 2,
    max_nodes: int = 120,
) -> dict[str, Any]:
    from .tools.windows import wait_for_window_content_change as windows_wait_for_window_content_change

    return windows_wait_for_window_content_change(
        title_regex=title_regex,
        handle=handle,
        baseline_hash=baseline_hash,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        use_ocr=use_ocr,
        uia_depth=uia_depth,
        max_nodes=max_nodes,
    )


def scroll(clicks: int) -> dict[str, int]:
    from .tools.input import scroll as input_scroll

    return input_scroll(clicks=clicks)


def type_text(text: str, interval: float = 0.01, require_handle: int | None = None) -> dict[str, Any]:
    from .tools.input import type_text as input_type_text

    return input_type_text(text=text, interval=interval, require_handle=require_handle)


def type_text_unicode(text: str, require_handle: int | None = None) -> dict[str, Any]:
    from .tools.input import type_text_unicode as input_type_text_unicode

    return input_type_text_unicode(text=text, require_handle=require_handle)


def press_key(key: str, presses: int = 1, interval: float = 0.05) -> dict[str, Any]:
    from .tools.input import press_key as input_press_key

    return input_press_key(key=key, presses=presses, interval=interval)


def hotkey(keys: list[str], interval: float = 0.05) -> dict[str, Any]:
    from .tools.input import hotkey as input_hotkey

    return input_hotkey(keys=keys, interval=interval)


def clipboard_get() -> dict[str, Any]:
    from .tools.runtime import clipboard_get as runtime_clipboard_get

    return runtime_clipboard_get()


def clipboard_set(text: str) -> dict[str, Any]:
    from .tools.runtime import clipboard_set as runtime_clipboard_set

    return runtime_clipboard_set(text=text)


def macro_record_action(macro_id: str, action: dict[str, Any]) -> dict[str, Any]:
    from .tools.runtime import macro_record_action as runtime_macro_record_action

    return runtime_macro_record_action(macro_id=macro_id, action=action)


def macro_replay(macro_id: str, speed: float = 1.0) -> dict[str, Any]:
    from .tools.runtime import macro_replay as runtime_macro_replay

    return runtime_macro_replay(macro_id=macro_id, speed=speed)


def macro_list() -> dict[str, Any]:
    from .tools.runtime import macro_list as runtime_macro_list

    return runtime_macro_list()


def macro_clear(macro_id: str | None = None) -> dict[str, Any]:
    from .tools.runtime import macro_clear as runtime_macro_clear

    return runtime_macro_clear(macro_id=macro_id)


def run_command(
    command: list[str],
    cwd: str | None = None,
    wait: bool = False,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    from .tools.runtime import run_command as runtime_run_command

    return runtime_run_command(command=command, cwd=cwd, wait=wait, timeout_seconds=timeout_seconds)


def screen_watch(
    left: int | None = None,
    top: int | None = None,
    width: int | None = None,
    height: int | None = None,
    change_threshold: float = 0.02,
    max_seconds: float = 30.0,
    interval_seconds: float = 0.5,
) -> dict[str, Any]:
    from .tools.capture import screen_watch as capture_screen_watch

    region = None
    if None not in (left, top, width, height):
        region = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
    return capture_screen_watch(
        region=region,
        change_threshold=change_threshold,
        max_seconds=max_seconds,
        interval_seconds=interval_seconds,
    )


def diff_screenshots(path_a: str, path_b: str, threshold: float = 0.01) -> dict[str, Any]:
    from .tools.capture import diff_screenshots as capture_diff_screenshots

    return capture_diff_screenshots(path_a=path_a, path_b=path_b, threshold=threshold)


def find_image_on_screen(template_path: str, confidence: float = 0.8, region: dict[str, int] | None = None) -> dict[str, Any]:
    from .tools.capture import find_image_on_screen as capture_find_image_on_screen

    return capture_find_image_on_screen(template_path=template_path, confidence=confidence, region=region)


def describe_screen(
    region: dict[str, int] | None = None,
    prompt: str = "Describe what is visible on this screen. Identify the main interactive elements and current state.",
    model: str | None = None,
    max_tokens: int = 700,
    capture_path: str | None = None,
) -> dict[str, Any]:
    from .tools.capture import describe_screen as capture_describe_screen

    return capture_describe_screen(
        region=region,
        prompt=prompt,
        model=model,
        max_tokens=max_tokens,
        capture_path=capture_path,
    )


def inspect_ui_tree(title_regex: str | None = None, handle: int | None = None, depth: int = 2, max_nodes: int = 200) -> dict[str, Any]:
    from .tools.windows import inspect_ui_tree as windows_inspect_ui_tree

    return windows_inspect_ui_tree(title_regex=title_regex, handle=handle, depth=depth, max_nodes=max_nodes)


def find_ui_elements(
    title_regex: str | None = None,
    handle: int | None = None,
    automation_id: str | None = None,
    title: str | None = None,
    title_contains: str | None = None,
    control_type: str | None = None,
    class_name: str | None = None,
    max_results: int = 50,
) -> dict[str, Any]:
    from .tools.windows import find_ui_elements as windows_find_ui_elements

    return windows_find_ui_elements(
        title_regex=title_regex,
        handle=handle,
        automation_id=automation_id,
        title=title,
        title_contains=title_contains,
        control_type=control_type,
        class_name=class_name,
        max_results=max_results,
    )


def wait_for_ui_element(
    title_regex: str | None = None,
    handle: int | None = None,
    automation_id: str | None = None,
    title: str | None = None,
    title_contains: str | None = None,
    control_type: str | None = None,
    class_name: str | None = None,
    timeout_seconds: float = 10.0,
    interval_seconds: float = 0.2,
) -> dict[str, Any]:
    from .tools.windows import wait_for_ui_element as windows_wait_for_ui_element

    return windows_wait_for_ui_element(
        title_regex=title_regex,
        handle=handle,
        automation_id=automation_id,
        title=title,
        title_contains=title_contains,
        control_type=control_type,
        class_name=class_name,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
    )


def click_ui_element(
    title_regex: str | None = None,
    handle: int | None = None,
    automation_id: str | None = None,
    title: str | None = None,
    title_contains: str | None = None,
    control_type: str | None = None,
    class_name: str | None = None,
    found_index: int = 0,
    click_type: str = "single",
) -> dict[str, Any]:
    from .tools.windows import click_ui_element as windows_click_ui_element

    return windows_click_ui_element(
        title_regex=title_regex,
        handle=handle,
        automation_id=automation_id,
        title=title,
        title_contains=title_contains,
        control_type=control_type,
        class_name=class_name,
        found_index=found_index,
        click_type=click_type,
    )


def ocr_region(left: int, top: int, width: int, height: int, language: str = "eng") -> dict[str, Any]:
    from .tools.ocr import ocr_region as ocr_ocr_region

    return ocr_ocr_region(left=left, top=top, width=width, height=height, language=language)


def ocr_window(
    title_regex: str | None = None,
    handle: int | None = None,
    language: str = "eng",
    padding: int = 0,
) -> dict[str, Any]:
    from .tools.ocr import ocr_window as ocr_ocr_window

    return ocr_ocr_window(title_regex=title_regex, handle=handle, language=language, padding=padding)


def ocr_image_file(path: str, language: str = "eng") -> dict[str, Any]:
    from .tools.ocr import ocr_image_file as ocr_ocr_image_file

    return ocr_ocr_image_file(path=path, language=language)


def find_ocr_text(
    text: str,
    title_regex: str | None = None,
    handle: int | None = None,
    left: int | None = None,
    top: int | None = None,
    width: int | None = None,
    height: int | None = None,
    language: str = "eng",
    exact: bool = False,
) -> dict[str, Any]:
    from .tools.ocr import find_ocr_text as ocr_find_ocr_text

    return ocr_find_ocr_text(
        text=text,
        title_regex=title_regex,
        handle=handle,
        left=left,
        top=top,
        width=width,
        height=height,
        language=language,
        exact=exact,
    )


def focused_window_text_map(
    language: str = "eng",
    padding: int = 0,
    min_confidence: float = -1.0,
) -> dict[str, Any]:
    from .tools.ocr import focused_window_text_map as ocr_focused_window_text_map

    return ocr_focused_window_text_map(language=language, padding=padding, min_confidence=min_confidence)


def click_ocr_text(
    text: str,
    title_regex: str | None = None,
    handle: int | None = None,
    left: int | None = None,
    top: int | None = None,
    width: int | None = None,
    height: int | None = None,
    language: str = "eng",
    exact: bool = False,
    found_index: int = 0,
    button: str = "left",
    clicks: int = 1,
) -> dict[str, Any]:
    from .tools.ocr import click_ocr_text as ocr_click_ocr_text

    return ocr_click_ocr_text(
        text=text,
        title_regex=title_regex,
        handle=handle,
        left=left,
        top=top,
        width=width,
        height=height,
        language=language,
        exact=exact,
        found_index=found_index,
        button=button,
        clicks=clicks,
    )


def click_visible_text(
    text: str,
    title_regex: str | None = None,
    handle: int | None = None,
    language: str = "eng",
    exact: bool = False,
    found_index: int = 0,
    button: str = "left",
    clicks: int = 1,
) -> dict[str, Any]:
    from .tools.ocr import click_visible_text as ocr_click_visible_text

    return ocr_click_visible_text(
        text=text,
        title_regex=title_regex,
        handle=handle,
        language=language,
        exact=exact,
        found_index=found_index,
        button=button,
        clicks=clicks,
    )


def intent_click(
    description: str,
    title_regex: str | None = None,
    handle: int | None = None,
    language: str = "eng",
    exact: bool = False,
    found_index: int = 0,
    click_type: str = "single",
) -> dict[str, Any]:
    query = _normalize_intent_query(description)
    attempts: list[str] = []
    try:
        info = _uia_click_by_text(
            text=query,
            title_regex=title_regex,
            handle=handle,
            found_index=found_index,
            click_type=click_type,
        )
        result = {
            "ok": True,
            "source": "uia",
            "description": description,
            "query": query,
            "click_type": click_type,
            "match": info,
            "attempts": attempts + ["uia"],
        }
        record_event("intent_click", source="uia", query=query, click_type=click_type)
        return result
    except Exception as exc:
        attempts.append(f"uia:{exc}")
    try:
        result = click_ocr_text(
            text=query,
            title_regex=title_regex,
            handle=handle,
            language=language,
            exact=exact,
            found_index=found_index,
            button="right" if click_type == "right" else "left",
            clicks=2 if click_type == "double" else 1,
        )
        result.update({"source": "ocr", "description": description, "query": query, "attempts": attempts + ["ocr"]})
        record_event("intent_click", source="ocr", query=query, click_type=click_type)
        return result
    except Exception as exc:
        attempts.append(f"ocr:{exc}")
    raise ValueError(f"Could not resolve click intent for {description!r}. Attempts: {' | '.join(attempts)}")


@mcp.tool()
def smart_action(
    action: str,
    target: str,
    title_regex: str | None = None,
    handle: int | None = None,
    language: str = "eng",
    exact: bool = False,
    found_index: int = 0,
) -> dict[str, Any]:
    from .tools_ai import intent_click as ai_intent_click

    resolved = (action or "click").lower()
    if resolved not in {"click", "double_click", "right_click"}:
        raise ValueError("Use action='click', 'double_click', or 'right_click'.")
    click_type = {"click": "single", "double_click": "double", "right_click": "right"}[resolved]
    result = ai_intent_click(
        intent=target,
        title_regex=title_regex,
        handle=handle,
        button="right" if click_type == "right" else "left",
        clicks=2 if click_type == "double" else 1,
        use_ocr=True,
    )
    result["action"] = resolved
    return result


def screen_annotate(
    mode: str = "interactive",
    title_regex: str | None = None,
    handle: int | None = None,
    left: int | None = None,
    top: int | None = None,
    width: int | None = None,
    height: int | None = None,
    query: str | None = None,
    exact: bool = False,
    language: str = "eng",
    limit: int = 25,
    path: str | None = None,
) -> dict[str, Any]:
    resolved_mode = (mode or "interactive").lower()
    if resolved_mode not in {"interactive", "uia", "ocr"}:
        raise ValueError("Use mode='interactive', 'uia', or 'ocr'.")
    if resolved_mode in {"interactive", "uia"}:
        info, region, items = _uia_annotation_items(title_regex=title_regex, handle=handle, limit=limit)
        if resolved_mode == "interactive" and not items:
            info, region, items = _ocr_annotation_items(
                title_regex=title_regex,
                handle=handle,
                left=left,
                top=top,
                width=width,
                height=height,
                query=query,
                exact=exact,
                language=language,
                limit=limit,
            )
        png_bytes, _ = grab_png_bytes(region)
    else:
        info, region, items = _ocr_annotation_items(
            title_regex=title_regex,
            handle=handle,
            left=left,
            top=top,
            width=width,
            height=height,
            query=query,
            exact=exact,
            language=language,
            limit=limit,
        )
        png_bytes, _ = grab_png_bytes(region)
    annotated_path = _annotate_capture(png_bytes, items, path)
    numbered = [{"index": idx, **item} for idx, item in enumerate(items, start=1)]
    result = {
        "mode": resolved_mode,
        "path": annotated_path,
        "count": len(numbered),
        "region": region,
        "window": info,
        "items": numbered,
    }
    record_event("screen_annotate", mode=resolved_mode, count=len(numbered), path=annotated_path)
    return result


def watch_until_goal(
    goal_description: str,
    max_seconds: float = 10.0,
    interval_seconds: float = 0.4,
    title_regex: str | None = None,
    handle: int | None = None,
    use_ocr: bool = True,
    uia_depth: int = 2,
    max_nodes: int = 120,
) -> dict[str, Any]:
    goal = " ".join((goal_description or "").strip().split())
    if not goal:
        raise ValueError("Provide non-empty goal_description.")
    deadline = time.time() + max(max_seconds, 0.1)
    lowered = goal.lower()

    def predicate():
        if lowered.startswith("window:"):
            target = lowered.split(":", 1)[1].strip()
            summary = desktop_overview(max_windows=10, include_window_summaries=False)
            focused = summary["focused_window"]
            if target in (focused.get("title") or "").lower():
                return {"matched": True, "source": "focused_window", "summary": summary, "goal": goal}
            for item in summary["windows"]:
                if target in ((item.get("window") or {}).get("title") or "").lower():
                    return {"matched": True, "source": "desktop_overview", "summary": summary, "goal": goal}
            return None
        if lowered.startswith("title_regex:"):
            pattern = goal.split(":", 1)[1].strip()
            import re

            summary = desktop_overview(max_windows=10, include_window_summaries=False)
            if re.search(pattern, (summary["focused_window"].get("title") or ""), re.I):
                return {"matched": True, "source": "focused_window_regex", "summary": summary, "goal": goal}
            for item in summary["windows"]:
                if re.search(pattern, ((item.get("window") or {}).get("title") or ""), re.I):
                    return {"matched": True, "source": "desktop_overview_regex", "summary": summary, "goal": goal}
            return None
        target_text = goal.split(":", 1)[1].strip() if ":" in goal and lowered.split(":", 1)[0] in {"text", "ocr", "uia"} else goal
        summary = (
            _window_summary_from_target(title_regex=title_regex, handle=handle, use_ocr=use_ocr, uia_depth=uia_depth, max_nodes=max_nodes)
            if title_regex or handle
            else focused_window_summary(use_ocr=use_ocr, uia_depth=uia_depth, max_nodes=max_nodes)
        )
        target_lower = target_text.lower()
        if any(target_lower in (title or "").lower() for title in summary["uia_titles"]):
            return {"matched": True, "source": "uia", "summary": summary, "goal": goal}
        if use_ocr:
            if target_lower in (summary.get("ocr_excerpt") or "").lower():
                return {"matched": True, "source": "ocr_excerpt", "summary": summary, "goal": goal}
            try:
                if title_regex or handle:
                    ocr_result = ocr_window(title_regex=title_regex, handle=handle)
                else:
                    focused = summary.get("window") or focused_window_data()
                    ocr_result = ocr_window(handle=focused.get("handle"))
                if target_lower in (ocr_result.get("text") or "").lower():
                    enriched_summary = dict(summary)
                    enriched_summary["ocr_excerpt"] = (ocr_result.get("text") or "").strip()[:1000]
                    enriched_summary["ocr_word_count"] = int(ocr_result.get("word_count") or 0)
                    return {"matched": True, "source": "ocr_full", "summary": enriched_summary, "goal": goal}
            except Exception:
                pass
        return None

    result = wait_until(deadline, max(interval_seconds, 0.05), predicate, description=f"goal {goal!r}")
    if not result:
        raise ValueError(f"Timed out waiting for goal: {goal!r}")
    record_event("watch_until_goal", goal=goal, source=result.get("source"))
    return result


def wait_for_text(
    title_regex: str | None = None,
    handle: int | None = None,
    text: str = "",
    timeout_seconds: float = 10.0,
    interval_seconds: float = 0.4,
    use_ocr: bool = True,
 ) -> dict[str, Any]:
    from .tools.windows import wait_for_text as windows_wait_for_text

    return windows_wait_for_text(
        title_regex=title_regex,
        handle=handle,
        text=text,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        use_ocr=use_ocr,
    )


def click_text(title_regex: str | None = None, handle: int | None = None, text: str = "", found_index: int = 0) -> dict[str, Any]:
    from .tools.windows import click_text as windows_click_text

    return windows_click_text(title_regex=title_regex, handle=handle, text=text, found_index=found_index)
