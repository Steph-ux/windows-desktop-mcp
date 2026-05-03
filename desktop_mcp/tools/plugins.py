"""Plugin system — load custom tools from ~/.pm/desktop-mcp/plugins/.

Each plugin is a single .py file that exposes:
  TOOL_NAME: str          — unique tool name (e.g. "my_custom_tool")
  TOOL_DOC: str           — description shown to MCP clients
  ACTIONS: dict[str, Callable]  — action_name -> function mapping

Example plugin (~/.pm/desktop-mcp/plugins/hello.py):

    TOOL_NAME = "hello"
    TOOL_DOC = "A simple greeting tool.\\nActions: greet, farewell"
    ACTIONS = {
        "greet": lambda name="World": {"message": f"Hello, {name}!"},
        "farewell": lambda name="World": {"message": f"Goodbye, {name}!"},
    }
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("desktop-mcp.plugins")

PLUGIN_DIR = Path.home() / ".pm" / "desktop-mcp" / "plugins"


def discover_plugins() -> dict[str, tuple[str, dict[str, Any]]]:
    """Scan plugin directory and return {name: (doc, actions)} for each valid plugin."""
    plugins: dict[str, tuple[str, dict[str, Any]]] = {}

    if not PLUGIN_DIR.exists():
        PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        # Create example plugin
        _write_example_plugin()
        return plugins

    for py_file in sorted(PLUGIN_DIR.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            plugin = _load_plugin_file(py_file)
            if plugin:
                name, doc, actions = plugin
                plugins[name] = (doc, actions)
                logger.info("Loaded plugin: %s (%d actions)", name, len(actions))
        except Exception as e:
            logger.warning("Failed to load plugin %s: %s", py_file.name, e)

    return plugins


def _load_plugin_file(path: Path) -> tuple[str, str, dict] | None:
    """Import a single plugin file and extract TOOL_NAME, TOOL_DOC, ACTIONS."""
    module_name = f"desktop_mcp_plugin_{path.stem}"

    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    tool_name = getattr(module, "TOOL_NAME", None)
    tool_doc = getattr(module, "TOOL_DOC", None)
    actions = getattr(module, "ACTIONS", None)

    if not tool_name or not isinstance(actions, dict):
        logger.warning("Plugin %s missing TOOL_NAME or ACTIONS dict, skipping.", path.name)
        return None

    doc = tool_doc or f"Custom plugin: {tool_name}"
    return tool_name, doc, actions


def _write_example_plugin():
    """Write a commented-out example plugin for users to reference."""
    example = PLUGIN_DIR / "_example.py"
    if example.exists():
        return
    example.write_text('''\
"""Example desktop-mcp plugin — rename to enable.

Copy this file, remove the underscore prefix, and customize.
"""

TOOL_NAME = "example"

TOOL_DOC = """Example plugin with demo actions.
Actions: hello, add"""

ACTIONS = {
    "hello": lambda name="World": {"message": f"Hello, {name}!"},
    "add": lambda a=0, b=0: {"result": a + b},
}
''', encoding="utf-8")
