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


def _call_social_search(**kwargs: Any) -> dict[str, Any]:
    from .tools.social_media import social_search

    return social_search(**kwargs)


def _parse_suites(suite: str) -> list[str]:
    raw = [part.strip().lower() for part in str(suite or "quick").replace("+", ",").split(",") if part.strip()]
    if not raw:
        raw = ["quick"]
    suites: list[str] = []
    for item in raw:
        expanded = ["quick", "social", "windows"] if item == "all" else [item]
        for name in expanded:
            if name not in {"quick", "social", "windows"}:
                raise ValueError("suite must be one of: quick, social, windows, all")
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
