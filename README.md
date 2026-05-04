# windows-desktop-mcp

[![CI](https://github.com/Steph-ux/windows-desktop-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Steph-ux/windows-desktop-mcp/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Windows desktop & browser control MCP server — **15 super-tools** for full automation.

## Install

```bash
pip install windows-desktop-mcp
playwright install chromium
```

## Quick Start

```json
{
  "mcpServers": {
    "desktop-mcp": {
      "command": "windows-desktop-mcp"
    }
  }
}
```

## Dashboard (New!)

Monitor tools usage, MCP events, and capture history in real-time with the local dashboard:

```bash
desktop-mcp-dashboard
```
Then open `http://localhost:8080` in your browser.

## Super-Tools (16)

| Tool | Actions | Domain |
|---|---|---|
| `browser_session` | open, user_open, close, attach_cdp, profiles, presets… | Browser lifecycle |
| `browser_navigate` | goto, back, forward, scroll, pages… | Navigation |
| `browser_content` | DOM, text, eval, frames, shadow DOM… | Content |
| `browser_interact` | click, type, fill, upload… | Interaction |
| `browser_observe` | screenshots, wait conditions… | Observation |
| `browser_network` | cookies, intercept, HAR, storage… | Network |
| `browser_debug` | traces, coverage, CDP, metrics… | Debug |
| `desktop_interact` | click, keyboard, mouse, clipboard, macros | Desktop input |
| `desktop_window` | list, focus, resize, UI inspect… | Windows |
| `desktop_observe` | capture, OCR, smart OCR, video, monitors | Vision |
| `desktop_monitor` | watchers, wait conditions… | Monitoring |
| `system_info` | sysinfo, network, services, registry… | System |
| `system_ops` | files, processes, archives… | Operations |
| `runtime` | health, events, status… | MCP runtime |
| `workflow` | run, record, templates, plugins… | Automation |
| `operator` | start, step, finish, session | Model-operated task sessions |

## Key Features

- **Smart OCR** — fuzzy text matching on screen with element positions
- **Video Recording** — capture desktop sessions as WebM/GIF
- **Multi-Monitor** — capture and interact across all displays
- **Workflow Engine** — chain actions, use variables, pre-built templates
- **Operator Sessions** — log goals, steps, risk, evidence, and outcomes
- **Plugin System** — drop `.py` files in `~/.pm/desktop-mcp/plugins/`
- **CDP Attach** — connect to an existing Chrome browser with your cookies
- **Thread-Safe** — async dispatcher with `anyio.to_thread` for Playwright

## Workflow Templates

```python
# List available templates
workflow → template_list

# Instantiate a login flow
workflow → template_instantiate(template_id="login_flow", variables={"url": "...", "username": "..."})
```

Built-in templates: `scrape_page`, `screenshot_flow`, `fill_form`, `login_flow`, `search_and_extract`, `desktop_screenshot_report`

## Plugins

Create `~/.pm/desktop-mcp/plugins/my_tool.py`:

```python
TOOL_NAME = "my_tool"
TOOL_DOC = "My custom tool.\nActions: greet"
ACTIONS = {
    "greet": lambda name="World": {"message": f"Hello, {name}!"},
}
```

Restart the server — your tool appears automatically.

## Requirements

- Windows 10/11
- Python 3.10+
- Tesseract OCR (`choco install tesseract`)
- Playwright (`playwright install chromium`)
