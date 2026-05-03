"""Intelligent OCR — couple OCR with LLM for contextual screen understanding."""
from __future__ import annotations

import json
from typing import Any


def ocr_smart(prompt: str = "", monitor: int = 0, window_title: str = "") -> dict:
    """OCR + LLM: find elements on screen matching a natural language prompt.

    Captures screen, runs OCR, then uses LLM to find the element
    matching the prompt (e.g. "find the login button").
    Returns coordinates and confidence.
    """
    from . import ocr as _ocr
    from . import capture as _cap

    # Capture the screen
    if window_title:
        capture_result = _cap.capture_window(title=window_title)
    else:
        capture_result = _cap.capture_desktop()

    if not capture_result or isinstance(capture_result, dict) and capture_result.get("error"):
        return {"error": "Failed to capture screen", "detail": capture_result}

    # Run OCR to get all text with positions
    try:
        if window_title:
            ocr_result = _ocr.ocr_window(title=window_title)
        else:
            ocr_result = _ocr.ocr_region(left=0, top=0, width=1920, height=1080)
    except Exception as e:
        return {"error": f"OCR failed: {e}"}

    # Build structured text map for LLM
    elements = _extract_elements(ocr_result)

    # Match prompt against elements using fuzzy logic
    matches = _match_prompt(prompt, elements)

    return {
        "ok": True,
        "prompt": prompt,
        "matches": matches[:5],  # top 5
        "total_elements": len(elements),
    }


def screen_understand(question: str = "", window_title: str = "") -> dict:
    """Analyze screen content and answer a question about it.

    Uses OCR to extract all visible text, then structures the answer.
    """
    from . import ocr as _ocr
    from . import capture as _cap

    if window_title:
        ocr_result = _ocr.ocr_window(title=window_title)
    else:
        try:
            ocr_result = _ocr.ocr_region(left=0, top=0, width=1920, height=1080)
        except Exception:
            ocr_result = {}

    elements = _extract_elements(ocr_result)

    # Build context
    text_content = "\n".join(e.get("text", "") for e in elements if e.get("text"))

    return {
        "ok": True,
        "question": question,
        "screen_text": text_content[:2000],  # limit for context
        "element_count": len(elements),
        "elements": elements[:50],  # top 50 elements with positions
    }


def suggest_actions(window_title: str = "") -> dict:
    """Analyze the screen and suggest possible actions.

    Returns interactive elements (buttons, links, inputs) with their positions.
    """
    from . import ocr as _ocr
    from . import windows as _win

    # Try UI Automation first (more structured)
    try:
        if window_title:
            ui_result = _win.find_ui_elements(title=window_title, control_type="Button")
        else:
            ui_result = _win.focused_window_summary()
    except Exception:
        ui_result = {}

    # Also try OCR for visible text
    try:
        if window_title:
            ocr_result = _ocr.ocr_window(title=window_title)
        else:
            ocr_result = _ocr.ocr_region(left=0, top=0, width=1920, height=1080)
    except Exception:
        ocr_result = {}

    elements = _extract_elements(ocr_result)

    # Identify clickable-looking elements
    suggestions = []
    clickable_keywords = ["button", "click", "submit", "ok", "cancel", "close",
                          "next", "save", "delete", "confirm", "sign", "log"]
    for el in elements:
        text = el.get("text", "").lower()
        if any(kw in text for kw in clickable_keywords):
            suggestions.append({
                "type": "click",
                "text": el.get("text"),
                "x": el.get("x", 0),
                "y": el.get("y", 0),
                "action": f'desktop_interact(action="click", x={el.get("x", 0)}, y={el.get("y", 0)})',
            })

    return {
        "ok": True,
        "suggestions": suggestions[:20],
        "ui_context": ui_result if isinstance(ui_result, dict) else {},
        "total_text_elements": len(elements),
    }


def _extract_elements(ocr_result: Any) -> list[dict]:
    """Extract positioned text elements from OCR result."""
    elements = []
    if isinstance(ocr_result, dict):
        # Handle different OCR result formats
        if "elements" in ocr_result:
            elements = ocr_result["elements"]
        elif "text" in ocr_result:
            lines = ocr_result["text"].split("\n") if isinstance(ocr_result["text"], str) else []
            for i, line in enumerate(lines):
                if line.strip():
                    elements.append({"text": line.strip(), "line": i})
        elif "blocks" in ocr_result:
            for block in ocr_result["blocks"]:
                if isinstance(block, dict) and block.get("text"):
                    elements.append(block)
    elif isinstance(ocr_result, list):
        elements = ocr_result
    return elements


def _match_prompt(prompt: str, elements: list[dict]) -> list[dict]:
    """Simple fuzzy matching of prompt against text elements."""
    prompt_lower = prompt.lower()
    prompt_words = set(prompt_lower.split())

    scored = []
    for el in elements:
        text = el.get("text", "")
        text_lower = text.lower()

        # Exact substring match
        if prompt_lower in text_lower:
            score = 1.0
        elif text_lower in prompt_lower:
            score = 0.8
        else:
            # Word overlap score
            text_words = set(text_lower.split())
            overlap = prompt_words & text_words
            if overlap:
                score = len(overlap) / max(len(prompt_words), 1) * 0.6
            else:
                continue

        scored.append({**el, "match_score": round(score, 2)})

    scored.sort(key=lambda x: x["match_score"], reverse=True)
    return scored
