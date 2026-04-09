from __future__ import annotations

import os
import threading
import weakref
from collections import deque
from typing import Any

import pyautogui

pyautogui.FAILSAFE = True
DEFAULT_PYAUTOGUI_PAUSE = 0.1
SESSION_MAX_AGE_MINUTES = 30
BROWSER_PROFILE_MAX_AGE_HOURS = 12
pyautogui.PAUSE = float(
    os.getenv("PM_MCP_PAUSE", os.getenv("PM_PYAUTOGUI_PAUSE", str(DEFAULT_PYAUTOGUI_PAUSE)))
)

PLAYWRIGHT_SESSIONS: dict[str, dict[str, Any]] = {}
PLAYWRIGHT_SESSIONS_LOCK = threading.RLock()
OBSERVED_PLAYWRIGHT_PAGES: weakref.WeakSet[Any] = weakref.WeakSet()
ACTION_EVENT_LOG: deque[dict[str, Any]] = deque(maxlen=250)
DESKTOP_WATCH_SESSIONS: dict[str, dict[str, Any]] = {}
DESKTOP_WATCH_LOCK = threading.Lock()
