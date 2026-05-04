from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from ..runtime import record_event
from .workflows import workflow_act_verify, workflow_observe


_OPERATOR_SESSIONS: dict[str, dict[str, Any]] = {}


def operator_start(
    goal: str,
    context: dict | str = "",
    constraints: list | str = "",
    observe_scope: str = "desktop",
    include_screenshot: bool = False,
) -> dict[str, Any]:
    """Start a model-operated task session with an initial observation."""
    session_id = f"op-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    initial_observation = workflow_observe(scope=observe_scope, include_screenshot=include_screenshot)
    session = {
        "session_id": session_id,
        "goal": goal,
        "context": _coerce_dict(context, "context"),
        "constraints": _coerce_list(constraints, "constraints"),
        "status": "active",
        "created_at": _now(),
        "updated_at": _now(),
        "initial_observation": initial_observation,
        "steps": [],
    }
    _OPERATOR_SESSIONS[session_id] = session
    record_event("operator_start", session_id=session_id, goal=goal)
    return {"ok": True, "session_id": session_id, "session": session}


def operator_step(
    session_id: str,
    tool: str,
    target_action: str,
    kwargs: dict | str = "",
    preconditions: dict | str = "",
    verify: dict | str = "",
    rationale: str = "",
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
    """Run one operator step through workflow.act_verify and append evidence."""
    session = _OPERATOR_SESSIONS.get(session_id)
    if not session:
        return {"ok": False, "phase": "session", "error": f"Unknown operator session: {session_id!r}"}
    if session.get("status") != "active":
        return {"ok": False, "phase": "session", "error": f"Operator session is not active: {session_id!r}"}

    action_result = workflow_act_verify(
        tool=tool,
        target_action=target_action,
        kwargs=kwargs,
        preconditions=preconditions,
        verify=verify,
        observe_scope=observe_scope,
        include_screenshot=include_screenshot,
        require_confirmation_for=require_confirmation_for,
        confirmed=confirmed,
        confirmation_source=confirmation_source,
        allowed_tools=allowed_tools,
        denied_tools=denied_tools,
        allowed_actions=allowed_actions,
        denied_actions=denied_actions,
        timeout_ms=timeout_ms,
    )
    index = len(session["steps"])
    step = {
        "index": index,
        "tool": tool,
        "action": target_action,
        "risk": action_result.get("risk", "medium") if isinstance(action_result, dict) else "medium",
        "ok": bool(action_result.get("ok", False)) if isinstance(action_result, dict) else False,
        "blocked": bool(action_result.get("blocked", False)) if isinstance(action_result, dict) else False,
        "rationale": rationale,
        "created_at": _now(),
        "evidence": _extract_evidence(action_result),
    }
    session["steps"].append(step)
    session["updated_at"] = _now()
    record_event(
        "operator_step",
        session_id=session_id,
        tool=tool,
        action=target_action,
        risk=step["risk"],
        ok=step["ok"],
        blocked=step["blocked"],
    )
    return {
        "ok": step["ok"],
        "session_id": session_id,
        "step": step,
        "action_result": action_result,
        "session": session,
    }


def operator_finish(
    session_id: str,
    outcome: str = "",
    success: bool = True,
    observe_scope: str = "desktop",
    include_screenshot: bool = False,
) -> dict[str, Any]:
    """Finish an operator session with final observation and outcome summary."""
    session = _OPERATOR_SESSIONS.get(session_id)
    if not session:
        return {"ok": False, "phase": "session", "error": f"Unknown operator session: {session_id!r}"}

    final_observation = workflow_observe(scope=observe_scope, include_screenshot=include_screenshot)
    session["status"] = "completed" if success else "failed"
    session["success"] = bool(success)
    session["outcome"] = outcome
    session["final_observation"] = final_observation
    session["finished_at"] = _now()
    session["updated_at"] = _now()
    summary = _summarize(session)
    record_event("operator_finish", session_id=session_id, success=bool(success), step_count=summary["step_count"])
    return {"ok": True, "session_id": session_id, "session": session, "summary": summary}


def operator_session(session_id: str = "") -> dict[str, Any]:
    """Return one operator session or list all known sessions."""
    if session_id:
        session = _OPERATOR_SESSIONS.get(session_id)
        if not session:
            return {"ok": False, "phase": "session", "error": f"Unknown operator session: {session_id!r}"}
        return {"ok": True, "session_id": session_id, "session": session, "summary": _summarize(session)}
    sessions = list(_OPERATOR_SESSIONS.values())
    return {
        "ok": True,
        "sessions": sessions,
        "session_count": len(sessions),
    }


def _extract_evidence(action_result: Any) -> dict[str, Any]:
    if not isinstance(action_result, dict):
        return {"ok": False, "raw": action_result}
    return {
        "ok": action_result.get("ok", False),
        "blocked": action_result.get("blocked", False),
        "before": action_result.get("before"),
        "result": action_result.get("result"),
        "verification": action_result.get("verification"),
        "after": action_result.get("after"),
        "reason": action_result.get("reason"),
        "error": action_result.get("error"),
    }


def _summarize(session: dict[str, Any]) -> dict[str, Any]:
    steps = session.get("steps", [])
    return {
        "status": session.get("status"),
        "success": session.get("success"),
        "step_count": len(steps),
        "failed_steps": sum(1 for step in steps if not step.get("ok")),
        "blocked_steps": sum(1 for step in steps if step.get("blocked")),
    }


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


def _coerce_list(value: list | str, label: str) -> list[Any]:
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
