from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from PIL import Image as PILImage


def ocr_availability() -> dict[str, Any]:
    try:
        import pytesseract  # noqa: F401
        pytesseract_available = True
    except ImportError:
        pytesseract_available = False

    tesseract_cmd = shutil.which("tesseract")
    if not tesseract_cmd:
        for candidate in (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ):
            if Path(candidate).exists():
                tesseract_cmd = candidate
                break

    return {
        "pytesseract_available": pytesseract_available,
        "tesseract_available": bool(tesseract_cmd),
        "tesseract_executable": tesseract_cmd,
        "ocr_ready": pytesseract_available and bool(tesseract_cmd),
    }


def ocr_image_object(image: PILImage.Image, language: str = "eng") -> dict[str, Any]:
    try:
        import pytesseract
    except ImportError as exc:
        raise RuntimeError(
            "OCR requires the optional Python package 'pytesseract'. "
            "Install it and ensure the 'tesseract' executable is available."
        ) from exc

    availability = ocr_availability()
    tesseract_cmd = availability["tesseract_executable"]
    if not tesseract_cmd:
        raise RuntimeError(
            "OCR requires the 'tesseract' executable on PATH. "
            "Install Tesseract OCR for Windows first."
        )

    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    prepared = image.convert("L")
    scale_factor = 2
    prepared = prepared.resize(
        (max(1, prepared.width * scale_factor), max(1, prepared.height * scale_factor)),
        PILImage.Resampling.LANCZOS,
    )
    text = pytesseract.image_to_string(prepared, lang=language).strip()
    data = pytesseract.image_to_data(
        prepared, lang=language, output_type=pytesseract.Output.DICT
    )

    words = []
    for idx, raw_text in enumerate(data.get("text", [])):
        if not raw_text or not raw_text.strip():
            continue
        try:
            confidence = float(data["conf"][idx])
        except Exception:
            confidence = -1.0
        words.append(
            {
                "text": raw_text.strip(),
                "confidence": confidence,
                "left": int(data["left"][idx]) // scale_factor,
                "top": int(data["top"][idx]) // scale_factor,
                "width": max(1, int(data["width"][idx]) // scale_factor),
                "height": max(1, int(data["height"][idx]) // scale_factor),
            }
        )

    return {"text": text, "word_count": len(words), "words": words, "scale_factor": scale_factor}


def find_ocr_text_spans(words: list[dict[str, Any]], text: str, exact: bool = False) -> list[dict[str, Any]]:
    target = " ".join(text.strip().lower().split())
    if not target:
        raise ValueError("Provide non-empty text.")
    normalized_words = []
    for word in words:
        normalized = " ".join(str(word.get("text", "")).strip().lower().split())
        if not normalized:
            continue
        normalized_words.append((normalized, word))

    matches: list[dict[str, Any]] = []
    target_parts = target.split()
    for idx in range(len(normalized_words)):
        joined_parts: list[str] = []
        matched_words: list[dict[str, Any]] = []
        for next_idx in range(idx, min(len(normalized_words), idx + len(target_parts) + 4)):
            normalized, word = normalized_words[next_idx]
            joined_parts.append(normalized)
            matched_words.append(word)
            candidate = " ".join(joined_parts)
            if exact:
                matched = candidate == target
            else:
                matched = target in candidate
            if not matched:
                continue
            left = min(int(item["left"]) for item in matched_words)
            top = min(int(item["top"]) for item in matched_words)
            right = max(int(item["left"]) + int(item["width"]) for item in matched_words)
            bottom = max(int(item["top"]) + int(item["height"]) for item in matched_words)
            avg_conf = sum(float(item.get("confidence", -1.0)) for item in matched_words) / max(len(matched_words), 1)
            matches.append(
                {
                    "text": candidate,
                    "left": left,
                    "top": top,
                    "width": right - left,
                    "height": bottom - top,
                    "confidence": round(avg_conf, 2),
                    "word_count": len(matched_words),
                }
            )
            break
    return matches
