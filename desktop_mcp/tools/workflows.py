"""Autonomous workflow engine — chain MCP actions into reusable sequences."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..paths import SCREENSHOT_DIR
from ..runtime import record_event
from ..tool_policy import classify_action_risk, risk_manifest

WORKFLOW_DIR = SCREENSHOT_DIR / "workflows"
WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)

_recording: dict[str, Any] | None = None
_recorded_steps: list[dict] = []


def workflow_run(steps: list[dict] | str = "", timeout_ms: int = 60000,
                 stop_on_error: bool = True) -> dict:
    """Execute a workflow: a list of action steps sequentially.

    Each step: {"tool": "browser_navigate", "action": "goto", "url": "https://..."}
    Supports variables: {{step_N.result.field}} references output of step N.
    """
    from .consolidated import R, _d

    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON steps: {e}"}

    if not steps:
        return {"error": "No steps provided"}

    results = []
    start = time.time()
    variables: dict[str, Any] = {}

    for i, step in enumerate(steps):
        if time.time() - start > timeout_ms / 1000:
            return {"error": "Workflow timeout", "completed": i, "results": results}

        tool_name = step.get("tool", "")
        action = step.get("action", "")
        kwargs = {k: v for k, v in step.items() if k not in ("tool", "action", "comment")}

        # Variable substitution
        kwargs = _substitute_vars(kwargs, variables)

        # Condition check
        condition = step.get("if_visible") or step.get("condition")
        if condition:
            if not _check_condition(condition, variables):
                results.append({"step": i, "skipped": True, "condition": condition})
                continue

        # Resolve tool actions
        tool_entry = R.get(tool_name)
        if not tool_entry:
            err = {"step": i, "error": f"Unknown tool: {tool_name!r}", "available": sorted(R.keys())}
            results.append(err)
            if stop_on_error:
                return {"error": "Step failed", "results": results}
            continue

        _doc, actions = tool_entry

        try:
            result = _d(actions, action, **kwargs)
            step_result = {"step": i, "ok": True, "result": result}
            variables[f"step_{i}"] = result
        except Exception as e:
            step_result = {"step": i, "ok": False, "error": str(e)}
            if stop_on_error:
                results.append(step_result)
                return {"error": f"Step {i} failed: {e}", "results": results}

        results.append(step_result)

    return {
        "ok": True,
        "steps_completed": len(results),
        "duration_s": round(time.time() - start, 2),
        "results": results,
    }


def workflow_record_start(workflow_id: str = "") -> dict:
    """Start recording MCP actions into a workflow."""
    global _recording, _recorded_steps
    if _recording:
        return {"error": "Already recording", "id": _recording["id"]}
    wf_id = workflow_id or f"wf_{int(time.time())}"
    _recording = {"id": wf_id, "started_at": time.time()}
    _recorded_steps = []
    return {"ok": True, "recording": True, "id": wf_id}


def workflow_record_step(tool: str, action: str, **kwargs) -> dict:
    """Add a step to the current recording."""
    if not _recording:
        return {"error": "No active recording. Call workflow_record_start first."}
    step = {"tool": tool, "action": action, **kwargs}
    _recorded_steps.append(step)
    return {"ok": True, "step_index": len(_recorded_steps) - 1, "step": step}


def workflow_record_stop(save: bool = True) -> dict:
    """Stop recording and optionally save the workflow."""
    global _recording
    if not _recording:
        return {"error": "No active recording"}

    wf_id = _recording["id"]
    result = {
        "id": wf_id,
        "steps": _recorded_steps.copy(),
        "step_count": len(_recorded_steps),
        "duration_s": round(time.time() - _recording["started_at"], 2),
    }

    if save:
        path = WORKFLOW_DIR / f"{wf_id}.json"
        path.write_text(json.dumps({"id": wf_id, "steps": _recorded_steps}, indent=2))
        result["saved_to"] = str(path)

    _recording = None
    return {"ok": True, **result}


def workflow_list() -> dict:
    """List saved workflows."""
    workflows = []
    if WORKFLOW_DIR.exists():
        for f in WORKFLOW_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                workflows.append({
                    "id": data.get("id", f.stem),
                    "step_count": len(data.get("steps", [])),
                    "path": str(f),
                })
            except Exception:
                workflows.append({"id": f.stem, "error": "invalid JSON"})
    return {"workflows": workflows}


def workflow_load(workflow_id: str) -> dict:
    """Load a saved workflow by ID."""
    path = WORKFLOW_DIR / f"{workflow_id}.json"
    if not path.exists():
        return {"error": f"Workflow {workflow_id!r} not found"}
    try:
        data = json.loads(path.read_text())
        return {"ok": True, **data}
    except Exception as e:
        return {"error": f"Failed to load: {e}"}


def workflow_delete(workflow_id: str) -> dict:
    """Delete a saved workflow."""
    path = WORKFLOW_DIR / f"{workflow_id}.json"
    if not path.exists():
        return {"error": f"Workflow {workflow_id!r} not found"}
    path.unlink()
    return {"ok": True, "deleted": workflow_id}


def workflow_observe(
    scope: str = "desktop",
    include_screenshot: bool = False,
    include_windows: bool = True,
    include_summary: bool = False,
    title_regex: str | None = None,
    handle: int | None = None,
    session_id: str | None = None,
    page_id: str | None = None,
    max_windows: int = 20,
) -> dict[str, Any]:
    """Return a structured observation for model-side planning."""
    resolved = (scope or "desktop").strip().lower()
    path = str(SCREENSHOT_DIR / f"observe-{int(time.time() * 1000)}.png") if include_screenshot else None

    if resolved == "desktop":
        from .capture import desktop_snapshot_state

        result = desktop_snapshot_state(path=path, include_windows=include_windows, max_windows=max_windows)
    elif resolved == "focused_window":
        from .windows import focused_window_summary

        result = focused_window_summary(use_ocr=include_summary)
        if include_screenshot:
            from .capture import save_window_screenshot

            result["screenshot"] = save_window_screenshot(prefix="observe-focused")
    elif resolved == "window":
        from .windows import window_summary

        result = window_summary(title_regex=title_regex, handle=handle, use_ocr=include_summary)
        if include_screenshot:
            from .capture import save_window_screenshot

            result["screenshot"] = save_window_screenshot(prefix="observe-window", title_regex=title_regex, handle=handle)
    elif resolved == "browser":
        if not session_id:
            raise ValueError("scope='browser' requires session_id.")
        from .browser_sessions import browser_get_page_summary, browser_snapshot_state

        result = {
            "summary": browser_get_page_summary(session_id=session_id, page_id=page_id),
            "state": browser_snapshot_state(session_id=session_id, page_id=page_id, path=path),
        }
    else:
        raise ValueError("Use scope='desktop', 'focused_window', 'window', or 'browser'.")

    payload = {"ok": True, "scope": resolved, "observation": result}
    record_event("workflow_observe", scope=resolved)
    return payload


def workflow_risk(tool: str = "", target_action: str = "") -> dict[str, Any]:
    """Return risk metadata for one action or the full consolidated registry."""
    from .consolidated import R

    if tool and target_action:
        return {"ok": True, "tool": tool, "action": target_action, "risk": classify_action_risk(tool, target_action)}
    return risk_manifest(R)


def workflow_act_verify(
    tool: str,
    target_action: str,
    kwargs: dict | str = "",
    preconditions: dict | str = "",
    verify: dict | str = "",
    observe_scope: str = "desktop",
    include_screenshot: bool = False,
    require_confirmation_for: list[str] | None = None,
    confirmed: bool = False,
    confirmation_source: str = "",
    allowed_tools: list[str] | str = "",
    denied_tools: list[str] | str = "",
    allowed_actions: list[str] | str = "",
    denied_actions: list[str] | str = "",
    timeout_ms: int = 30000,
) -> dict[str, Any]:
    """Run one model-chosen action with explicit preconditions and verification."""
    from .consolidated import R, _d

    kw = _coerce_dict(kwargs, "kwargs")
    checks = _coerce_dict(preconditions, "preconditions")
    verification = _coerce_dict(verify, "verify")
    risk = classify_action_risk(tool, target_action)
    policy = _evaluate_action_policy(
        tool=tool,
        target_action=target_action,
        allowed_tools=allowed_tools,
        denied_tools=denied_tools,
        allowed_actions=allowed_actions,
        denied_actions=denied_actions,
    )
    if not policy["ok"]:
        return {
            "ok": False,
            "blocked": True,
            "phase": "policy",
            "risk": risk,
            "reason": policy["reason"],
            "tool": tool,
            "action": target_action,
            "policy": policy,
        }

    blocked_levels = set(require_confirmation_for or ["high", "destructive"])
    if risk in blocked_levels and not confirmed:
        return {
            "ok": False,
            "blocked": True,
            "phase": "confirmation",
            "risk": risk,
            "reason": "confirmed=true is required for this action risk level.",
            "tool": tool,
            "action": target_action,
            "confirmation": {
                "confirmed": False,
                "source": confirmation_source or None,
                "required": True,
                "allowed_sources": ["host", "user"] if risk in {"high", "destructive"} else [],
            },
        }
    if risk in {"high", "destructive"} and confirmed and not _is_host_confirmation(confirmation_source):
        return {
            "ok": False,
            "blocked": True,
            "phase": "confirmation",
            "risk": risk,
            "reason": "confirmation_source='host' or 'user' is required for high/destructive actions.",
            "tool": tool,
            "action": target_action,
            "confirmation": {
                "confirmed": True,
                "source": confirmation_source or None,
                "required": True,
                "allowed_sources": ["host", "user"],
            },
        }

    precheck = _evaluate_conditions(checks)
    if not precheck["ok"]:
        return {"ok": False, "phase": "precondition", "risk": risk, **precheck}

    before = workflow_observe(scope=observe_scope, include_screenshot=include_screenshot)
    tool_entry = R.get(tool)
    if not tool_entry:
        return {"ok": False, "phase": "action", "error": f"Unknown tool: {tool!r}", "available": sorted(R)}
    _doc, actions = tool_entry
    result = _d(actions, target_action, timeout_ms=timeout_ms, **kw)
    after = workflow_observe(scope=observe_scope, include_screenshot=include_screenshot)
    verify_result = _evaluate_conditions(verification) if verification else {"ok": True, "checks": []}
    ok = bool(result.get("ok", True)) and verify_result["ok"]
    payload = {
        "ok": ok,
        "tool": tool,
        "action": target_action,
        "risk": risk,
        "policy": policy,
        "confirmation": {
            "confirmed": bool(confirmed),
            "source": confirmation_source or None,
            "required": risk in blocked_levels,
            "allowed_sources": ["host", "user"] if risk in {"high", "destructive"} else [],
        },
        "preconditions": precheck,
        "result": result,
        "verification": verify_result,
        "before": before,
        "after": after,
    }
    record_event("workflow_act_verify", tool=tool, action=target_action, risk=risk, ok=ok)
    return payload


def _substitute_vars(kwargs: dict, variables: dict) -> dict:
    """Replace {{step_N.field}} patterns with actual values."""
    import re
    result = {}
    for k, v in kwargs.items():
        if isinstance(v, str) and "{{" in v:
            def _replace(m):
                path = m.group(1).strip().split(".")
                val = variables
                for part in path:
                    if isinstance(val, dict):
                        val = val.get(part, m.group(0))
                    else:
                        return m.group(0)
                return str(val) if not isinstance(val, str) else val
            result[k] = re.sub(r"\{\{(.+?)\}\}", _replace, v)
        else:
            result[k] = v
    return result


def _coerce_dict(value: dict | str, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid {label} JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{label} must be a JSON object.")
        return parsed
    raise ValueError(f"{label} must be a dict or JSON object string.")


def _coerce_list(value: list[str] | str, label: str) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [part.strip() for part in value.split(",")]
        if isinstance(parsed, str):
            parsed = [parsed]
        if not isinstance(parsed, list):
            raise ValueError(f"{label} must be a JSON list or comma-separated string.")
        return [str(item).strip() for item in parsed if str(item).strip()]
    raise ValueError(f"{label} must be a list, JSON list, or comma-separated string.")


def _evaluate_action_policy(
    tool: str,
    target_action: str,
    allowed_tools: list[str] | str = "",
    denied_tools: list[str] | str = "",
    allowed_actions: list[str] | str = "",
    denied_actions: list[str] | str = "",
) -> dict[str, Any]:
    allowed_tool_items = _coerce_list(allowed_tools, "allowed_tools")
    denied_tool_items = _coerce_list(denied_tools, "denied_tools")
    allowed_action_items = _coerce_list(allowed_actions, "allowed_actions")
    denied_action_items = _coerce_list(denied_actions, "denied_actions")

    if denied_tool_items and _matches_any(tool, denied_tool_items):
        return {"ok": False, "reason": f"Tool is denied: {tool}", "matched": tool}
    denied_action = _matched_action(tool, target_action, denied_action_items)
    if denied_action:
        return {"ok": False, "reason": f"Action is denied: {denied_action}", "matched": denied_action}
    if allowed_tool_items and not _matches_any(tool, allowed_tool_items):
        return {"ok": False, "reason": f"Tool is not allowed: {tool}", "matched": None}
    if allowed_action_items and not _matched_action(tool, target_action, allowed_action_items):
        return {"ok": False, "reason": f"Action is not allowed: {tool}/{target_action}", "matched": None}

    return {
        "ok": True,
        "allowed_tools": allowed_tool_items,
        "denied_tools": denied_tool_items,
        "allowed_actions": allowed_action_items,
        "denied_actions": denied_action_items,
    }


def _matches_any(value: str, patterns: list[str]) -> bool:
    return any(pattern == "*" or pattern == value for pattern in patterns)


def _matched_action(tool: str, target_action: str, patterns: list[str]) -> str | None:
    action_key = f"{tool}/{target_action}"
    for pattern in patterns:
        normalized = pattern.replace(":", "/")
        if normalized in {"*", action_key, target_action}:
            return pattern
        if normalized.endswith("/*") and normalized[:-2] == tool:
            return pattern
    return None


def _is_host_confirmation(source: str) -> bool:
    return (source or "").strip().lower() in {"host", "user"}


def _evaluate_conditions(conditions: dict[str, Any]) -> dict[str, Any]:
    checks = []
    if not conditions:
        return {"ok": True, "checks": checks}

    from .windows import get_focused_window, wait_for_text

    focused = None
    if any(key.startswith("focused_") for key in conditions):
        focused = get_focused_window()

    if "focused_handle" in conditions:
        expected = int(conditions["focused_handle"])
        actual = int((focused or {}).get("handle") or 0)
        checks.append({"name": "focused_handle", "ok": actual == expected, "expected": expected, "actual": actual})

    if "focused_title_contains" in conditions:
        expected = str(conditions["focused_title_contains"]).lower()
        actual = str((focused or {}).get("title") or "")
        checks.append({
            "name": "focused_title_contains",
            "ok": expected in actual.lower(),
            "expected": conditions["focused_title_contains"],
            "actual": actual,
        })

    if "text_visible" in conditions:
        text = str(conditions["text_visible"])
        timeout_seconds = float(conditions.get("timeout_seconds", 3.0))
        try:
            match = wait_for_text(text=text, timeout_seconds=timeout_seconds)
            checks.append({"name": "text_visible", "ok": True, "expected": text, "match": match})
        except Exception as exc:
            checks.append({"name": "text_visible", "ok": False, "expected": text, "error": str(exc)})

    return {"ok": all(item["ok"] for item in checks), "checks": checks}


def _check_condition(condition: str, variables: dict) -> bool:
    """Simple condition checker for workflow steps."""
    # Check if a previous step result exists and is truthy
    parts = condition.split(".")
    val = variables
    for part in parts:
        if isinstance(val, dict):
            val = val.get(part)
        else:
            return False
    return bool(val)
