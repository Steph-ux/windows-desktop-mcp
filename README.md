# windows-desktop-mcp

[![CI](https://github.com/Steph-ux/windows-desktop-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Steph-ux/windows-desktop-mcp/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Windows desktop & browser control MCP server - **18 super-tools** for full automation.

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

## Super-Tools (18)

| Tool | Actions | Domain |
|---|---|---|
| `browser_session` | open, user_open, close, attach_cdp, profiles, presets… | Browser lifecycle |
| `browser_navigate` | goto, back, forward, scroll, pages… | Navigation |
| `browser_content` | DOM, text, eval, frames, shadow DOM… | Content |
| `browser_interact` | click, type, fill, upload… | Interaction |
| `browser_observe` | screenshots, wait conditions… | Observation |
| `browser_network` | cookies, intercept, HAR, storage… | Network |
| `browser_debug` | traces, coverage, CDP, metrics… | Debug |
| `agent_browser` | ensure_profile, start, status, stop | Dedicated agent browser |
| `social_media` | platform_url, supported_platforms, search, extract | Read-only social DOM extraction |
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
- **Dedicated Agent Browser** — isolated Playwright profiles for model-controlled browsing without using the host mouse, keyboard, or default browser
- **Social Media Read-Only DOM Extraction** — X, YouTube, YouTube Studio, TikTok, and Instagram search/read scenarios through DOM/CDP instead of OCR where possible
- **Strict Non-Interactive Mode** — blocks host mouse, keyboard, focus, and default-browser actions unless the host/user explicitly confirms
- **Plugin System** — drop `.py` files in `~/.pm/desktop-mcp/plugins/`
- **CDP Attach** — connect to an existing Chrome browser with your cookies
- **Thread-Safe** — async dispatcher with `anyio.to_thread` for Playwright

## Host Interaction Safety

Strict non-interactive mode is enabled by default. Actions that can affect the user's real desktop, cursor, keyboard focus, clipboard, or default browser are marked as `host_interactive` in `runtime(manifest)` and are blocked unless the call includes `confirmed=true` and `confirmation_source="user"` or `"host"`.

Set `WINDOWS_DESKTOP_MCP_STRICT_NON_INTERACTIVE=0` only in a dedicated automation VM or throwaway desktop session.

## Dedicated Agent Browser

Use `agent_browser` for logged-in or persistent browser automation that must not touch the user's real browser, cursor, or keyboard. It creates named Playwright profiles such as `agent-social-x` and starts isolated instances controlled through browser APIs.

```python
agent_browser -> ensure_profile(platform="x")
agent_browser -> start(platform="x", url="https://x.com/search?q=codex&src=typed_query&f=top")
```

For social media read-only work, use `social_media` instead of `browser_session -> user_open`:

```python
social_media -> search(platform="x", query="codex", limit=10)
social_media -> search(platform="youtube", query="codex", limit=10)
social_media -> search(platform="tiktok", query="codex", limit=10)
social_media -> search(platform="instagram", query="codex", limit=10)
social_media -> extract(platform="youtube_studio", session_id="...")
```

For higher-volume read-only discovery, keep the dedicated browser warm and let the DOM extractor scroll and rank results:

```python
social_media -> search(platform="x", query="codex", limit=50, browser_engine="cdp", keep_open=True)
social_media -> search(platform="youtube", query="codex", limit=25, scroll_steps=4)
social_media -> search(platform="instagram", query="codex", limit=25, rank=True)
social_media -> search(platform="tiktok", query="codex", limit=25, keep_open=False)
```

These actions are designed for model planning: `social_media/search` and `social_media/extract` are `read` risk, non-host-interactive, and return structured items with `extraction_method="dom"`. When ranking is enabled, items include `metrics`, `rank_score`, and `rank_position` parsed from views, likes, replies/comments, reposts/shares, and bookmarks/saves where the platform exposes them in visible DOM text.

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
