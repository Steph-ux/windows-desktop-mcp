# Windows Desktop MCP

Full Windows desktop, browser, and system control via 14 MCP tools.

236 capabilities consolidated into **14 action-parameterized super-tools** — each tool accepts an `action` parameter that routes to the underlying function.

## Tools

| Tool | Actions | What it does |
|------|---------|--------------|
| `browser_session` | 28 | Lifecycle, instances, profiles, presets |
| `browser_navigate` | 9 | Navigation, tabs, scroll |
| `browser_content` | 16 | DOM, JS eval, frames, shadow DOM, annotations |
| `browser_interact` | 13 | Click, type, fill, upload |
| `browser_observe` | 13 | Screenshots, wait conditions |
| `browser_network` | 17 | Network, cookies, console, intercepts, permissions |
| `browser_debug` | 17 | Traces, coverage, CDP DevTools, metrics |
| `desktop_interact` | 22 | Click, mouse, keyboard, clipboard, macros |
| `desktop_window` | 13 | Window management, UI inspection |
| `desktop_observe` | 22 | Capture, stream, OCR, visual analysis |
| `desktop_monitor` | 16 | Watch sessions, wait conditions |
| `system_info` | 25 | Sysinfo, env, network, services, registry, power |
| `system_ops` | 14 | Files, archives, processes |
| `runtime` | 7 | Health, events, analysis |

## Usage

```python
# Example: navigate to a URL
browser_navigate(action="goto", url="https://example.com")

# Example: click by text on desktop
desktop_interact(action="click_text", text="OK")

# Example: capture a screenshot
desktop_observe(action="capture")
```

## Install & Run

```powershell
pip install -e .
windows-desktop-mcp
```

## MCP Config

```json
{
  "mcpServers": {
    "desktop-mcp": {
      "command": "windows-desktop-mcp"
    }
  }
}
```

## Requirements

- Windows only
- Playwright (`playwright install chromium`) for browser tools
- Tesseract on PATH for OCR features
