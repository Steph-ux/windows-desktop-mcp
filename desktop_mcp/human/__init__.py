"""Human-like behavior engine for desktop automation (pyautogui).

Adapted from CloakBrowser's human behavior module (MIT License).
Provides Bézier mouse curves, realistic keyboard timing, and smooth scrolling
for pyautogui-based desktop interactions.
"""

from .config import HumanConfig, HumanPreset, resolve_config
from .mouse import human_move, human_click, human_idle
from .keyboard import human_type, human_press
from .scroll import human_scroll

__all__ = [
    "HumanConfig",
    "HumanPreset",
    "resolve_config",
    "human_move",
    "human_click",
    "human_idle",
    "human_type",
    "human_press",
    "human_scroll",
]
