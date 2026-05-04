from __future__ import annotations

import inspect
from typing import Any

from .app import mcp
from .runtime import clear_events, recent_events, runtime_health_check, runtime_status
from .tool_policy import classify_action_risk, is_host_interactive_action, strict_non_interactive_enabled


def runtime_get_status() -> dict:
    """Return consolidated runtime status for the MCP server."""
    return runtime_status()


def runtime_get_recent_events(limit: int = 25) -> dict:
    """Return recent in-memory action events recorded by the MCP server."""
    events = recent_events(limit=limit)
    return {"count": len(events), "events": events}


def runtime_clear_events() -> dict:
    """Clear the in-memory runtime event buffer."""
    return clear_events()


def runtime_healthcheck() -> dict:
    """Return a consolidated runtime health report for the MCP server."""
    return runtime_health_check()


def runtime_tool_manifest(tool: str | None = None, include_signatures: bool = True) -> dict[str, Any]:
    """Return the consolidated tool/action manifest for model-side planning."""
    from .tools.consolidated import R

    requested = (tool or "").strip()
    if requested and requested not in R:
        return {"ok": False, "error": f"Unknown tool: {requested!r}", "available_tools": sorted(R)}

    items: dict[str, Any] = {}
    selected = {requested: R[requested]} if requested else R
    for tool_name, (doc, actions) in selected.items():
        action_items: dict[str, Any] = {}
        for action_name, fn in sorted(actions.items()):
            payload: dict[str, Any] = {
                "doc": inspect.getdoc(fn) or "",
                "risk": classify_action_risk(tool_name, action_name),
                "host_interactive": is_host_interactive_action(tool_name, action_name),
                "requires_host_confirmation": (
                    strict_non_interactive_enabled()
                    and is_host_interactive_action(tool_name, action_name)
                ),
            }
            if include_signatures:
                payload["signature"] = str(inspect.signature(fn))
                payload["parameters"] = [
                    {
                        "name": name,
                        "required": param.default is inspect.Parameter.empty,
                        "default": None if param.default is inspect.Parameter.empty else repr(param.default),
                        "kind": str(param.kind).split(".")[-1],
                    }
                    for name, param in inspect.signature(fn).parameters.items()
                ]
            action_items[action_name] = payload
        items[tool_name] = {
            "doc": doc,
            "actions": action_items,
        }

    return {
        "ok": True,
        "tool_count": len(items),
        "strict_non_interactive": strict_non_interactive_enabled(),
        "tools": items,
    }
