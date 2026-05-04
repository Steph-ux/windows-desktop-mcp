from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..paths import GOAL_ROOT
from ..runtime import record_event
from ..tool_policy import RISK_LEVELS, classify_action_risk
from .workflows import workflow_act_verify, workflow_observe


ACTIVE_GOAL_FILE = GOAL_ROOT / "active.json"
_RISK_ORDER = {name: index for index, name in enumerate(RISK_LEVELS)}


def goal_create(
    objective: str,
    success_criteria: list | str = "",
    constraints: list | str = "",
    context: dict | str = "",
    risk_max: str = "medium",
    allowed_tools: list | str = "",
    denied_tools: list | str = "",
    allowed_actions: list | str = "",
    denied_actions: list | str = "",
    observe_scope: str = "desktop",
    include_screenshot: bool = False,
    observe: bool = True,
    goal_id: str = "",
    set_active: bool = True,
) -> dict[str, Any]:
    """Create a persistent model-operated goal with policy, criteria, and initial evidence."""
    text = str(objective or "").strip()
    if not text:
        raise ValueError("objective is required.")
    resolved_risk_max = _validate_risk(risk_max)
    resolved_goal_id = _safe_goal_id(goal_id)
    now = _now()
    initial_observation = workflow_observe(scope=observe_scope, include_screenshot=include_screenshot) if observe else None
    goal = {
        "goal_id": resolved_goal_id,
        "objective": text,
        "success_criteria": _coerce_list(success_criteria, "success_criteria"),
        "constraints": _coerce_list(constraints, "constraints"),
        "context": _coerce_dict(context, "context"),
        "policy": {
            "risk_max": resolved_risk_max,
            "allowed_tools": _coerce_list(allowed_tools, "allowed_tools"),
            "denied_tools": _coerce_list(denied_tools, "denied_tools"),
            "allowed_actions": _coerce_list(allowed_actions, "allowed_actions"),
            "denied_actions": _coerce_list(denied_actions, "denied_actions"),
        },
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "initial_observation": initial_observation,
        "steps": [],
        "history": [
            {
                "type": "created",
                "at": now,
                "status": "active",
                "observation": initial_observation,
            }
        ],
    }
    _write_goal(goal)
    if set_active:
        _set_active_goal_id(resolved_goal_id)
    record_event("goal_create", goal_id=resolved_goal_id, risk_max=resolved_risk_max)
    return {
        "ok": True,
        "goal_id": resolved_goal_id,
        "active": bool(set_active),
        "goal": goal,
        "summary": _summarize(goal),
    }


def goal_status(
    goal_id: str = "",
    include_history: bool = False,
    include_observation: bool = False,
) -> dict[str, Any]:
    """Return one goal status. Defaults to the active goal."""
    goal = _load_goal(goal_id)
    summary = _summarize(goal)
    payload: dict[str, Any] = {
        "ok": True,
        "goal_id": goal["goal_id"],
        "active_goal_id": _active_goal_id(),
        "summary": summary,
        "goal": _public_goal(goal, include_history=include_history, include_observation=include_observation),
        "next": _next_instruction(goal),
    }
    return payload


def goal_list(status: str = "", limit: int = 50) -> dict[str, Any]:
    """List stored goals, newest first."""
    GOAL_ROOT.mkdir(parents=True, exist_ok=True)
    status_filter = str(status or "").strip().lower()
    goals: list[dict[str, Any]] = []
    for path in GOAL_ROOT.glob("*.json"):
        if path.name == ACTIVE_GOAL_FILE.name:
            continue
        try:
            goal = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(goal, dict) or not goal.get("goal_id"):
            continue
        if status_filter and str(goal.get("status") or "").lower() != status_filter:
            continue
        goals.append(_summarize(goal))
    goals.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    safe_limit = max(1, min(int(limit), 200))
    return {
        "ok": True,
        "active_goal_id": _active_goal_id(),
        "goals": goals[:safe_limit],
        "goal_count": len(goals[:safe_limit]),
        "total_count": len(goals),
    }


def goal_history(goal_id: str = "", limit: int = 20) -> dict[str, Any]:
    """Return recent goal history events."""
    goal = _load_goal(goal_id)
    safe_limit = max(1, min(int(limit), 200))
    history = list(goal.get("history") or [])[-safe_limit:]
    return {
        "ok": True,
        "goal_id": goal["goal_id"],
        "history": history,
        "history_count": len(history),
        "summary": _summarize(goal),
    }


def goal_step(
    goal_id: str = "",
    tool: str = "",
    target_action: str = "",
    kwargs: dict | str = "",
    preconditions: dict | str = "",
    verify: dict | str = "",
    rationale: str = "",
    expected_outcome: str = "",
    observe_scope: str = "desktop",
    include_screenshot: bool = False,
    require_confirmation_for: list[str] | None = None,
    confirmed: bool = False,
    confirmation_source: str = "",
    allowed_tools: list | str = "",
    denied_tools: list | str = "",
    allowed_actions: list | str = "",
    denied_actions: list | str = "",
    timeout_ms: int = 30000,
    mark_complete: bool = False,
    completion_note: str = "",
) -> dict[str, Any]:
    """Execute one model-selected observe -> act -> verify step and append evidence to the goal."""
    goal = _load_goal(goal_id)
    if goal.get("status") in {"paused", "complete", "failed", "cleared"}:
        return {
            "ok": False,
            "phase": "goal_status",
            "goal_id": goal["goal_id"],
            "status": goal.get("status"),
            "error": "Goal is not runnable. Resume it or create a new goal.",
            "summary": _summarize(goal),
        }
    resolved_tool = str(tool or "").strip()
    resolved_action = str(target_action or "").strip()
    if not resolved_tool or not resolved_action:
        return {
            "ok": False,
            "phase": "action",
            "goal_id": goal["goal_id"],
            "error": "tool and target_action are required for goal.step.",
            "summary": _summarize(goal),
            "next": _next_instruction(goal),
        }

    risk = classify_action_risk(resolved_tool, resolved_action)
    policy = goal.get("policy") if isinstance(goal.get("policy"), dict) else {}
    allowed = _risk_allowed(risk, str(policy.get("risk_max") or "medium"))
    if not allowed:
        action_result = {
            "ok": False,
            "blocked": True,
            "phase": "goal_policy",
            "risk": risk,
            "reason": f"Action risk {risk!r} exceeds goal risk_max {policy.get('risk_max')!r}.",
            "tool": resolved_tool,
            "action": resolved_action,
        }
    else:
        action_result = workflow_act_verify(
            tool=resolved_tool,
            target_action=resolved_action,
            kwargs=kwargs,
            preconditions=preconditions,
            verify=verify,
            observe_scope=observe_scope,
            include_screenshot=include_screenshot,
            require_confirmation_for=require_confirmation_for,
            confirmed=confirmed,
            confirmation_source=confirmation_source,
            allowed_tools=_merge_policy_lists(policy.get("allowed_tools"), allowed_tools),
            denied_tools=_merge_policy_lists(policy.get("denied_tools"), denied_tools),
            allowed_actions=_merge_policy_lists(policy.get("allowed_actions"), allowed_actions),
            denied_actions=_merge_policy_lists(policy.get("denied_actions"), denied_actions),
            timeout_ms=timeout_ms,
        )

    step = _build_step(
        goal=goal,
        tool=resolved_tool,
        target_action=resolved_action,
        risk=risk,
        action_result=action_result,
        rationale=rationale,
        expected_outcome=expected_outcome,
    )
    goal.setdefault("steps", []).append(step)
    if bool(action_result.get("blocked")):
        goal["status"] = "blocked"
    elif mark_complete and bool(action_result.get("ok")):
        goal["status"] = "complete"
        goal["completed_at"] = _now()
        goal["outcome"] = completion_note or expected_outcome or rationale
        if _active_goal_id() == goal["goal_id"]:
            _clear_active_goal_id()
    else:
        goal["status"] = "active"
    _append_history(goal, "step", {"step": step, "status": goal["status"]})
    _write_goal(goal)
    record_event(
        "goal_step",
        goal_id=goal["goal_id"],
        tool=resolved_tool,
        action=resolved_action,
        risk=risk,
        ok=step["ok"],
        blocked=step["blocked"],
    )
    return {
        "ok": step["ok"],
        "goal_id": goal["goal_id"],
        "status": goal["status"],
        "step": step,
        "action_result": action_result,
        "summary": _summarize(goal),
        "next": _next_instruction(goal),
    }


def goal_pause(goal_id: str = "", reason: str = "") -> dict[str, Any]:
    """Pause a goal without deleting its state."""
    return _set_status(goal_id=goal_id, status="paused", reason=reason)


def goal_resume(goal_id: str = "", reason: str = "") -> dict[str, Any]:
    """Resume a paused or blocked goal."""
    goal = _load_goal(goal_id)
    goal["status"] = "active"
    _set_active_goal_id(goal["goal_id"])
    _append_history(goal, "resumed", {"reason": reason})
    _write_goal(goal)
    record_event("goal_resume", goal_id=goal["goal_id"])
    return {"ok": True, "goal_id": goal["goal_id"], "summary": _summarize(goal), "goal": _public_goal(goal)}


def goal_complete(goal_id: str = "", outcome: str = "", observe_scope: str = "desktop", include_screenshot: bool = False) -> dict[str, Any]:
    """Mark a goal complete with final evidence."""
    return _finish_goal(goal_id=goal_id, status="complete", outcome=outcome, observe_scope=observe_scope, include_screenshot=include_screenshot)


def goal_fail(goal_id: str = "", outcome: str = "", observe_scope: str = "desktop", include_screenshot: bool = False) -> dict[str, Any]:
    """Mark a goal failed with final evidence."""
    return _finish_goal(goal_id=goal_id, status="failed", outcome=outcome, observe_scope=observe_scope, include_screenshot=include_screenshot)


def goal_clear(goal_id: str = "", delete: bool = False, reason: str = "") -> dict[str, Any]:
    """Clear the active goal pointer or delete a stored goal when explicitly requested."""
    goal = _load_goal(goal_id)
    resolved_id = goal["goal_id"]
    if delete:
        path = _goal_path(resolved_id)
        if path.exists():
            path.unlink()
        if _active_goal_id() == resolved_id:
            _clear_active_goal_id()
        record_event("goal_clear", goal_id=resolved_id, deleted=True)
        return {"ok": True, "goal_id": resolved_id, "deleted": True}
    goal["status"] = "cleared"
    _append_history(goal, "cleared", {"reason": reason})
    _write_goal(goal)
    if _active_goal_id() == resolved_id:
        _clear_active_goal_id()
    record_event("goal_clear", goal_id=resolved_id, deleted=False)
    return {"ok": True, "goal_id": resolved_id, "deleted": False, "summary": _summarize(goal)}


def _finish_goal(goal_id: str, status: str, outcome: str, observe_scope: str, include_screenshot: bool) -> dict[str, Any]:
    goal = _load_goal(goal_id)
    final_observation = workflow_observe(scope=observe_scope, include_screenshot=include_screenshot)
    goal["status"] = status
    goal["outcome"] = outcome
    goal["final_observation"] = final_observation
    goal["finished_at"] = _now()
    _append_history(goal, status, {"outcome": outcome, "observation": final_observation})
    _write_goal(goal)
    if _active_goal_id() == goal["goal_id"]:
        _clear_active_goal_id()
    record_event("goal_finish", goal_id=goal["goal_id"], status=status)
    return {"ok": True, "goal_id": goal["goal_id"], "summary": _summarize(goal), "goal": _public_goal(goal, include_observation=True)}


def _set_status(goal_id: str, status: str, reason: str = "") -> dict[str, Any]:
    goal = _load_goal(goal_id)
    goal["status"] = status
    _append_history(goal, status, {"reason": reason})
    _write_goal(goal)
    record_event(f"goal_{status}", goal_id=goal["goal_id"])
    return {"ok": True, "goal_id": goal["goal_id"], "summary": _summarize(goal), "goal": _public_goal(goal)}


def _build_step(
    goal: dict[str, Any],
    tool: str,
    target_action: str,
    risk: str,
    action_result: dict[str, Any],
    rationale: str,
    expected_outcome: str,
) -> dict[str, Any]:
    return {
        "index": len(goal.get("steps") or []),
        "tool": tool,
        "action": target_action,
        "risk": risk,
        "ok": bool(action_result.get("ok", False)),
        "blocked": bool(action_result.get("blocked", False)),
        "phase": action_result.get("phase"),
        "rationale": rationale,
        "expected_outcome": expected_outcome,
        "created_at": _now(),
        "evidence": _extract_evidence(action_result),
    }


def _extract_evidence(action_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": action_result.get("ok", False),
        "blocked": action_result.get("blocked", False),
        "risk": action_result.get("risk"),
        "policy": action_result.get("policy"),
        "confirmation": action_result.get("confirmation"),
        "preconditions": action_result.get("preconditions"),
        "before": action_result.get("before"),
        "result": action_result.get("result"),
        "verification": action_result.get("verification"),
        "after": action_result.get("after"),
        "reason": action_result.get("reason"),
        "error": action_result.get("error"),
    }


def _next_instruction(goal: dict[str, Any]) -> dict[str, Any]:
    status = str(goal.get("status") or "")
    if status == "complete":
        model_should = "Goal is complete. No further action is required."
    elif status == "failed":
        model_should = "Goal failed. Inspect outcome and create a new goal if needed."
    elif status == "paused":
        model_should = "Goal is paused. Resume before running goal.step."
    elif status == "blocked":
        model_should = "Goal is blocked. Inspect the last step reason, adjust policy, confirm safely, or resume with a safer action."
    else:
        model_should = "Choose exactly one next action and call goal.step, or call goal.complete when success criteria are met."
    return {
        "model_should": model_should,
        "allowed_step_statuses": ["active", "blocked"],
        "success_criteria": goal.get("success_criteria") or [],
        "constraints": goal.get("constraints") or [],
        "policy": goal.get("policy") or {},
    }


def _public_goal(goal: dict[str, Any], include_history: bool = False, include_observation: bool = False) -> dict[str, Any]:
    public = {
        "goal_id": goal.get("goal_id"),
        "objective": goal.get("objective"),
        "success_criteria": goal.get("success_criteria") or [],
        "constraints": goal.get("constraints") or [],
        "context": goal.get("context") or {},
        "policy": goal.get("policy") or {},
        "status": goal.get("status"),
        "created_at": goal.get("created_at"),
        "updated_at": goal.get("updated_at"),
        "completed_at": goal.get("completed_at"),
        "finished_at": goal.get("finished_at"),
        "outcome": goal.get("outcome"),
        "steps": goal.get("steps") or [],
    }
    if include_history:
        public["history"] = goal.get("history") or []
    if include_observation:
        public["initial_observation"] = goal.get("initial_observation")
        public["final_observation"] = goal.get("final_observation")
    return public


def _summarize(goal: dict[str, Any]) -> dict[str, Any]:
    steps = list(goal.get("steps") or [])
    return {
        "goal_id": goal.get("goal_id"),
        "objective": goal.get("objective"),
        "status": goal.get("status"),
        "risk_max": (goal.get("policy") or {}).get("risk_max"),
        "created_at": goal.get("created_at"),
        "updated_at": goal.get("updated_at"),
        "step_count": len(steps),
        "failed_steps": sum(1 for step in steps if not step.get("ok")),
        "blocked_steps": sum(1 for step in steps if step.get("blocked")),
        "last_step": steps[-1] if steps else None,
    }


def _append_history(goal: dict[str, Any], event_type: str, payload: dict[str, Any]) -> None:
    now = _now()
    goal["updated_at"] = now
    goal.setdefault("history", []).append({"type": event_type, "at": now, **payload})


def _load_goal(goal_id: str = "") -> dict[str, Any]:
    resolved_id = _resolve_goal_id(goal_id)
    path = _goal_path(resolved_id)
    if not path.exists():
        raise ValueError(f"Unknown goal: {resolved_id!r}.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid goal file: {path}")
    return data


def _write_goal(goal: dict[str, Any]) -> None:
    GOAL_ROOT.mkdir(parents=True, exist_ok=True)
    goal["updated_at"] = goal.get("updated_at") or _now()
    path = _goal_path(str(goal["goal_id"]))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(goal, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _goal_path(goal_id: str) -> Path:
    return GOAL_ROOT / f"{_safe_goal_id(goal_id)}.json"


def _resolve_goal_id(goal_id: str = "") -> str:
    resolved = str(goal_id or "").strip()
    if resolved:
        return _safe_goal_id(resolved)
    active = _active_goal_id()
    if not active:
        raise ValueError("No active goal. Pass goal_id or call goal.create first.")
    return active


def _safe_goal_id(goal_id: str = "") -> str:
    if not str(goal_id or "").strip():
        return f"goal-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(goal_id).strip()).strip("-._")
    if not cleaned:
        raise ValueError("goal_id must contain at least one alphanumeric character.")
    return cleaned[:96]


def _active_goal_id() -> str:
    try:
        payload = json.loads(ACTIVE_GOAL_FILE.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str(payload.get("goal_id") or "").strip() if isinstance(payload, dict) else ""


def _set_active_goal_id(goal_id: str) -> None:
    GOAL_ROOT.mkdir(parents=True, exist_ok=True)
    ACTIVE_GOAL_FILE.write_text(json.dumps({"goal_id": goal_id, "updated_at": _now()}, indent=2), encoding="utf-8")


def _clear_active_goal_id() -> None:
    if ACTIVE_GOAL_FILE.exists():
        ACTIVE_GOAL_FILE.unlink()


def _validate_risk(value: str) -> str:
    risk = str(value or "medium").strip().lower()
    if risk not in _RISK_ORDER:
        raise ValueError(f"risk_max must be one of: {', '.join(RISK_LEVELS)}.")
    return risk


def _risk_allowed(risk: str, risk_max: str) -> bool:
    return _RISK_ORDER.get(_validate_risk(risk), 99) <= _RISK_ORDER.get(_validate_risk(risk_max), 2)


def _merge_policy_lists(base: Any, override: Any) -> list[Any]:
    result: list[Any] = []
    for item in _coerce_list(base, "policy"):
        if item not in result:
            result.append(item)
    for item in _coerce_list(override, "policy"):
        if item not in result:
            result.append(item)
    return result


def _coerce_dict(value: dict | str, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    if isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError(f"{label} must be a JSON object.")
        return parsed
    raise ValueError(f"{label} must be a dict or JSON object string.")


def _coerce_list(value: Any, label: str) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, str):
            return [parsed]
    raise ValueError(f"{label} must be a list, JSON list, or string.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "goal_clear",
    "goal_complete",
    "goal_create",
    "goal_fail",
    "goal_history",
    "goal_list",
    "goal_pause",
    "goal_resume",
    "goal_status",
    "goal_step",
]
