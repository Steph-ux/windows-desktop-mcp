"""Autonomous workflow engine — chain MCP actions into reusable sequences."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..paths import SCREENSHOT_DIR

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
