"""OCR MCP tools."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pyautogui
from mcp.server.fastmcp import Image
from PIL import Image as PILImage

from ..app import mcp
from ..desktop_core import find_window, focused_window_data, grab_png_bytes, validate_screen_point, window_capture_bounds, window_info
from ..ocr_core import find_ocr_text_spans, ocr_image_object


@mcp.tool()
def ocr_region(left: int, top: int, width: int, height: int, language: str = "eng") -> dict[str, Any]:
    png_bytes, region = grab_png_bytes({"left": left, "top": top, "width": width, "height": height})
    image = PILImage.open(io.BytesIO(png_bytes))
    result = ocr_image_object(image, language=language)
    result["region"] = region
    return result


@mcp.tool()
def ocr_window(
    title_regex: str | None = None,
    handle: int | None = None,
    language: str = "eng",
    padding: int = 0,
) -> dict[str, Any]:
    window = find_window(title_regex=title_regex, handle=handle)
    info = window_info(window)
    bounds = info["bounds"]
    if not bounds:
        raise ValueError("Could not determine window bounds.")
    region = window_capture_bounds(bounds, padding=max(padding, 0))
    png_bytes, _ = grab_png_bytes(region)
    image = PILImage.open(io.BytesIO(png_bytes))
    result = ocr_image_object(image, language=language)
    result["window"] = info
    result["region"] = region
    return result


@mcp.tool()
def ocr_image_file(path: str, language: str = "eng") -> dict[str, Any]:
    image_path = Path(path)
    if not image_path.exists():
        raise ValueError(f"Image file not found: {path}")
    image = PILImage.open(image_path)
    result = ocr_image_object(image, language=language)
    result["path"] = str(image_path)
    return result


@mcp.tool()
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
    if not text.strip():
        raise ValueError("Provide non-empty text.")
    if handle is not None or title_regex is not None:
        result = ocr_window(title_regex=title_regex, handle=handle, language=language)
        origin_left = int(result["region"]["left"])
        origin_top = int(result["region"]["top"])
    else:
        if None in (left, top, width, height):
            raise ValueError("Provide either a window target or a full region.")
        result = ocr_region(left=int(left), top=int(top), width=int(width), height=int(height), language=language)
        origin_left = int(result["region"]["left"])
        origin_top = int(result["region"]["top"])
    matches = find_ocr_text_spans(result["words"], text=text, exact=exact)
    enriched = []
    for idx, match in enumerate(matches):
        center_x = origin_left + int(match["left"]) + int(match["width"]) // 2
        center_y = origin_top + int(match["top"]) + int(match["height"]) // 2
        enriched.append(
            {
                "found_index": idx,
                **match,
                "screen_left": origin_left + int(match["left"]),
                "screen_top": origin_top + int(match["top"]),
                "center_x": center_x,
                "center_y": center_y,
            }
        )
    return {"query": text, "exact": exact, "count": len(enriched), "matches": enriched, "region": result["region"]}


@mcp.tool()
def focused_window_text_map(
    language: str = "eng",
    padding: int = 0,
    min_confidence: float = -1.0,
) -> dict[str, Any]:
    focused = focused_window_data()
    handle = focused.get("handle")
    if not handle:
        raise ValueError("Could not determine the focused window handle.")
    result = ocr_window(handle=handle, language=language, padding=padding)
    threshold = float(min_confidence)
    words = []
    for idx, word in enumerate(result["words"]):
        confidence = float(word.get("confidence", -1.0))
        if confidence < threshold:
            continue
        words.append(
            {
                "found_index": idx,
                **word,
                "screen_left": int(result["region"]["left"]) + int(word["left"]),
                "screen_top": int(result["region"]["top"]) + int(word["top"]),
                "center_x": int(result["region"]["left"]) + int(word["left"]) + int(word["width"]) // 2,
                "center_y": int(result["region"]["top"]) + int(word["top"]) + int(word["height"]) // 2,
            }
        )
    return {
        "window": result["window"],
        "region": result["region"],
        "text": result["text"],
        "word_count": len(words),
        "words": words,
    }


@mcp.tool()
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
    result = find_ocr_text(
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
    if not result["matches"]:
        raise ValueError(f"No OCR text matched text={text!r}")
    if found_index < 0 or found_index >= len(result["matches"]):
        raise ValueError(f"found_index={found_index} is out of range for {len(result['matches'])} OCR matches.")
    match = result["matches"][found_index]
    validate_screen_point(int(match["center_x"]), int(match["center_y"]))
    pyautogui.click(x=int(match["center_x"]), y=int(match["center_y"]), button=button, clicks=clicks)
    return {
        "ok": True,
        "query": text,
        "button": button,
        "clicks": clicks,
        "match": match,
    }


@mcp.tool()
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
    from .windows import click_text

    try:
        info = click_text(
            title_regex=title_regex,
            handle=handle,
            text=text,
            found_index=found_index,
        )
        return {"source": "uia", "ok": True, "query": text, "match": info}
    except Exception:
        result = click_ocr_text(
            text=text,
            title_regex=title_regex,
            handle=handle,
            language=language,
            exact=exact,
            found_index=found_index,
            button=button,
            clicks=clicks,
        )
        result["source"] = "ocr"
        return result


__all__ = [
    "click_ocr_text",
    "click_visible_text",
    "find_ocr_text",
    "focused_window_text_map",
    "ocr_image_file",
    "ocr_region",
    "ocr_window",
]
