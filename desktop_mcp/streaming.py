"""MJPEG streaming server for desktop watch sessions."""

from __future__ import annotations

import io
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

from .desktop_core import grab_png_bytes
from .state import DESKTOP_WATCH_LOCK, DESKTOP_WATCH_SESSIONS


_mjpeg_server = None
_mjpeg_thread = None
_mjpeg_port = 8080
_latest_frame = None
_latest_frame_time = 0


class MJPEGRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for MJPEG streaming."""
    
    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/stream":
            self.send_stream()
        elif self.path == "/status":
            self.send_status()
        else:
            self.send_error(404)
    
    def send_stream(self):
        """Send MJPEG stream."""
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=boundary")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        
        try:
            while True:
                # Get latest frame
                frame = _get_latest_frame()
                if frame:
                    self.wfile.write(b"--boundary\r\n")
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(frame)))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                
                time.sleep(0.05)  # ~20 FPS
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            pass
    
    def send_status(self):
        """Send server status."""
        import json
        from .state import DESKTOP_WATCH_SESSIONS
        
        with DESKTOP_WATCH_LOCK:
            watch_count = len(DESKTOP_WATCH_SESSIONS)
        
        status = {
            "running": True,
            "port": _mjpeg_port,
            "active_watches": watch_count,
            "latest_frame_time": _latest_frame_time,
        }
        
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(status).encode())
    
    def log_message(self, format, *args):
        """Suppress log messages."""
        pass


def _get_latest_frame() -> bytes | None:
    """Get the latest frame from active watch session."""
    global _latest_frame, _latest_frame_time
    
    # Check if frame is recent (within 1 second)
    if _latest_frame and (time.time() - _latest_frame_time) < 1.0:
        return _latest_frame
    
    # Try to get frame from active watch session
    with DESKTOP_WATCH_LOCK:
        for watch_id, watch in DESKTOP_WATCH_SESSIONS.items():
            if watch["history"]:
                latest = list(watch["history"])[-1]
                capture_path = latest.get("capture_path")
                if capture_path:
                    try:
                        from pathlib import Path
                        path = Path(capture_path)
                        if path.exists():
                            _latest_frame = path.read_bytes()
                            _latest_frame_time = time.time()
                            return _latest_frame
                    except Exception:
                        pass
    
    # Fallback: capture fresh frame
    try:
        png_bytes, _ = grab_png_bytes()
        _latest_frame = png_bytes
        _latest_frame_time = time.time()
        return png_bytes
    except Exception:
        return None


def update_latest_frame(frame: bytes) -> None:
    """Update the latest frame."""
    global _latest_frame, _latest_frame_time
    _latest_frame = frame
    _latest_frame_time = time.time()


def start_mjpeg_server(port: int = 8080) -> dict[str, Any]:
    """Start MJPEG streaming server."""
    global _mjpeg_server, _mjpeg_thread, _mjpeg_port
    
    if _mjpeg_server:
        return {"running": True, "port": _mjpeg_port}
    
    _mjpeg_port = port
    
    def run_server():
        global _mjpeg_server
        _mjpeg_server = HTTPServer(("0.0.0.0", port), MJPEGRequestHandler)
        _mjpeg_server.serve_forever()
    
    _mjpeg_thread = threading.Thread(target=run_server, daemon=True)
    _mjpeg_thread.start()
    
    return {"running": True, "port": port}


def stop_mjpeg_server() -> dict[str, Any]:
    """Stop MJPEG streaming server."""
    global _mjpeg_server, _mjpeg_thread
    
    if _mjpeg_server:
        _mjpeg_server.shutdown()
        _mjpeg_server = None
    
    if _mjpeg_thread:
        _mjpeg_thread.join(timeout=2.0)
        _mjpeg_thread = None
    
    return {"running": False}


def get_mjpeg_status() -> dict[str, Any]:
    """Get MJPEG server status."""
    return {
        "running": _mjpeg_server is not None,
        "port": _mjpeg_port,
        "latest_frame_time": _latest_frame_time,
    }


__all__ = [
    "start_mjpeg_server",
    "stop_mjpeg_server",
    "get_mjpeg_status",
    "update_latest_frame",
]
