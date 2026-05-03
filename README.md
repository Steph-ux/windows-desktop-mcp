# Windows Desktop MCP

Full Windows desktop, browser, and system control via **15 MCP tools**.

240+ capabilities consolidated into action-parameterized super-tools — each tool accepts an `action` parameter + `timeout_ms` for safe dispatch with structured error handling.

## Tools

| Tool | Actions | What it does |
|------|---------|----|
| `browser_session` | 28 | Lifecycle, instances, profiles, presets |
| `browser_navigate` | 9 | Navigation, tabs, scroll |
| `browser_content` | 16 | DOM, JS eval, frames, shadow DOM, annotations |
| `browser_interact` | 13 | Click, type, fill, upload |
| `browser_observe` | 13 | Screenshots, wait conditions |
| `browser_network` | 17 | Network, cookies, console, intercepts, permissions |
| `browser_debug` | 17 | Traces, coverage, CDP DevTools, metrics |
| `desktop_interact` | 22 | Click, mouse, keyboard, clipboard, macros |
| `desktop_window` | 13 | Window management, UI inspection |
| `desktop_observe` | 34 | Capture, stream, OCR, **smart OCR**, video recording, multi-monitor |
| `desktop_monitor` | 16 | Watch sessions, wait conditions |
| `system_info` | 25 | Sysinfo, env, network, services, registry, power |
| `system_ops` | 14 | Files, archives, processes |
| `runtime` | 7 | Health, events, analysis |
| `workflow` | 7 | **Autonomous action chaining** — run, record, replay |

## New capabilities

- 🎥 **Video recording** — capture desktop sessions as WebM/GIF (`record_start`, `record_stop`)
- 🧠 **Smart OCR** — `ocr_smart` (find elements by description), `screen_understand`, `suggest_actions`
- 🖥️ **Multi-monitor** — enumerate, capture, and coordinate across displays
- 🔗 **Workflows** — chain MCP actions into reusable JSON sequences with variable substitution
- 🛡️ **Safe dispatch** — structured `{ok, error, trace}` responses, per-action `timeout_ms`

## Usage

```python
# Navigate to a URL
browser_navigate(action="goto", url="https://example.com")

# Smart OCR: find element by description
desktop_observe(action="ocr_smart", prompt="find the login button")

# Record a video
desktop_observe(action="record_start", output_name="demo")

# Run a workflow
workflow(action="run", steps=[
    {"tool": "browser_navigate", "action": "goto", "url": "https://example.com"},
    {"tool": "browser_observe", "action": "capture"}
])
```

## Install & Run

```powershell
pip install -e .
windows-desktop-mcp
```

## Requirements

- Windows only
- Playwright (`playwright install chromium`) for browser tools
- Tesseract on PATH for OCR features
- ffmpeg on PATH for WebM video (optional, GIF fallback via Pillow)
