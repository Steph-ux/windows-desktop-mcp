"""Human-like scrolling for pyautogui.

Adapted from CloakBrowser's human/scroll.py (MIT License).
Simulates accelerate → cruise → decelerate with optional overshoot.
"""

from __future__ import annotations

import math
import random
import pyautogui

from .config import HumanConfig, rand, rand_range, rand_int_range, sleep_ms


def human_scroll(
    clicks: int,
    x: float | None = None,
    y: float | None = None,
    cfg: HumanConfig | None = None,
) -> None:
    """Scroll with human-like inertia pattern.

    Args:
        clicks: Number of scroll clicks (positive=up, negative=down).
        x: X position to scroll at (None = current position).
        y: Y position to scroll at (None = current position).
        cfg: HumanConfig for timing parameters.
    """
    if cfg is None:
        cfg = HumanConfig()

    if clicks == 0:
        return

    # Move to position first if specified
    if x is not None and y is not None:
        from .mouse import human_move
        human_move(x, y, cfg)

    abs_clicks = abs(clicks)
    direction = 1 if clicks > 0 else -1

    # Determine phases
    accel_steps = min(rand_int_range(cfg.scroll_accel_steps), abs_clicks // 3 + 1)
    decel_steps = min(rand_int_range(cfg.scroll_decel_steps), abs_clicks // 3 + 1)
    cruise_steps = max(0, abs_clicks - accel_steps - decel_steps)

    original_pause = pyautogui.PAUSE
    pyautogui.PAUSE = 0

    try:
        sent = 0

        # Acceleration phase
        for i in range(accel_steps):
            delta = rand(1, 2)
            _smooth_scroll(direction * delta, cfg)
            sent += 1
            sleep_ms(rand_range(cfg.scroll_pause_slow))

        # Cruise phase
        for i in range(cruise_steps):
            delta = rand(2, 4)
            delta *= 1 + (random.random() - 0.5) * 2 * cfg.scroll_delta_variance
            _smooth_scroll(direction * delta, cfg)
            sent += 1
            sleep_ms(rand_range(cfg.scroll_pause_fast))

        # Deceleration phase
        for i in range(decel_steps):
            delta = rand(1, 2)
            _smooth_scroll(direction * delta, cfg)
            sent += 1
            sleep_ms(rand_range(cfg.scroll_pause_slow))

        # Optional overshoot + correction
        if random.random() < cfg.scroll_overshoot_chance and abs_clicks > 3:
            overshoot = rand(1, 2)
            _smooth_scroll(direction * overshoot, cfg)
            sleep_ms(rand_range(cfg.scroll_settle_delay))
            # Correct back
            _smooth_scroll(-direction * overshoot * 0.5, cfg)

        # Settle pause
        sleep_ms(rand_range(cfg.scroll_settle_delay))

    finally:
        pyautogui.PAUSE = original_pause


def _smooth_scroll(clicks: float, cfg: HumanConfig) -> None:
    """Send one logical scroll as micro-bursts (simulates inertia)."""
    abs_clicks = abs(clicks)
    direction = 1 if clicks > 0 else -1

    # Split into 1-2 micro events
    remaining = abs_clicks
    while remaining > 0:
        chunk = min(remaining, rand(0.5, 1.5))
        pyautogui.scroll(round(chunk) * direction, _pause=False)
        remaining -= chunk
        if remaining > 0:
            sleep_ms(rand(8, 20))
