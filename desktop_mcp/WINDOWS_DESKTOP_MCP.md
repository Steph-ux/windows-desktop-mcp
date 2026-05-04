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

Compatibility note: a small set of flat legacy MCP aliases such as `ping`,
`browser_open_session`, and `desktop_watch_start` remains registered for older
clients and tests. New model code should prefer `runtime(manifest)` and the
super-tools below.

## Agent Protocol

This MCP is not an autonomous agent. The model owns planning, judgment, and stop/continue decisions. The MCP provides perception, action, short-lived runtime state, and verifiable results.

Recommended loop:

1. `runtime(manifest)` to inspect available tools, actions, signatures, and risk metadata.
2. `workflow(observe)` to capture the current desktop, window, or browser state.
3. `workflow(act_verify)` to execute one model-chosen action with explicit preconditions.
4. Inspect the returned `before`, `result`, `verification`, and `after` fields before choosing the next action.

`workflow(act_verify)` blocks `high` and `destructive` actions unless `confirmed=true` and `confirmation_source="host"` or `"user"` are provided. Model-only confirmation is not enough for sensitive actions.

Use policy parameters to constrain task execution:

1. `allowed_tools` and `denied_tools` restrict whole super-tools.
2. `allowed_actions` and `denied_actions` restrict specific actions such as `runtime/status` or `system_ops/delete`.
3. Policy checks run before observation or action dispatch.

For longer tasks, use `operator` above `workflow`:

1. `operator(start)` creates a task session with `goal`, `context`, `constraints`, and an initial observation.
2. `operator(step)` runs exactly one `workflow(act_verify)` action and appends risk plus evidence to the session log.
3. `operator(finish)` captures the final state and records the outcome.
4. `operator(session)` returns the current mission log for audit or continuation.

The `operator` layer is a log and control surface for the model. It does not decide the next step by itself.

For logged-in web apps such as X, YouTube Studio, TikTok, or Instagram, prefer `browser_session(user_open)` when the task should use the user's existing browser account. `browser_session(open)` and `browser_session(launch)` create MCP-controlled browser contexts and may not share the user's logged-in cookies.

Current Codex setup for this standalone repo should point `windows-desktop` at:

```text
C:\Development\Dev\project-manager\desktop-mcp-standalone\.venv\Scripts\windows-desktop-mcp.exe
```

Avoid pointing Codex at `pm-desktop-mcp` when working on this standalone package; that command loads the older embedded `pm.desktop_mcp` server.
