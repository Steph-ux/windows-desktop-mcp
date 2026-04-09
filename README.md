# Windows Desktop MCP

Standalone Windows desktop and browser control MCP server extracted from a larger project.

It is designed for MCP-capable coding agents that need to:
- inspect and control Windows applications
- capture the desktop, regions, and windows
- use UI Automation and OCR for desktop interaction
- drive browser sessions with Playwright
- attach to Chrome debug/CDP sessions
- collect traces, HAR-like artifacts, coverage, and browser debug bundles

## Package Layout

- `desktop_mcp/app.py`: shared `FastMCP` app instance
- `desktop_mcp/server.py`: MCP stdio entrypoint
- `desktop_mcp/tools/`: primary MCP tool surface by domain
- `desktop_mcp/shared/`: shared helpers for Windows, screenshots, and Playwright
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

## Notes

- This package targets Windows.
- OCR features require Tesseract to be installed and available.
- Browser features rely on Playwright and local browser availability.
- The host model should prefer returned images directly for visual inspection; `describe_screen` is an optional fallback.
