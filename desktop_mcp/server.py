"""Windows desktop control MCP server for Codex-style agents."""

from __future__ import annotations

import atexit

from .app import mcp
from .browser_core import (
    build_browser_screenshot_args as _build_browser_screenshot_args,
    cleanup_stale_browser_profiles as _cleanup_stale_browser_profiles,
    cleanup_stale_playwright_sessions as _cleanup_stale_playwright_sessions,
    close_all_playwright_sessions as _close_all_playwright_sessions,
    close_playwright_session as _close_playwright_session,
    runtime_housekeeping as _runtime_housekeeping,
)
from .desktop_core import (
    validate_screen_point as _validate_screen_point,
    virtual_screen_bounds as _virtual_screen_bounds,
    window_capture_bounds as _window_capture_bounds,
)
from .helpers import ensure_windows as _ensure_windows
from .paths import BROWSER_CAPTURE_ROOT, BROWSER_PROFILE_ROOT, SCREENSHOT_DIR, ensure_runtime_dirs
from .state import DEFAULT_PYAUTOGUI_PAUSE, PLAYWRIGHT_SESSIONS as _PLAYWRIGHT_SESSIONS, SESSION_MAX_AGE_MINUTES
from .tools.browser_headless import *  # noqa: F401,F403
from .tools.browser_sessions import *  # noqa: F401,F403
from .tools.capture import *  # noqa: F401,F403
from .tools.chrome_devtools import *  # noqa: F401,F403
from .streaming import stop_mjpeg_server as _stop_mjpeg_server
from .tools.input import *  # noqa: F401,F403
from .tools.ocr import *  # noqa: F401,F403
from .tools.runtime import *  # noqa: F401,F403
from .tools.system import *  # noqa: F401,F403
from .tools.windows import *  # noqa: F401,F403
from .tools_ai import *  # noqa: F401,F403
from .tools import consolidated as _consolidated  # noqa: F401 — side-effect registers super-tools
from .tools.capture import stop_all_desktop_watch_sessions as _stop_all_desktop_watch_sessions

ensure_runtime_dirs()
atexit.register(_close_all_playwright_sessions)
atexit.register(_stop_all_desktop_watch_sessions)
atexit.register(_stop_mjpeg_server)


def main() -> None:
    """Run the MCP server over stdio."""
    _ensure_windows()
    _runtime_housekeeping()
    try:
        mcp.run()
    finally:
        _stop_all_desktop_watch_sessions()
        _close_all_playwright_sessions()
        _cleanup_stale_browser_profiles()
