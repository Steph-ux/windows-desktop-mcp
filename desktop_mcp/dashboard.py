"""Local web dashboard for Desktop MCP — lightweight HTTP server."""
from __future__ import annotations

import json
import mimetypes
import os
import platform
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

from .paths import SCREENSHOT_DIR

WORKFLOW_DIR = SCREENSHOT_DIR / "workflows"
RECORDING_DIR = SCREENSHOT_DIR / "recordings"
PLUGIN_DIR = Path.home() / ".pm" / "desktop-mcp" / "plugins"
DASHBOARD_HTML = Path(__file__).parent / "dashboard.html"

DEFAULT_PORT = 8420


def _json_response(handler: BaseHTTPRequestHandler, data: Any, status: int = 200):
    body = json.dumps(data, indent=2, default=str).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, html: str):
    body = html.encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _file_response(handler: BaseHTTPRequestHandler, path: Path):
    if not path.exists():
        handler.send_error(404)
        return
    mime, _ = mimetypes.guess_type(str(path))
    data = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", mime or "application/octet-stream")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


# ── API Handlers ─────────────────────────────────────────────────────

def _api_status() -> dict:
    return {
        "ok": True,
        "server": "desktop-mcp-dashboard",
        "version": "0.2.0",
        "python": sys.version,
        "platform": platform.platform(),
        "uptime_s": round(time.time() - _start_time, 1),
        "dirs": {
            "screenshots": str(SCREENSHOT_DIR),
            "workflows": str(WORKFLOW_DIR),
            "recordings": str(RECORDING_DIR),
            "plugins": str(PLUGIN_DIR),
        },
    }


def _api_workflows() -> dict:
    workflows = []
    if WORKFLOW_DIR.exists():
        for f in sorted(WORKFLOW_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                workflows.append({
                    "id": data.get("id", f.stem),
                    "step_count": len(data.get("steps", [])),
                    "size_bytes": f.stat().st_size,
                    "modified": f.stat().st_mtime,
                })
            except Exception:
                workflows.append({"id": f.stem, "error": "invalid"})
    return {"workflows": workflows, "count": len(workflows)}


def _api_workflow_detail(wf_id: str) -> dict:
    path = WORKFLOW_DIR / f"{wf_id}.json"
    if not path.exists():
        return {"error": f"Workflow {wf_id!r} not found"}
    return json.loads(path.read_text())


def _api_recordings() -> dict:
    recordings = []
    if RECORDING_DIR.exists():
        for d in sorted(RECORDING_DIR.iterdir()):
            if d.is_dir():
                outputs = [str(o.name) for o in d.glob("output.*")]
                recordings.append({
                    "id": d.name,
                    "outputs": outputs,
                    "modified": d.stat().st_mtime,
                })
    return {"recordings": recordings, "count": len(recordings)}


def _api_screenshots() -> dict:
    shots = []
    if SCREENSHOT_DIR.exists():
        for f in sorted(SCREENSHOT_DIR.glob("*.png"), key=lambda x: x.stat().st_mtime, reverse=True)[:50]:
            shots.append({
                "name": f.name,
                "size_bytes": f.stat().st_size,
                "modified": f.stat().st_mtime,
                "path": f"/_files/{f.name}",
            })
    return {"screenshots": shots, "count": len(shots)}


def _api_plugins() -> dict:
    plugins = []
    if PLUGIN_DIR.exists():
        for f in sorted(PLUGIN_DIR.glob("*.py")):
            if f.name.startswith("_"):
                continue
            plugins.append({
                "name": f.stem,
                "size_bytes": f.stat().st_size,
                "modified": f.stat().st_mtime,
            })
    return {"plugins": plugins, "count": len(plugins)}


# ── Request Handler ──────────────────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Quiet logs

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        # HTML dashboard
        if path == "/" or path == "/index.html":
            if DASHBOARD_HTML.exists():
                _html_response(self, DASHBOARD_HTML.read_text(encoding="utf-8"))
            else:
                _html_response(self, "<h1>Dashboard HTML not found</h1>")
            return

        # API routes
        if path == "/api/status":
            return _json_response(self, _api_status())
        if path == "/api/workflows":
            return _json_response(self, _api_workflows())
        if path.startswith("/api/workflows/"):
            wf_id = path.split("/")[-1]
            return _json_response(self, _api_workflow_detail(wf_id))
        if path == "/api/recordings":
            return _json_response(self, _api_recordings())
        if path == "/api/screenshots":
            return _json_response(self, _api_screenshots())
        if path == "/api/plugins":
            return _json_response(self, _api_plugins())

        # Serve screenshot files
        if path.startswith("/_files/"):
            fname = path[len("/_files/"):]
            fpath = SCREENSHOT_DIR / fname
            if fpath.exists() and fpath.is_file():
                return _file_response(self, fpath)

        self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()


_start_time = time.time()


def run_dashboard(port: int = DEFAULT_PORT, open_browser: bool = True):
    """Start the dashboard HTTP server."""
    server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"🖥️  Desktop MCP Dashboard running at {url}")
    print("   Press Ctrl+C to stop.\n")

    if open_browser:
        import webbrowser
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Dashboard stopped.")
        server.server_close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Desktop MCP Dashboard")
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    run_dashboard(port=args.port, open_browser=not args.no_open)


if __name__ == "__main__":
    main()
