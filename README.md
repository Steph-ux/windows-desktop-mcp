# Windows Desktop MCP

Windows desktop and browser control MCP server for local agent workflows.

**236 capabilities consolidated into 14 action-parameterized super-tools** — full Windows desktop control, browser automation, and system administration through a single MCP surface.

## Architecture

Instead of registering hundreds of individual MCP tools, this server uses an **action-parameterized dispatch pattern**: each super-tool accepts an `action` parameter that routes to the underlying function. This keeps the tool count well under MCP host limits while preserving 100% feature parity.

```
14 MCP tools → 232 actions → full desktop/browser/system control
```

## Capabilities

| Domain | Tools | Actions | Description |
|--------|-------|---------|-------------|
| **Browser** | 7 | 113 | Full Playwright automation, CDP, network, debug |
| **Desktop** | 4 | 73 | Click, type, capture, OCR, window management |
| **System** | 2 | 39 | Files, processes, registry, services, power |
| **Runtime** | 1 | 7 | MCP health, events, analysis |

## Tool Reference

### Browser Tools (7)

#### `browser_session`
Manage browser lifecycle, instances, profiles, and presets.

| Action | Description |
|--------|-------------|
| `open` | Open a new Playwright session |
| `close` | Close a session |
| `list` | List active sessions |
| `cleanup` | Cleanup stale sessions |
| `start` | Start a browser instance |
| `stop` | Stop an instance |
| `stop_all` | Stop instance and browser |
| `resume` | Resume a stopped instance |
| `get` | Get instance details |
| `list_instances` | List all instances |
| `delete` | Delete an instance |
| `attach_cdp` | Attach via CDP endpoint |
| `attach_existing` | Attach to running browser |
| `launch` | Launch and auto-attach |
| `debug` | Launch debug browser |
| `endpoints` | List debug endpoints |
| `profile_create` | Create browser profile |
| `profile_get` | Get profile details |
| `profile_update` | Update profile config |
| `profile_list` | List all profiles |
| `profile_delete` | Delete a profile |
| `profile_cleanup` | Cleanup unused profiles |
| `profile_import` | Import profile config |
| `profile_export` | Export profile config |
| `preset_save` | Save browser preset |
| `preset_get` | Get preset details |
| `preset_list` | List all presets |
| `preset_delete` | Delete a preset |

#### `browser_navigate`
Navigate browser pages.

| Action | Description |
|--------|-------------|
| `goto` | Navigate to URL |
| `reload` | Reload current page |
| `back` | Go back |
| `forward` | Go forward |
| `scroll` | Scroll page |
| `new_page` | Open new tab |
| `switch_page` | Switch to tab |
| `close_page` | Close current tab |
| `list_pages` | List open tabs |

#### `browser_content`
Read DOM, execute JS, annotate elements, interact with frames/shadow DOM.

| Action | Description |
|--------|-------------|
| `get` | Get DOM content |
| `dump` | Dump full DOM |
| `text` | Get text content |
| `summary` | Get page summary |
| `count` | Count matching selectors |
| `a11y` | Accessibility snapshot |
| `eval` | Execute JavaScript |
| `annotate` | Annotate page elements (AI) |
| `interactive` | List interactive elements |
| `forms` | List form fields |
| `downloads` | List downloads |
| `frame_list` | List iframes |
| `frame_eval` | Eval in iframe |
| `frame_click` | Click in iframe |
| `frame_fill` | Fill in iframe |
| `shadow_query` | Query shadow DOM |

#### `browser_interact`
Click, type, and fill in browser.

| Action | Description |
|--------|-------------|
| `click` | Click selector |
| `click_text` | Click by text |
| `click_interactive` | Click interactive element |
| `hover` | Hover over selector |
| `focus` | Focus selector |
| `click_download` | Click and wait for download |
| `click_text_download` | Click text and wait for download |
| `type` | Type into selector |
| `press` | Press key |
| `fill_field` | Fill form field |
| `fill_form` | Fill entire form |
| `toggle` | Toggle form field |
| `upload` | Upload files |

#### `browser_observe`
Capture screenshots and wait for conditions.

| Action | Description |
|--------|-------------|
| `capture` | Capture full page |
| `capture_element` | Capture specific element |
| `capture_session` | Capture session state |
| `capture_live` | Capture live page |
| `save` | Save screenshot to disk |
| `save_live` | Save live screenshot |
| `wait_selector` | Wait for selector |
| `wait_text` | Wait for text |
| `wait_load` | Wait for load state |
| `wait_dom` | Wait for DOM change |
| `wait_url` | Wait for URL change |
| `wait_visual` | Wait for visual change |
| `wait_download` | Wait for download |

#### `browser_network`
Network monitoring, cookies, storage, console, interception, and permissions.

| Action | Description |
|--------|-------------|
| `net_list` | List network requests |
| `net_summary` | Network summary |
| `net_errors` | Network errors |
| `net_detail` | Request details |
| `net_har` | Export HAR |
| `intercept_set` | Set request intercept |
| `intercept_list` | List intercepts |
| `intercept_clear` | Clear intercepts |
| `console_logs` | Get console logs |
| `dialogs` | Get dialogs |
| `cookie_get` | Get cookies |
| `cookie_set` | Set cookies |
| `cookie_delete` | Delete cookies |
| `storage_export` | Export storage state |
| `perm_grant` | Grant permissions |
| `perm_revoke` | Revoke permissions |
| `perm_clear` | Clear permissions |

#### `browser_debug`
Browser debugging, tracing, coverage, and DevTools.

| Action | Description |
|--------|-------------|
| `bundle` | Debug bundle |
| `report` | Debug report |
| `snapshot` | Debug snapshot |
| `metrics` | Performance metrics |
| `viewport` | Viewport state |
| `state` | Snapshot state |
| `trace_start` | Start trace |
| `trace_stop` | Stop trace |
| `cov_start` | Start coverage |
| `cov_stop` | Stop coverage |
| `cov_export` | Export coverage JSON |
| `cdp_eval` | CDP evaluate JS |
| `cdp_trace_start` | CDP start trace |
| `cdp_trace_stop` | CDP stop trace |
| `cdp_network` | CDP network logs |
| `cdp_console` | CDP console logs |
| `cdp_metrics` | CDP page metrics |

---

### Desktop Tools (4)

#### `desktop_interact`
All desktop interaction: click, mouse, keyboard, clipboard, macros.

| Action | Description |
|--------|-------------|
| `click` | Click at x,y |
| `double_click` | Double click |
| `right_click` | Right click |
| `click_text` | Click by text (UIA) |
| `click_ocr` | Click by OCR text |
| `click_ui` | Click UI element |
| `click_intent` | Click by semantic intent (AI) |
| `click_visible` | Click visible text |
| `mouse_move` | Move mouse |
| `mouse_drag` | Drag mouse |
| `mouse_position` | Get cursor position |
| `mouse_scroll` | Scroll |
| `kb_type` | Type text |
| `kb_unicode` | Type Unicode text |
| `kb_press` | Press key |
| `kb_hotkey` | Hotkey combo |
| `clip_get` | Get clipboard |
| `clip_set` | Set clipboard |
| `macro_record` | Record macro action |
| `macro_replay` | Replay macro |
| `macro_list` | List macros |
| `macro_clear` | Clear macros |

#### `desktop_window`
Window management and UI element inspection.

| Action | Description |
|--------|-------------|
| `list` | List all windows |
| `focus` | Focus window |
| `active` | Get active window |
| `focused` | Get focused window |
| `minimize` | Minimize window |
| `maximize` | Maximize window |
| `close` | Close window |
| `resize` | Move/resize window |
| `summary` | Window summary |
| `focused_summary` | Focused window summary |
| `text_map` | OCR text map of focused window |
| `ui_find` | Find UI elements |
| `ui_inspect` | Inspect UI tree |

#### `desktop_observe`
Screenshots, streaming, OCR, and visual analysis.

| Action | Description |
|--------|-------------|
| `capture` | Capture full desktop |
| `capture_window` | Capture specific window |
| `capture_focused` | Capture focused window |
| `capture_region` | Capture screen region |
| `save_desktop` | Save desktop screenshot |
| `save_window` | Save window screenshot |
| `stream_start` | Start MJPEG stream |
| `stream_stop` | Stop MJPEG stream |
| `stream_status` | Stream status |
| `ocr_window` | OCR window content |
| `ocr_region` | OCR screen region |
| `ocr_file` | OCR image file |
| `ocr_find` | Find text via OCR |
| `annotate` | Annotate screenshot (AI) |
| `watch` | Watch screen changes |
| `describe` | Describe screen |
| `overview` | Desktop overview |
| `perception` | Perception snapshot |
| `snapshot` | Desktop state snapshot |
| `find_image` | Find image on screen |
| `diff` | Diff screenshots |
| `compare` | Compare captures |

#### `desktop_monitor`
Desktop change monitoring and waiting.

| Action | Description |
|--------|-------------|
| `watch_start` | Start watch session |
| `watch_stop` | Stop watch session |
| `watch_list` | List watch sessions |
| `watch_states` | Get watch states |
| `watch_latest` | Get latest capture |
| `watch_summary` | Change summary |
| `watch_compare` | Compare latest frames |
| `watch_wait` | Wait for change |
| `watch_until` | Watch until condition (AI) |
| `watch_goal` | Watch until goal |
| `wait_window` | Wait for window |
| `wait_text` | Wait for text |
| `wait_ui` | Wait for UI element |
| `wait_focus` | Wait for focus change |
| `wait_desktop` | Wait for desktop change |
| `wait_content` | Wait for content change |

---

### System Tools (2)

#### `system_info`
System info, environment, network, power, services, registry, and misc.

| Action | Description |
|--------|-------------|
| `info` | System information |
| `uptime` | System uptime |
| `battery` | Battery status |
| `disk` | Disk usage |
| `network` | Network info |
| `env_get` | Get env variable |
| `env_list` | List env variables |
| `env_set` | Set env variable |
| `ping` | Ping host |
| `port` | Check port |
| `resolve` | Resolve hostname |
| `notify` | Show notification |
| `wallpaper` | Set wallpaper |
| `shutdown` | Shutdown computer |
| `restart` | Restart computer |
| `sleep` | Sleep computer |
| `lock` | Lock computer |
| `svc_list` | List Windows services |
| `svc_status` | Service status |
| `svc_start` | Start service |
| `svc_stop` | Stop service |
| `svc_restart` | Restart service |
| `reg_read` | Read registry key |
| `reg_write` | Write registry value |
| `reg_delete` | Delete registry value |

#### `system_ops`
File operations, archives, and process management.

| Action | Description |
|--------|-------------|
| `read` | Read file |
| `write` | Write file |
| `delete` | Delete file |
| `list` | List directory |
| `search` | Search files |
| `mkdir` | Create directory |
| `symlink` | Create symlink |
| `hash` | File hash |
| `compress` | Compress files |
| `decompress` | Decompress files |
| `ps_list` | List processes |
| `ps_detail` | Process details |
| `ps_kill` | Kill process |
| `run` | Run command |

---

### Runtime (1)

#### `runtime`
MCP runtime status, analysis, and health.

| Action | Description |
|--------|-------------|
| `status` | Runtime status |
| `events` | Recent events |
| `clear` | Clear events |
| `health` | Health check |
| `analysis_export` | Export analysis history |
| `analysis_latest` | Latest analysis |
| `ping` | Ping |

## Install

```powershell
python -m pip install -e .
```

## Run

```powershell
windows-desktop-mcp
```

Or via Python module:

```powershell
python -m desktop_mcp
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

Or with a virtualenv:

```json
{
  "mcpServers": {
    "desktop-mcp": {
      "command": "c:/path/to/.venv/Scripts/python.exe",
      "args": ["-m", "desktop_mcp"]
    }
  }
}
```

## Package Layout

```
desktop_mcp/
├── app.py                  # FastMCP instance
├── server.py               # Stdio entrypoint + consolidated import
├── tools/
│   ├── consolidated.py     # Dynamic super-tool registry (14 tools → 232 actions)
│   ├── browser_sessions.py # Playwright browser automation
│   ├── browser_headless.py # Headless browser utilities
│   ├── capture.py          # Desktop capture, streaming, watch
│   ├── chrome_devtools.py  # CDP DevTools integration
│   ├── input.py            # Mouse/keyboard input
│   ├── ocr.py              # OCR and text recognition
│   ├── runtime.py          # Clipboard, macros, commands
│   ├── system.py           # System administration
│   └── windows.py          # Window management, UIA
├── tools_ai.py             # AI-native annotation tools
├── tools_runtime.py        # Runtime status/health
└── shared/                 # Internal helpers
```

## Notes

- **Windows only.**
- OCR features require Tesseract to be installed and on PATH.
- Browser features rely on Playwright (`playwright install chromium`).
- The preferred visual flow is to return images directly to the host model.
- `describe_screen` is an optional fallback when the host cannot inspect images.
