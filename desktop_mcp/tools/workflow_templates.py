"""Pre-built workflow templates for common automation scenarios."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .workflows import WORKFLOW_DIR

# ═══ TEMPLATE DEFINITIONS ══════════════════════════════════════════

TEMPLATES: dict[str, dict[str, Any]] = {
    "scrape_page": {
        "description": "Open a URL, extract text content, and close the browser.",
        "variables": {"url": "https://example.com"},
        "steps": [
            {"tool": "browser_session", "action": "open", "url": "{{url}}", "headless": True},
            {"tool": "browser_content", "action": "text", "selector": "body"},
            {"tool": "browser_session", "action": "close", "session_id": "{{step_0.session_id}}"},
        ],
    },
    "screenshot_flow": {
        "description": "Open a URL, take a screenshot, and close.",
        "variables": {"url": "https://example.com"},
        "steps": [
            {"tool": "browser_session", "action": "open", "url": "{{url}}", "headless": True},
            {"tool": "browser_observe", "action": "capture", "session_id": "{{step_0.session_id}}"},
            {"tool": "browser_session", "action": "close", "session_id": "{{step_0.session_id}}"},
        ],
    },
    "fill_form": {
        "description": "Open a URL, fill a form field, and submit.",
        "variables": {"url": "https://example.com", "selector": "#email", "value": "test@test.com", "submit_selector": "button[type=submit]"},
        "steps": [
            {"tool": "browser_session", "action": "open", "url": "{{url}}", "headless": True},
            {"tool": "browser_interact", "action": "fill_field", "session_id": "{{step_0.session_id}}", "selector": "{{selector}}", "value": "{{value}}"},
            {"tool": "browser_interact", "action": "click", "session_id": "{{step_0.session_id}}", "selector": "{{submit_selector}}"},
            {"tool": "browser_observe", "action": "capture", "session_id": "{{step_0.session_id}}"},
            {"tool": "browser_session", "action": "close", "session_id": "{{step_0.session_id}}"},
        ],
    },
    "login_flow": {
        "description": "Login to a website with username/password.",
        "variables": {
            "url": "https://example.com/login",
            "username_selector": "#username",
            "password_selector": "#password",
            "username": "",
            "password": "",
            "submit_selector": "button[type=submit]",
        },
        "steps": [
            {"tool": "browser_session", "action": "open", "url": "{{url}}", "headless": True},
            {"tool": "browser_interact", "action": "fill_field", "session_id": "{{step_0.session_id}}", "selector": "{{username_selector}}", "value": "{{username}}"},
            {"tool": "browser_interact", "action": "fill_field", "session_id": "{{step_0.session_id}}", "selector": "{{password_selector}}", "value": "{{password}}"},
            {"tool": "browser_interact", "action": "click", "session_id": "{{step_0.session_id}}", "selector": "{{submit_selector}}"},
            {"tool": "browser_observe", "action": "wait_load", "session_id": "{{step_0.session_id}}", "state": "networkidle"},
            {"tool": "browser_observe", "action": "capture", "session_id": "{{step_0.session_id}}"},
        ],
    },
    "search_and_extract": {
        "description": "Open a search engine, type a query, and extract results.",
        "variables": {"query": "MCP automation", "search_url": "https://www.google.com"},
        "steps": [
            {"tool": "browser_session", "action": "open", "url": "{{search_url}}", "headless": True},
            {"tool": "browser_interact", "action": "fill_field", "session_id": "{{step_0.session_id}}", "selector": "textarea[name=q]", "value": "{{query}}"},
            {"tool": "browser_interact", "action": "press", "session_id": "{{step_0.session_id}}", "key": "Enter"},
            {"tool": "browser_observe", "action": "wait_load", "session_id": "{{step_0.session_id}}", "state": "networkidle"},
            {"tool": "browser_content", "action": "text", "session_id": "{{step_0.session_id}}", "selector": "#search"},
            {"tool": "browser_session", "action": "close", "session_id": "{{step_0.session_id}}"},
        ],
    },
    "desktop_screenshot_report": {
        "description": "Capture desktop, run OCR, and save results.",
        "variables": {},
        "steps": [
            {"tool": "desktop_observe", "action": "capture"},
            {"tool": "desktop_observe", "action": "ocr_smart", "prompt": "all visible text"},
            {"tool": "desktop_observe", "action": "list_monitors"},
        ],
    },
    "multi_page_scrape": {
        "description": "Open browser, visit multiple pages, extract content from each.",
        "variables": {"urls": "https://example.com,https://httpbin.org"},
        "steps": [
            {"tool": "browser_session", "action": "open", "url": "https://example.com", "headless": True},
            {"comment": "Additional pages navigated via workflow variables"},
        ],
    },
}


# ═══ TEMPLATE API ══════════════════════════════════════════════════

def template_list() -> dict:
    """List all available workflow templates."""
    return {
        "templates": [
            {
                "id": tid,
                "description": tmpl["description"],
                "variables": tmpl.get("variables", {}),
                "step_count": len(tmpl["steps"]),
            }
            for tid, tmpl in TEMPLATES.items()
        ]
    }


def template_get(template_id: str) -> dict:
    """Get a template's full definition."""
    tmpl = TEMPLATES.get(template_id)
    if not tmpl:
        return {"error": f"Template {template_id!r} not found", "available": sorted(TEMPLATES.keys())}
    return {"ok": True, "id": template_id, **tmpl}


def template_instantiate(template_id: str, variables: dict | str = "",
                         save_as: str = "") -> dict:
    """Create a workflow from a template with custom variables.

    Substitutes template variables with provided values and optionally saves.
    """
    tmpl = TEMPLATES.get(template_id)
    if not tmpl:
        return {"error": f"Template {template_id!r} not found"}

    # Merge default vars with provided overrides
    if isinstance(variables, str):
        try:
            variables = json.loads(variables) if variables else {}
        except json.JSONDecodeError as e:
            return {"error": f"Invalid variables JSON: {e}"}

    merged_vars = {**tmpl.get("variables", {}), **variables}

    # Deep-copy steps and substitute variables
    steps = json.loads(json.dumps(tmpl["steps"]))
    steps = _substitute_template_vars(steps, merged_vars)

    result = {
        "ok": True,
        "template_id": template_id,
        "variables": merged_vars,
        "steps": steps,
        "step_count": len(steps),
    }

    if save_as:
        path = WORKFLOW_DIR / f"{save_as}.json"
        path.write_text(json.dumps({"id": save_as, "template": template_id, "steps": steps}, indent=2))
        result["saved_to"] = str(path)

    return result


def _substitute_template_vars(obj: Any, variables: dict) -> Any:
    """Recursively substitute {{var}} patterns in steps."""
    if isinstance(obj, str):
        for key, val in variables.items():
            obj = obj.replace(f"{{{{{key}}}}}", str(val))
        return obj
    if isinstance(obj, list):
        return [_substitute_template_vars(item, variables) for item in obj]
    if isinstance(obj, dict):
        return {k: _substitute_template_vars(v, variables) for k, v in obj.items()}
    return obj
