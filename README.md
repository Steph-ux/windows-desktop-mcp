# Windows Desktop MCP

Windows desktop and browser control MCP server for local agent workflows.

It gives an MCP-capable model a single Windows-native surface for:

- desktop capture: full desktop, region, focused window, or specific window
- desktop interaction: click, type, hotkeys, drag, scroll, focus, move, resize
- UI Automation and OCR: inspect controls, find visible text, click by intent
- browser automation: Playwright sessions, profiles, presets, instances, CDP attach
- browser diagnostics: network logs, HAR-like exports, trace, coverage, debug bundles
- visual workflows: annotated screenshots, watchers, diffing, perception snapshots

## Main Capabilities

### Desktop

- list and focus windows
- inspect UI Automation trees
- capture desktop, region, or window screenshots
- watch for visual or semantic desktop changes
- click by semantic intent with UIA and OCR fallback
- use clipboard, macros, and Unicode-safe typing

### Browser

- open persistent Playwright sessions
- attach to existing Chromium debug endpoints
- manage profiles, presets, and reusable instances
- intercept requests, inspect network traffic, and export HAR-like artifacts
- collect trace and coverage data
- generate browser debug snapshots, reports, and bundles

### AI-Native

- annotate Windows screenshots with numbered targets
- annotate browser pages with visible interactive elements
- return `Image(...)` artifacts directly so the host multimodal model can inspect them

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

## MCP Config

```json
{
  "mcpServers": {
    "windows-desktop": {
      "command": "windows-desktop-mcp"
    }
  }
}
```

Or:

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

## Package Layout

- `desktop_mcp/app.py`: shared `FastMCP` app instance
- `desktop_mcp/server.py`: MCP stdio entrypoint
- `desktop_mcp/tools/`: primary MCP tool surface by domain
- `desktop_mcp/shared/`: shared helpers for Windows, screenshots, and Playwright
- `desktop_mcp/tools_desktop.py`: legacy compatibility shim
- `desktop_mcp/tools_browser.py`: legacy compatibility shim

## Notes

- Windows only.
- OCR features require Tesseract to be installed and available.
- Browser features rely on Playwright and local browser availability.
- The preferred visual flow is to return images directly to the host model; `describe_screen` is optional fallback behavior.
- See [`desktop_mcp/WINDOWS_DESKTOP_MCP.md`](desktop_mcp/WINDOWS_DESKTOP_MCP.md) for the internal package layout and tool families.
