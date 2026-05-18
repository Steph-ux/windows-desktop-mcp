from __future__ import annotations

import hashlib
import io
import re
import time
from pathlib import Path
from typing import Any

import pyautogui
from mcp.server.fastmcp import Image
from PIL import Image as PILImage, ImageDraw, ImageFont

from .app import mcp
from .desktop_core import (
    find_matching_elements,
    find_window,
    focused_window_data,
    grab_png_bytes,
    list_windows_data,
    validate_screen_point,
    window_capture_bounds,
    window_info,
)
from .helpers import now_stamp, wait_until
from .ocr_core import find_ocr_text_spans, ocr_image_object
from .paths import BROWSER_CAPTURE_ROOT, DESKTOP_WATCH_CAPTURE_ROOT
from .runtime import record_event

_PALETTE = [
    (225, 29, 72),
    (37, 99, 235),
    (16, 185, 129),
    (245, 158, 11),
    (124, 58, 237),
    (236, 72, 153),
    (13, 148, 136),
    (249, 115, 22),
    (79, 70, 229),
    (132, 204, 22),
]


def _try_load_font(size: int):
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _bounds_to_image_rect(bounds, origin_x, origin_y, img_w, img_h):
    if not bounds:
        return None
    left = int(bounds.get("left", 0)) - int(origin_x)
    top = int(bounds.get("top", 0)) - int(origin_y)
    right = int(bounds.get("right", left + int(bounds.get("width", 0)))) - int(origin_x)
    bottom = int(bounds.get("bottom", top + int(bounds.get("height", 0)))) - int(origin_y)
    left = max(0, min(left, img_w))
    top = max(0, min(top, img_h))
    right = max(0, min(right, img_w))
    bottom = max(0, min(bottom, img_h))
    if right - left < 4 or bottom - top < 4:
        return None
    return (left, top, right, bottom)


def _build_legend_strip(manifest, img_width):
    header_font = _try_load_font(16)
    body_font = _try_load_font(14)
    row_h = 28
    columns = [50, 110, max(260, img_width - 410), 190]
    rows = min(len(manifest), 40)
    strip_h = 42 + max(rows, 1) * row_h + 8
    strip = PILImage.new("RGBA", (img_width, strip_h), (15, 15, 15, 255))
    draw = ImageDraw.Draw(strip)
    labels = ["#", "Type", "Title", "Center (x,y)"]
    x = 12
    for label, width in zip(labels, columns):
        draw.text((x, 10), label, font=header_font, fill=(230, 230, 230, 255))
        x += width
    draw.line((10, 34, img_width - 10, 34), fill=(70, 70, 70, 255), width=1)
    for idx, item in enumerate(manifest[:40], start=1):
        x = 12
        y = 40 + (idx - 1) * row_h
        draw.text((x, y), str(item["index"]), font=body_font, fill=item["color"] + (255,))
        x += columns[0]
        draw.text((x, y), str(item["control_type"])[:18], font=body_font, fill=(220, 220, 220, 255))
        x += columns[1]
        draw.text((x, y), str(item["title"])[:80], font=body_font, fill=(220, 220, 220, 255))
        x += columns[2]
        draw.text((x, y), f"({item['center_x']},{item['center_y']})", font=body_font, fill=(220, 220, 220, 255))
    return strip


def _annotate_image(png_bytes, elements, origin_x, origin_y):
    image = PILImage.open(io.BytesIO(png_bytes)).convert("RGBA")
    overlay = PILImage.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    badge_font = _try_load_font(16)
    manifest = []
    for idx, element in enumerate(elements, start=1):
        rect = _bounds_to_image_rect(element.get("bounds"), origin_x, origin_y, image.width, image.height)
        if not rect:
            continue
        left, top, right, bottom = rect
        color = _PALETTE[(idx - 1) % len(_PALETTE)]
        draw.rectangle((left, top, right, bottom), fill=color + (45,), outline=color + (220,), width=2)
        badge_top = max(0, top - 24)
        draw.rounded_rectangle((left, badge_top, left + 28, badge_top + 22), radius=5, fill=color + (235,))
        draw.text((left + 8, badge_top + 2), str(idx), font=badge_font, fill=(255, 255, 255, 255))
        center_x = int(element.get("center_x", origin_x + ((left + right) // 2)))
        center_y = int(element.get("center_y", origin_y + ((top + bottom) // 2)))
        manifest.append(
            {
                "index": idx,
                "title": str(element.get("title") or "").strip() or "(untitled)",
                "control_type": str(element.get("control_type") or "unknown"),
                "center_x": center_x,
                "center_y": center_y,
                "color": color,
            }
        )
    combined = PILImage.alpha_composite(image, overlay)
    legend = _build_legend_strip(manifest, combined.width)
    final = PILImage.new("RGBA", (combined.width, combined.height + legend.height), (255, 255, 255, 255))
    final.paste(combined, (0, 0))
    final.paste(legend, (0, combined.height))
    out = io.BytesIO()
    final.save(out, format="PNG")
    return out.getvalue(), manifest


def _save_if_needed(png_bytes: bytes, path: str | None) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(png_bytes)
def screen_annotate(
    title_regex: str | None = None,
    handle: int | None = None,
    mode: str = "window",
    left: int | None = None,
    top: int | None = None,
    width: int | None = None,
    height: int | None = None,
    uia_depth: int = 3,
    max_elements: int = 50,
    path: str | None = None,
) -> Image:
    resolved_mode = (mode or "window").lower()
    elements: list[dict[str, Any]] = []
    origin_x = 0
    origin_y = 0
    if resolved_mode == "window":
        root = find_window(title_regex=title_regex, handle=handle)
        info = window_info(root)
        bounds = info.get("bounds")
        if not bounds:
            raise ValueError("Could not determine window bounds.")
        region = window_capture_bounds(bounds, padding=0)
        png_bytes, _ = grab_png_bytes(region)
        origin_x = int(region["left"])
        origin_y = int(region["top"])
        nodes: list[dict[str, Any]] = []

        def walk(node: Any, depth: int) -> None:
            if len(nodes) >= max_elements or depth > int(uia_depth):
                return
            info = window_info(node)
            nodes.append(info)
            if depth == int(uia_depth):
                return
            try:
                children = node.children()
            except Exception:
                children = []
            for child in children:
                if len(nodes) >= max_elements:
                    break
                walk(child, depth + 1)

        walk(root, 0)
        for item in nodes:
            bounds = item.get("bounds")
            if not bounds or int(bounds.get("width", 0)) <= 4 or int(bounds.get("height", 0)) <= 4:
                continue
            elements.append(
                {
                    "title": item.get("title") or item.get("automation_id") or item.get("class_name") or "(node)",
                    "control_type": item.get("control_type") or "uia",
                    "bounds": bounds,
                    "center_x": int(bounds["left"]) + int(bounds["width"]) // 2,
                    "center_y": int(bounds["top"]) + int(bounds["height"]) // 2,
                }
            )
    elif resolved_mode == "desktop":
        png_bytes, region = grab_png_bytes()
        origin_x = int(region["left"])
        origin_y = int(region["top"])
        for item in list_windows_data(visible_only=True)[: max(1, min(int(max_elements), 200))]:
            bounds = item.get("bounds")
            if not bounds:
                continue
            elements.append(
                {
                    "title": item.get("title") or "(window)",
                    "control_type": item.get("class_name") or "window",
                    "bounds": bounds,
                    "center_x": int(bounds["left"]) + int(bounds["width"]) // 2,
                    "center_y": int(bounds["top"]) + int(bounds["height"]) // 2,
                }
            )
    elif resolved_mode == "region":
        if None in (left, top, width, height):
            raise ValueError("Region mode requires left, top, width, and height.")
        region = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
        png_bytes, _ = grab_png_bytes(region)
        origin_x = int(region["left"])
        origin_y = int(region["top"])
    else:
        raise ValueError("Use mode='window', 'desktop', or 'region'.")
    annotated_bytes, manifest = _annotate_image(png_bytes, elements, origin_x, origin_y)
    _save_if_needed(annotated_bytes, path)
    record_event("screen_annotate", mode=resolved_mode, count=len(manifest), path=path or "")
    return Image(data=annotated_bytes, format="png")
def browser_annotate_page(
    session_id: str,
    page_id: str | None = None,
    max_elements: int = 50,
    path: str | None = None,
) -> Image:
    from .browser_sessions import get_playwright_page

    _, _, page = get_playwright_page(session_id, page_id)
    raw = page.evaluate(
        """() => {
            const selector = 'a[href], button, input, select, textarea, [role="button"], [role="link"], [role="tab"], [role="menuitem"], [tabindex]:not([tabindex="-1"])';
            const vw = window.innerWidth;
            const vh = window.innerHeight;
            const textOf = (el) => (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
            return Array.from(document.querySelectorAll(selector)).map((el) => {
                const r = el.getBoundingClientRect();
                if (r.width < 2 || r.height < 2) return null;
                if (r.right < 0 || r.bottom < 0 || r.left > vw || r.top > vh) return null;
                return {
                    tag: el.tagName.toLowerCase(),
                    text: textOf(el),
                    role: el.getAttribute('role'),
                    aria_label: el.getAttribute('aria-label'),
                    type: el.getAttribute('type'),
                    bounds: {
                        left: Math.round(r.left),
                        top: Math.round(r.top),
                        width: Math.round(r.width),
                        height: Math.round(r.height),
                        right: Math.round(r.right),
                        bottom: Math.round(r.bottom)
                    }
                };
            }).filter(Boolean);
        }"""
    )
    elements = []
    for item in raw[: max(1, min(int(max_elements), 200))]:
        bounds = item["bounds"]
        elements.append(
            {
                "title": item.get("aria_label") or item.get("text") or item.get("type") or item.get("tag"),
                "control_type": item.get("role") or item.get("tag") or "interactive",
                "bounds": bounds,
                "center_x": int(bounds["left"]) + int(bounds["width"]) // 2,
                "center_y": int(bounds["top"]) + int(bounds["height"]) // 2,
            }
        )
    png_bytes = page.screenshot(full_page=False)
    annotated_bytes, manifest = _annotate_image(png_bytes, elements, 0, 0)
    target = Path(path) if path else BROWSER_CAPTURE_ROOT / f"annotate-{session_id}-{now_stamp()}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(annotated_bytes)
    record_event("browser_annotate_page", session_id=session_id, page_id=page_id or "", count=len(manifest), path=str(target))
    return Image(data=annotated_bytes, format="png")
def intent_click(
    intent: str,
    title_regex: str | None = None,
    handle: int | None = None,
    button: str = "left",
    clicks: int = 1,
    use_ocr: bool = True,
) -> dict[str, Any]:
    """Click an element by natural language intent — 7-strategy cascade.

    Strategies (in order):
      1. UIA exact title match
      2. UIA contains title match
      3. UIA automation_id match
      4. UIA control_type + title (Button, CheckBox, MenuItem, etc.)
      5. UIA class_name match
      6. UIA regex title match (fuzzy)
      7. OCR fallback with fuzzy text scoring
    """
    focused = focused_window_data() if title_regex is None and handle is None else None
    window = find_window(title_regex=title_regex, handle=handle or (focused or {}).get("handle"))
    query = " ".join((intent or "").strip().split())
    if not query:
        raise ValueError("Provide non-empty intent.")

    # Strategies 1-3: exact, contains, automation_id
    for source, kwargs in (
        ("uia_exact", {"title": query}),
        ("uia_contains", {"title_contains": query}),
        ("uia_automation_id", {"automation_id": query}),
    ):
        matches = find_matching_elements(window, **kwargs)
        if not matches:
            continue
        element, info = matches[0]
        element.click_input()
        result = {"ok": True, "intent": query, "source": source, "element": info, "x": None, "y": None}
        record_event("intent_click", intent=query, source=source)
        return result

    # Strategy 4: control_type + title_contains for interactive roles
    for ct in ("Button", "CheckBox", "MenuItem", "TabItem", "Hyperlink", "ComboBox", "RadioButton"):
        matches = find_matching_elements(window, control_type=ct, title_contains=query)
        if matches:
            element, info = matches[0]
            element.click_input()
            result = {"ok": True, "intent": query, "source": f"uia_{ct.lower()}", "element": info, "x": None, "y": None}
            record_event("intent_click", intent=query, source=f"uia_{ct.lower()}")
            return result

    # Strategy 5: class_name match
    matches = find_matching_elements(window, class_name=query)
    if matches:
        element, info = matches[0]
        element.click_input()
        result = {"ok": True, "intent": query, "source": "uia_classname", "element": info, "x": None, "y": None}
        record_event("intent_click", intent=query, source="uia_classname")
        return result

    # Strategy 6: regex title match
    query_pattern = re.compile(re.escape(query), re.IGNORECASE)
    try:
        all_elements = find_matching_elements(window, title_contains="")
        for element, info in all_elements:
            title = info.get("title") or ""
            if query_pattern.search(title):
                element.click_input()
                result = {"ok": True, "intent": query, "source": "uia_regex", "element": info, "x": None, "y": None}
                record_event("intent_click", intent=query, source="uia_regex")
                return result
    except Exception:
        pass

    # Strategy 7: OCR with fuzzy scoring
    if use_ocr:
        info = window_info(window)
        bounds = info.get("bounds")
        if bounds:
            region = window_capture_bounds(bounds, padding=0)
            png_bytes, _ = grab_png_bytes(region)
            image = PILImage.open(io.BytesIO(png_bytes))
            ocr = ocr_image_object(image)
            matches = find_ocr_text_spans(ocr["words"], query, exact=False)
            if matches:
                match = matches[0]
                cx = int(region["left"]) + int(match["left"]) + int(match["width"]) // 2
                cy = int(region["top"]) + int(match["top"]) + int(match["height"]) // 2
                validate_screen_point(cx, cy)
                pyautogui.click(x=cx, y=cy, button=button, clicks=max(int(clicks), 1))
                result = {"ok": True, "intent": query, "source": "ocr", "element": match, "x": cx, "y": cy}
                record_event("intent_click", intent=query, source="ocr")
                return result
    raise ValueError(f"No element found for intent={intent!r}. Try desktop_suggest_actions() or screen_annotate() first.")


def _window_text_snapshot(window: Any, use_ocr: bool = True) -> dict[str, Any]:
    info = window_info(window)
    titles: list[str] = []
    try:
        for _, match in find_matching_elements(window, title_contains=""):
            title = (match.get("title") or "").strip()
            if title:
                titles.append(title)
    except Exception:
        pass
    ocr_text = ""
    if use_ocr:
        try:
            bounds = info.get("bounds")
            if bounds:
                region = window_capture_bounds(bounds, padding=0)
                png_bytes, _ = grab_png_bytes(region)
                image = PILImage.open(io.BytesIO(png_bytes))
                ocr_text = ocr_image_object(image).get("text", "")
        except Exception:
            ocr_text = ""
    return {"window": info, "titles": titles, "ocr_text": ocr_text}
def desktop_watch_until(
    condition_type: str,
    condition_value: str = "",
    title_regex: str | None = None,
    handle: int | None = None,
    timeout_seconds: float = 30.0,
    interval_seconds: float = 0.5,
    capture_on_match: bool = True,
    use_ocr: bool = True,
) -> dict[str, Any]:
    resolved = (condition_type or "").strip().lower()
    supported = {"text_appears", "text_disappears", "window_appears", "window_disappears", "visual_change", "focus_changes"}
    if resolved not in supported:
        raise ValueError("Unsupported condition_type.")
    started = time.time()
    baseline_png, baseline_region = grab_png_bytes()
    baseline_hash = hashlib.sha256(baseline_png).hexdigest()
    baseline_focus = focused_window_data().get("handle")
    target_window = None
    if resolved in {"text_appears", "text_disappears"}:
        target_window = find_window(title_regex=title_regex, handle=handle or baseline_focus)
    elif resolved == "window_disappears":
        target_window = find_window(title_regex=condition_value or title_regex, handle=handle)

    deadline = started + max(timeout_seconds, 0.1)

    def predicate():
        if resolved == "focus_changes":
            current = focused_window_data()
            return {"focused_window": current} if current.get("handle") != baseline_focus else None
        if resolved == "visual_change":
            current_png, current_region = grab_png_bytes()
            current_hash = hashlib.sha256(current_png).hexdigest()
            if current_hash != baseline_hash:
                return {"before_hash": baseline_hash, "after_hash": current_hash, "region": current_region}
            return None
        if resolved == "window_appears":
            for info in list_windows_data(visible_only=True):
                if re.search(condition_value, info.get("title") or "", re.I):
                    return {"window": info}
            return None
        if resolved == "window_disappears":
            try:
                find_window(handle=window_info(target_window).get("handle"))
                return None
            except Exception:
                return {"window_gone": True}
        snapshot = _window_text_snapshot(find_window(title_regex=title_regex, handle=handle or baseline_focus), use_ocr=use_ocr)
        target = condition_value.lower()
        in_uia = any(target in title.lower() for title in snapshot["titles"])
        in_ocr = use_ocr and target in snapshot["ocr_text"].lower()
        if resolved == "text_appears" and (in_uia or in_ocr):
            return {"source": "uia" if in_uia else "ocr", "snapshot": snapshot}
        if resolved == "text_disappears" and not in_uia and not in_ocr:
            return {"snapshot": snapshot}
        return None

    detail = wait_until(deadline, max(interval_seconds, 0.05), predicate, description=resolved)
    elapsed = round(time.time() - started, 3)
    capture_path = None
    if capture_on_match and detail:
        target = DESKTOP_WATCH_CAPTURE_ROOT / "watch_until" / f"{now_stamp()}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        current_png, _ = grab_png_bytes()
        target.write_bytes(current_png)
        capture_path = str(target)
    result = {
        "matched": bool(detail),
        "condition_type": resolved,
        "condition_value": condition_value,
        "elapsed_seconds": elapsed,
        "capture_path": capture_path,
        "detail": detail or {},
    }
    record_event("desktop_watch_until", condition_type=resolved, matched=result["matched"], elapsed_seconds=elapsed)
    return result


def desktop_check_actionable(
    title_regex: str | None = None,
    handle: int | None = None,
    selector: str = "",
    control_type: str | None = None,
    timeout_ms: int = 3000,
) -> dict[str, Any]:
    """Check if a desktop UI element is actionable before interacting.

    Verifies: exists, IsEnabled, IsOffscreen, window focused, position stable.
    Returns detailed actionability state.
    """
    import win32gui
    focused = focused_window_data() if title_regex is None and handle is None else None
    target_handle = handle or (focused or {}).get("handle")
    window = find_window(title_regex=title_regex, handle=target_handle)
    w_info = window_info(window)

    # Check window is focused
    foreground_handle = win32gui.GetForegroundWindow()
    window_focused = foreground_handle == w_info.get("handle")

    # Find the element
    query = " ".join((selector or "").strip().split())
    kwargs: dict[str, Any] = {}
    if query:
        kwargs["title_contains"] = query
    if control_type:
        kwargs["control_type"] = control_type
    if not kwargs:
        raise ValueError("Provide selector or control_type.")

    matches = find_matching_elements(window, **kwargs)
    if not matches:
        return {
            "ok": False, "actionable": False, "exists": False,
            "reason": f"No element found for selector={selector!r}",
        }

    element, info = matches[0]

    # Check IsEnabled
    try:
        is_enabled = element.is_enabled()
    except Exception:
        is_enabled = True  # assume enabled if can't check

    # Check IsOffscreen (not visible)
    try:
        rect = element.rectangle()
        is_offscreen = rect.width() <= 0 or rect.height() <= 0
    except Exception:
        is_offscreen = False

    # Check position stability (sample twice with 100ms gap)
    try:
        rect1 = element.rectangle()
        pos1 = (rect1.left, rect1.top)
        time.sleep(0.1)
        rect2 = element.rectangle()
        pos2 = (rect2.left, rect2.top)
        is_stable = pos1 == pos2
    except Exception:
        is_stable = True

    actionable = is_enabled and not is_offscreen and is_stable
    result = {
        "ok": True,
        "actionable": actionable,
        "exists": True,
        "is_enabled": is_enabled,
        "is_offscreen": is_offscreen,
        "is_stable": is_stable,
        "window_focused": window_focused,
        "element": info,
    }
    if not actionable:
        reasons = []
        if not is_enabled:
            reasons.append("disabled")
        if is_offscreen:
            reasons.append("offscreen")
        if not is_stable:
            reasons.append("unstable position")
        result["reason"] = ", ".join(reasons)
    record_event("desktop_check_actionable", actionable=actionable, selector=selector)
    return result


def desktop_suggest_actions(
    title_regex: str | None = None,
    handle: int | None = None,
    max_items: int = 30,
    use_ocr: bool = False,
) -> dict[str, Any]:
    """Scan the focused/target window and suggest possible actions.

    Uses UIA tree to find all interactive controls (buttons, checkboxes,
    menus, etc.) with positions and ready-to-use action strings.
    Falls back to OCR if use_ocr=True.
    """
    focused = focused_window_data() if title_regex is None and handle is None else None
    window = find_window(title_regex=title_regex, handle=handle or (focused or {}).get("handle"))
    w_info = window_info(window)
    safe_max = max(1, min(int(max_items), 100))
    suggestions: list[dict[str, Any]] = []

    interactive_types = {
        "Button": "click", "CheckBox": "toggle", "RadioButton": "toggle",
        "MenuItem": "click", "TabItem": "click", "Hyperlink": "click",
        "ComboBox": "select", "Edit": "fill", "Slider": "drag",
        "ListItem": "click", "TreeItem": "click",
    }
    for ct, action_type in interactive_types.items():
        if len(suggestions) >= safe_max:
            break
        try:
            matches = find_matching_elements(window, control_type=ct)
            for element, info in matches:
                if len(suggestions) >= safe_max:
                    break
                title = (info.get("title") or "").strip()
                bounds = info.get("bounds")
                if not bounds:
                    continue
                try:
                    enabled = element.is_enabled()
                except Exception:
                    enabled = True
                if not enabled:
                    continue
                cx = bounds.get("left", 0) + bounds.get("width", 0) // 2
                cy = bounds.get("top", 0) + bounds.get("height", 0) // 2
                suggestions.append({
                    "control_type": ct,
                    "text": title[:80],
                    "action_type": action_type,
                    "x": cx,
                    "y": cy,
                    "width": bounds.get("width", 0),
                    "height": bounds.get("height", 0),
                    "enabled": enabled,
                    "suggested_action": f'desktop_interact(action="click_intent", intent="{title}")' if action_type == "click" else f'desktop_interact(action="click", x={cx}, y={cy})',
                })
        except Exception:
            continue

    # OCR fallback for additional text-based suggestions
    if use_ocr and len(suggestions) < safe_max:
        try:
            info = window_info(window)
            bounds = info.get("bounds")
            if bounds:
                region = window_capture_bounds(bounds, padding=0)
                png_bytes, _ = grab_png_bytes(region)
                image = PILImage.open(io.BytesIO(png_bytes))
                ocr = ocr_image_object(image)
                for word in (ocr.get("words") or [])[:safe_max - len(suggestions)]:
                    text = word.get("text", "").strip()
                    if len(text) < 2:
                        continue
                    wx = int(region["left"]) + int(word.get("left", 0)) + int(word.get("width", 0)) // 2
                    wy = int(region["top"]) + int(word.get("top", 0)) + int(word.get("height", 0)) // 2
                    suggestions.append({
                        "control_type": "ocr_text",
                        "text": text[:80],
                        "action_type": "click",
                        "x": wx,
                        "y": wy,
                        "source": "ocr",
                        "suggested_action": f'desktop_interact(action="click", x={wx}, y={wy})',
                    })
        except Exception:
            pass

    result = {
        "ok": True,
        "suggestions": suggestions,
        "count": len(suggestions),
        "window": w_info,
    }
    record_event("desktop_suggest_actions", count=len(suggestions))
    return result


def desktop_observe_rich(
    title_regex: str | None = None,
    handle: int | None = None,
    include_interactive: bool = True,
    include_text: bool = True,
    max_interactive: int = 30,
    use_ocr: bool = True,
) -> dict[str, Any]:
    """Rich desktop observation: window state + interactive elements + visible text.

    Combines window info, UIA tree scan, and OCR text extraction
    into a single observation for model-side planning.
    """
    focused = focused_window_data() if title_regex is None and handle is None else None
    window = find_window(title_regex=title_regex, handle=handle or (focused or {}).get("handle"))
    w_info = window_info(window)
    observation: dict[str, Any] = {
        "ok": True,
        "window": w_info,
        "focused": w_info.get("handle") == (focused or {}).get("handle"),
    }

    if include_interactive:
        try:
            actions = desktop_suggest_actions(
                handle=w_info.get("handle"), max_items=max_interactive, use_ocr=False,
            )
            observation["interactive"] = actions.get("suggestions", [])
            observation["interactive_count"] = actions.get("count", 0)
        except Exception as e:
            observation["interactive"] = []
            observation["interactive_error"] = str(e)

    if include_text:
        try:
            snapshot = _window_text_snapshot(window, use_ocr=use_ocr)
            observation["uia_titles"] = snapshot.get("titles", [])[:50]
            observation["ocr_text"] = (snapshot.get("ocr_text") or "")[:3000]
        except Exception:
            observation["uia_titles"] = []
            observation["ocr_text"] = ""

    record_event("desktop_observe_rich", handle=w_info.get("handle"))
    return observation


def desktop_human_idle(
    title_regex: str | None = None,
    handle: int | None = None,
    duration_ms: int = 2000,
    preset: str = "default",
) -> dict[str, Any]:
    """Simulate human idle behavior between desktop actions.

    Adds small random mouse micro-movements within the target window
    and variable wait times to appear more human-like.
    """
    import random
    from .human import human_move, resolve_config

    focused = focused_window_data() if title_regex is None and handle is None else None
    window = find_window(title_regex=title_regex, handle=handle or (focused or {}).get("handle"))
    w_info = window_info(window)
    bounds = w_info.get("bounds", {})
    safe_duration = max(200, min(int(duration_ms), 10000))
    cfg = resolve_config(preset)

    left = bounds.get("left", 100)
    top = bounds.get("top", 100)
    width = bounds.get("width", 800)
    height = bounds.get("height", 600)

    steps = random.randint(1, 3)
    for _ in range(steps):
        x = random.randint(left + int(width * 0.1), left + int(width * 0.9))
        y = random.randint(top + int(height * 0.1), top + int(height * 0.9))
        try:
            validate_screen_point(x, y)
            human_move(x, y, cfg)
        except Exception:
            pass
        time.sleep(random.uniform(0.1, safe_duration / 1000.0 / steps))

    result = {
        "ok": True,
        "idle_ms": safe_duration,
        "mouse_steps": steps,
        "window": {"handle": w_info.get("handle"), "title": w_info.get("title")},
        "preset": preset,
    }
    record_event("desktop_human_idle", idle_ms=safe_duration, steps=steps)
    return result


def desktop_screenshot_actions(
    title_regex: str | None = None,
    handle: int | None = None,
    max_items: int = 20,
) -> dict:
    """Capture screenshot + annotate interactive elements + suggest actions in one call.
    
    Returns screenshot path, list of interactive elements with positions, and suggested actions.
    """
    import base64
    from .desktop_core import find_matching_elements, get_foreground_window_info
    from .tools.capture import capture_desktop_screenshot

    # Get target window
    if title_regex:
        import re
        from .desktop_core import list_windows
        for w in list_windows():
            if re.search(title_regex, w.get("title", ""), re.IGNORECASE):
                handle = w.get("handle")
                break

    w_info = get_foreground_window_info() if not handle else {"handle": handle}

    # Capture screenshot
    screenshot_result = capture_desktop_screenshot()
    screenshot_path = screenshot_result.get("path", "")

    # Get UIA elements
    interactive_types = [
        "Button", "Edit", "ComboBox", "CheckBox", "RadioButton",
        "Hyperlink", "MenuItem", "Tab", "ListItem", "Slider",
    ]
    elements = []
    for ct in interactive_types:
        found = find_matching_elements(
            control_type=ct,
            title_regex=title_regex,
            handle=handle,
            max_results=5,
        )
        for el in found:
            elements.append({
                "type": ct,
                "name": el.get("name", ""),
                "automation_id": el.get("automation_id", ""),
                "rect": el.get("rect"),
                "enabled": el.get("is_enabled", True),
            })
        if len(elements) >= max_items:
            break

    elements = elements[:max_items]

    # Generate suggested actions
    suggestions = []
    for el in elements:
        if el["enabled"] and el["name"]:
            if el["type"] in ("Button", "Hyperlink", "MenuItem", "Tab", "ListItem"):
                suggestions.append(f"click_intent(intent='{el['name']}')")
            elif el["type"] in ("Edit", "ComboBox"):
                suggestions.append(f"click_intent(intent='{el['name']}') then kb_type(text='...')")
            elif el["type"] in ("CheckBox", "RadioButton"):
                suggestions.append(f"click_intent(intent='{el['name']}') to toggle")

    return {
        "ok": True,
        "screenshot": screenshot_path,
        "elements": elements,
        "element_count": len(elements),
        "suggestions": suggestions[:15],
        "window": w_info,
    }
