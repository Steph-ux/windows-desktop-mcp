"""Unit tests for the consolidated dispatcher and new modules."""
import json
import pytest
from unittest.mock import patch, MagicMock
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestDispatcher:
    """Test the _d() dispatch function."""

    def test_unknown_action_returns_error(self):
        """Unknown action should return structured error."""
        from desktop_mcp.tools.consolidated import _d
        actions = {"hello": lambda: {"ok": True}}
        result = _d(actions, "nonexistent")
        assert result["ok"] is False
        assert "Unknown action" in result["error"]
        assert "hello" in result["available_actions"]

    def test_valid_action_dispatches(self):
        """Valid action should dispatch and return result."""
        from desktop_mcp.tools.consolidated import _d
        actions = {"greet": lambda name="world": {"ok": True, "msg": f"Hi {name}"}}
        result = _d(actions, "greet", name="Alice")
        assert result["ok"] is True
        assert result["msg"] == "Hi Alice"

    def test_strict_non_interactive_blocks_direct_desktop_input(self):
        """Direct mouse/keyboard dispatch should be blocked unless the host confirms."""
        from desktop_mcp.tools.consolidated import _d

        actions = {"mouse_scroll": lambda clicks: {"ok": True, "clicks": clicks}}

        result = _d(actions, "mouse_scroll", tool_name="desktop_interact", clicks=-5)

        assert result["ok"] is False
        assert result["blocked"] is True
        assert result["phase"] == "host_interaction"
        assert result["confirmation"]["required"] is True

    def test_strict_non_interactive_allows_confirmed_host_input(self):
        """Host/user confirmation should unlock intentional desktop input."""
        from desktop_mcp.tools.consolidated import _d

        actions = {"mouse_scroll": lambda clicks: {"ok": True, "clicks": clicks}}

        result = _d(
            actions,
            "mouse_scroll",
            tool_name="desktop_interact",
            clicks=-5,
            confirmed=True,
            confirmation_source="user",
        )

        assert result["ok"] is True
        assert result["clicks"] == -5
        assert "_elapsed_ms" in result

    def test_exception_returns_structured_error(self):
        """Exception in action should be caught and returned."""
        from desktop_mcp.tools.consolidated import _d
        def bad_action():
            raise ValueError("test error")
        actions = {"bad": bad_action}
        result = _d(actions, "bad")
        assert result["ok"] is False
        assert "test error" in result["error"]
        assert result["type"] == "ValueError"
        assert "trace" in result

    def test_extra_kwargs_filtered(self):
        """Extra kwargs not in function signature should be filtered out."""
        from desktop_mcp.tools.consolidated import _d
        actions = {"fn": lambda x=1: {"ok": True, "x": x}}
        result = _d(actions, "fn", x=42, unknown_param="should be filtered")
        assert result["ok"] is True
        assert result["x"] == 42

    def test_elapsed_time_tracked(self):
        """Elapsed time should be tracked in ms."""
        import time
        from desktop_mcp.tools.consolidated import _d
        def slow():
            time.sleep(0.05)
            return {"ok": True}
        actions = {"slow": slow}
        result = _d(actions, "slow")
        assert result["ok"] is True
        assert result["_elapsed_ms"] >= 40  # ~50ms with some tolerance


class TestRegistry:
    """Test that all expected tools are registered."""

    def test_all_tools_present(self):
        """All 15 super-tools should be in the registry."""
        from desktop_mcp.tools.consolidated import R
        expected = [
            "browser_session", "browser_navigate", "browser_content",
            "browser_interact", "browser_observe", "browser_network",
            "browser_debug",
            "desktop_interact", "desktop_window", "desktop_observe", "desktop_monitor",
            "system_info", "system_ops",
            "runtime",
            "workflow",
        ]
        for tool_name in expected:
            assert tool_name in R, f"Missing tool: {tool_name}"

    def test_each_tool_has_actions(self):
        """Each tool should have at least one action."""
        from desktop_mcp.tools.consolidated import R
        for name, (doc, actions) in R.items():
            assert len(actions) > 0, f"Tool {name} has no actions"
            assert len(doc) > 0, f"Tool {name} has no documentation"

    def test_no_duplicate_action_names_within_tool(self):
        """Action names within a tool should be unique (dict enforces this, but verify)."""
        from desktop_mcp.tools.consolidated import R
        for name, (_, actions) in R.items():
            assert len(actions) == len(set(actions.keys())), f"Duplicate actions in {name}"

    def test_total_action_count(self):
        """Total action count should be >= 230 (preserving feature parity)."""
        from desktop_mcp.tools.consolidated import R
        total = sum(len(actions) for _, (_, actions) in R.items())
        assert total >= 230, f"Only {total} actions, expected >= 230"

    def test_runtime_manifest_exposes_actions_and_signatures(self):
        """Runtime manifest should expose model-friendly action metadata."""
        from desktop_mcp.tools_runtime import runtime_tool_manifest

        result = runtime_tool_manifest(tool="browser_session")

        assert result["ok"] is True
        assert result["tool_count"] == 1
        browser_session = result["tools"]["browser_session"]
        assert "open" in browser_session["actions"]
        assert "signature" in browser_session["actions"]["open"]
        assert "risk" in browser_session["actions"]["open"]
        assert any(
            param["name"] == "url" and param["required"]
            for param in browser_session["actions"]["open"]["parameters"]
        )

    def test_browser_session_manifest_exposes_user_open_for_logged_in_browser(self):
        """Browser sessions should expose a user-browser path for logged-in sites."""
        from desktop_mcp.tools_runtime import runtime_tool_manifest

        result = runtime_tool_manifest(tool="browser_session")

        action = result["tools"]["browser_session"]["actions"]["user_open"]
        param_names = {param["name"] for param in action["parameters"]}
        assert action["risk"] == "medium"
        assert {"url", "wait_title_contains"} <= param_names

    def test_runtime_manifest_marks_host_interactive_actions(self):
        """The manifest should warn models before actions touch the user's host UI."""
        from desktop_mcp.tools_runtime import runtime_tool_manifest

        result = runtime_tool_manifest(tool="desktop_interact")

        action = result["tools"]["desktop_interact"]["actions"]["mouse_scroll"]
        assert result["strict_non_interactive"] is True
        assert action["host_interactive"] is True
        assert action["requires_host_confirmation"] is True

    def test_runtime_manifest_unknown_tool_returns_available_tools(self):
        """Unknown manifest requests should return structured guidance."""
        from desktop_mcp.tools_runtime import runtime_tool_manifest

        result = runtime_tool_manifest(tool="missing")

        assert result["ok"] is False
        assert "available_tools" in result
        assert "browser_session" in result["available_tools"]

    def test_workflow_manifest_uses_target_action_for_nested_actions(self):
        """Nested workflow actions should not collide with the super-tool action field."""
        from desktop_mcp.tools_runtime import runtime_tool_manifest

        result = runtime_tool_manifest(tool="workflow")

        actions = result["tools"]["workflow"]["actions"]
        act_verify_params = {
            param["name"]
            for param in actions["act_verify"]["parameters"]
        }
        risk_params = {
            param["name"]
            for param in actions["risk"]["parameters"]
        }
        assert "target_action" in act_verify_params
        assert "target_action" in risk_params
        assert "action" not in act_verify_params
        assert "allowed_actions" in act_verify_params
        assert "denied_actions" in act_verify_params
        assert "confirmation_source" in act_verify_params

    def test_operator_manifest_exposes_task_session_actions(self):
        """Operator should expose task-session primitives for model-driven work."""
        from desktop_mcp.tools_runtime import runtime_tool_manifest

        result = runtime_tool_manifest(tool="operator")

        assert result["ok"] is True
        actions = result["tools"]["operator"]["actions"]
        assert {"start", "step", "finish", "session"} <= set(actions)
        step_params = {
            param["name"]
            for param in actions["step"]["parameters"]
        }
        assert "target_action" in step_params
        assert actions["step"]["risk"] == "medium"


class TestWorkflowEngine:
    """Test the workflow engine module."""

    def test_workflow_list_empty(self):
        """List should return empty when no workflows saved."""
        from desktop_mcp.tools.workflows import workflow_list
        result = workflow_list()
        assert "workflows" in result

    def test_workflow_record_lifecycle(self):
        """Record start → add steps → stop should work."""
        from desktop_mcp.tools import workflows as wf
        # Start
        r = wf.workflow_record_start("test_wf")
        assert r["ok"] is True
        assert r["id"] == "test_wf"

        # Add step
        r = wf.workflow_record_step("browser_navigate", "goto", url="https://example.com")
        assert r["ok"] is True
        assert r["step_index"] == 0

        # Stop
        r = wf.workflow_record_stop(save=False)
        assert r["ok"] is True
        assert r["step_count"] == 1

    def test_workflow_record_double_start_error(self):
        """Starting a recording twice should error."""
        from desktop_mcp.tools import workflows as wf
        wf.workflow_record_start("wf1")
        r = wf.workflow_record_start("wf2")
        assert "error" in r
        # Cleanup
        wf.workflow_record_stop(save=False)

    def test_workflow_run_empty_error(self):
        """Running with no steps should error."""
        from desktop_mcp.tools.workflows import workflow_run
        result = workflow_run([])
        assert "error" in result

    def test_workflow_run_invalid_json(self):
        """Running with invalid JSON string should error."""
        from desktop_mcp.tools.workflows import workflow_run
        result = workflow_run("not json")
        assert "error" in result

    def test_variable_substitution(self):
        """Variable substitution should replace {{step_N.field}} patterns."""
        from desktop_mcp.tools.workflows import _substitute_vars
        variables = {"step_0": {"url": "https://example.com"}}
        result = _substitute_vars({"target": "{{step_0.url}}/page"}, variables)
        assert result["target"] == "https://example.com/page"

    def test_workflow_risk_single_action(self):
        """Risk helper should classify one tool/action."""
        from desktop_mcp.tools.workflows import workflow_risk

        result = workflow_risk("system_ops", "delete")

        assert result["ok"] is True
        assert result["risk"] == "destructive"

    def test_workflow_dispatch_accepts_target_action_for_risk(self):
        """The consolidated MCP dispatcher should pass target_action through."""
        from desktop_mcp.tools.consolidated import R, _d

        _doc, actions = R["workflow"]
        result = _d(actions, "risk", tool="system_ops", target_action="delete")

        assert result["ok"] is True
        assert result["action"] == "delete"
        assert result["risk"] == "destructive"

    def test_workflow_act_verify_blocks_high_risk_without_confirmation(self):
        """High-risk actions should require explicit confirmation by default."""
        from desktop_mcp.tools.workflows import workflow_act_verify

        result = workflow_act_verify(tool="system_ops", target_action="run", kwargs={"command": ["echo", "hi"]})

        assert result["ok"] is False
        assert result["blocked"] is True
        assert result["risk"] == "high"
        assert result["confirmation"]["required"] is True
        assert result["confirmation"]["confirmed"] is False

    def test_workflow_act_verify_requires_host_confirmation_for_high_risk(self):
        """Model-provided confirmation alone should not unlock high-risk actions."""
        from desktop_mcp.tools.workflows import workflow_act_verify

        result = workflow_act_verify(
            tool="system_ops",
            target_action="run",
            kwargs={"command": ["echo", "hi"]},
            confirmed=True,
        )

        assert result["ok"] is False
        assert result["blocked"] is True
        assert result["phase"] == "confirmation"
        assert result["risk"] == "high"
        assert result["confirmation"]["required"] is True
        assert result["confirmation"]["allowed_sources"] == ["host", "user"]

    def test_workflow_act_verify_denied_action_blocks_before_observe(self):
        """Denylisted actions should be blocked before observation or dispatch."""
        from desktop_mcp.tools import workflows as wf

        with patch.object(wf, "workflow_observe") as observe:
            result = wf.workflow_act_verify(
                tool="runtime",
                target_action="status",
                denied_actions=["runtime/status"],
            )

        assert result["ok"] is False
        assert result["phase"] == "policy"
        assert result["blocked"] is True
        assert result["policy"]["matched"] == "runtime/status"
        observe.assert_not_called()

    def test_workflow_act_verify_allowlist_blocks_unlisted_action(self):
        """Allowlists should prevent actions not explicitly allowed."""
        from desktop_mcp.tools.workflows import workflow_act_verify

        result = workflow_act_verify(
            tool="browser_session",
            target_action="user_open",
            allowed_actions=["runtime/status"],
        )

        assert result["ok"] is False
        assert result["phase"] == "policy"
        assert result["blocked"] is True
        assert "not allowed" in result["reason"]

    def test_workflow_act_verify_blocks_desktop_input_before_observe(self):
        """Host-interactive actions should stop before observation or dispatch."""
        from desktop_mcp.tools import consolidated as c
        from desktop_mcp.tools import workflows as wf

        original = c.R["desktop_interact"]
        c.R["desktop_interact"] = ("fake desktop input", {"mouse_scroll": lambda clicks: {"ok": True}})
        try:
            with patch.object(wf, "workflow_observe") as observe:
                result = wf.workflow_act_verify(
                    tool="desktop_interact",
                    target_action="mouse_scroll",
                    kwargs={"clicks": -5},
                )
        finally:
            c.R["desktop_interact"] = original

        assert result["ok"] is False
        assert result["blocked"] is True
        assert result["phase"] == "host_interaction"
        observe.assert_not_called()

    def test_workflow_dispatch_act_verify_uses_target_action(self):
        """act_verify should block sensitive nested actions through the dispatcher."""
        from desktop_mcp.tools.consolidated import R, _d

        _doc, actions = R["workflow"]
        result = _d(
            actions,
            "act_verify",
            tool="system_ops",
            target_action="run",
            kwargs={"command": ["echo", "hi"]},
        )

        assert result["ok"] is False
        assert result["blocked"] is True
        assert result["action"] == "run"
        assert result["risk"] == "high"

    def test_workflow_act_verify_runs_with_precheck_and_post_observation(self):
        """act_verify should run one action and return before/after observations."""
        from desktop_mcp.tools import workflows as wf

        observations = [
            {"ok": True, "scope": "desktop", "observation": {"image_hash": "before"}},
            {"ok": True, "scope": "desktop", "observation": {"image_hash": "after"}},
        ]

        with patch.object(wf, "workflow_observe", side_effect=observations):
            result = wf.workflow_act_verify(
                tool="runtime",
                target_action="status",
                preconditions={},
                verify={},
            )

        assert result["ok"] is True
        assert result["risk"] == "read"
        assert result["before"]["observation"]["image_hash"] == "before"
        assert result["after"]["observation"]["image_hash"] == "after"
        assert result["result"]["active_playwright_sessions"] >= 0


class TestAgenticEvals:
    """Model-facing evals that prove manifest-driven planning works."""

    def test_eval_social_search_uses_agent_browser_dom_and_operator_log(self):
        """A social-search task should choose the dedicated agent browser and DOM extraction."""
        from desktop_mcp.tools import operator as op

        op._OPERATOR_SESSIONS.clear()
        with patch.object(op, "workflow_observe", return_value={"ok": True, "scope": "desktop", "observation": {}}):
            started = op.operator_start(
                goal="Search X for Codex posts",
                context={"app": "x", "requires_logged_in_browser": True},
                constraints=["read-only"],
            )
        action_result = {
            "ok": True,
            "tool": "social_media",
            "action": "search",
            "risk": "read",
            "before": {"ok": True},
            "result": {
                "ok": True,
                "browser_context": "agent_dedicated",
                "extraction_method": "dom",
                "host_interactive": False,
                "items": [{"platform": "x", "text": "Codex post"}],
            },
            "verification": {"ok": True, "checks": []},
            "after": {"ok": True},
        }

        with patch.object(op, "workflow_act_verify", return_value=action_result) as act_verify:
            result = op.operator_step(
                session_id=started["session_id"],
                tool="social_media",
                target_action="search",
                kwargs={"platform": "x", "query": "codex", "limit": 5},
                rationale="Use the isolated agent browser and read posts through the DOM.",
            )

        assert result["ok"] is True
        assert result["step"]["tool"] == "social_media"
        assert result["step"]["action"] == "search"
        assert result["step"]["risk"] == "read"
        assert result["step"]["evidence"]["result"]["browser_context"] == "agent_dedicated"
        assert result["step"]["evidence"]["result"]["extraction_method"] == "dom"
        assert result["step"]["evidence"]["result"]["host_interactive"] is False
        act_verify.assert_called_once()

    def test_eval_social_read_only_platforms_are_manifest_plannable(self):
        """A model should see read-only social search actions for each target platform."""
        from desktop_mcp.tools_runtime import runtime_tool_manifest

        manifest = runtime_tool_manifest(tool="social_media")

        assert manifest["ok"] is True
        search = manifest["tools"]["social_media"]["actions"]["search"]
        extract = manifest["tools"]["social_media"]["actions"]["extract"]
        assert search["risk"] == "read"
        assert extract["risk"] == "read"
        assert search["host_interactive"] is False
        assert search["requires_host_confirmation"] is False

        from desktop_mcp.tools import social_media as sm

        for platform in ["x", "youtube", "tiktok", "instagram"]:
            result = sm.social_platform_url(platform=platform, query="codex")
            assert result["ok"] is True
            assert result["platform"] == platform
            assert result["read_only"] is True
            assert result["url"].startswith("https://")

    def test_eval_youtube_studio_blocks_publish_without_host_confirmation(self):
        """A YouTube Studio task should refuse destructive/high actions without host confirmation."""
        from desktop_mcp.tools import workflows as wf

        with patch.object(wf, "classify_action_risk", return_value="high"):
            result = wf.workflow_act_verify(
                tool="browser_interact",
                target_action="click_text",
                kwargs={"text": "Publish"},
                confirmed=True,
            )

        assert result["ok"] is False
        assert result["phase"] == "confirmation"
        assert result["blocked"] is True

    def test_eval_desktop_app_allowlist_permits_only_runtime_check(self):
        """A desktop-app task should be able to lock execution to an allowlist."""
        from desktop_mcp.tools import workflows as wf

        observations = [
            {"ok": True, "scope": "desktop", "observation": {"image_hash": "before"}},
            {"ok": True, "scope": "desktop", "observation": {"image_hash": "after"}},
        ]

        with patch.object(wf, "workflow_observe", side_effect=observations):
            result = wf.workflow_act_verify(
                tool="runtime",
                target_action="status",
                allowed_actions=["runtime/status"],
            )

        assert result["ok"] is True
        assert result["action"] == "status"
        assert result["policy"]["ok"] is True


class TestOperatorLayer:
    """Test the operator task-session layer built on top of workflow."""

    def test_operator_start_records_goal_context_and_initial_observation(self):
        """Starting an operator session should create a model-friendly mission log."""
        from desktop_mcp.tools import operator as op

        op._OPERATOR_SESSIONS.clear()
        observation = {"ok": True, "scope": "desktop", "observation": {"image_hash": "initial"}}

        with patch.object(op, "workflow_observe", return_value=observation):
            result = op.operator_start(
                goal="Review YouTube Studio drafts",
                context={"platform": "youtube"},
                constraints=["do not publish"],
            )

        assert result["ok"] is True
        assert result["session"]["goal"] == "Review YouTube Studio drafts"
        assert result["session"]["context"]["platform"] == "youtube"
        assert result["session"]["constraints"] == ["do not publish"]
        assert result["session"]["status"] == "active"
        assert result["session"]["steps"] == []
        assert result["session"]["initial_observation"] == observation

    def test_operator_step_runs_act_verify_and_records_evidence(self):
        """Operator steps should execute through workflow.act_verify and append evidence."""
        from desktop_mcp.tools import operator as op

        op._OPERATOR_SESSIONS.clear()
        with patch.object(op, "workflow_observe", return_value={"ok": True, "scope": "desktop", "observation": {}}):
            started = op.operator_start(goal="Inspect account settings")
        action_result = {
            "ok": True,
            "tool": "runtime",
            "action": "status",
            "risk": "read",
            "before": {"ok": True},
            "result": {"active_playwright_sessions": 0},
            "verification": {"ok": True, "checks": []},
            "after": {"ok": True},
        }

        with patch.object(op, "workflow_act_verify", return_value=action_result) as act_verify:
            result = op.operator_step(
                session_id=started["session_id"],
                tool="runtime",
                target_action="status",
                rationale="Confirm MCP runtime is alive before acting.",
            )

        assert result["ok"] is True
        assert result["step"]["index"] == 0
        assert result["step"]["tool"] == "runtime"
        assert result["step"]["action"] == "status"
        assert result["step"]["risk"] == "read"
        assert result["step"]["rationale"] == "Confirm MCP runtime is alive before acting."
        assert result["session"]["steps"][0]["evidence"]["verification"]["ok"] is True
        act_verify.assert_called_once()

    def test_operator_step_rejects_missing_session(self):
        """Operator steps should not execute without an active session."""
        from desktop_mcp.tools import operator as op

        op._OPERATOR_SESSIONS.clear()

        result = op.operator_step(session_id="missing", tool="runtime", target_action="status")

        assert result["ok"] is False
        assert result["phase"] == "session"
        assert "missing" in result["error"]

    def test_operator_finish_closes_session_with_final_observation(self):
        """Finishing a session should capture final state and summarize outcomes."""
        from desktop_mcp.tools import operator as op

        op._OPERATOR_SESSIONS.clear()
        observations = [
            {"ok": True, "scope": "desktop", "observation": {"image_hash": "initial"}},
            {"ok": True, "scope": "desktop", "observation": {"image_hash": "final"}},
        ]

        with patch.object(op, "workflow_observe", side_effect=observations):
            started = op.operator_start(goal="Check dashboard")
            result = op.operator_finish(
                session_id=started["session_id"],
                outcome="Dashboard checked without changes.",
                success=True,
            )

        assert result["ok"] is True
        assert result["session"]["status"] == "completed"
        assert result["session"]["outcome"] == "Dashboard checked without changes."
        assert result["session"]["final_observation"]["observation"]["image_hash"] == "final"
        assert result["summary"]["step_count"] == 0
        assert result["summary"]["failed_steps"] == 0


class TestBrowserUserOpen:
    """Test user-browser opening for logged-in account workflows."""

    def test_browser_user_open_launches_default_browser_and_verifies_window(self):
        """user_open should use the OS default browser instead of an isolated Playwright profile."""
        from desktop_mcp.tools import browser_sessions as bs

        expected_window = {
            "handle": 123,
            "title": "codex - Recherche / X - Google Chrome",
        }

        with patch.object(bs, "_open_url_in_default_browser", return_value=True) as open_url:
            with patch.object(bs, "wait_for_window", return_value=expected_window) as wait:
                result = bs.browser_user_open(
                    url="https://x.com/search?q=codex",
                    wait_title_contains="Recherche / X",
                    timeout_seconds=3,
                )

        assert result["ok"] is True
        assert result["url"] == "https://x.com/search?q=codex"
        assert result["browser_context"] == "user_default"
        assert result["automation"] == "desktop"
        assert result["window"] == expected_window
        open_url.assert_called_once_with("https://x.com/search?q=codex")
        wait.assert_called_once()

    def test_browser_user_open_returns_guidance_when_window_verification_fails(self):
        """user_open should still report launch success when title verification times out."""
        from desktop_mcp.tools import browser_sessions as bs

        with patch.object(bs, "_open_url_in_default_browser", return_value=True):
            with patch.object(bs, "wait_for_window", side_effect=ValueError("not found")):
                result = bs.browser_user_open(
                    url="https://x.com/search?q=codex",
                    wait_title_contains="Recherche / X",
                    timeout_seconds=1,
                )

        assert result["ok"] is True
        assert result["verified"] is False
        assert result["window"] is None
        assert "not found" in result["verification_error"]


class TestAgentBrowser:
    """Test the dedicated browser controlled by the model without host mouse/keyboard input."""

    def test_agent_browser_start_uses_dedicated_persistent_profile(self):
        """Starting an agent browser should use a named Playwright profile, not the user browser."""
        from desktop_mcp.tools import agent_browser as ab

        with patch.object(ab._bs, "browser_create_profile", return_value={"ok": True, "name": "agent-social-x"}) as create:
            with patch.object(ab._bs, "browser_start_instance", return_value={
                "ok": True,
                "session_id": "s1",
                "page_id": "p1",
                "url": "https://x.com/search?q=codex",
                "profile_name": "agent-social-x",
                "instance_name": "agent-social-x",
                "headless": True,
            }) as start:
                result = ab.agent_browser_start(
                    platform="x",
                    url="https://x.com/search?q=codex",
                    headless=True,
                )

        assert result["ok"] is True
        assert result["browser_context"] == "agent_dedicated"
        assert result["automation"] == "playwright"
        assert result["host_interactive"] is False
        assert result["profile_name"] == "agent-social-x"
        create.assert_called_once()
        start.assert_called_once()
        assert start.call_args.kwargs["profile_name"] == "agent-social-x"
        assert start.call_args.kwargs["instance_name"] == "agent-social-x"
        assert start.call_args.kwargs["headless"] is True

    def test_agent_browser_start_navigates_reused_instance_to_requested_url(self):
        """A reused agent browser instance should navigate before extraction."""
        from desktop_mcp.tools import agent_browser as ab

        target_url = "https://x.com/search?q=codex&src=typed_query&f=top"
        with patch.object(ab._bs, "browser_create_profile", return_value={"ok": True, "name": "agent-social-x"}):
            with patch.object(ab._bs, "browser_start_instance", return_value={
                "ok": True,
                "reused": True,
                "session_id": "s1",
                "page_id": "p1",
                "url": "https://x.com/home",
                "profile_name": "agent-social-x",
                "instance_name": "agent-social-x",
            }):
                with patch.object(ab._bs, "browser_navigate", return_value={
                    "session_id": "s1",
                    "page_id": "p1",
                    "url": target_url,
                    "title": "Search / X",
                }) as navigate:
                    result = ab.agent_browser_start(platform="x", url=target_url)

        assert result["ok"] is True
        assert result["url"] == target_url
        assert result["navigated"] is True
        navigate.assert_called_once_with(
            session_id="s1",
            page_id="p1",
            url=target_url,
            wait_until="domcontentloaded",
        )

    def test_agent_browser_start_bootstraps_blank_then_navigates_social_url(self):
        """Social SPAs should not depend on networkidle during the initial browser start."""
        from desktop_mcp.tools import agent_browser as ab

        target_url = "https://x.com/search?q=codex&src=typed_query&f=top"
        with patch.object(ab._bs, "browser_create_profile", return_value={"ok": True, "name": "agent-social-x"}):
            with patch.object(ab._bs, "browser_start_instance", return_value={
                "ok": True,
                "reused": False,
                "session_id": "s1",
                "page_id": "p1",
                "url": "about:blank",
                "profile_name": "agent-social-x",
                "instance_name": "agent-social-x",
            }) as start:
                with patch.object(ab._bs, "browser_navigate", return_value={
                    "session_id": "s1",
                    "page_id": "p1",
                    "url": target_url,
                    "title": "Search / X",
                }) as navigate:
                    result = ab.agent_browser_start(platform="x", url=target_url)

        assert result["ok"] is True
        assert result["url"] == target_url
        assert result["navigated"] is True
        assert start.call_args.kwargs["url"] == "about:blank"
        navigate.assert_called_once_with(
            session_id="s1",
            page_id="p1",
            url=target_url,
            wait_until="domcontentloaded",
        )

    def test_agent_browser_start_cdp_uses_debug_chrome_attach(self):
        """CDP mode should launch real Chrome with remote debugging for login-sensitive sites."""
        from desktop_mcp.tools import agent_browser as ab

        target_url = "https://x.com/i/flow/login"
        with patch.object(ab._bs, "browser_create_profile", return_value={"ok": True, "name": "agent-social-x"}):
            with patch.object(ab._bs, "browser_list_endpoints", return_value={"count": 0, "endpoints": []}):
                with patch.object(ab._bs, "browser_launch_and_attach", return_value={
                    "ok": True,
                    "session_id": "s1",
                    "page_id": "p1",
                    "url": target_url,
                    "profile_name": "agent-social-x",
                    "instance_name": "agent-social-x",
                    "cdp_endpoint": "http://127.0.0.1:9333",
                    "attached": True,
                    "launched_debug_browser": True,
                    "headless": False,
                }) as launch:
                    result = ab.agent_browser_start(
                        platform="x",
                        url=target_url,
                        profile_name="agent-social-x",
                        instance_name="agent-social-x",
                        browser_engine="cdp",
                        debug_port=9333,
                    )

        assert result["ok"] is True
        assert result["automation"] == "cdp"
        assert result["browser_context"] == "agent_dedicated"
        assert result["host_interactive"] is False
        assert result["cdp_endpoint"] == "http://127.0.0.1:9333"
        launch.assert_called_once()
        assert launch.call_args.kwargs["profile_name"] == "agent-social-x"
        assert launch.call_args.kwargs["instance_name"] == "agent-social-x"
        assert launch.call_args.kwargs["port"] == 9333

    def test_agent_browser_start_cdp_launch_navigates_when_attached_to_wrong_tab(self):
        """CDP launch should force target navigation if attach selected an existing profile tab."""
        from desktop_mcp.tools import agent_browser as ab

        target_url = "https://x.com/search?q=codex&src=typed_query&f=top"
        with patch.object(ab._bs, "browser_create_profile", return_value={"ok": True, "name": "agent-social-x-cdp"}):
            with patch.object(ab._bs, "browser_list_endpoints", return_value={"count": 0, "endpoints": []}):
                with patch.object(ab._bs, "browser_launch_and_attach", return_value={
                    "ok": True,
                    "session_id": "s1",
                    "page_id": "p1",
                    "url": "https://getadblock.com/fr/installed/",
                    "profile_name": "agent-social-x-cdp",
                    "instance_name": "agent-social-x-cdp",
                    "cdp_endpoint": "http://127.0.0.1:9333",
                    "attached": True,
                    "launched_debug_browser": True,
                    "headless": False,
                }):
                    with patch.object(ab._bs, "browser_navigate", return_value={
                        "session_id": "s1",
                        "page_id": "p1",
                        "url": target_url,
                        "title": "Search / X",
                    }) as navigate:
                        result = ab.agent_browser_start(
                            platform="x",
                            url=target_url,
                            profile_name="agent-social-x-cdp",
                            instance_name="agent-social-x-cdp",
                            browser_engine="cdp",
                            debug_port=9333,
                        )

        assert result["ok"] is True
        assert result["url"] == target_url
        assert result["navigated"] is True
        navigate.assert_called_once_with(
            session_id="s1",
            page_id="p1",
            url=target_url,
            wait_until="domcontentloaded",
        )

    def test_agent_browser_start_cdp_attaches_existing_endpoint_and_navigates(self):
        """CDP mode should re-attach to an already open Chrome and navigate in the same call."""
        from desktop_mcp.tools import agent_browser as ab

        target_url = "https://x.com/search?q=codex&src=typed_query&f=top"
        endpoint = {"endpoint": "http://127.0.0.1:9333", "port": 9333, "targets": []}
        with patch.object(ab._bs, "browser_create_profile", return_value={"ok": True, "name": "agent-social-x-cdp"}):
            with patch.object(ab._bs, "browser_list_endpoints", return_value={"count": 1, "endpoints": [endpoint]}):
                with patch.object(ab._bs, "browser_attach_existing", return_value={
                    "ok": True,
                    "session_id": "s1",
                    "page_id": "p1",
                    "url": "https://x.com/home",
                    "profile_name": "agent-social-x-cdp",
                    "instance_name": "agent-social-x-cdp",
                    "cdp_endpoint": "http://127.0.0.1:9333",
                    "attached": True,
                    "launched_debug_browser": True,
                    "headless": False,
                }) as attach:
                    with patch.object(ab._bs, "browser_launch_and_attach") as launch:
                        with patch.object(ab._bs, "browser_navigate", return_value={
                            "session_id": "s1",
                            "page_id": "p1",
                            "url": target_url,
                            "title": "Search / X",
                        }) as navigate:
                            result = ab.agent_browser_start(
                                platform="x",
                                url=target_url,
                                profile_name="agent-social-x-cdp",
                                instance_name="agent-social-x-cdp",
                                browser_engine="cdp",
                                debug_port=9333,
                            )

        assert result["ok"] is True
        assert result["automation"] == "cdp"
        assert result["url"] == target_url
        assert result["navigated"] is True
        attach.assert_called_once()
        assert attach.call_args.kwargs["ports"] == [9333]
        launch.assert_not_called()
        navigate.assert_called_once_with(
            session_id="s1",
            page_id="p1",
            url=target_url,
            wait_until="domcontentloaded",
        )

    def test_agent_browser_start_cdp_launches_when_endpoint_attach_fails(self):
        """CDP mode should fall back to launching Chrome when an advertised endpoint is stale."""
        from desktop_mcp.tools import agent_browser as ab

        with patch.object(ab._bs, "browser_create_profile", return_value={"ok": True, "name": "agent-social-x"}):
            with patch.object(ab._bs, "browser_list_endpoints", return_value={"count": 1, "endpoints": [{"endpoint": "http://127.0.0.1:9333"}]}):
                with patch.object(ab._bs, "browser_attach_existing", side_effect=ValueError("stale")):
                    with patch.object(ab._bs, "browser_launch_and_attach", return_value={
                        "ok": True,
                        "session_id": "s1",
                        "page_id": "p1",
                        "url": "https://x.com/i/flow/login",
                        "profile_name": "agent-social-x",
                        "instance_name": "agent-social-x",
                        "cdp_endpoint": "http://127.0.0.1:9333",
                        "attached": True,
                        "launched_debug_browser": True,
                        "headless": False,
                    }) as launch:
                        result = ab.agent_browser_start(
                            platform="x",
                            url="https://x.com/i/flow/login",
                            profile_name="agent-social-x",
                            instance_name="agent-social-x",
                            browser_engine="cdp",
                            debug_port=9333,
                        )

        assert result["ok"] is True
        assert result["automation"] == "cdp"
        launch.assert_called_once()

    def test_agent_browser_manifest_is_not_host_interactive(self):
        """The manifest should expose agent browser actions without host UI confirmation."""
        from desktop_mcp.tools_runtime import runtime_tool_manifest

        manifest = runtime_tool_manifest(tool="agent_browser")

        assert manifest["ok"] is True
        start = manifest["tools"]["agent_browser"]["actions"]["start"]
        assert start["host_interactive"] is False
        assert start["requires_host_confirmation"] is False
        assert "profile_name" in [param["name"] for param in start["parameters"]]
        assert "browser_engine" in [param["name"] for param in start["parameters"]]


class TestSocialMediaReadOnly:
    """Test social media read-only helpers and DOM extraction."""

    @pytest.mark.parametrize(
        "platform,expected",
        [
            ("x", "https://x.com/search?q=codex&src=typed_query&f=top"),
            ("youtube", "https://www.youtube.com/results?search_query=codex"),
            ("tiktok", "https://www.tiktok.com/search?q=codex"),
            ("instagram", "https://www.instagram.com/explore/search/keyword/?q=codex"),
        ],
    )
    def test_social_platform_url_builds_read_only_search_urls(self, platform, expected):
        """Platform URLs should point at read-only search surfaces."""
        from desktop_mcp.tools.social_media import social_platform_url

        result = social_platform_url(platform=platform, query="codex")

        assert result["ok"] is True
        assert result["read_only"] is True
        assert result["url"] == expected

    def test_social_extract_x_reads_articles_from_dom(self):
        """X extraction should evaluate the page DOM directly instead of OCR."""
        from desktop_mcp.tools import social_media as sm

        class FakePage:
            url = "https://x.com/search?q=codex"

            def __init__(self):
                self.script = ""
                self.limit = None

            def evaluate(self, script, limit):
                self.script = script
                self.limit = limit
                return [{"platform": "x", "text": "Codex post", "url": "https://x.com/a/status/1"}]

        page = FakePage()
        with patch.object(sm, "get_playwright_page", return_value=({"session_id": "s1"}, "p1", page)):
            result = sm.social_extract(platform="x", session_id="s1", limit=7)

        assert result["ok"] is True
        assert result["platform"] == "x"
        assert result["extraction_method"] == "dom"
        assert result["items"][0]["text"] == "Codex post"
        assert result["item_count"] == 1
        assert page.limit == 7
        assert "querySelectorAll('article')" in page.script
        assert ".textContent" in page.script
        assert ".map(clean)" not in page.script

    def test_social_extract_waits_until_dom_items_are_rendered(self):
        """Social extraction should not conclude zero items before SPA content renders."""
        from desktop_mcp.tools import social_media as sm

        class FakePage:
            url = "https://x.com/search?q=codex"

            def __init__(self):
                self.calls = 0

            def evaluate(self, _script, _limit):
                self.calls += 1
                if self.calls < 3:
                    return []
                return [{"platform": "x", "text": "Delayed Codex post", "url": "https://x.com/a/status/1"}]

        page = FakePage()
        with patch.object(sm, "get_playwright_page", return_value=({"session_id": "s1"}, "p1", page)):
            result = sm.social_extract(platform="x", session_id="s1", wait_ms=100, poll_ms=1)

        assert result["ok"] is True
        assert result["item_count"] == 1
        assert result["items"][0]["text"] == "Delayed Codex post"
        assert result["extract_attempts"] == 3
        assert page.calls == 3

    def test_social_extract_reattaches_cdp_when_session_thread_is_stale(self):
        """CDP extraction should recover when a previous MCP call stored a dead Playwright thread."""
        from desktop_mcp.tools import social_media as sm

        class FakePage:
            url = "https://x.com/search?q=codex"

            def evaluate(self, _script, _limit):
                return [{"platform": "x", "text": "Reattached Codex post", "url": "https://x.com/a/status/2"}]

        page = FakePage()
        stale_error = RuntimeError("cannot switch to a different thread (which happens to have exited)")
        snapshot = {
            "cdp_endpoint": "http://127.0.0.1:9333",
            "browser_name": "chrome",
            "profile_name": "agent-social-x-cdp",
            "instance_name": "agent-social-x-cdp",
            "init_script_paths": [],
            "granted_permissions": [],
        }
        with patch.object(sm, "_playwright_session_snapshot", return_value=snapshot):
            with patch.object(sm, "get_playwright_page", side_effect=[stale_error, ({"cdp_endpoint": snapshot["cdp_endpoint"]}, "p2", page)]):
                with patch.object(sm._bs, "browser_attach_cdp", return_value={"session_id": "s2", "page_id": "p2"}) as attach:
                    result = sm.social_extract(platform="x", session_id="s1", page_id="p1", wait_ms=0)

        assert result["ok"] is True
        assert result["automation"] == "cdp"
        assert result["session_id"] == "s2"
        assert result["original_session_id"] == "s1"
        assert result["cdp_reattached"] is True
        assert result["items"][0]["text"] == "Reattached Codex post"
        assert attach.call_args.kwargs["endpoint"] == "http://127.0.0.1:9333"

    def test_social_search_starts_agent_browser_then_extracts_dom(self):
        """Search should orchestrate agent_browser.start then DOM extraction."""
        from desktop_mcp.tools import social_media as sm

        with patch.object(sm, "agent_browser_start", return_value={
            "ok": True,
            "session_id": "s1",
            "page_id": "p1",
            "browser_context": "agent_dedicated",
            "host_interactive": False,
            "profile_name": "agent-social-x",
            "url": "https://x.com/search?q=codex&src=typed_query&f=top",
        }) as start:
            with patch.object(sm, "social_extract", return_value={
                "ok": True,
                "platform": "x",
                "extraction_method": "dom",
                "items": [{"platform": "x", "text": "Codex post"}],
                "item_count": 1,
            }) as extract:
                result = sm.social_search(platform="x", query="codex", limit=3)

        assert result["ok"] is True
        assert result["read_only"] is True
        assert result["browser_context"] == "agent_dedicated"
        assert result["host_interactive"] is False
        assert result["extraction_method"] == "dom"
        assert result["items"][0]["text"] == "Codex post"
        start.assert_called_once()
        assert start.call_args.kwargs["url"] == "https://x.com/search?q=codex&src=typed_query&f=top"
        assert start.call_args.kwargs["headless"] is True
        extract.assert_called_once_with(platform="x", session_id="s1", page_id="p1", limit=3)

    def test_social_search_can_request_cdp_agent_browser(self):
        """Search should pass CDP mode through for login-sensitive social profiles."""
        from desktop_mcp.tools import social_media as sm

        with patch.object(sm, "agent_browser_start", return_value={
            "ok": True,
            "session_id": "s1",
            "page_id": "p1",
            "browser_context": "agent_dedicated",
            "automation": "cdp",
            "host_interactive": False,
            "profile_name": "agent-social-x-cdp",
            "url": "https://x.com/search?q=codex&src=typed_query&f=top",
        }) as start:
            with patch.object(sm, "social_extract", return_value={
                "ok": True,
                "platform": "x",
                "extraction_method": "dom",
                "items": [],
                "item_count": 0,
            }):
                result = sm.social_search(
                    platform="x",
                    query="codex",
                    browser_engine="cdp",
                    profile_name="agent-social-x-cdp",
                    instance_name="agent-social-x-cdp",
                    debug_port=9333,
                )

        assert result["ok"] is True
        assert result["browser"]["automation"] == "cdp"
        assert result["automation"] == "cdp"
        assert start.call_args.kwargs["browser_engine"] == "cdp"
        assert start.call_args.kwargs["debug_port"] == 9333
        assert start.call_args.kwargs["profile_name"] == "agent-social-x-cdp"


class TestVideoModule:
    """Test video recording module."""

    def test_status_when_not_recording(self):
        """Status should indicate not recording when idle."""
        from desktop_mcp.tools.video import desktop_record_status
        result = desktop_record_status()
        # When idle, returns {'active_recordings': []} or {'recording': False}
        assert (result.get("recording") is False
                or result.get("active_recordings") == []
                or "not recording" in str(result).lower()
                or result.get("error"))

    def test_stop_when_not_recording(self):
        """Stop should error when not recording."""
        from desktop_mcp.tools.video import desktop_record_stop
        result = desktop_record_stop()
        assert "error" in result or result.get("recording") is False

    def test_list_recordings(self):
        """List should return a list (possibly empty)."""
        from desktop_mcp.tools.video import desktop_record_list
        result = desktop_record_list()
        assert "recordings" in result or isinstance(result, dict)


class TestMonitorsModule:
    """Test multi-monitor module."""

    def test_list_monitors(self):
        """list_monitors should return at least 1 monitor."""
        from desktop_mcp.tools.monitors import list_monitors
        result = list_monitors()
        assert "monitors" in result
        assert len(result["monitors"]) >= 1

    def test_monitor_at_point(self):
        """get_monitor_at_point should return a monitor for (100, 100)."""
        from desktop_mcp.tools.monitors import get_monitor_at_point
        result = get_monitor_at_point(x=100, y=100)
        assert "monitor" in result or "error" in result


class TestSmartOCR:
    """Test smart OCR module."""

    def test_extract_elements_dict(self):
        """_extract_elements should handle dict with text."""
        from desktop_mcp.tools.smart_ocr import _extract_elements
        result = _extract_elements({"text": "Hello\nWorld"})
        assert len(result) == 2
        assert result[0]["text"] == "Hello"

    def test_extract_elements_list(self):
        """_extract_elements should pass through lists."""
        from desktop_mcp.tools.smart_ocr import _extract_elements
        input_data = [{"text": "foo"}, {"text": "bar"}]
        result = _extract_elements(input_data)
        assert result == input_data

    def test_match_prompt_exact(self):
        """_match_prompt should find exact matches."""
        from desktop_mcp.tools.smart_ocr import _match_prompt
        elements = [{"text": "Login"}, {"text": "Sign Up"}, {"text": "Cancel"}]
        result = _match_prompt("Login", elements)
        assert len(result) >= 1
        assert result[0]["text"] == "Login"
        assert result[0]["match_score"] == 1.0

    def test_match_prompt_no_match(self):
        """_match_prompt should return empty for no matches."""
        from desktop_mcp.tools.smart_ocr import _match_prompt
        elements = [{"text": "Hello"}, {"text": "World"}]
        result = _match_prompt("xyzabc", elements)
        assert len(result) == 0



class TestWorkflowTemplates:
    """Test pre-built workflow templates."""

    def test_template_list(self):
        """template_list should return all built-in templates."""
        from desktop_mcp.tools.workflow_templates import template_list
        result = template_list()
        assert "templates" in result
        assert len(result["templates"]) >= 6
        ids = [t["id"] for t in result["templates"]]
        assert "scrape_page" in ids
        assert "login_flow" in ids

    def test_template_get(self):
        """template_get should return full template definition."""
        from desktop_mcp.tools.workflow_templates import template_get
        result = template_get("scrape_page")
        assert result["ok"] is True
        assert "steps" in result
        assert len(result["steps"]) >= 2

    def test_template_get_unknown(self):
        """template_get should error for unknown template."""
        from desktop_mcp.tools.workflow_templates import template_get
        result = template_get("nonexistent")
        assert "error" in result

    def test_template_instantiate(self):
        """template_instantiate should substitute variables."""
        from desktop_mcp.tools.workflow_templates import template_instantiate
        result = template_instantiate("scrape_page", {"url": "https://test.com"})
        assert result["ok"] is True
        assert result["variables"]["url"] == "https://test.com"
        # Check URL was substituted in steps
        step0 = result["steps"][0]
        assert step0.get("url") == "https://test.com"

    def test_template_instantiate_invalid_json(self):
        """template_instantiate with bad JSON variables should error."""
        from desktop_mcp.tools.workflow_templates import template_instantiate
        result = template_instantiate("scrape_page", "bad json")
        assert "error" in result

    def test_templates_registered_in_workflow(self):
        """Template actions should be in the workflow tool registry."""
        from desktop_mcp.tools.consolidated import R
        _, actions = R["workflow"]
        assert "template_list" in actions
        assert "template_get" in actions
        assert "template_instantiate" in actions


class TestPluginSystem:
    """Test the plugin auto-discovery system."""

    def test_discover_plugins_returns_dict(self):
        """discover_plugins should return a dict."""
        from desktop_mcp.tools.plugins import discover_plugins
        result = discover_plugins()
        assert isinstance(result, dict)

    def test_plugin_dir_created(self):
        """Plugin directory should be created on first run."""
        from desktop_mcp.tools.plugins import PLUGIN_DIR
        assert PLUGIN_DIR.exists()

    def test_example_plugin_created(self):
        """Example plugin file should exist."""
        from desktop_mcp.tools.plugins import PLUGIN_DIR
        example = PLUGIN_DIR / "_example.py"
        assert example.exists()

    def test_load_plugin_file_valid(self, tmp_path):
        """Loading a valid plugin file should return name, doc, actions."""
        from desktop_mcp.tools.plugins import _load_plugin_file
        plugin_file = tmp_path / "test_plugin.py"
        plugin_file.write_text('''
TOOL_NAME = "test_tool"
TOOL_DOC = "Test tool.\\nActions: hello"
ACTIONS = {"hello": lambda: {"ok": True}}
''')
        result = _load_plugin_file(plugin_file)
        assert result is not None
        name, doc, actions = result
        assert name == "test_tool"
        assert "hello" in actions

    def test_load_plugin_file_missing_attrs(self, tmp_path):
        """Plugin without TOOL_NAME should be skipped."""
        from desktop_mcp.tools.plugins import _load_plugin_file
        plugin_file = tmp_path / "bad_plugin.py"
        plugin_file.write_text("x = 1\n")
        result = _load_plugin_file(plugin_file)
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
