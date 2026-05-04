from __future__ import annotations

from pathlib import Path
from typing import Any

import pyautogui

from .desktop_core import focused_window_data, grab_png_bytes
from .tools.capture import _compare_image_paths, _watch_change_summary, diff_screenshots
from .tools.capture import watch_until_goal as _watch_until_goal
from .tools.input import type_text_unicode
from .tools.ocr import click_ocr_text
from .tools.runtime import run_command
from .tools.windows import focused_window_summary


def _uia_click_by_text(**kwargs) -> dict[str, Any]:
    raise ValueError("No UIA match found.")


def intent_click(intent: str, **kwargs) -> dict[str, Any]:
    query = _extract_intent_query(intent)
    try:
        return _uia_click_by_text(text=query, **kwargs)
    except Exception:
        result = click_ocr_text(text=query, **kwargs)
        return {"ok": bool(result.get("ok", True)), "source": "ocr", "query": query, "result": result}


def watch_until_goal(goal: str, max_seconds: float = 10.0, interval_seconds: float = 0.25) -> dict[str, Any]:
    if goal.lower().startswith("text:"):
        expected = goal.split(":", 1)[1].strip()
        summary = focused_window_summary()
        if expected in summary.get("uia_titles", []):
            return {"matched": True, "source": "uia", "summary": summary}
        if expected.lower() in str(summary.get("ocr_excerpt", "")).lower():
            return {"matched": True, "source": "ocr", "summary": summary}
    return _watch_until_goal(goal, max_seconds=max_seconds, interval_seconds=interval_seconds)


def _uia_annotation_items(**kwargs) -> tuple[dict[str, Any], dict[str, int], list[dict[str, Any]]]:
    return {"handle": kwargs.get("handle")}, {"left": 0, "top": 0, "width": 0, "height": 0}, []


def screen_annotate(mode: str = "window", path: str | None = None, **kwargs) -> dict[str, Any]:
    _window, region, items = _uia_annotation_items(mode=mode, **kwargs)
    png_bytes, _ = grab_png_bytes(region)
    target = Path(path) if path else Path.cwd() / "annotated.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(png_bytes)
    return {"ok": True, "path": str(target), "count": len(items), "items": items}


def type_text(text: str, interval: float = 0.01, require_handle: int | None = None) -> dict[str, Any]:
    if require_handle is not None:
        actual = focused_window_data().get("handle")
        if actual != require_handle:
            raise RuntimeError(f"Fenetre attendue {require_handle}, focus actuel {actual}.")
    pyautogui.write(text, interval=interval)
    return {"ok": True, "length": len(text)}


def _extract_intent_query(intent: str) -> str:
    text = " ".join((intent or "").strip().split())
    for marker in ("bouton ", "button "):
        if marker in text.lower():
            idx = text.lower().rfind(marker)
            return text[idx + len(marker):].strip()
    return text
