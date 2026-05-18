# windows-desktop-mcp

[![CI](https://github.com/Steph-ux/windows-desktop-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Steph-ux/windows-desktop-mcp/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Windows desktop & browser control MCP server — **21 tools, 319 actions** for full automation.

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

## Dashboard

Monitor tools usage, MCP events, and capture history in real-time:

```bash
desktop-mcp-dashboard
```
Then open `http://localhost:8080` in your browser.

## Tools (21)

| Tool | Actions | Domain |
|---|---|---|
| `do` | *(global router)* | Natural language → auto-route to any tool/action |
| `browser_session` | open, user_open, close, attach_cdp, profiles, presets… | Browser lifecycle |
| `browser_navigate` | goto, back, forward, scroll, scroll_extract, smart_wait, pages, tab_summary… | Navigation |
| `browser_content` | DOM, text, eval, stealth_eval, frames, shadow DOM… | Content |
| `browser_interact` | click, type, fill, smart_fill, auto_login, upload… | Interaction |
| `browser_observe` | screenshots, wait conditions, page_diff, captcha_detect, perf_profile, pdf_export… | Observation |
| `browser_session_state` | save, restore, cookie_list/get/set/delete/clear | Session persistence |
| `browser_network` | cookies, intercept, intercept_smart, HAR, capture, storage… | Network |
| `browser_debug` | traces, coverage, CDP, metrics… | Debug |
| `agent_browser` | ensure_profile, start, status, stop | Dedicated agent browser |
| `social_media` | platform_url, supported_platforms, search, extract, detail | Read-only social DOM extraction |
| `desktop_interact` | click, keyboard, mouse, clipboard, macros, human simulation… | Desktop input |
| `desktop_window` | list, focus, resize, UI inspect… | Windows |
| `desktop_observe` | capture, OCR, smart OCR, video, monitors, screenshot_actions… | Vision |
| `desktop_monitor` | watchers, wait conditions… | Monitoring |
| `system_info` | sysinfo, network, services, registry… | System |
| `system_ops` | files, processes, archives… | Operations |
| `runtime` | health, events, clipboard_bridge, replay_last, proxy_manager, multi_browser, recorder… | MCP runtime & utilities |
| `workflow` | run, record, templates, plugins… | Automation |
| `operator` | start, step, finish, session | Model-operated task sessions |
| `goal` | create, status, list, history, step, pause, resume, complete, fail, clear | Persistent long-running goals |

## AI Intelligence Features

### Global Router (`do`)
One-shot natural language routing for models that struggle with 20+ tools:
```python
do(instruction="scroll down the page")           # → browser_navigate.scroll
do(instruction="take a screenshot")               # → desktop_observe.capture
do(instruction="go to https://example.com")       # → browser_navigate.goto
do(instruction="click the login button")          # → browser_interact.click_intent
```

### Error Recovery
Every failed action returns `suggested_fix`, `hint`, and `example`:
```json
{
  "ok": false,
  "error": "Unknown session 'abc'",
  "suggested_fix": "browser_session(action='open', kwargs='{\"url\": \"...\"}')",
  "hint": "No active browser session. Open one first, then retry."
}
```

### Batch Actions
Chain multiple actions in one call:
```python
browser_interact(action="batch", actions=[
    {"action": "click_text", "text": "Login"},
    {"action": "type", "selector": "#email", "text": "user@example.com"},
    {"action": "press", "key": "Enter"}
])
```

### Context Memory
Track state across actions — last action, tool, URL, error, action count:
```python
runtime(action="context")  # → {"last_action": "click", "last_url": "...", "action_count": 42}
```

### Smart Wait
Wait for page stability (network idle + DOM stable + visual stable) instead of arbitrary sleeps:
```python
browser_navigate(action="smart_wait", timeout_ms=10000, checks=["network", "dom", "visual"])
```

### Network Intercept
Block ads, mock APIs, or capture all requests:
```python
browser_network(action="intercept_smart", sub_action="add_rule", pattern="**/*.png", block=True)
browser_network(action="intercept_smart", sub_action="capture_start")
```

### Session Persistence
Save and restore full browser state (cookies, localStorage, URL):
```python
browser_session_state(action="save", session_id="my_session")
browser_session_state(action="restore", session_id="my_session")
```

### Smart Form Fill
Auto-detect form fields and fill by fuzzy matching labels/placeholders:
```python
browser_interact(action="smart_fill", fields={"Email": "user@test.com", "Password": "secret"})
```

### Auto-Login
Detect login forms automatically and fill credentials:
```python
browser_interact(action="auto_login", credentials={"username": "admin", "password": "pass"})
```

### Page Diff
Compare page state before/after actions (DOM + visual):
```python
browser_observe(action="page_diff", mode="full")  # First call = baseline
# ... perform actions ...
browser_observe(action="page_diff", mode="full")  # Second call = diff result
```

### Captcha Detection
Detect reCAPTCHA, hCaptcha, Cloudflare Turnstile, FunCaptcha:
```python
browser_observe(action="captcha_detect")
# → {"captcha_detected": true, "captcha_types": ["cloudflare_turnstile"]}
```

### Multi-Browser Parallel
Scrape N URLs simultaneously:
```python
runtime(action="multi_browser", urls=["https://a.com", "https://b.com"], action_per_page="text")
```

### Action Recorder
Record and replay action sequences:
```python
runtime(action="recorder", sub_action="start", name="login_flow")
runtime(action="recorder", sub_action="add_step", step_tool="browser_interact", step_action="click_text", step_kwargs={"text": "Login"})
runtime(action="recorder", sub_action="stop")
runtime(action="recorder", sub_action="replay", name="login_flow")
```

### Proxy Manager
Round-robin proxy pool with health checks:
```python
runtime(action="proxy_manager", sub_action="add", proxy_url="http://proxy:8080")
runtime(action="proxy_manager", sub_action="next")  # → next healthy proxy
```

### Screenshot → Actions
Capture + annotate interactive elements + suggest next actions:
```python
desktop_observe(action="screenshot_actions")
# → {"screenshot": "...", "elements": [...], "suggestions": ["click_intent('Save')", ...]}
```

### Performance Profiler
Measure page load time, FCP, resource sizes, DOM count:
```python
browser_observe(action="perf_profile")
# → {"load_time_ms": 1200, "first_contentful_paint_ms": 450, "resource_count": 34, ...}
```

### PDF/Image Export
Export page as PDF or full-page screenshot:
```python
browser_observe(action="pdf_export", format_type="pdf")
browser_observe(action="pdf_export", format_type="image", full_page=True)
```

### Cookie Editor
Full CRUD on browser cookies:
```python
browser_session_state(action="cookie_list")
browser_session_state(action="cookie_set", name="token", value="abc123")
browser_session_state(action="cookie_delete", name="token")
```

### Clipboard Bridge
Copy between browser and desktop clipboard:
```python
runtime(action="clipboard_bridge", direction="browser_to_desktop")
```

## Core Features

- **Smart OCR** — fuzzy text matching on screen with element positions
- **Video Recording** — capture desktop sessions as WebM/GIF
- **Multi-Monitor** — capture and interact across all displays
- **Workflow Engine** — chain actions, use variables, pre-built templates
- **Operator Sessions** — log goals, steps, risk, evidence, and outcomes
- **Persistent Goals** — store long-running objectives, policies, steps, evidence, status, and history across turns/restarts
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

`social_media/detail` filters obvious platform bootstrap/script noise before returning model-facing text. X generic shell/footer fallbacks are marked with `quality="noisy"` and blank `text/full_text`. If a platform page exposes only a broken or noisy fallback, callers should keep the original ranked search item text as the fallback.

## Runtime Evals

Use `runtime -> evals` after a server restart to get a structured `prod-ready`, `degraded`, or `failed` report.

```python
runtime -> evals(suite="quick")
runtime -> evals(suite="social", platforms=["x", "youtube"], query="codex")
runtime -> evals(suite="mission", goal="Find exact Codex improvement posts", platforms=["x", "youtube"], risk_mode="read-only", max_actions=8)
runtime -> evals(suite="all", limit=3, detail_limit=1)
```

Suites:

- `quick`: manifest, health, risk policy, and confirmation guards; no network.
- `social`: live read-only CDP extraction for X, YouTube, TikTok, and Instagram with checks for no host mouse/keyboard, ranking, non-empty extraction, and detail `quality`.
- `windows`: desktop-app safety checks plus Notepad/File Explorer/Calculator scenario plans. It does not type, focus, or launch apps by itself; host-interactive app evals must be run as individual confirmed `workflow.act_verify` steps.
- `mission`: dry-run model mission templates. It returns bounded plans, proof expectations, denied actions, risk checks, and output contracts for real tasks such as cross-platform social research, detail drilldown, desktop read-only inventory, and host-confirmed observe -> act -> verify flows. The eval does not execute the mission by itself.

Mission evals are designed for models: the MCP provides plans, eyes, arms, proofs, and guardrails; the model still chooses each next action.

```python
runtime -> evals(
    suite="mission",
    goal="Research Codex improvements and return the five best sourced posts",
    platforms=["x", "youtube", "tiktok", "instagram"],
    query="codex",
    risk_mode="read-only",
    max_actions=8,
)
```

Use `risk_mode="medium"` only when the model should see host-confirmed templates such as Notepad observe -> act -> verify or operator evidence sessions. Those templates still require explicit `host`/`user` confirmation before any host-interactive execution.

## Desktop Input Safety

Host desktop input can affect the user's real focus, mouse, keyboard, and clipboard. Prefer API/DOM/CDP actions when possible. When text entry is necessary, use `desktop_interact -> kb_unicode(text=..., require_handle=...)` for punctuation-sensitive or Unicode text. `kb_type` uses the active Windows keyboard layout and is only safe for simple text.

Always observe the target window and pass `require_handle` before typing. If focus changes, the action must fail instead of typing into the wrong application.

## Persistent Goals

Use `goal` when a model needs to keep a durable objective across multiple observe -> act -> verify cycles. Goals are persisted under `.pm-runtime/goals` and store objective, success criteria, constraints, risk policy, steps, evidence, status, and history.

```python
goal -> create(
    objective="Ship the MCP release",
    success_criteria=["tests pass", "docs updated", "repo pushed"],
    risk_max="medium",
)
goal -> step(tool="runtime", target_action="status", rationale="Confirm runtime health")
goal -> complete(outcome="Release is validated and pushed")
```

The MCP does not decide autonomously. The model chooses each next action, while `goal.step` enforces the goal risk policy and records the result from `workflow.act_verify`.

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
