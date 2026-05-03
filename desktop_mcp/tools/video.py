"""Video recording for desktop and browser sessions."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from ..paths import SCREENSHOT_DIR

RECORDING_DIR = SCREENSHOT_DIR / "recordings"
RECORDING_DIR.mkdir(parents=True, exist_ok=True)

_active_recordings: dict[str, dict[str, Any]] = {}


def _capture_loop(rec_id: str, fps: int, monitor: int, region: dict | None):
    """Background thread that captures frames for desktop recording."""
    import mss
    from PIL import Image

    rec = _active_recordings.get(rec_id)
    if not rec:
        return

    interval = 1.0 / fps
    frames_dir = rec["frames_dir"]
    frame_idx = 0

    with mss.mss() as sct:
        monitors = sct.monitors
        mon = monitors[monitor] if monitor < len(monitors) else monitors[0]
        if region:
            mon = {"left": region["x"], "top": region["y"],
                   "width": region["width"], "height": region["height"]}

        while rec.get("running", False):
            t0 = time.monotonic()
            try:
                img = sct.grab(mon)
                frame = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
                frame.save(frames_dir / f"frame_{frame_idx:06d}.png", "PNG")
                frame_idx += 1
                rec["frame_count"] = frame_idx
            except Exception:
                pass
            elapsed = time.monotonic() - t0
            if elapsed < interval:
                time.sleep(interval - elapsed)


def desktop_record_start(
    fps: int = 5,
    monitor: int = 0,
    region: dict | None = None,
    recording_id: str = "",
) -> dict:
    """Start recording the desktop."""
    rec_id = recording_id or f"rec_{int(time.time())}"
    if rec_id in _active_recordings:
        return {"error": f"Recording {rec_id!r} already active"}

    frames_dir = RECORDING_DIR / rec_id / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    rec = {
        "id": rec_id,
        "fps": fps,
        "monitor": monitor,
        "region": region,
        "frames_dir": frames_dir,
        "frame_count": 0,
        "running": True,
        "started_at": time.time(),
    }
    _active_recordings[rec_id] = rec

    t = threading.Thread(target=_capture_loop, args=(rec_id, fps, monitor, region), daemon=True)
    t.start()
    rec["thread"] = t

    return {"ok": True, "recording_id": rec_id, "fps": fps, "monitor": monitor}


def desktop_record_stop(recording_id: str = "", output_format: str = "webm") -> dict:
    """Stop recording and assemble output file."""
    if not recording_id:
        if not _active_recordings:
            return {"error": "No active recordings"}
        recording_id = next(iter(_active_recordings))

    rec = _active_recordings.pop(recording_id, None)
    if not rec:
        return {"error": f"Recording {recording_id!r} not found"}

    rec["running"] = False
    thread = rec.get("thread")
    if thread:
        thread.join(timeout=5)

    frames_dir: Path = rec["frames_dir"]
    frames = sorted(frames_dir.glob("frame_*.png"))
    if not frames:
        return {"error": "No frames captured", "recording_id": recording_id}

    output_path = RECORDING_DIR / recording_id / f"output.{output_format}"

    try:
        if output_format == "gif":
            _assemble_gif(frames, output_path, rec["fps"])
        else:
            _assemble_webm(frames, output_path, rec["fps"])
    except Exception as e:
        return {"error": f"Assembly failed: {e}", "frame_count": len(frames),
                "frames_dir": str(frames_dir)}

    # Cleanup frames
    for f in frames:
        f.unlink(missing_ok=True)
    if frames_dir.exists():
        try:
            frames_dir.rmdir()
        except OSError:
            pass

    return {
        "ok": True,
        "recording_id": recording_id,
        "output": str(output_path),
        "format": output_format,
        "frame_count": len(frames),
        "duration_s": round(len(frames) / max(rec["fps"], 1), 2),
    }


def desktop_record_status(recording_id: str = "") -> dict:
    """Get status of a recording."""
    if recording_id:
        rec = _active_recordings.get(recording_id)
        if not rec:
            return {"error": f"Recording {recording_id!r} not found"}
        return {
            "recording_id": recording_id,
            "running": rec["running"],
            "frame_count": rec["frame_count"],
            "elapsed_s": round(time.time() - rec["started_at"], 1),
            "fps": rec["fps"],
        }
    return {
        "active_recordings": [
            {"id": k, "frames": v["frame_count"], "elapsed_s": round(time.time() - v["started_at"], 1)}
            for k, v in _active_recordings.items()
        ]
    }


def desktop_record_list() -> dict:
    """List all recordings (active and saved)."""
    saved = []
    if RECORDING_DIR.exists():
        for d in RECORDING_DIR.iterdir():
            if d.is_dir():
                outputs = list(d.glob("output.*"))
                saved.append({
                    "id": d.name,
                    "active": d.name in _active_recordings,
                    "outputs": [str(o) for o in outputs],
                })
    return {"recordings": saved}


def _assemble_gif(frames: list[Path], output: Path, fps: int):
    from PIL import Image
    images = [Image.open(f) for f in frames]
    if not images:
        return
    duration_ms = max(int(1000 / fps), 20)
    # Resize for GIF size limit
    max_dim = 800
    w, h = images[0].size
    if w > max_dim or h > max_dim:
        ratio = min(max_dim / w, max_dim / h)
        new_size = (int(w * ratio), int(h * ratio))
        images = [img.resize(new_size, Image.LANCZOS) for img in images]
    images[0].save(output, save_all=True, append_images=images[1:],
                   duration=duration_ms, loop=0, optimize=True)


def _assemble_webm(frames: list[Path], output: Path, fps: int):
    import subprocess
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        # Fallback to GIF
        _assemble_gif(frames, output.with_suffix(".gif"), fps)
        return

    frames_dir = frames[0].parent
    subprocess.run([
        ffmpeg, "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%06d.png"),
        "-vf", "scale='min(1280,iw)':-2",
        "-c:v", "libvpx", "-crf", "20", "-b:v", "2M",
        "-pix_fmt", "yuv420p",
        str(output),
    ], capture_output=True, timeout=300)
