"""Multi-monitor detection and capture support."""
from __future__ import annotations

from typing import Any


def list_monitors() -> dict:
    """List all connected monitors with resolution and position."""
    import mss
    with mss.mss() as sct:
        monitors = []
        for i, m in enumerate(sct.monitors):
            monitors.append({
                "index": i,
                "label": "all" if i == 0 else f"monitor_{i}",
                "left": m["left"],
                "top": m["top"],
                "width": m["width"],
                "height": m["height"],
                "is_primary": i == 1,
                "is_virtual": i == 0,
            })
        return {"monitors": monitors, "count": len(monitors) - 1}  # exclude virtual


def capture_monitor(monitor: int = 1, save_path: str = "") -> dict:
    """Capture a specific monitor by index."""
    import mss
    from PIL import Image
    from ..paths import SCREENSHOT_DIR

    with mss.mss() as sct:
        if monitor >= len(sct.monitors):
            return {"error": f"Monitor {monitor} not found. Available: 0-{len(sct.monitors)-1}"}

        img = sct.grab(sct.monitors[monitor])
        frame = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")

        if save_path:
            from pathlib import Path
            out = Path(save_path)
        else:
            out = SCREENSHOT_DIR / f"monitor_{monitor}.png"
            out.parent.mkdir(parents=True, exist_ok=True)

        frame.save(str(out), "PNG")
        return {"ok": True, "monitor": monitor, "path": str(out),
                "width": frame.width, "height": frame.height}


def capture_all_monitors(save_dir: str = "") -> dict:
    """Capture all monitors individually."""
    import mss
    from PIL import Image
    from pathlib import Path
    from ..paths import SCREENSHOT_DIR

    out_dir = Path(save_dir) if save_dir else SCREENSHOT_DIR / "multi-monitor"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    with mss.mss() as sct:
        for i in range(1, len(sct.monitors)):  # skip virtual (0)
            img = sct.grab(sct.monitors[i])
            frame = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
            path = out_dir / f"monitor_{i}.png"
            frame.save(str(path), "PNG")
            results.append({"monitor": i, "path": str(path),
                           "width": frame.width, "height": frame.height})

    return {"ok": True, "captures": results, "count": len(results)}


def get_monitor_at_point(x: int, y: int) -> dict:
    """Determine which monitor contains the given coordinates."""
    import mss
    with mss.mss() as sct:
        for i in range(1, len(sct.monitors)):
            m = sct.monitors[i]
            if (m["left"] <= x < m["left"] + m["width"] and
                    m["top"] <= y < m["top"] + m["height"]):
                return {"monitor": i, "bounds": m,
                        "relative_x": x - m["left"], "relative_y": y - m["top"]}
        return {"monitor": None, "error": f"No monitor contains point ({x}, {y})"}


def absolute_to_monitor(x: int, y: int) -> dict:
    """Convert absolute screen coords to monitor-relative coords."""
    return get_monitor_at_point(x, y)


def monitor_to_absolute(monitor: int, x: int, y: int) -> dict:
    """Convert monitor-relative coords to absolute screen coords."""
    import mss
    with mss.mss() as sct:
        if monitor >= len(sct.monitors):
            return {"error": f"Monitor {monitor} not found"}
        m = sct.monitors[monitor]
        return {"abs_x": m["left"] + x, "abs_y": m["top"] + y, "monitor": monitor}
