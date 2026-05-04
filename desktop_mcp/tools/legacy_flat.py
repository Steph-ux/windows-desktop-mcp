"""Backward-compatible flat MCP tool aliases.

The model-facing API is the consolidated super-tool manifest. These aliases keep
older MCP clients and legacy tests working without changing the new dispatcher.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any

import anyio

from ..app import mcp
from . import browser_sessions as _browser
from . import capture as _capture
from . import runtime as _runtime


_LEGACY_TOOLS: dict[str, Callable[..., Any]] = {
    "ping": _runtime.ping,
    "describe_screen": _capture.describe_screen,
    "desktop_snapshot_state": _capture.desktop_snapshot_state,
    "desktop_watch_start": _capture.desktop_watch_start,
    "desktop_watch_list": _capture.desktop_watch_list,
    "desktop_watch_get_states": _capture.desktop_watch_get_states,
    "desktop_watch_stop": _capture.desktop_watch_stop,
    "browser_open_session": _browser.browser_open_session,
    "browser_get_page_summary": _browser.browser_get_page_summary,
    "browser_list_pages": _browser.browser_list_pages,
    "browser_capture_session": _browser.browser_capture_session,
    "browser_close_session": _browser.browser_close_session,
    "browser_create_profile": _browser.browser_create_profile,
    "browser_list_profiles": _browser.browser_list_profiles,
    "browser_start_instance": _browser.browser_start_instance,
    "browser_get_instance": _browser.browser_get_instance,
    "browser_list_instances": _browser.browser_list_instances,
    "browser_stop_instance": _browser.browser_stop_instance,
    "browser_delete_instance": _browser.browser_delete_instance,
    "browser_delete_profile": _browser.browser_delete_profile,
}


def _make_legacy_tool(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    async def tool_fn(**kwargs: Any) -> Any:
        return await anyio.to_thread.run_sync(functools.partial(fn, **kwargs))

    tool_fn.__name__ = name
    tool_fn.__qualname__ = name
    tool_fn.__doc__ = fn.__doc__
    tool_fn.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
    return tool_fn


for _name, _fn in _LEGACY_TOOLS.items():
    mcp.tool(name=_name)(_make_legacy_tool(_name, _fn))


__all__ = ["_LEGACY_TOOLS"]
