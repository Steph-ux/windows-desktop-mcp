"""Desktop capture and perception MCP tools."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

import pyautogui
from mcp.server.fastmcp import Image
import numpy as np
from PIL import Image as PILImage
from PIL import ImageChops

from ..app import mcp
from ..desktop_core import grab_png_bytes, focused_window_data, list_windows_data, save_png_bytes, window_capture_bounds
from ..helpers import now_stamp, wait_until
from ..paths import DESKTOP_WATCH_CAPTURE_ROOT, SCREENSHOT_DIR
from ..shared.window_utils import find_window, window_info
from ..runtime import record_event
from ..state import DESKTOP_WATCH_LOCK, DESKTOP_WATCH_SESSIONS
from .windows import focused_window_summary, window_summary
def capture_desktop(path: str | None = None) -> Image:
    png_bytes, _ = grab_png_bytes()
    save_png_bytes(png_bytes, path)
    return Image(data=png_bytes, format="png")
def capture_region(left: int, top: int, width: int, height: int, path: str | None = None) -> Image:
    png_bytes, _ = grab_png_bytes({"left": left, "top": top, "width": width, "height": height})
    save_png_bytes(png_bytes, path)
    return Image(data=png_bytes, format="png")
def capture_window(
    title_regex: str | None = None,
    handle: int | None = None,
    path: str | None = None,
    padding: int = 0,
) -> Image:
    window = find_window(title_regex=title_regex, handle=handle)
    bounds = window_info(window)["bounds"]
    if not bounds:
        raise ValueError("Could not determine window bounds.")
    region = window_capture_bounds(bounds, padding=max(padding, 0))
    png_bytes, _ = grab_png_bytes(region)
    save_png_bytes(png_bytes, path)
    return Image(data=png_bytes, format="png")
def capture_focused_window(path: str | None = None, padding: int = 0) -> Image:
    focused = focused_window_data()
    handle = focused.get("handle")
    if not handle:
        raise ValueError("Could not determine the focused window handle.")
    return capture_window(handle=handle, path=path, padding=padding)
def desktop_snapshot_state(path: str | None = None, include_windows: bool = True, title_filter: str = "", max_windows: int = 50) -> dict[str, Any]:
    png_bytes, region = grab_png_bytes()
    target_path = None
    if path:
        save_png_bytes(png_bytes, path)
        target_path = str(Path(path))
    image_hash = hashlib.sha256(png_bytes).hexdigest()
    cursor = pyautogui.position()
    focused = focused_window_data()
    windows = list_windows_data(title_filter=title_filter, visible_only=True) if include_windows else []
    return {
        "path": target_path,
        "image_hash": image_hash,
        "region": region,
        "cursor": {"x": cursor.x, "y": cursor.y},
        "focused_window": focused,
        "window_count": len(windows),
        "windows": windows[: max(1, min(int(max_windows), 200))],
    }
def desktop_overview(
    title_filter: str = "",
    max_windows: int = 10,
    include_window_summaries: bool = False,
    use_ocr: bool = False,
    uia_depth: int = 1,
    max_nodes: int = 60,
) -> dict[str, Any]:
    from .windows import window_summary

    snapshot = desktop_snapshot_state(include_windows=True, title_filter=title_filter, max_windows=max_windows)
    windows = snapshot["windows"][: max(1, min(int(max_windows), 50))]
    items = []
    for info in windows:
        item = {"window": info}
        if include_window_summaries and info.get("handle"):
            try:
                item["summary"] = window_summary(
                    handle=int(info["handle"]),
                    use_ocr=use_ocr,
                    uia_depth=uia_depth,
                    max_nodes=max_nodes,
                )
            except Exception as exc:
                item["summary_error"] = str(exc)
        items.append(item)
    return {
        "focused_window": snapshot["focused_window"],
        "cursor": snapshot["cursor"],
        "image_hash": snapshot["image_hash"],
        "window_count": snapshot["window_count"],
        "windows": items,
    }
def save_desktop_screenshot(prefix: str = "desktop") -> dict[str, Any]:
    path = SCREENSHOT_DIR / f"{prefix}-{now_stamp()}.png"
    png_bytes, region = grab_png_bytes()
    path.write_bytes(png_bytes)
    return {"path": str(path), "region": region}
def save_window_screenshot(
    prefix: str = "window",
    title_regex: str | None = None,
    handle: int | None = None,
    padding: int = 0,
) -> dict[str, Any]:
    window = find_window(title_regex=title_regex, handle=handle)
    info = window_info(window)
    bounds = info["bounds"]
    if not bounds:
        raise ValueError("Could not determine window bounds.")
    region = window_capture_bounds(bounds, padding=max(padding, 0))
    path = SCREENSHOT_DIR / f"{prefix}-{now_stamp()}.png"
    png_bytes, _ = grab_png_bytes(region)
    path.write_bytes(png_bytes)
    result = {"path": str(path), "window": info, "region": region}
    record_event("save_window_screenshot", path=str(path), handle=info.get("handle"), title=info.get("title"))
    return result
def wait_for_desktop_change(
    baseline_hash: str | None = None,
    timeout_seconds: float = 10.0,
    interval_seconds: float = 0.2,
    left: int | None = None,
    top: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    region = None
    if None not in (left, top, width, height):
        region = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
    png_bytes, captured_region = grab_png_bytes(region)
    before_hash = baseline_hash or hashlib.sha256(png_bytes).hexdigest()
    deadline = time.time() + max(timeout_seconds, 0.1)

    def predicate():
        current_bytes, current_region = grab_png_bytes(region)
        current_hash = hashlib.sha256(current_bytes).hexdigest()
        if current_hash != before_hash:
            return {"changed": True, "before_hash": before_hash, "after_hash": current_hash, "region": current_region}
        return None

    result = wait_until(deadline, max(interval_seconds, 0.05), predicate)
    if not result:
        return {"changed": False, "before_hash": before_hash, "after_hash": before_hash, "region": captured_region}
    return result
def screen_watch(
    region: dict[str, int] | None = None,
    change_threshold: float = 0.02,
    max_seconds: float = 30.0,
    interval_seconds: float = 0.5,
) -> dict[str, Any]:
    deadline = time.time() + max(max_seconds, 0.1)
    before_bytes, _ = grab_png_bytes(region)
    import io
    import numpy as np
    from PIL import Image as PILImage

    before = np.array(PILImage.open(io.BytesIO(before_bytes)).convert("L"), dtype=np.int16)

    while time.time() < deadline:
        time.sleep(max(interval_seconds, 0.05))
        after_bytes, current_region = grab_png_bytes(region)
        after = np.array(PILImage.open(io.BytesIO(after_bytes)).convert("L"), dtype=np.int16)
        diff = np.abs(before - after)
        ratio = float((diff > 10).mean())
        if ratio >= float(change_threshold):
            return {"changed": True, "change_ratio": ratio, "elapsed": time.time() - (deadline - max(max_seconds, 0.1)), "region": current_region}
        before = after

    return {"changed": False, "change_ratio": 0.0, "elapsed": max(max_seconds, 0.1), "region": region}
def diff_screenshots(path_a: str, path_b: str, threshold: float = 0.01) -> dict[str, Any]:
    img_a = np.array(PILImage.open(path_a).convert("RGB"))
    img_b = np.array(PILImage.open(path_b).convert("RGB"))
    if img_a.shape != img_b.shape:
        return {"comparable": False, "reason": "dimensions differentes"}
    diff = np.abs(img_a.astype(int) - img_b.astype(int))
    changed_ratio = float((diff.max(axis=2) > 15).mean())
    diff_path = None
    if changed_ratio > float(threshold):
        diff_img = PILImage.fromarray((diff * 3).clip(0, 255).astype(np.uint8))
        target = SCREENSHOT_DIR / f"diff-{now_stamp()}.png"
        diff_img.save(target)
        diff_path = str(target)
    return {"changed_ratio": changed_ratio, "changed": changed_ratio > float(threshold), "diff_path": diff_path}
def find_image_on_screen(
    template_path: str,
    confidence: float = 0.8,
    region: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Trouve une image sur l'ecran via template matching OpenCV."""
    try:
        import cv2
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("OpenCV is required for find_image_on_screen(). Install opencv-python-headless.") from exc

    template = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise ValueError(f"Could not read template image: {template_path}")
    png_bytes, screen_region = grab_png_bytes(region)
    screen = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    if screen is None:
        raise RuntimeError("Failed to decode captured screen image.")

    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if float(max_val) < float(confidence):
        payload = {"found": False, "confidence": float(max_val), "region": screen_region}
        record_event("find_image_on_screen", template_path=template_path, found=False, confidence=float(max_val))
        return payload

    center_x = int(screen_region["left"] + max_loc[0] + template.shape[1] // 2)
    center_y = int(screen_region["top"] + max_loc[1] + template.shape[0] // 2)
    payload = {
        "found": True,
        "x": center_x,
        "y": center_y,
        "confidence": float(max_val),
        "region": screen_region,
    }
    record_event(
        "find_image_on_screen",
        template_path=template_path,
        found=True,
        confidence=float(max_val),
        x=center_x,
        y=center_y,
    )
    return payload
def describe_screen(
    region: dict[str, int] | None = None,
    prompt: str = "Describe what is visible on this screen. Identify the main interactive elements and current state.",
    model: str | None = None,
    max_tokens: int = 700,
    capture_path: str | None = None,
) -> dict[str, Any]:
    """Capture l'ecran et demande a Claude Vision d'en produire une description."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for describe_screen().")
    try:
        import httpx
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("httpx is required for describe_screen(). Install httpx.") from exc

    png_bytes, screen_region = grab_png_bytes(region)
    if capture_path:
        save_png_bytes(png_bytes, capture_path)
    encoded = base64.b64encode(png_bytes).decode("ascii")
    resolved_model = model or os.getenv("PM_MCP_ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": resolved_model,
            "max_tokens": max(64, int(max_tokens)),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": encoded,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        },
        timeout=60.0,
    )
    response.raise_for_status()
    payload = response.json()
    parts = payload.get("content") or []
    description = "\n".join(
        str(part.get("text", "")).strip()
        for part in parts
        if isinstance(part, dict) and part.get("type") == "text" and part.get("text")
    ).strip()
    result = {
        "model": resolved_model,
        "description": description,
        "region": screen_region,
        "capture_path": str(Path(capture_path)) if capture_path else None,
        "image_bytes": len(png_bytes),
    }
    record_event(
        "describe_screen",
        model=resolved_model,
        capture_path=result["capture_path"],
        image_bytes=len(png_bytes),
    )
    return result


def _window_summary_from_target(
    title_regex: str | None = None,
    handle: int | None = None,
    use_ocr: bool = True,
    uia_depth: int = 2,
    max_nodes: int = 120,
) -> dict[str, Any]:
    return window_summary(
        title_regex=title_regex,
        handle=handle,
        use_ocr=use_ocr,
        uia_depth=uia_depth,
        max_nodes=max_nodes,
    )


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


def _perform_analysis(capture_path: str | None, mode: str, region: dict[str, int] | None, enable_vision: bool) -> dict[str, Any]:
    """Perform analysis on captured screen."""
    from .ocr import ocr_region, ocr_image_file
    
    result = {"type": "ocr", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}
    
    # OCR analysis
    if capture_path:
        try:
            ocr_result = ocr_image_file(capture_path, language="eng")
            result["ocr"] = {
                "text": ocr_result.get("text", "")[:500],
                "word_count": ocr_result.get("word_count", 0),
            }
            _log_analysis("ocr", result)
        except Exception as e:
            result["ocr_error"] = str(e)
    
    # Vision analysis (optional, requires API key)
    if enable_vision and capture_path:
        try:
            import os
            if os.getenv("ANTHROPIC_API_KEY"):
                vision_result = describe_screen(
                    region=region,
                    prompt="Briefly describe what is visible on this screen.",
                    max_tokens=300,
                )
                result["vision"] = {
                    "description": vision_result.get("description", "")[:500],
                    "model": vision_result.get("model"),
                }
                _log_analysis("vision", result)
        except Exception as e:
            result["vision_error"] = str(e)
    
    return result


def _log_analysis(analysis_type: str, result: dict[str, Any]) -> None:
    """Log analysis results to file."""
    from ..paths import SCREENSHOT_DIR
    
    log_path = SCREENSHOT_DIR.parent / "stream_analysis.log"
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] {analysis_type.upper()}: "
        
        if analysis_type == "ocr" and "ocr" in result:
            text = result["ocr"].get("text", "")[:200]
            words = result["ocr"].get("word_count", 0)
            log_entry += f"{words} words - {text}"
        elif analysis_type == "vision" and "vision" in result:
            desc = result["vision"].get("description", "")[:200]
            model = result["vision"].get("model", "unknown")
            log_entry += f"[{model}] {desc}"
        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_entry + "\n")
    except Exception:
        pass


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
    enable_analysis: bool = False,
    analysis_interval: int = 5,
    enable_vision: bool = False,
    sample_count: int = 0,
) -> dict[str, Any]:
    from .ocr import ocr_region, ocr_window

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
        summary = desktop_overview(max_windows=8, include_window_summaries=False, use_ocr=False, uia_depth=uia_depth, max_nodes=max_nodes)
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
        ocr_result = ocr_region(left=region["left"], top=region["top"], width=region["width"], height=region["height"], language="eng") if use_ocr else {"text": "", "word_count": 0, "words": [], "region": region}
        summary = {"region": region, "ocr_excerpt": (ocr_result.get("text") or "").strip()[:1000], "ocr_word_count": int(ocr_result.get("word_count") or 0)}
        if capture:
            capture_target = DESKTOP_WATCH_CAPTURE_ROOT / watch_id / f"{now_stamp()}.png"
            capture_target.parent.mkdir(parents=True, exist_ok=True)
            capture_region(left=region["left"], top=region["top"], width=region["width"], height=region["height"], path=str(capture_target))
            capture_path = str(capture_target)
        png_bytes, _ = grab_png_bytes(region)
        payload = {"region": region, "image_hash": hashlib.sha256(png_bytes).hexdigest(), "ocr_excerpt": summary["ocr_excerpt"], "ocr_word_count": summary["ocr_word_count"]}
    else:
        summary = _window_summary_from_target(handle=handle, title_regex=title_regex, use_ocr=use_ocr, uia_depth=uia_depth, max_nodes=max_nodes)
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
    
    # Add analysis results if enabled and interval matches
    analysis_result = None
    if enable_analysis and sample_count % analysis_interval == 0:
        analysis_result = _perform_analysis(capture_path, mode, region, enable_vision)
        if analysis_result:
            payload["analysis"] = analysis_result
            print(f"[ANALYSIS] {watch_id}: {analysis_result.get('type', 'unknown')}")
    
    sample_hash = hashlib.sha256(repr(payload).encode("utf-8", errors="replace")).hexdigest()
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "epoch": round(time.time(), 3), "hash": sample_hash, "summary": summary, "capture_path": capture_path, "analysis": analysis_result}


def _desktop_watch_loop(watch_id: str) -> None:
    consecutive_errors = 0
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
            enable_analysis = watch.get("enable_analysis", False)
            analysis_interval = watch.get("analysis_interval", 5)
            enable_vision = watch.get("enable_vision", False)
        if stop_event.is_set():
            return
        try:
            sample_count = watch.get("sample_count", 0)
            sample = _desktop_watch_sample(mode, handle, title_regex, region, use_ocr, uia_depth, max_nodes, capture, watch_id, enable_analysis, analysis_interval, enable_vision, sample_count)
            consecutive_errors = 0
            with DESKTOP_WATCH_LOCK:
                current = DESKTOP_WATCH_SESSIONS.get(watch_id)
                if current:
                    last_hash = current.get("last_hash")
                    current["sample_count"] = sample_count + 1
                    if sample["hash"] != last_hash:
                        current["change_count"] += 1
                        current["last_hash"] = sample["hash"]
                    current["history"].append(sample)
        except Exception as exc:
            consecutive_errors += 1
            with DESKTOP_WATCH_LOCK:
                current = DESKTOP_WATCH_SESSIONS.get(watch_id)
                if current:
                    current["last_error"] = str(exc)
            backoff_seconds = min(2**consecutive_errors, 30)
            if stop_event.wait(backoff_seconds):
                return
            continue
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
    return {"watch_id": watch_id, "stopped": True, "sample_count": watch.get("sample_count", 0), "change_count": watch.get("change_count", 0)}


def stop_all_desktop_watch_sessions() -> dict[str, Any]:
    with DESKTOP_WATCH_LOCK:
        ids = list(DESKTOP_WATCH_SESSIONS.keys())
    results = [_stop_desktop_watch_session(watch_id) for watch_id in ids]
    return {"stopped": len([item for item in results if item["stopped"]]), "results": results}
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
    from .ocr import ocr_region

    resolved_mode = (mode or "focused").lower()
    if resolved_mode not in {"focused", "window", "desktop", "region"}:
        raise ValueError("Use mode='focused', 'window', 'desktop', or 'region'.")
    region = None
    if resolved_mode == "focused":
        summary = focused_window_summary(use_ocr=use_ocr, uia_depth=uia_depth, max_nodes=max_nodes)
    elif resolved_mode == "window":
        summary = _window_summary_from_target(title_regex=title_regex, handle=handle, use_ocr=use_ocr, uia_depth=uia_depth, max_nodes=max_nodes)
    elif resolved_mode == "desktop":
        summary = desktop_overview(max_windows=8, include_window_summaries=False, use_ocr=False, uia_depth=uia_depth, max_nodes=max_nodes)
    else:
        if None in (left, top, width, height):
            raise ValueError("Provide left, top, width, and height for mode='region'.")
        region = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
        ocr = ocr_region(left=region["left"], top=region["top"], width=region["width"], height=region["height"], language="eng") if use_ocr else {"text": "", "word_count": 0, "words": [], "region": region}
        summary = {"region": region, "ocr_excerpt": (ocr.get("text") or "").strip()[:1000], "ocr_word_count": int(ocr.get("word_count") or 0)}
    capture_path = None
    image_hash = None
    if capture:
        capture_target = DESKTOP_WATCH_CAPTURE_ROOT / "snapshots" / f"{resolved_mode}-{now_stamp()}.png"
        capture_target.parent.mkdir(parents=True, exist_ok=True)
        if resolved_mode == "focused":
            capture_focused_window(path=str(capture_target))
        elif resolved_mode == "window":
            target_handle = summary["window"].get("handle")
            capture_window(handle=target_handle, path=str(capture_target))
        elif resolved_mode == "desktop":
            capture_desktop(path=str(capture_target))
        else:
            capture_region(left=region["left"], top=region["top"], width=region["width"], height=region["height"], path=str(capture_target))
        capture_path = str(capture_target)
        image_hash = hashlib.sha256(capture_target.read_bytes()).hexdigest()
    return {"mode": resolved_mode, "capture_path": capture_path, "image_hash": image_hash, "summary": summary}
def compare_capture_images(before_path: str, after_path: str) -> dict[str, Any]:
    before = Path(before_path)
    after = Path(after_path)
    if not before.exists():
        raise ValueError(f"Image file not found: {before_path}")
    if not after.exists():
        raise ValueError(f"Image file not found: {after_path}")
    return {"before_path": str(before), "after_path": str(after), **_compare_image_paths(str(before), str(after))}
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
    enable_analysis: bool = False,
    analysis_interval: int = 5,
    enable_vision: bool = False,
) -> dict[str, Any]:
    resolved_mode = (mode or "focused").lower()
    if resolved_mode not in {"focused", "window", "desktop", "region"}:
        raise ValueError("Use mode='focused', 'window', 'desktop', or 'region'.")
    if resolved_mode == "window" and handle is None and not title_regex:
        raise ValueError("Provide handle or title_regex for mode='window'.")
    region = None
    if resolved_mode == "region":
        if None in (left, top, width, height):
            raise ValueError("Provide left, top, width, and height for mode='region'.")
        region = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
    watch_id = uuid.uuid4().hex[:12]
    watch = {
        "watch_id": watch_id,
        "mode": resolved_mode,
        "handle": handle,
        "title_regex": title_regex,
        "region": region,
        "interval_seconds": max(interval_seconds, 0.1),
        "capture": capture,
        "use_ocr": use_ocr,
        "uia_depth": max(0, int(uia_depth)),
        "max_nodes": max(1, int(max_nodes)),
        "history": deque(maxlen=max(1, min(int(history_limit), 200))),
        "stop_event": threading.Event(),
        "thread": None,
        "created_at": round(time.time(), 3),
        "sample_count": 0,
        "change_count": 0,
        "last_hash": None,
        "last_error": None,
        "enable_analysis": enable_analysis,
        "analysis_interval": max(1, int(analysis_interval)),
        "enable_vision": enable_vision,
    }
    thread = threading.Thread(target=_desktop_watch_loop, args=(watch_id,), daemon=True)
    watch["thread"] = thread
    with DESKTOP_WATCH_LOCK:
        DESKTOP_WATCH_SESSIONS[watch_id] = watch
    thread.start()
    record_event("desktop_watch_start", watch_id=watch_id, mode=resolved_mode, handle=handle, title_regex=title_regex, enable_analysis=enable_analysis)
    return {"watch_id": watch_id, "mode": resolved_mode, "handle": handle, "title_regex": title_regex, "region": region, "interval_seconds": watch["interval_seconds"], "capture": capture, "enable_analysis": enable_analysis}
def desktop_watch_list() -> dict[str, Any]:
    with DESKTOP_WATCH_LOCK:
        items = []
        for watch_id, watch in DESKTOP_WATCH_SESSIONS.items():
            items.append({"watch_id": watch_id, "mode": watch["mode"], "handle": watch.get("handle"), "title_regex": watch.get("title_regex"), "region": watch.get("region"), "interval_seconds": watch["interval_seconds"], "capture": watch["capture"], "sample_count": watch["sample_count"], "change_count": watch["change_count"], "last_error": watch.get("last_error")})
    return {"count": len(items), "watches": items}
def desktop_watch_get_states(watch_id: str, limit: int = 10) -> dict[str, Any]:
    with DESKTOP_WATCH_LOCK:
        watch = DESKTOP_WATCH_SESSIONS.get(watch_id)
        if not watch:
            raise ValueError(f"Unknown desktop watch session: {watch_id}")
        states = list(watch["history"])[-max(1, min(int(limit), 100)) :]
        summary = {"watch_id": watch_id, "mode": watch["mode"], "sample_count": watch["sample_count"], "change_count": watch["change_count"], "last_error": watch.get("last_error")}
    return {**summary, "count": len(states), "states": states}
def desktop_watch_get_change_summary(watch_id: str, limit: int = 20) -> dict[str, Any]:
    with DESKTOP_WATCH_LOCK:
        watch = DESKTOP_WATCH_SESSIONS.get(watch_id)
        if not watch:
            raise ValueError(f"Unknown desktop watch session: {watch_id}")
        states = list(watch["history"])[-max(1, min(int(limit), 100)) :]
        meta = {"watch_id": watch_id, "mode": watch["mode"], "sample_count": watch["sample_count"], "change_count": watch["change_count"], "last_error": watch.get("last_error")}
    return {**meta, **_watch_change_summary(states)}
def desktop_watch_compare_latest_frames(watch_id: str) -> dict[str, Any]:
    with DESKTOP_WATCH_LOCK:
        watch = DESKTOP_WATCH_SESSIONS.get(watch_id)
        if not watch:
            raise ValueError(f"Unknown desktop watch session: {watch_id}")
        states = [state for state in watch["history"] if state.get("capture_path")]
    if len(states) < 2:
        raise ValueError(f"Desktop watch session {watch_id} needs at least two captured states.")
    before = states[-2]
    after = states[-1]
    comparison = _compare_image_paths(before["capture_path"], after["capture_path"])
    return {"watch_id": watch_id, "before_ts": before.get("ts"), "after_ts": after.get("ts"), "before_path": before.get("capture_path"), "after_path": after.get("capture_path"), **comparison}
def desktop_watch_get_latest_capture(watch_id: str) -> Image:
    with DESKTOP_WATCH_LOCK:
        watch = DESKTOP_WATCH_SESSIONS.get(watch_id)
        if not watch:
            raise ValueError(f"Unknown desktop watch session: {watch_id}")
        if not watch["history"]:
            raise ValueError(f"Desktop watch session {watch_id} has no samples yet.")
        latest = list(watch["history"])[-1]
    capture_path = latest.get("capture_path")
    if not capture_path:
        raise ValueError(f"Desktop watch session {watch_id} was not started with capture=True.")
    path = Path(capture_path)
    if not path.exists():
        raise ValueError(f"Latest desktop watch capture not found: {capture_path}")
    return Image(data=path.read_bytes(), format="png")
def desktop_watch_wait_change(watch_id: str, baseline_change_count: int | None = None, timeout_seconds: float = 10.0, interval_seconds: float = 0.2) -> dict[str, Any]:
    deadline = time.time() + max(timeout_seconds, 0.1)
    with DESKTOP_WATCH_LOCK:
        watch = DESKTOP_WATCH_SESSIONS.get(watch_id)
        if not watch:
            raise ValueError(f"Unknown desktop watch session: {watch_id}")
        baseline = int(baseline_change_count) if baseline_change_count is not None else int(watch["change_count"])

    def predicate():
        with DESKTOP_WATCH_LOCK:
            current = DESKTOP_WATCH_SESSIONS.get(watch_id)
            if not current:
                return {"watch_id": watch_id, "stopped": True}
            if int(current["change_count"]) > baseline:
                latest = list(current["history"])[-1] if current["history"] else None
                return {"watch_id": watch_id, "changed": True, "change_count": current["change_count"], "sample_count": current["sample_count"], "latest_state": latest}
        return None

    result = wait_until(deadline, max(interval_seconds, 0.05), predicate)
    if result:
        return result
    with DESKTOP_WATCH_LOCK:
        current = DESKTOP_WATCH_SESSIONS.get(watch_id)
        if not current:
            return {"watch_id": watch_id, "stopped": True}
        latest = list(current["history"])[-1] if current["history"] else None
        return {"watch_id": watch_id, "changed": False, "change_count": current["change_count"], "sample_count": current["sample_count"], "latest_state": latest}
def desktop_watch_stop(watch_id: str) -> dict[str, Any]:
    result = _stop_desktop_watch_session(watch_id)
    record_event("desktop_watch_stop", **result)
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
    from .ocr import ocr_window

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
        summary = _window_summary_from_target(title_regex=title_regex, handle=handle, use_ocr=use_ocr, uia_depth=uia_depth, max_nodes=max_nodes) if title_regex or handle else focused_window_summary(use_ocr=use_ocr, uia_depth=uia_depth, max_nodes=max_nodes)
        target_lower = target_text.lower()
        if any(target_lower in (title or "").lower() for title in summary["uia_titles"]):
            return {"matched": True, "source": "uia", "summary": summary, "goal": goal}
        if use_ocr:
            if target_lower in (summary.get("ocr_excerpt") or "").lower():
                return {"matched": True, "source": "ocr_excerpt", "summary": summary, "goal": goal}
            try:
                ocr_result = ocr_window(title_regex=title_regex, handle=handle) if title_regex or handle else ocr_window(handle=(summary.get("window") or focused_window_data()).get("handle"))
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
def start_mjpeg_stream(port: int = 8080) -> dict[str, Any]:
    """Start MJPEG streaming server for screen capture."""
    from ..streaming import start_mjpeg_server as _start_server
    
    result = _start_server(port=port)
    record_event("start_mjpeg_stream", port=port, running=result["running"])
    return result
def stop_mjpeg_stream() -> dict[str, Any]:
    """Stop MJPEG streaming server."""
    from ..streaming import stop_mjpeg_server as _stop_server
    
    result = _stop_server()
    record_event("stop_mjpeg_stream", running=result["running"])
    return result
def get_mjpeg_status() -> dict[str, Any]:
    """Get MJPEG server status."""
    from ..streaming import get_mjpeg_status as _get_status
    
    return _get_status()
def get_latest_analysis(watch_id: str, limit: int = 10) -> dict[str, Any]:
    """Get latest analysis results from a watch session."""
    with DESKTOP_WATCH_LOCK:
        watch = DESKTOP_WATCH_SESSIONS.get(watch_id)
        if not watch:
            raise ValueError(f"Unknown desktop watch session: {watch_id}")
        states = [state for state in list(watch["history"])[-max(1, min(int(limit), 100)):] if state.get("analysis")]
    return {"watch_id": watch_id, "count": len(states), "analyses": states}
def export_analysis_history(watch_id: str, path: str) -> dict[str, Any]:
    """Export analysis history to JSON file."""
    import json
    from pathlib import Path
    
    with DESKTOP_WATCH_LOCK:
        watch = DESKTOP_WATCH_SESSIONS.get(watch_id)
        if not watch:
            raise ValueError(f"Unknown desktop watch session: {watch_id}")
        analyses = [state for state in list(watch["history"]) if state.get("analysis")]
    
    export_path = Path(path)
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_text(json.dumps(analyses, indent=2))
    
    record_event("export_analysis_history", watch_id=watch_id, path=str(path), count=len(analyses))
    return {"watch_id": watch_id, "path": str(export_path), "count": len(analyses)}


__all__ = [
    "capture_desktop",
    "capture_focused_window",
    "capture_region",
    "capture_window",
    "compare_capture_images",
    "desktop_overview",
    "desktop_perception_snapshot",
    "desktop_snapshot_state",
    "desktop_watch_compare_latest_frames",
    "desktop_watch_get_change_summary",
    "desktop_watch_get_latest_capture",
    "desktop_watch_get_states",
    "desktop_watch_list",
    "desktop_watch_start",
    "desktop_watch_stop",
    "desktop_watch_wait_change",
    "describe_screen",
    "diff_screenshots",
    "find_image_on_screen",
    "focused_window_summary",
    "save_desktop_screenshot",
    "save_window_screenshot",
    "screen_watch",
    "start_mjpeg_stream",
    "stop_mjpeg_stream",
    "get_mjpeg_status",
    "get_latest_analysis",
    "export_analysis_history",
    "stop_all_desktop_watch_sessions",
    "wait_for_desktop_change",
    "watch_until_goal",
]
