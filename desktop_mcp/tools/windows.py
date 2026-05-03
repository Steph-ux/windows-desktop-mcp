"""Window and UI Automation MCP tools."""

from __future__ import annotations

import time
from typing import Any

from ..app import mcp
from ..desktop_core import (
    find_matching_elements,
    find_window,
    focused_window_data,
    focus_window_data,
    list_windows_data,
    move_resize_window_data,
    wait_for_window_data,
    window_info,
)
from ..helpers import wait_until
from ..runtime import record_event, tool_log


def _window_summary_from_target(
    title_regex: str | None = None,
    handle: int | None = None,
    use_ocr: bool = True,
    uia_depth: int = 2,
    max_nodes: int = 120,
) -> dict[str, Any]:
    from .ocr import ocr_window

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
def list_windows(title_filter: str = "", visible_only: bool = True) -> list[dict[str, Any]]:
    return list_windows_data(title_filter=title_filter, visible_only=visible_only)
def wait_for_window(
    title_regex: str | None = None,
    title_filter: str = "",
    timeout_seconds: float = 10.0,
    interval_seconds: float = 0.2,
    visible_only: bool = True,
) -> dict[str, Any]:
    return wait_for_window_data(
        title_regex=title_regex,
        title_filter=title_filter,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        visible_only=visible_only,
    )
def focus_window(title_regex: str | None = None, handle: int | None = None, wait_seconds: float = 0.3) -> dict[str, Any]:
    result = focus_window_data(title_regex=title_regex, handle=handle, wait_seconds=wait_seconds)
    record_event("focus_window", handle=result.get("handle"), title=result.get("title"))
    return result
def get_focused_window() -> dict[str, Any]:
    return focused_window_data()
@tool_log
def get_active_window() -> dict[str, Any]:
    """Retourne la fenetre actuellement au premier plan."""
    return focused_window_data()
def move_resize_window(handle: int, x: int, y: int, width: int, height: int) -> dict[str, Any]:
    return move_resize_window_data(handle=handle, x=x, y=y, width=width, height=height)
def wait_for_focus_change(
    baseline_handle: int | None = None,
    timeout_seconds: float = 10.0,
    interval_seconds: float = 0.2,
) -> dict[str, Any]:
    deadline = time.time() + max(timeout_seconds, 0.1)
    initial = baseline_handle if baseline_handle is not None else focused_window_data().get("handle")

    def predicate():
        info = focused_window_data()
        return info if info.get("handle") != initial else None

    result = wait_until(deadline, max(interval_seconds, 0.05), predicate)
    if not result:
        raise ValueError("Timed out waiting for focused window to change.")
    return result
def inspect_ui_tree(title_regex: str | None = None, handle: int | None = None, depth: int = 2, max_nodes: int = 200) -> dict[str, Any]:
    root = find_window(title_regex=title_regex, handle=handle)
    results: list[dict[str, Any]] = []

    def walk(node: Any, current_depth: int) -> None:
        if len(results) >= max_nodes or current_depth > depth:
            return
        info = window_info(node)
        info["depth"] = current_depth
        results.append(info)
        if current_depth == depth:
            return
        for attempt in range(2):
            try:
                children = node.children()
                break
            except Exception:
                children = []
                if attempt == 1:
                    break
                time.sleep(0.1)
        for child in children:
            if len(results) >= max_nodes:
                break
            walk(child, current_depth + 1)

    walk(root, 0)
    return {"root": window_info(root), "nodes": results}
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
    window = find_window(title_regex=title_regex, handle=handle)
    matches = find_matching_elements(window, automation_id, title, title_contains, control_type, class_name)
    items = []
    for idx, (_, info) in enumerate(matches[: max(max_results, 1)]):
        info["found_index"] = idx
        items.append(info)
    return {"window": window_info(window), "count": len(matches), "matches": items}
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
    window = find_window(title_regex=title_regex, handle=handle)
    deadline = time.time() + max(timeout_seconds, 0.1)

    def predicate():
        matches = find_matching_elements(window, automation_id, title, title_contains, control_type, class_name)
        if not matches:
            return None
        _, info = matches[0]
        return info

    result = wait_until(deadline, max(interval_seconds, 0.05), predicate)
    if not result:
        raise ValueError("Timed out waiting for a matching UI element.")
    return result
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
    window = find_window(title_regex=title_regex, handle=handle)
    matches = find_matching_elements(window, automation_id, title, title_contains, control_type, class_name)
    if not matches:
        raise ValueError("No UI element matched the requested filters.")
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
def focused_window_summary(use_ocr: bool = True, uia_depth: int = 2, max_nodes: int = 120) -> dict[str, Any]:
    focused = focused_window_data()
    handle = focused.get("handle")
    if not handle:
        raise ValueError("Could not determine the focused window handle.")
    return _window_summary_from_target(handle=int(handle), use_ocr=use_ocr, uia_depth=uia_depth, max_nodes=max_nodes)
def window_summary(
    title_regex: str | None = None,
    handle: int | None = None,
    use_ocr: bool = True,
    uia_depth: int = 2,
    max_nodes: int = 120,
) -> dict[str, Any]:
    return _window_summary_from_target(
        title_regex=title_regex,
        handle=handle,
        use_ocr=use_ocr,
        uia_depth=uia_depth,
        max_nodes=max_nodes,
    )
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
    baseline_summary = _window_summary_from_target(
        title_regex=title_regex,
        handle=handle,
        use_ocr=use_ocr,
        uia_depth=uia_depth,
        max_nodes=max_nodes,
    )
    import hashlib

    baseline_payload = {
        "uia_titles": baseline_summary["uia_titles"],
        "ocr_excerpt": baseline_summary["ocr_excerpt"],
        "window_title": baseline_summary["window"].get("title"),
    }
    before_hash = baseline_hash or hashlib.sha256(repr(baseline_payload).encode("utf-8", errors="replace")).hexdigest()
    deadline = time.time() + max(timeout_seconds, 0.1)

    def predicate():
        current = _window_summary_from_target(
            title_regex=title_regex,
            handle=handle,
            use_ocr=use_ocr,
            uia_depth=uia_depth,
            max_nodes=max_nodes,
        )
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
def wait_for_text(
    title_regex: str | None = None,
    handle: int | None = None,
    text: str = "",
    timeout_seconds: float = 10.0,
    interval_seconds: float = 0.4,
    use_ocr: bool = True,
) -> dict[str, Any]:
    from .ocr import ocr_window

    if not text:
        raise ValueError("Provide non-empty text.")
    window = find_window(title_regex=title_regex, handle=handle)
    target = text.lower()
    deadline = time.time() + max(timeout_seconds, 0.1)

    def predicate():
        try:
            tree = inspect_ui_tree(handle=int(window.handle), depth=3, max_nodes=300)
            for node in tree["nodes"]:
                if target in (node.get("title") or "").lower():
                    return {"source": "uia", "match": node, "window": tree["root"]}
        except Exception:
            pass
        if use_ocr:
            try:
                result = ocr_window(handle=int(window.handle))
                if target in result["text"].lower():
                    return {"source": "ocr", "window": result["window"], "text": result["text"]}
            except Exception:
                pass
        return None

    result = wait_until(deadline, max(interval_seconds, 0.05), predicate)
    if not result:
        raise ValueError(f"Timed out waiting for text: {text!r}")
    return result
def click_text(title_regex: str | None = None, handle: int | None = None, text: str = "", found_index: int = 0) -> dict[str, Any]:
    if not text:
        raise ValueError("Provide non-empty text.")
    window = find_window(title_regex=title_regex, handle=handle)
    matches = find_matching_elements(window, title=text)
    if not matches:
        matches = find_matching_elements(window, title_contains=text)
    if not matches:
        raise ValueError(f"No UI element matched text={text!r}")
    if found_index < 0 or found_index >= len(matches):
        raise ValueError(f"found_index={found_index} is out of range for {len(matches)} matches.")
    element, info = matches[found_index]
    element.click_input()
    return info
def minimize_window(title_regex: str | None = None, handle: int | None = None) -> dict[str, Any]:
    """Minimize a window by title or handle."""
    window = find_window(title_regex=title_regex, handle=handle)
    info = window_info(window)
    window.minimize()
    record_event("minimize_window", handle=info.get("handle"), title=info.get("title"))
    return {**info, "minimized": True}
def maximize_window(title_regex: str | None = None, handle: int | None = None) -> dict[str, Any]:
    """Maximize a window by title or handle."""
    window = find_window(title_regex=title_regex, handle=handle)
    info = window_info(window)
    window.maximize()
    record_event("maximize_window", handle=info.get("handle"), title=info.get("title"))
    return {**info, "maximized": True}
def close_window(title_regex: str | None = None, handle: int | None = None) -> dict[str, Any]:
    """Close a window by title or handle."""
    window = find_window(title_regex=title_regex, handle=handle)
    info = window_info(window)
    window.close()
    record_event("close_window", handle=info.get("handle"), title=info.get("title"))
    return {**info, "closed": True}


__all__ = [
    "click_text",
    "click_ui_element",
    "close_window",
    "find_ui_elements",
    "focus_window",
    "focused_window_summary",
    "get_active_window",
    "get_focused_window",
    "inspect_ui_tree",
    "list_windows",
    "maximize_window",
    "minimize_window",
    "move_resize_window",
    "wait_for_focus_change",
    "wait_for_text",
    "wait_for_ui_element",
    "wait_for_window",
    "wait_for_window_content_change",
    "window_summary",
]
