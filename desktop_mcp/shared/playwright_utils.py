"""Playwright-related shared helpers."""

from __future__ import annotations

import re
from typing import Any

from ..browser_core import open_exact_playwright_page, open_playwright_runtime
from ..browser_sessions import (
    apply_playwright_intercepts,
    attach_playwright_page_observers,
    cleanup_stale_playwright_sessions,
    close_playwright_session,
    get_playwright_page,
    get_playwright_session,
    refresh_playwright_pages,
    register_playwright_page,
    store_playwright_session,
    wait_for_url_pattern,
)


def ensure_dom_revision_tracker(page: Any) -> int:
    return int(
        page.evaluate(
            """() => {
                if (!window.__pmMcpDomTrackerInstalled) {
                    window.__pmMcpDomRevision = 0;
                    const target = document.documentElement || document;
                    new MutationObserver(() => {
                        window.__pmMcpDomRevision += 1;
                    }).observe(target, {
                        subtree: true,
                        childList: true,
                        attributes: true,
                        characterData: true,
                    });
                    window.__pmMcpDomTrackerInstalled = true;
                }
                return window.__pmMcpDomRevision || 0;
            }"""
        )
    )


def visual_signature(page: Any, selector: str) -> str:
    return str(
        page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (!el) return "__missing__";
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return JSON.stringify({
                    text: (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 4000),
                    html: (el.innerHTML || '').slice(0, 4000),
                    className: el.className || '',
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    display: style.display,
                    visibility: style.visibility,
                    opacity: style.opacity,
                    color: style.color,
                    backgroundColor: style.backgroundColor,
                    transform: style.transform,
                });
            }""",
            selector,
        )
    )


BLOCKED_JS_PATTERNS = [
    r"require\s*\(",
    r"\bprocess\.(env|exit|kill|spawn|exec)\b",
    r"\bchild_process\b",
    r"\bchildProcess\b",
    r"__proto__",
    r"constructor\s*\[",
]


def validate_js_expression(expr: str) -> None:
    for pattern in BLOCKED_JS_PATTERNS:
        if re.search(pattern, expr):
            raise ValueError(f"Expression JS bloquee (pattern interdit: {pattern})")


__all__ = [
    "BLOCKED_JS_PATTERNS",
    "apply_playwright_intercepts",
    "attach_playwright_page_observers",
    "cleanup_stale_playwright_sessions",
    "close_playwright_session",
    "ensure_dom_revision_tracker",
    "get_playwright_page",
    "get_playwright_session",
    "open_exact_playwright_page",
    "open_playwright_runtime",
    "refresh_playwright_pages",
    "register_playwright_page",
    "store_playwright_session",
    "validate_js_expression",
    "visual_signature",
    "wait_for_url_pattern",
]
