from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any


def ensure_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("This MCP server only runs on Windows.")


def now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def wait_until(
    deadline: float,
    interval: float,
    predicate: Callable[[], Any],
    description: str = "condition",
):
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except Exception as exc:
            last_error = exc
        time.sleep(interval)
    if last_error:
        raise TimeoutError(
            f"Timed out waiting for {description}. "
            f"Last error: {type(last_error).__name__}: {last_error}"
        ) from last_error
    return None
