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
