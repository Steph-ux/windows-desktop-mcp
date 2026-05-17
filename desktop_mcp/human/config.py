"""Human behavior configuration and presets for desktop automation.

Adapted from CloakBrowser's human/config.py (MIT License).
All numeric parameters for human-like behavior are centralized here.
Two built-in presets: 'default' (normal human speed) and 'careful' (slower, more cautious).
"""

from __future__ import annotations

import random
import time
import asyncio
from dataclasses import dataclass
from typing import Literal, Tuple

Range = Tuple[float, float]
HumanPreset = Literal["default", "careful"]


@dataclass
class HumanConfig:
    """All tunable parameters for human-like desktop behavior."""

    # Keyboard
    typing_delay: float = 70          # ms per character base
    typing_delay_spread: float = 40   # random spread around base
    typing_pause_chance: float = 0.1  # chance of thinking pause
    typing_pause_range: Range = (400, 1000)  # thinking pause duration ms
    shift_down_delay: Range = (30, 70)
    shift_up_delay: Range = (20, 50)
    key_hold: Range = (15, 35)

    # Mistype (typo simulation)
    mistype_chance: float = 0.02      # 2% typo rate
    mistype_delay_notice: Range = (100, 300)  # time to notice typo
    mistype_delay_correct: Range = (50, 150)  # time to correct

    field_switch_delay: Range = (800, 1500)

    # Mouse — movement
    mouse_steps_divisor: float = 8    # dist / divisor = steps
    mouse_min_steps: int = 25
    mouse_max_steps: int = 80
    mouse_wobble_max: float = 1.5     # max wobble amplitude px
    mouse_overshoot_chance: float = 0.15
    mouse_overshoot_px: Range = (3, 6)
    mouse_burst_size: Range = (3, 5)  # steps between pauses
    mouse_burst_pause: Range = (8, 18)  # pause between bursts ms

    # Mouse — clicks
    click_aim_delay: Range = (60, 200)   # pre-click aim delay ms
    click_hold: Range = (40, 150)        # mouse button hold time ms
    click_double_gap: Range = (50, 120)  # gap between double-click ms

    # Mouse — idle
    idle_drift_px: float = 3
    idle_pause_range: Range = (300, 1000)

    # Scroll
    scroll_delta_base: Range = (80, 130)   # px per scroll tick
    scroll_delta_variance: float = 0.2
    scroll_pause_fast: Range = (30, 80)
    scroll_pause_slow: Range = (80, 200)
    scroll_accel_steps: Range = (2, 3)
    scroll_decel_steps: Range = (2, 3)
    scroll_overshoot_chance: float = 0.1
    scroll_overshoot_px: Range = (50, 150)
    scroll_settle_delay: Range = (300, 600)

    # Idle micro-movements between actions
    idle_between_actions: bool = False
    idle_between_duration: Range = (0.3, 0.8)


PRESET_CAREFUL = HumanConfig(
    typing_delay=120,
    typing_delay_spread=60,
    typing_pause_chance=0.15,
    typing_pause_range=(600, 1500),
    mistype_chance=0.04,
    mouse_steps_divisor=5,
    mouse_min_steps=35,
    mouse_max_steps=120,
    mouse_wobble_max=1.0,
    mouse_overshoot_chance=0.20,
    mouse_overshoot_px=(4, 10),
    mouse_burst_pause=(12, 30),
    click_aim_delay=(100, 300),
    click_hold=(60, 200),
    scroll_pause_fast=(50, 120),
    scroll_pause_slow=(120, 350),
    idle_between_actions=True,
    idle_between_duration=(0.5, 1.5),
)


def resolve_config(preset: HumanPreset = "default", overrides: dict | None = None) -> HumanConfig:
    """Resolve a HumanConfig from preset name + optional overrides."""
    if preset == "careful":
        cfg = PRESET_CAREFUL
    else:
        cfg = HumanConfig()

    if overrides:
        for key, value in overrides.items():
            if hasattr(cfg, key):
                object.__setattr__(cfg, key, value)
    return cfg


# --- Utility functions ---

def rand(low: float, high: float) -> float:
    """Random float in [low, high]."""
    return random.uniform(low, high)


def rand_range(r: Range) -> float:
    """Random float from a Range tuple."""
    return random.uniform(r[0], r[1])


def rand_int_range(r: Range) -> int:
    """Random int from a Range tuple."""
    return random.randint(int(r[0]), int(r[1]))


def sleep_ms(ms: float) -> None:
    """Sleep for ms milliseconds."""
    if ms > 0:
        time.sleep(ms / 1000.0)


async def async_sleep_ms(ms: float) -> None:
    """Async sleep for ms milliseconds."""
    if ms > 0:
        await asyncio.sleep(ms / 1000.0)
