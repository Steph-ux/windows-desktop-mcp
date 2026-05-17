"""Human-like keyboard input for pyautogui.

Adapted from CloakBrowser's human/keyboard.py (MIT License).
Per-character timing with spread, thinking pauses, and typo simulation.
"""

from __future__ import annotations

import random
import pyautogui

from .config import HumanConfig, rand, rand_range, sleep_ms


SHIFT_SYMBOLS = frozenset('@#!$%^&*()_+{}|:"<>?~')

NEARBY_KEYS = {
    'a': 'sqwz', 'b': 'vghn', 'c': 'xdfv', 'd': 'sfecx', 'e': 'wrsdf',
    'f': 'dgrtcv', 'g': 'fhtyb', 'h': 'gjybn', 'i': 'ujko', 'j': 'hkunm',
    'k': 'jloi', 'l': 'kop', 'm': 'njk', 'n': 'bhjm', 'o': 'iklp',
    'p': 'ol', 'q': 'wa', 'r': 'edft', 's': 'awedxz', 't': 'rfgy',
    'u': 'yhji', 'v': 'cfgb', 'w': 'qase', 'x': 'zsdc', 'y': 'tghu',
    'z': 'asx',
    '1': '2q', '2': '13qw', '3': '24we', '4': '35er', '5': '46rt',
    '6': '57ty', '7': '68yu', '8': '79ui', '9': '80io', '0': '9p',
}


def _get_nearby_key(char: str) -> str:
    """Get a random adjacent key for typo simulation."""
    lower = char.lower()
    neighbors = NEARBY_KEYS.get(lower, "")
    if neighbors:
        typo = random.choice(neighbors)
        return typo.upper() if char.isupper() else typo
    return char


def human_type(
    text: str,
    cfg: HumanConfig | None = None,
    interval: float | None = None,
) -> None:
    """Type text with human-like per-character timing and optional typos.

    Args:
        text: The text to type.
        cfg: HumanConfig for timing parameters.
        interval: Override base interval (ms). If None, uses cfg.typing_delay.
    """
    if cfg is None:
        cfg = HumanConfig()

    base_delay = interval if interval is not None else cfg.typing_delay

    original_pause = pyautogui.PAUSE
    pyautogui.PAUSE = 0

    try:
        for i, char in enumerate(text):
            # Typo simulation
            if cfg.mistype_chance > 0 and random.random() < cfg.mistype_chance:
                typo_char = _get_nearby_key(char)
                if typo_char != char:
                    _type_single_char(typo_char, cfg)
                    sleep_ms(rand_range(cfg.mistype_delay_notice))
                    # Delete the typo
                    pyautogui.press('backspace', _pause=False)
                    sleep_ms(rand_range(cfg.mistype_delay_correct))

            # Type the actual character
            _type_single_char(char, cfg)

            # Inter-character delay
            delay = base_delay + (random.random() - 0.5) * 2 * cfg.typing_delay_spread
            delay = max(20, delay)
            sleep_ms(delay)

            # Random thinking pause
            if random.random() < cfg.typing_pause_chance:
                sleep_ms(rand_range(cfg.typing_pause_range))

    finally:
        pyautogui.PAUSE = original_pause


def _type_single_char(char: str, cfg: HumanConfig) -> None:
    """Type a single character with realistic key hold."""
    if char in SHIFT_SYMBOLS or (char.isalpha() and char.isupper()):
        # Hold shift for uppercase/symbols
        pyautogui.keyDown('shift', _pause=False)
        sleep_ms(rand_range(cfg.shift_down_delay))
        pyautogui.press(char.lower() if char.isalpha() else char, _pause=False)
        sleep_ms(rand_range(cfg.shift_up_delay))
        pyautogui.keyUp('shift', _pause=False)
    elif char == '\n':
        pyautogui.press('enter', _pause=False)
    elif char == '\t':
        pyautogui.press('tab', _pause=False)
    else:
        pyautogui.press(char, _pause=False)


def human_press(
    key: str,
    cfg: HumanConfig | None = None,
    modifiers: list[str] | None = None,
) -> None:
    """Press a key with human-like hold time and optional modifiers.

    Args:
        key: Key name (e.g., 'enter', 'tab', 'a', 'f5').
        cfg: HumanConfig for timing.
        modifiers: List of modifier keys to hold (e.g., ['ctrl', 'shift']).
    """
    if cfg is None:
        cfg = HumanConfig()

    original_pause = pyautogui.PAUSE
    pyautogui.PAUSE = 0

    try:
        # Press modifiers down
        if modifiers:
            for mod in modifiers:
                pyautogui.keyDown(mod, _pause=False)
                sleep_ms(rand_range(cfg.shift_down_delay))

        # Press and hold the key
        pyautogui.keyDown(key, _pause=False)
        sleep_ms(rand_range(cfg.key_hold))
        pyautogui.keyUp(key, _pause=False)

        # Release modifiers
        if modifiers:
            for mod in reversed(modifiers):
                sleep_ms(rand_range(cfg.shift_up_delay))
                pyautogui.keyUp(mod, _pause=False)
    finally:
        pyautogui.PAUSE = original_pause
