"""Runtime evals for restart smoke tests and production-readiness checks."""

from __future__ import annotations

import inspect
import time
from typing import Any

from .runtime import record_event, runtime_health_check
from .tool_policy import (
    classify_action_risk,
    evaluate_host_interaction_guard,
    is_host_interactive_action,
    strict_non_interactive_enabled,
)

SOCIAL_EVAL_PLATFORMS = ("x", "youtube", "tiktok", "instagram")
DETAIL_QUALITIES = {"clean", "partial", "noisy"}
MISSION_RISK_ALIASES = {
    "read-only": "read",
    "readonly": "read",
    "safe": "read",
    "read": "read",
    "low": "low",
    "medium": "medium",
    "high": "high",
}
MISSION_DENIED_ACTIONS = (
    "browser_interact/click",
    "browser_interact/fill_field",
    "browser_interact/type",
    "desktop_interact/click",
    "desktop_interact/kb_type",
    "social_media/like",
    "social_media/comment",
    "social_media/follow",
    "social_media/publish",
    "system_ops/delete",
    "system_ops/run",
)
RISK_SCORE = {"read": 0, "low": 1, "medium": 2, "high": 3, "destructive": 4}

WINDOWS_APP_EVAL_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "notepad_unicode_focus_guard",
        "app": "notepad",
        "mode": "host-confirmed",
        "purpose": "Verify observe -> act -> verify text entry with require_handle.",
        "assertions": [
            "workflow.observe captures the Notepad window handle",
            "desktop_interact.kb_unicode uses require_handle",
            "focus mismatch blocks before typing",
            "verification reads back exact punctuation/Unicode text",
        ],
        "steps": [
            {"tool": "workflow", "action": "observe", "kwargs": {"scope": "desktop", "include_screenshot": False}},
            {
                "tool": "workflow",
                "action": "act_verify",
                "kwargs": {
                    "tool": "desktop_interact",
                    "target_action": "kb_unicode",
                    "kwargs": {"text": "UNICODE_OK: _ - ->", "require_handle": "<observed_notepad_handle>"},
                    "confirmed": True,
                    "confirmation_source": "user",
                },
            },
        ],
    },
    {
        "id": "file_explorer_read_only_observe",
        "app": "file_explorer",
        "mode": "read-only",
        "purpose": "Verify the model can observe Explorer without host input.",
        "assertions": [
            "workflow.observe returns visible windows",
            "desktop_window.ui_inspect/text_map can inspect the target only after a handle is selected",
            "no mouse/keyboard input is required",
        ],
        "steps": [
            {"tool": "workflow", "action": "observe", "kwargs": {"scope": "desktop", "include_windows": True}},
            {"tool": "desktop_window", "action": "list", "kwargs": {"visible_only": True}},
        ],
    },
    {
        "id": "calculator_focus_block",
        "app": "calculator",
        "mode": "host-confirmed",
        "purpose": "Verify Calculator input is blocked when focus changes.",
        "assertions": [
            "desktop_interact.kb_type is host_interactive",
            "strict non-interactive blocks unconfirmed key input",
            "require_handle prevents input into the wrong foreground window",
        ],
        "steps": [
            {"tool": "workflow", "action": "observe", "kwargs": {"scope": "desktop"}},
            {
                "tool": "workflow",
                "action": "act_verify",
                "kwargs": {
                    "tool": "desktop_interact",
                    "target_action": "kb_type",
                    "kwargs": {"text": "1", "require_handle": "<observed_calculator_handle>"},
                    "confirmed": True,
                    "confirmation_source": "user",
                },
            },
        ],
    },
]


def runtime_evals(
    suite: str = "quick",
    platforms: list[str] | str = "",
    query: str = "codex",
    limit: int = 3,
    detail_limit: int = 1,
    wait_ms: int = 15000,
    scroll_steps: int = 0,
    goal: str = "",
    risk_mode: str = "read-only",
    max_actions: int = 8,
) -> dict[str, Any]:
    """Run restart-friendly MCP evals and return a prod-ready/degraded/failed report."""
    started = time.monotonic()
    requested_suites = _parse_suites(suite)
    checks: list[dict[str, Any]] = []
    suite_reports: dict[str, Any] = {}

    if "quick" in requested_suites:
        suite_reports["quick"] = _run_quick_suite()
        checks.extend(suite_reports["quick"]["checks"])
    if "social" in requested_suites:
        suite_reports["social"] = _run_social_suite(
            platforms=_parse_platforms(platforms),
            query=query,
            limit=limit,
            detail_limit=detail_limit,
            wait_ms=wait_ms,
            scroll_steps=scroll_steps,
        )
        checks.extend(suite_reports["social"]["checks"])
    if "windows" in requested_suites:
        suite_reports["windows"] = _run_windows_suite()
        checks.extend(suite_reports["windows"]["checks"])
    if "mission" in requested_suites:
        suite_reports["mission"] = _run_mission_suite(
            goal=goal,
            platforms=_parse_platforms(platforms),
            query=query,
            limit=limit,
            detail_limit=detail_limit,
            risk_mode=risk_mode,
            max_actions=max_actions,
        )
        checks.extend(suite_reports["mission"]["checks"])

    summary = _summarize_checks(checks)
    status = _status_from_summary(summary)
    report = {
        "ok": status != "failed",
        "status": status,
        "suite": suite,
        "requested_suites": requested_suites,
        "summary": summary,
        "suites": suite_reports,
        "checks": checks,
        "read_only_by_default": True,
        "host_interactive_actions_require_confirmation": strict_non_interactive_enabled(),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
    record_event("runtime_evals", suite=suite, status=status, summary=summary)
    return report


def _run_quick_suite() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    health = runtime_health_check()
    _add_check(
        checks,
        "runtime.health.status",
        health.get("status") == "ok",
        severity="warning",
        evidence={"status": health.get("status"), "checks": health.get("checks", {})},
        remediation="Install a supported browser and Playwright if health is degraded.",
    )

    from .tools.consolidated import R
    from .tools.workflows import workflow_act_verify

    runtime_actions = R.get("runtime", ("", {}))[1]
    workflow_actions = R.get("workflow", ("", {}))[1]
    social_actions = R.get("social_media", ("", {}))[1]
    goal_actions = R.get("goal", ("", {}))[1]

    _add_check(checks, "manifest.runtime.evals", "evals" in runtime_actions)
    _add_check(checks, "manifest.workflow.observe", "observe" in workflow_actions)
    _add_check(checks, "manifest.workflow.act_verify", "act_verify" in workflow_actions)
    _add_check(checks, "manifest.goal.step", "step" in goal_actions)
    _add_check(checks, "manifest.social_media.detail", "detail" in social_actions)

    search_signature = str(inspect.signature(social_actions["search"])) if "search" in social_actions else ""
    detail_signature = str(inspect.signature(social_actions["detail"])) if "detail" in social_actions else ""
    _add_check(checks, "manifest.social_media.search.include_details", "include_details" in search_signature)
    _add_check(checks, "manifest.social_media.search.detail_limit", "detail_limit" in search_signature)
    _add_check(checks, "manifest.social_media.detail.browser_engine", "browser_engine" in detail_signature)

    _add_check(checks, "risk.social_media.search.read", classify_action_risk("social_media", "search") == "read")
    _add_check(checks, "risk.social_media.detail.read", classify_action_risk("social_media", "detail") == "read")
    _add_check(checks, "risk.system_ops.delete.destructive", classify_action_risk("system_ops", "delete") == "destructive")
    _add_check(checks, "risk.system_ops.run.high", classify_action_risk("system_ops", "run") == "high")

    host_guard = evaluate_host_interaction_guard("desktop_interact", "kb_unicode")
    _add_check(
        checks,
        "guard.desktop_interact.kb_unicode.blocked_without_confirmation",
        host_guard.get("ok") is False and host_guard.get("blocked") is True,
        evidence=host_guard,
    )
    _add_check(
        checks,
        "guard.browser_session.user_open.host_interactive",
        is_host_interactive_action("browser_session", "user_open") is True,
    )

    blocked = workflow_act_verify(
        tool="system_ops",
        target_action="run",
        kwargs={"command": ["cmd", "/c", "echo", "hi"]},
        confirmed=False,
    )
    _add_check(
        checks,
        "workflow.blocks_high_risk_without_confirmation",
        blocked.get("ok") is False
        and blocked.get("blocked") is True
        and blocked.get("phase") == "confirmation"
        and blocked.get("risk") == "high",
        evidence=blocked,
    )
    return {"status": _status_from_summary(_summarize_checks(checks)), "checks": checks}


def _run_social_suite(
    platforms: list[str],
    query: str,
    limit: int,
    detail_limit: int,
    wait_ms: int,
    scroll_steps: int,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    results: dict[str, Any] = {}
    safe_limit = max(1, min(int(limit), 10))
    safe_detail_limit = max(0, min(int(detail_limit), safe_limit))
    for platform in platforms:
        try:
            result = _call_social_search(
                platform=platform,
                query=query,
                limit=safe_limit,
                include_details=safe_detail_limit > 0,
                detail_limit=safe_detail_limit,
                keep_open=True,
                browser_engine="cdp",
                wait_until="domcontentloaded",
                scroll_steps=max(int(scroll_steps), 0),
            )
        except Exception as exc:
            _add_check(
                checks,
                f"social.{platform}.search.call",
                False,
                evidence={"error": str(exc), "type": type(exc).__name__},
            )
            continue

        results[platform] = _compact_social_result(result)
        items = result.get("items") if isinstance(result, dict) else []
        items = items if isinstance(items, list) else []
        browser = result.get("browser") if isinstance(result.get("browser"), dict) else {}
        details = [item.get("detail") for item in items if isinstance(item, dict) and isinstance(item.get("detail"), dict)]

        _add_check(checks, f"social.{platform}.ok", result.get("ok") is True, evidence=results[platform])
        _add_check(checks, f"social.{platform}.read_only", result.get("read_only") is True)
        _add_check(checks, f"social.{platform}.cdp_direct", result.get("cdp_direct") is True)
        _add_check(checks, f"social.{platform}.host_interactive_false", result.get("host_interactive") is False)
        _add_check(checks, f"social.{platform}.uses_host_mouse_false", browser.get("uses_host_mouse") is False)
        _add_check(checks, f"social.{platform}.uses_host_keyboard_false", browser.get("uses_host_keyboard") is False)
        _add_check(checks, f"social.{platform}.items_non_empty", len(items) > 0, severity="warning", evidence={"item_count": len(items)})
        _add_check(
            checks,
            f"social.{platform}.ranking_present",
            not items or all("rank_position" in item for item in items if isinstance(item, dict)),
            severity="warning",
        )
        if safe_detail_limit > 0:
            _add_check(checks, f"social.{platform}.detail_present", bool(details), severity="warning")
            if details:
                first_detail = details[0]
                text = str(first_detail.get("text") or first_detail.get("full_text") or "")
                _add_check(
                    checks,
                    f"social.{platform}.detail_quality_present",
                    first_detail.get("quality") in DETAIL_QUALITIES,
                    severity="warning",
                    evidence={
                        "quality": first_detail.get("quality"),
                        "quality_notes": first_detail.get("quality_notes"),
                    },
                )
                _add_check(
                    checks,
                    f"social.{platform}.detail_cdp_direct",
                    first_detail.get("cdp_direct") is True,
                    severity="warning",
                )
                if platform == "x":
                    _add_check(
                        checks,
                        "social.x.detail_not_bootstrap_or_footer",
                        "window.__INITIAL_STATE__" not in text
                        and "webpackChunk_twitter_responsive_web" not in text
                        and "Politique relative aux cookies" not in text,
                        severity="error",
                    )
    return {
        "status": _status_from_summary(_summarize_checks(checks)),
        "platforms": platforms,
        "query": query,
        "results": results,
        "checks": checks,
    }


def _run_windows_suite() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    kb_guard = evaluate_host_interaction_guard("desktop_interact", "kb_unicode")
    kb_confirmed = evaluate_host_interaction_guard(
        "desktop_interact",
        "kb_unicode",
        confirmed=True,
        confirmation_source="user",
    )
    _add_check(checks, "windows.kb_unicode.host_interactive", is_host_interactive_action("desktop_interact", "kb_unicode"))
    _add_check(checks, "windows.kb_unicode.risk_medium", classify_action_risk("desktop_interact", "kb_unicode") == "medium")
    _add_check(checks, "windows.kb_unicode.blocked_without_confirmation", kb_guard.get("blocked") is True, evidence=kb_guard)
    _add_check(checks, "windows.kb_unicode.confirmed_user_allowed", kb_confirmed.get("ok") is True, evidence=kb_confirmed)
    _add_check(checks, "windows.desktop_window.focus.host_interactive", is_host_interactive_action("desktop_window", "focus"))
    _add_check(checks, "windows.desktop_window.focus.risk_medium", classify_action_risk("desktop_window", "focus") == "medium")
    _add_check(checks, "windows.scenarios.notepad_present", _scenario_exists("notepad_unicode_focus_guard"))
    _add_check(checks, "windows.scenarios.file_explorer_present", _scenario_exists("file_explorer_read_only_observe"))
    _add_check(checks, "windows.scenarios.calculator_present", _scenario_exists("calculator_focus_block"))
    return {
        "status": _status_from_summary(_summarize_checks(checks)),
        "checks": checks,
        "scenarios": WINDOWS_APP_EVAL_SCENARIOS,
        "live_execution": {
            "enabled": False,
            "reason": "Host desktop app actions require explicit host/user confirmation and should be run as individual workflow.act_verify steps.",
        },
    }


def _run_mission_suite(
    goal: str,
    platforms: list[str],
    query: str,
    limit: int,
    detail_limit: int,
    risk_mode: str,
    max_actions: int,
) -> dict[str, Any]:
    """Validate model-plannable mission templates without executing the mission."""
    checks: list[dict[str, Any]] = []
    resolved_goal = str(goal or f"Research {query!r} across {', '.join(platforms)} and return ranked evidence.").strip()
    risk_ceiling = _mission_risk_ceiling(risk_mode)
    safe_max_actions = max(1, min(int(max_actions), 50))
    templates = _build_mission_templates(
        goal=resolved_goal,
        platforms=platforms,
        query=query,
        limit=max(1, min(int(limit), 50)),
        detail_limit=max(0, min(int(detail_limit), 10)),
    )
    selected_templates = _select_mission_templates(templates, risk_ceiling=risk_ceiling, max_actions=safe_max_actions)
    plan = _flatten_mission_plan(selected_templates)

    _add_check(checks, "mission.goal_present", bool(resolved_goal), evidence={"goal": resolved_goal})
    _add_check(checks, "mission.templates.available", len(templates) >= 5, evidence={"template_count": len(templates)})
    _add_check(checks, "mission.templates.selected_non_empty", bool(selected_templates), evidence={"selected_count": len(selected_templates)})
    _add_check(
        checks,
        "mission.actions.max_actions_respected",
        len(plan) <= safe_max_actions,
        evidence={"action_count": len(plan), "max_actions": safe_max_actions},
    )
    _add_check(
        checks,
        "mission.actions.exist_in_manifest",
        all(_mission_step_action_exists(step) for step in plan),
        evidence={"missing": [f"{step.get('tool')}/{step.get('action')}" for step in plan if not _mission_step_action_exists(step)]},
    )
    _add_check(
        checks,
        "mission.actions.within_risk_budget",
        all(_risk_allows(_mission_step_risk(step), risk_ceiling) for step in plan),
        evidence={"risk_mode": risk_mode, "risk_ceiling": risk_ceiling, "risks": sorted({_mission_step_risk(step) for step in plan})},
    )
    _add_check(
        checks,
        "mission.actions.no_host_interactive_without_confirmation",
        all(not is_host_interactive_action(str(step.get("tool")), str(step.get("action"))) or step.get("requires_host_confirmation") is True for step in plan),
    )
    _add_check(
        checks,
        "mission.sensitive_steps_marked_for_confirmation",
        all(step.get("requires_host_confirmation") is True for step in plan if step.get("sensitive_action")),
    )
    _add_check(
        checks,
        "mission.social.platform_coverage",
        all(_mission_has_social_platform(plan, platform) for platform in platforms),
        severity="warning",
        evidence={"platforms": platforms},
    )
    _add_check(
        checks,
        "mission.structured_proofs_present",
        all(step.get("proofs") for step in plan),
        evidence={"proof_fields": sorted({field for step in plan for field in step.get("proofs", [])})},
    )
    _add_check(
        checks,
        "mission.denied_actions_absent",
        not any(f"{step.get('tool')}/{step.get('action')}" in MISSION_DENIED_ACTIONS for step in plan),
        evidence={"denied_actions": list(MISSION_DENIED_ACTIONS)},
    )
    _add_check(
        checks,
        "mission.model_responsibility_declared",
        all(template.get("model_responsibility") for template in selected_templates),
    )

    return {
        "status": _status_from_summary(_summarize_checks(checks)),
        "goal": resolved_goal,
        "risk_mode": risk_mode,
        "risk_ceiling": risk_ceiling,
        "max_actions": safe_max_actions,
        "templates": templates,
        "selected_templates": selected_templates,
        "plan": plan,
        "output_contract": {
            "plan": "ordered tool/action/kwargs list with risk and proof expectations",
            "actions": "model-selected MCP calls; the eval itself is dry-run",
            "evidence": "before/result/verification/after for workflow steps, structured DOM items/details for social steps",
            "summary": "ranked findings, source URLs, risk notes, and skipped/blocked actions",
        },
        "live_execution": {
            "enabled": False,
            "reason": "Mission eval validates model-plannable templates only. Execute selected steps explicitly, using workflow.act_verify for confirmed host actions.",
        },
        "checks": checks,
    }


def _call_social_search(**kwargs: Any) -> dict[str, Any]:
    from .tools.social_media import social_search

    return social_search(**kwargs)


def _parse_suites(suite: str) -> list[str]:
    raw = [part.strip().lower() for part in str(suite or "quick").replace("+", ",").split(",") if part.strip()]
    if not raw:
        raw = ["quick"]
    suites: list[str] = []
    for item in raw:
        expanded = ["quick", "social", "windows", "mission"] if item == "all" else [item]
        for name in expanded:
            if name not in {"quick", "social", "windows", "mission"}:
                raise ValueError("suite must be one of: quick, social, windows, mission, all")
            if name not in suites:
                suites.append(name)
    return suites


def _parse_platforms(platforms: list[str] | str) -> list[str]:
    if isinstance(platforms, str):
        parsed = [part.strip().lower() for part in platforms.split(",") if part.strip()]
    else:
        parsed = [str(part).strip().lower() for part in platforms if str(part).strip()]
    values = parsed or list(SOCIAL_EVAL_PLATFORMS)
    invalid = [platform for platform in values if platform not in SOCIAL_EVAL_PLATFORMS]
    if invalid:
        raise ValueError(f"Unsupported social eval platform(s): {invalid}")
    return values


def _add_check(
    checks: list[dict[str, Any]],
    name: str,
    ok: bool,
    *,
    severity: str = "error",
    evidence: Any | None = None,
    remediation: str = "",
) -> None:
    check = {
        "name": name,
        "ok": bool(ok),
        "severity": severity,
        "status": "passed" if ok else "failed",
    }
    if evidence is not None:
        check["evidence"] = evidence
    if remediation:
        check["remediation"] = remediation
    checks.append(check)


def _summarize_checks(checks: list[dict[str, Any]]) -> dict[str, int]:
    passed = sum(1 for check in checks if check.get("ok") is True)
    failed_errors = sum(1 for check in checks if check.get("ok") is not True and check.get("severity") == "error")
    failed_warnings = sum(1 for check in checks if check.get("ok") is not True and check.get("severity") == "warning")
    return {
        "total": len(checks),
        "passed": passed,
        "failed_errors": failed_errors,
        "failed_warnings": failed_warnings,
        "failed": failed_errors + failed_warnings,
    }


def _status_from_summary(summary: dict[str, int]) -> str:
    if summary.get("failed_errors", 0) > 0:
        return "failed"
    if summary.get("failed_warnings", 0) > 0:
        return "degraded"
    return "prod-ready"


def _scenario_exists(scenario_id: str) -> bool:
    return any(item.get("id") == scenario_id for item in WINDOWS_APP_EVAL_SCENARIOS)


def _mission_risk_ceiling(risk_mode: str) -> str:
    key = str(risk_mode or "read-only").strip().lower()
    ceiling = MISSION_RISK_ALIASES.get(key)
    if not ceiling:
        raise ValueError("risk_mode must be one of: read-only, read, low, medium, high")
    return ceiling


def _risk_allows(risk: str, ceiling: str) -> bool:
    return RISK_SCORE.get(str(risk or "medium"), 2) <= RISK_SCORE.get(str(ceiling or "read"), 0)


def _build_mission_templates(
    goal: str,
    platforms: list[str],
    query: str,
    limit: int,
    detail_limit: int,
) -> list[dict[str, Any]]:
    social_search_steps = [
        {
            "id": f"search_{platform}",
            "tool": "social_media",
            "action": "search",
            "kwargs": {
                "platform": platform,
                "query": query,
                "limit": limit,
                "include_details": detail_limit > 0,
                "detail_limit": max(detail_limit, 0),
                "browser_engine": "cdp",
                "keep_open": True,
            },
            "proofs": ["items", "item_count", "rank_position", "metrics", "detail.quality", "browser.uses_host_mouse=false"],
        }
        for platform in platforms
    ]
    templates: list[dict[str, Any]] = [
        {
            "id": "cross_platform_social_scan",
            "title": "Cross-platform social scan",
            "purpose": "Search each requested social platform, rank visible results, and return source URLs plus metrics.",
            "goal": goal,
            "requires_risk": "read",
            "model_responsibility": "Choose which ranked items deserve detail and summarize findings with citations.",
            "denied_actions": list(MISSION_DENIED_ACTIONS),
            "steps": social_search_steps,
            "expected_output": ["ranked_items", "source_urls", "metrics", "detail_quality", "platform_notes"],
        },
        {
            "id": "social_detail_drilldown",
            "title": "Detail top ranked social item",
            "purpose": "Open a selected result in a temporary CDP tab and extract full visible detail without interacting.",
            "goal": goal,
            "requires_risk": "read",
            "model_responsibility": "Pick the highest-signal URL from the scan before calling detail.",
            "denied_actions": list(MISSION_DENIED_ACTIONS),
            "steps": [
                {
                    "id": "detail_top_social_item",
                    "tool": "social_media",
                    "action": "detail",
                    "kwargs": {
                        "platform": "<platform_from_ranked_item>",
                        "url": "<ranked_item_url>",
                        "browser_engine": "cdp",
                        "keep_open": True,
                        "temporary_detail_tab": True,
                    },
                    "proofs": ["text", "full_text", "metrics", "links", "quality", "cdp_direct"],
                }
            ],
            "expected_output": ["full_text", "quality", "links", "metrics"],
        },
        {
            "id": "desktop_read_only_inventory",
            "title": "Desktop read-only inventory",
            "purpose": "List visible desktop windows so the model can decide whether a host-confirmed app step is safe.",
            "goal": goal,
            "requires_risk": "read",
            "model_responsibility": "Select a target window handle before any future host-interactive action.",
            "denied_actions": list(MISSION_DENIED_ACTIONS),
            "steps": [
                {
                    "id": "list_visible_windows",
                    "tool": "desktop_window",
                    "action": "list",
                    "kwargs": {"visible_only": True},
                    "proofs": ["windows", "handle", "title", "process"],
                }
            ],
            "expected_output": ["visible_windows", "candidate_handles"],
        },
        {
            "id": "x_ranked_research",
            "title": "X ranked research",
            "purpose": "Extract and rank X posts about the query with details for the top results.",
            "goal": goal,
            "requires_risk": "read",
            "model_responsibility": "Separate exact post evidence from weak/generic platform text.",
            "denied_actions": list(MISSION_DENIED_ACTIONS),
            "steps": [
                {
                    "id": "inspect_social_manifest",
                    "tool": "runtime",
                    "action": "manifest",
                    "kwargs": {"tool": "social_media"},
                    "proofs": ["actions.search", "actions.detail", "risk=read"],
                },
                {
                    "id": "search_x_ranked",
                    "tool": "social_media",
                    "action": "search",
                    "kwargs": {
                        "platform": "x",
                        "query": query,
                        "limit": limit,
                        "include_details": detail_limit > 0,
                        "detail_limit": max(detail_limit, 0),
                        "browser_engine": "cdp",
                        "keep_open": True,
                    },
                    "proofs": ["items", "url", "metrics", "rank_position", "detail.quality"],
                },
            ],
            "expected_output": ["top_posts", "metrics", "links", "detail_quality"],
        },
        {
            "id": "youtube_video_brief",
            "title": "YouTube video brief",
            "purpose": "Find videos for the query and extract title/channel/detail text for the best candidates.",
            "goal": goal,
            "requires_risk": "read",
            "model_responsibility": "Rank videos by visible metadata and avoid playback/control clicks.",
            "denied_actions": list(MISSION_DENIED_ACTIONS),
            "steps": [
                {
                    "id": "search_youtube_ranked",
                    "tool": "social_media",
                    "action": "search",
                    "kwargs": {
                        "platform": "youtube",
                        "query": query,
                        "limit": limit,
                        "include_details": detail_limit > 0,
                        "detail_limit": max(detail_limit, 0),
                        "browser_engine": "cdp",
                        "keep_open": True,
                    },
                    "proofs": ["items", "title", "channel", "metadata", "url"],
                }
            ],
            "expected_output": ["video_candidates", "channels", "urls", "detail_quality"],
        },
        {
            "id": "host_confirmed_notepad_verify",
            "title": "Host-confirmed Notepad observe-act-verify",
            "purpose": "Demonstrate safe desktop app manipulation only after explicit host/user confirmation.",
            "goal": goal,
            "requires_risk": "medium",
            "model_responsibility": "Do not execute until the user grants confirmation and a target handle is observed.",
            "denied_actions": ["system_ops/delete", "system_ops/run"],
            "steps": [
                {
                    "id": "observe_desktop",
                    "tool": "workflow",
                    "action": "observe",
                    "kwargs": {"scope": "desktop", "include_windows": True, "include_screenshot": False},
                    "proofs": ["before", "visible_windows", "handle"],
                },
                {
                    "id": "type_with_handle_guard",
                    "tool": "workflow",
                    "action": "act_verify",
                    "kwargs": {
                        "tool": "desktop_interact",
                        "target_action": "kb_unicode",
                        "kwargs": {"text": "MISSION_VERIFY_OK", "require_handle": "<observed_target_handle>"},
                        "confirmed": "<explicit user/host confirmation required>",
                        "confirmation_source": "<user|host>",
                    },
                    "proofs": ["before", "result", "verification", "after", "ok"],
                    "sensitive_action": "desktop_interact/kb_unicode",
                    "requires_host_confirmation": True,
                },
            ],
            "expected_output": ["before", "result", "verification", "after"],
        },
        {
            "id": "operator_evidence_session",
            "title": "Operator evidence session",
            "purpose": "Wrap a model-operated task in start/step/finish records with risk and evidence.",
            "goal": goal,
            "requires_risk": "medium",
            "model_responsibility": "Choose every step; the MCP only records and guards execution.",
            "denied_actions": ["system_ops/delete", "system_ops/run"],
            "steps": [
                {
                    "id": "operator_start",
                    "tool": "operator",
                    "action": "start",
                    "kwargs": {"goal": goal, "constraints": ["read-only unless user confirms"]},
                    "proofs": ["session_id", "initial_observation"],
                },
                {
                    "id": "operator_runtime_status_step",
                    "tool": "operator",
                    "action": "step",
                    "kwargs": {"session_id": "<operator_session_id>", "tool": "runtime", "target_action": "status"},
                    "proofs": ["step", "risk", "evidence"],
                },
                {
                    "id": "operator_finish",
                    "tool": "operator",
                    "action": "finish",
                    "kwargs": {"session_id": "<operator_session_id>", "outcome": "<mission summary>"},
                    "proofs": ["summary", "final_observation"],
                },
            ],
            "expected_output": ["session", "steps", "summary"],
        },
    ]
    return templates


def _select_mission_templates(
    templates: list[dict[str, Any]],
    risk_ceiling: str,
    max_actions: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    action_count = 0
    for template in templates:
        steps = list(template.get("steps") or [])
        if not steps:
            continue
        if not all(_risk_allows(_mission_step_risk(step), risk_ceiling) for step in steps):
            continue
        if action_count + len(steps) > max_actions:
            continue
        selected.append(template)
        action_count += len(steps)
    return selected


def _flatten_mission_plan(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for template in templates:
        for step in template.get("steps") or []:
            item = dict(step)
            item["template_id"] = template.get("id")
            item["risk"] = _mission_step_risk(item)
            item["host_interactive"] = is_host_interactive_action(str(item.get("tool")), str(item.get("action")))
            plan.append(item)
    return plan


def _mission_step_risk(step: dict[str, Any]) -> str:
    return classify_action_risk(str(step.get("tool") or ""), str(step.get("action") or ""))


def _mission_step_action_exists(step: dict[str, Any]) -> bool:
    from .tools.consolidated import R

    tool = str(step.get("tool") or "")
    action = str(step.get("action") or "")
    actions = R.get(tool, ("", {}))[1]
    return action in actions


def _mission_has_social_platform(plan: list[dict[str, Any]], platform: str) -> bool:
    for step in plan:
        if step.get("tool") != "social_media":
            continue
        kwargs = step.get("kwargs") if isinstance(step.get("kwargs"), dict) else {}
        if kwargs.get("platform") == platform:
            return True
    return False


def _compact_social_result(result: dict[str, Any]) -> dict[str, Any]:
    items = result.get("items") if isinstance(result.get("items"), list) else []
    first = items[0] if items and isinstance(items[0], dict) else {}
    detail = first.get("detail") if isinstance(first.get("detail"), dict) else {}
    return {
        "ok": result.get("ok"),
        "platform": result.get("platform"),
        "item_count": len(items),
        "cdp_direct": result.get("cdp_direct"),
        "host_interactive": result.get("host_interactive"),
        "browser_uses_host_mouse": (result.get("browser") or {}).get("uses_host_mouse") if isinstance(result.get("browser"), dict) else None,
        "browser_uses_host_keyboard": (result.get("browser") or {}).get("uses_host_keyboard") if isinstance(result.get("browser"), dict) else None,
        "first_item": {
            "url": first.get("url"),
            "rank_position": first.get("rank_position"),
            "metrics": first.get("metrics"),
            "detail_quality": detail.get("quality"),
            "detail_quality_notes": detail.get("quality_notes"),
        },
    }


__all__ = ["runtime_evals", "WINDOWS_APP_EVAL_SCENARIOS", "SOCIAL_EVAL_PLATFORMS"]
