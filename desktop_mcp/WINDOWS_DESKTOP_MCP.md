# Windows Desktop MCP

Standalone Windows desktop and browser MCP server.

## Package Layout

- `desktop_mcp/server.py`: MCP server entrypoint
- `desktop_mcp/app.py`: shared `FastMCP` application
- `desktop_mcp/state.py`: runtime state and defaults
- `desktop_mcp/helpers.py`: common helpers
- `desktop_mcp/paths.py`: runtime paths and bootstrap
- `desktop_mcp/desktop_core.py`: Windows desktop, capture, and UI Automation helpers
- `desktop_mcp/browser_core.py`: browser discovery, debug browser helpers, and Playwright core
- `desktop_mcp/ocr_core.py`: OCR integration
- `desktop_mcp/tools/`: primary MCP tool surface split by domain
- `desktop_mcp/shared/`: shared helpers for screenshots, windows, and Playwright
- `desktop_mcp/tools_desktop.py`: legacy compatibility shim
- `desktop_mcp/tools_browser.py`: legacy compatibility shim

## Install

```powershell
python -m pip install -e .
```

## Run

```powershell
windows-desktop-mcp
```

Alternative module entrypoint:

```powershell
python -m desktop_mcp
```

## Example MCP Configuration

Executable entrypoint:

```json
{
  "mcpServers": {
    "windows-desktop": {
      "command": "windows-desktop-mcp"
    }
  }
}
```

Python module entrypoint:

```json
{
  "mcpServers": {
    "windows-desktop": {
      "command": "python",
      "args": ["-m", "desktop_mcp"]
    }
  }
}
```

## Recommended Visual Flow

The preferred visual path is:

1. capture or annotate with MCP image tools
2. let the host multimodal model inspect the returned `Image(...)`
3. use `describe_screen` only as an optional fallback

Recommended tools:

- `capture_desktop`
- `capture_window`
- `capture_region`
- `screen_annotate`
- `browser_annotate_page`
- `desktop_watch_get_latest_capture`
- `browser_capture_session`

## AI-Native Tools

- `screen_annotate`: capture and annotate a window, region, or desktop with numbered targets
- `browser_annotate_page`: annotate visible interactive elements in a browser page
- `intent_click`: click by semantic intent with UIA and OCR fallback
- `desktop_watch_until`: wait until a desktop condition is met
- `describe_screen`: optional external-vision fallback

## Exposed Tool Families

- desktop windows and focus
- capture and screenshots
- OCR and visible-text targeting
- desktop watch, diff, and perception snapshots
- browser headless capture and DOM dump
- persistent Playwright sessions
- profiles, presets, instances, and attach flows
- request interception, network inspection, HAR, trace, and coverage
- browser debug snapshots, reports, and bundles
- clipboard, input, macros, and runtime diagnostics

Use `list_tools` from the MCP client for the exact runtime surface.
