from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
PM_ROOT = PACKAGE_ROOT.parent
PROJECT_ROOT = PM_ROOT.parent

SCREENSHOT_DIR = Path.home() / ".pm" / "desktop-mcp" / "screenshots"
BROWSER_PROFILE_ROOT = PROJECT_ROOT / ".pm-runtime" / "browser-profiles"
BROWSER_INSTANCE_ROOT = PROJECT_ROOT / ".pm-runtime" / "browser-instances"
BROWSER_PRESET_ROOT = PROJECT_ROOT / ".pm-runtime" / "browser-presets"
BROWSER_CAPTURE_ROOT = PROJECT_ROOT / ".pm-runtime" / "browser-captures"
DOWNLOAD_ROOT = PROJECT_ROOT / ".pm-runtime" / "downloads"
DESKTOP_WATCH_CAPTURE_ROOT = PROJECT_ROOT / ".pm-runtime" / "desktop-watch"
RUNTIME_LOG_DIR = PROJECT_ROOT / ".pm-runtime" / "logs"
RUNTIME_EVENT_LOG = RUNTIME_LOG_DIR / "desktop-mcp-events.jsonl"
MCP_LOG_PATH = Path.home() / ".pm" / "desktop-mcp" / "mcp.log"


def ensure_runtime_dirs() -> None:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
    BROWSER_INSTANCE_ROOT.mkdir(parents=True, exist_ok=True)
    BROWSER_PRESET_ROOT.mkdir(parents=True, exist_ok=True)
    BROWSER_CAPTURE_ROOT.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    DESKTOP_WATCH_CAPTURE_ROOT.mkdir(parents=True, exist_ok=True)
    RUNTIME_LOG_DIR.mkdir(parents=True, exist_ok=True)
    MCP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
