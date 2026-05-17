"""Human-like mouse movement and clicking for pyautogui.

Adapted from CloakBrowser's human/mouse.py (MIT License).
Uses Bézier curves with wobble, overshoot, and burst timing.
"""

from __future__ import annotations

import math
import random
import pyautogui

from .config import HumanConfig, rand, rand_range, rand_int_range, sleep_ms


def _ease_in_out(t: float) -> float:
    """Cubic ease-in-out for natural acceleration/deceleration."""
    if t < 0.5:
        return 4 * t * t * t
    return 1 - pow(-2 * t + 2, 3) / 2


def _bezier(p0: tuple, p1: tuple, p2: tuple, p3: tuple, t: float) -> tuple:
    """Cubic Bézier interpolation between 4 control points."""
    u = 1 - t
    uu = u * u
    uuu = uu * u
    tt = t * t
    ttt = tt * t
    x = uuu * p0[0] + 3 * uu * t * p1[0] + 3 * u * tt * p2[0] + ttt * p3[0]
    y = uuu * p0[1] + 3 * uu * t * p1[1] + 3 * u * tt * p2[1] + ttt * p3[1]
    return (x, y)


def _random_control_points(start: tuple, end: tuple) -> tuple:
    """Generate random control points perpendicular to the movement vector."""
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dist = math.hypot(dx, dy) or 1
    # Perpendicular unit vector
    px = -dy / dist
    py = dx / dist
    # Random biases
    bias1 = rand(-0.3, 0.3) * dist
    bias2 = rand(-0.3, 0.3) * dist
    cp1 = (start[0] + dx * 0.25 + px * bias1, start[1] + dy * 0.25 + py * bias1)
    cp2 = (start[0] + dx * 0.75 + px * bias2, start[1] + dy * 0.75 + py * bias2)
    return cp1, cp2


def human_move(
    end_x: float,
    end_y: float,
    cfg: HumanConfig | None = None,
    start_x: float | None = None,
    start_y: float | None = None,
) -> tuple[float, float]:
    """Move mouse to (end_x, end_y) with human-like Bézier curve.

    Returns the final position (may slightly differ due to overshoot correction).
    """
    if cfg is None:
        cfg = HumanConfig()

    if start_x is None or start_y is None:
        pos = pyautogui.position()
        start_x = pos.x
        start_y = pos.y

    dist = math.hypot(end_x - start_x, end_y - start_y)
    if dist < 1:
        return (end_x, end_y)

    # Calculate number of intermediate steps
    steps = max(cfg.mouse_min_steps, min(cfg.mouse_max_steps, round(dist / cfg.mouse_steps_divisor)))

    start = (start_x, start_y)
    end = (end_x, end_y)
    cp1, cp2 = _random_control_points(start, end)

    burst_counter = 0
    burst_size = rand_int_range(cfg.mouse_burst_size)

    # Disable pyautogui's built-in pause for raw speed control
    original_pause = pyautogui.PAUSE
    pyautogui.PAUSE = 0

    try:
        for i in range(steps + 1):
            progress = i / steps
            eased_t = _ease_in_out(progress)
            pt = _bezier(start, cp1, cp2, end, eased_t)

            # Add wobble (sinusoidal, strongest in the middle)
            wobble_amp = math.sin(math.pi * progress) * cfg.mouse_wobble_max
            wx = pt[0] + (random.random() - 0.5) * 2 * wobble_amp
            wy = pt[1] + (random.random() - 0.5) * 2 * wobble_amp

            pyautogui.moveTo(round(wx), round(wy), _pause=False)

            # Burst pause pattern
            burst_counter += 1
            if burst_counter >= burst_size and i < steps:
                sleep_ms(rand_range(cfg.mouse_burst_pause))
                burst_counter = 0
                burst_size = rand_int_range(cfg.mouse_burst_size)

        # Optional overshoot
        final_x, final_y = end_x, end_y
        if random.random() < cfg.mouse_overshoot_chance:
            overshoot_dist = rand_range(cfg.mouse_overshoot_px)
            angle = math.atan2(end_y - start_y, end_x - start_x)
            ox = round(end_x + math.cos(angle) * overshoot_dist)
            oy = round(end_y + math.sin(angle) * overshoot_dist)
            pyautogui.moveTo(ox, oy, _pause=False)
            sleep_ms(rand(30, 70))
            # Correct back with slight imprecision
            final_x = round(end_x + (random.random() - 0.5) * 2)
            final_y = round(end_y + (random.random() - 0.5) * 2)
            pyautogui.moveTo(final_x, final_y, _pause=False)

    finally:
        pyautogui.PAUSE = original_pause

    return (final_x, final_y)


def human_click(
    x: float | None = None,
    y: float | None = None,
    button: str = "left",
    double: bool = False,
    cfg: HumanConfig | None = None,
) -> tuple[float, float]:
    """Click at (x, y) with human-like aim delay and hold time.

    If x/y are provided, moves there first with human_move.
    Returns the click position.
    """
    if cfg is None:
        cfg = HumanConfig()

    # Move to target if coordinates given
    if x is not None and y is not None:
        final_pos = human_move(x, y, cfg)
    else:
        pos = pyautogui.position()
        final_pos = (pos.x, pos.y)

    # Aim delay (hesitation before clicking)
    sleep_ms(rand_range(cfg.click_aim_delay))

    # Click with realistic hold time
    hold = rand_range(cfg.click_hold) / 1000.0

    original_pause = pyautogui.PAUSE
    pyautogui.PAUSE = 0
    try:
        if double:
            pyautogui.mouseDown(button=button, _pause=False)
            sleep_ms(rand_range(cfg.click_hold))
            pyautogui.mouseUp(button=button, _pause=False)
            sleep_ms(rand_range(cfg.click_double_gap))
            pyautogui.mouseDown(button=button, _pause=False)
            sleep_ms(rand_range(cfg.click_hold))
            pyautogui.mouseUp(button=button, _pause=False)
        else:
            pyautogui.mouseDown(button=button, _pause=False)
            sleep_ms(rand_range(cfg.click_hold))
            pyautogui.mouseUp(button=button, _pause=False)
    finally:
        pyautogui.PAUSE = original_pause

    return final_pos


def human_idle(
    seconds: float = 1.0,
    cfg: HumanConfig | None = None,
) -> None:
    """Simulate idle micro-movements (drift) for a duration."""
    if cfg is None:
        cfg = HumanConfig()

    import time as _time
    end_time = _time.monotonic() + seconds
    pos = pyautogui.position()
    x, y = float(pos.x), float(pos.y)

    original_pause = pyautogui.PAUSE
    pyautogui.PAUSE = 0
    try:
        while _time.monotonic() < end_time:
            dx = (random.random() - 0.5) * 2 * cfg.idle_drift_px
            dy = (random.random() - 0.5) * 2 * cfg.idle_drift_px
            x += dx
            y += dy
            pyautogui.moveTo(round(x), round(y), _pause=False)
            sleep_ms(rand_range(cfg.idle_pause_range))
    finally:
        pyautogui.PAUSE = original_pause
