from __future__ import annotations

import os
from typing import Any


RISK_LEVELS = ("read", "low", "medium", "high", "destructive")
STRICT_NON_INTERACTIVE_ENV = "WINDOWS_DESKTOP_MCP_STRICT_NON_INTERACTIVE"
CONFIRMATION_SOURCES = ("host", "user")

_RISK_BY_TOOL_ACTION: dict[tuple[str, str], str] = {
    ("system_ops", "delete"): "destructive",
    ("system_ops", "write"): "high",
    ("system_ops", "mkdir"): "medium",
    ("system_ops", "symlink"): "high",
    ("system_ops", "decompress"): "medium",
    ("system_ops", "ps_kill"): "destructive",
    ("system_ops", "run"): "high",
    ("system_info", "env_set"): "high",
    ("system_info", "reg_write"): "high",
    ("system_info", "reg_delete"): "destructive",
    ("system_info", "svc_start"): "high",
    ("system_info", "svc_stop"): "high",
    ("system_info", "svc_restart"): "high",
    ("system_info", "shutdown"): "destructive",
    ("system_info", "restart"): "destructive",
    ("system_info", "sleep"): "high",
    ("system_info", "lock"): "high",
    ("system_info", "wallpaper"): "medium",
    ("desktop_window", "close"): "high",
    ("desktop_window", "focus"): "medium",
    ("desktop_window", "resize"): "medium",
    ("desktop_window", "minimize"): "medium",
    ("desktop_window", "maximize"): "medium",
    ("desktop_interact", "click"): "medium",
    ("desktop_interact", "double_click"): "medium",
    ("desktop_interact", "right_click"): "medium",
    ("desktop_interact", "click_text"): "medium",
    ("desktop_interact", "click_ocr"): "medium",
    ("desktop_interact", "click_ui"): "medium",
    ("desktop_interact", "click_intent"): "medium",
    ("desktop_interact", "click_visible"): "medium",
    ("desktop_interact", "kb_type"): "medium",
    ("desktop_interact", "kb_unicode"): "medium",
    ("desktop_interact", "kb_press"): "medium",
    ("desktop_interact", "kb_hotkey"): "medium",
    ("desktop_interact", "clip_set"): "medium",
    ("desktop_interact", "macro_replay"): "high",
    ("browser_session", "user_open"): "medium",
    ("browser_session", "profile_delete"): "destructive",
    ("browser_session", "delete"): "medium",
    ("browser_session", "stop"): "medium",
    ("browser_session", "stop_all"): "high",
    ("browser_session", "cookie_set"): "high",
    ("browser_interact", "click"): "medium",
    ("browser_interact", "click_text"): "medium",
    ("browser_interact", "click_interactive"): "medium",
    ("browser_interact", "click_download"): "medium",
    ("browser_interact", "click_text_download"): "medium",
    ("browser_interact", "type"): "medium",
    ("browser_interact", "press"): "medium",
    ("browser_interact", "fill_field"): "medium",
    ("browser_interact", "fill_form"): "medium",
    ("browser_interact", "toggle"): "medium",
    ("browser_interact", "upload"): "medium",
    ("browser_network", "cookie_set"): "high",
    ("browser_network", "cookie_delete"): "high",
    ("browser_network", "perm_grant"): "high",
    ("browser_network", "perm_revoke"): "medium",
    ("browser_network", "perm_clear"): "medium",
    ("browser_network", "intercept_set"): "medium",
    ("browser_network", "intercept_clear"): "medium",
    ("browser_debug", "cdp_eval"): "high",
    ("browser_content", "eval"): "high",
    ("browser_content", "frame_eval"): "high",
    ("browser_content", "frame_click"): "medium",
    ("browser_content", "frame_fill"): "medium",
    ("agent_browser", "ensure_profile"): "medium",
    ("agent_browser", "start"): "medium",
    ("agent_browser", "status"): "read",
    ("agent_browser", "stop"): "medium",
    ("social_media", "platform_url"): "read",
    ("social_media", "supported_platforms"): "read",
    ("social_media", "search"): "read",
    ("social_media", "extract"): "read",
    ("social_media", "detail"): "read",
    ("operator", "start"): "medium",
    ("operator", "step"): "medium",
    ("operator", "finish"): "medium",
    ("operator", "session"): "read",
    ("workflow", "run"): "high",
    ("workflow", "act_verify"): "medium",
    ("workflow", "delete"): "medium",
}

_HOST_INTERACTIVE_BY_TOOL_ACTION: set[tuple[str, str]] = {
    ("browser_session", "user_open"),
    ("desktop_window", "close"),
    ("desktop_window", "focus"),
    ("desktop_window", "maximize"),
    ("desktop_window", "minimize"),
    ("desktop_window", "resize"),
    ("desktop_interact", "click"),
    ("desktop_interact", "double_click"),
    ("desktop_interact", "right_click"),
    ("desktop_interact", "click_text"),
    ("desktop_interact", "click_ocr"),
    ("desktop_interact", "click_ui"),
    ("desktop_interact", "click_intent"),
    ("desktop_interact", "click_visible"),
    ("desktop_interact", "mouse_move"),
    ("desktop_interact", "mouse_drag"),
    ("desktop_interact", "mouse_scroll"),
    ("desktop_interact", "kb_type"),
    ("desktop_interact", "kb_unicode"),
    ("desktop_interact", "kb_press"),
    ("desktop_interact", "kb_hotkey"),
    ("desktop_interact", "clip_set"),
    ("desktop_interact", "macro_replay"),
}

_READ_TOOLS = {
    "browser_content",
    "browser_observe",
    "browser_debug",
    "desktop_observe",
    "desktop_monitor",
    "desktop_window",
    "runtime",
    "social_media",
}


def strict_non_interactive_enabled() -> bool:
    """Return whether host-interactive actions require explicit host/user confirmation."""
    value = os.getenv(STRICT_NON_INTERACTIVE_ENV, "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def classify_action_risk(tool: str, action: str) -> str:
    """Classify tool/action risk for model-side planning and confirmations."""
    key = ((tool or "").strip(), (action or "").strip())
    explicit = _RISK_BY_TOOL_ACTION.get(key)
    if explicit:
        return explicit
    if key[0] in _READ_TOOLS:
        return "read"
    if key[0].endswith("_observe") or key[0].endswith("_content"):
        return "read"
    if key[1].startswith(("get", "list", "read", "count", "summary", "status", "health", "manifest")):
        return "read"
    if key[1].startswith(("wait", "capture", "save", "export", "snapshot", "overview", "annotate")):
        return "low"
    return "medium"


def is_host_interactive_action(tool: str, action: str) -> bool:
    """Return whether an action can steal focus, move the cursor, type, or mutate host UI state."""
    key = ((tool or "").strip(), (action or "").strip())
    return key in _HOST_INTERACTIVE_BY_TOOL_ACTION


def is_host_confirmation(source: str) -> bool:
    return (source or "").strip().lower() in CONFIRMATION_SOURCES


def evaluate_host_interaction_guard(
    tool: str,
    action: str,
    confirmed: bool = False,
    confirmation_source: str = "",
    strict: bool | None = None,
) -> dict[str, Any]:
    """Block host-interactive actions unless strict mode is disabled or host/user confirms."""
    enabled = strict_non_interactive_enabled() if strict is None else bool(strict)
    host_interactive = is_host_interactive_action(tool, action)
    if not enabled or not host_interactive:
        return {
            "ok": True,
            "strict_non_interactive": enabled,
            "host_interactive": host_interactive,
        }
    confirmation = {
        "confirmed": bool(confirmed),
        "source": confirmation_source or None,
        "required": True,
        "allowed_sources": list(CONFIRMATION_SOURCES),
    }
    if confirmed and is_host_confirmation(confirmation_source):
        return {
            "ok": True,
            "strict_non_interactive": enabled,
            "host_interactive": True,
            "confirmation": confirmation,
        }
    return {
        "ok": False,
        "blocked": True,
        "phase": "host_interaction",
        "risk": classify_action_risk(tool, action),
        "reason": "Strict non-interactive mode blocks host UI input/focus actions without host/user confirmation.",
        "tool": tool,
        "action": action,
        "strict_non_interactive": enabled,
        "host_interactive": True,
        "confirmation": confirmation,
    }


def risk_manifest(actions_by_tool: dict[str, tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Return risk metadata for every consolidated action."""
    tools: dict[str, Any] = {}
    for tool_name, (doc, actions) in sorted(actions_by_tool.items()):
        tools[tool_name] = {
            "doc": doc,
            "actions": {
                action_name: {
                    "risk": classify_action_risk(tool_name, action_name),
                    "host_interactive": is_host_interactive_action(tool_name, action_name),
                }
                for action_name in sorted(actions)
            },
        }
    return {
        "ok": True,
        "levels": list(RISK_LEVELS),
        "strict_non_interactive": strict_non_interactive_enabled(),
        "host_confirmation_sources": list(CONFIRMATION_SOURCES),
        "tools": tools,
    }
