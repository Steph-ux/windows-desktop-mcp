from __future__ import annotations

from .app import mcp
from .runtime import clear_events, recent_events, runtime_health_check, runtime_status


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
