# Changelog

## [0.3.0] - 2026-05-19

### AI Intelligence (21 phases)

**Phase 1-9 — Core Intelligence**
- **Auto-session** — default `session_id`, auto-resolve from context
- **Global Router (`do`)** — natural language routes to correct tool/action with auto-extracted kwargs
- **Error Recovery** — every error returns `suggested_fix`, `hint`, `example`
- **Batch Actions** — chain N actions in one call with `stop_on_error`
- **Context Memory** — tracks `last_action`, `last_tool`, `last_url`, `last_error`, `action_count`
- **Auto-scroll + Extract** — `scroll_extract` scrolls full page and returns text/links
- **Smart Form Fill** — fuzzy match fields by label/placeholder/name
- **Screenshot → Actions** — capture + annotate UIA elements + suggest next actions
- **Clipboard Bridge & Replay** — copy between browser/desktop clipboard, replay last N actions

**Phase 10-21 — Advanced Browser & Automation**
- **Smart Wait** — wait for network idle + DOM stable + visual stable
- **Network Intercept** — block/mock/capture requests by URL pattern
- **Session Persistence** — save/restore cookies + localStorage + URL to disk
- **Proxy Manager** — add/remove/round-robin/health-check proxy pool
- **Page Diff** — DOM + visual before/after comparison
- **Auto-Login** — detect password forms, fill credentials, submit
- **Multi-Browser Parallel** — scrape N URLs simultaneously
- **Action Recorder** — record/replay/list/delete action sequences
- **Performance Profiler** — load time, FCP, resource sizes, DOM element count
- **Captcha Detection** — reCAPTCHA, hCaptcha, Cloudflare Turnstile, FunCaptcha
- **PDF/Image Export** — export page as PDF or full-page PNG
- **Cookie Editor** — full CRUD on browser cookies

### Stats
- **21 MCP tools** (+2 new: `do`, `browser_session_state`)
- **319 actions** (+85 from v0.2.0)
- New module: `tools/router.py` — global NL router + utilities

---

## [0.2.0] - 2026-05-15

- 19 consolidated MCP tools
- Smart OCR, video recording, multi-monitor
- Social media DOM extraction (X, YouTube, TikTok, Instagram)
- Persistent goals, operator sessions, workflow engine
- CloakBrowser stealth integration
- Dedicated agent browser profiles

## [0.1.0] - 2026-05-10

- Initial release
- Windows desktop control + Playwright browser automation
