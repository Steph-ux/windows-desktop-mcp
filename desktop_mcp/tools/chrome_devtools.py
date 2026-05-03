"""Chrome DevTools MCP tools for advanced browser debugging."""

from __future__ import annotations

from typing import Any

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

from functools import wraps

from ..app import mcp
from ..helpers import ensure_windows as _ensure_windows
from ..runtime import record_event


def ensure_windows(fn):
    """Decorator to ensure function runs on Windows."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        _ensure_windows()
        return fn(*args, **kwargs)
    return wrapper


_driver_instance = None


def _get_driver():
    """Get or create Chrome WebDriver instance with DevTools enabled."""
    global _driver_instance
    if _driver_instance is None:
        options = Options()
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        options.add_argument("--enable-logging")
        options.add_argument("--log-level=0")
        _driver_instance = webdriver.Chrome(options=options)
    return _driver_instance
@ensure_windows
def start_performance_trace(url: str) -> dict[str, Any]:
    """Start Chrome with performance tracing enabled and navigate to URL."""
    try:
        global _driver_instance
        if _driver_instance is not None:
            _driver_instance.quit()
            _driver_instance = None
        
        options = Options()
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        options.add_argument("--enable-logging")
        options.add_argument("--log-level=0")
        options.add_argument("--enable-chrome-browser-cloud-management")
        
        _driver_instance = webdriver.Chrome(options=options)
        _driver_instance.get(url)
        
        record_event("start_performance_trace", url=url)
        return {
            "url": url,
            "started": True,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to start performance trace: {e}")
@ensure_windows
def stop_performance_trace() -> dict[str, Any]:
    """Stop performance tracing and collect logs."""
    try:
        global _driver_instance
        if _driver_instance is None:
            raise RuntimeError("No active performance trace")
        
        logs = _driver_instance.get_log("performance")
        
        _driver_instance.quit()
        _driver_instance = None
        
        record_event("stop_performance_trace")
        return {
            "logs": logs,
            "stopped": True,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to stop performance trace: {e}")
@ensure_windows
def get_network_logs() -> dict[str, Any]:
    """Get network logs from current Chrome session."""
    try:
        driver = _get_driver()
        logs = driver.get_log("performance")
        
        network_entries = []
        for entry in logs:
            message = entry.get("message", "{}")
            if "Network" in message:
                network_entries.append(entry)
        
        record_event("get_network_logs", count=len(network_entries))
        return {
            "count": len(network_entries),
            "logs": network_entries,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to get network logs: {e}")
@ensure_windows
def get_console_logs() -> dict[str, Any]:
    """Get console logs from current Chrome session."""
    try:
        driver = _get_driver()
        logs = driver.get_log("browser")
        
        record_event("get_console_logs", count=len(logs))
        return {
            "count": len(logs),
            "logs": logs,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to get console logs: {e}")
@ensure_windows
def get_page_metrics() -> dict[str, Any]:
    """Get page performance metrics."""
    try:
        driver = _get_driver()
        
        metrics = driver.execute_script("""
            return {
                timing: performance.timing,
                navigation: performance.navigation,
                memory: performance.memory ? {
                    usedJSHeapSize: performance.memory.usedJSHeapSize,
                    totalJSHeapSize: performance.memory.totalJSHeapSize,
                    jsHeapSizeLimit: performance.memory.jsHeapSizeLimit
                } : null
            };
        """)
        
        record_event("get_page_metrics")
        return {
            "metrics": metrics,
        }
    except Exception as e:
        raise RuntimeError(f"Failed to get page metrics: {e}")


def _validate_js_expression(code: str) -> None:
    """Basic safety check for JavaScript code executed via DevTools."""
    import re
    blocked = [
        r"\brequire\s*\(",
        r"\bchild_process\b",
        r"\bexecSync\b",
        r"\bspawnSync\b",
        r"\bfs\.\w+Sync\b",
        r"\bprocess\.exit\b",
        r"\bfetch\s*\(",
        r"\bXMLHttpRequest\b",
        r"\bnew\s+WebSocket\b",
        r"\bdocument\.cookie\b",
        r"\blocalStorage\b",
        r"\bsessionStorage\b",
    ]
    for pattern in blocked:
        if re.search(pattern, code, re.IGNORECASE):
            raise ValueError(f"JavaScript blocked: matches forbidden pattern {pattern!r}")
@ensure_windows
def evaluate_javascript(code: str) -> dict[str, Any]:
    """Execute JavaScript code in the current page (with safety checks)."""
    try:
        _validate_js_expression(code)
        driver = _get_driver()
        result = driver.execute_script(code)
        
        record_event("evaluate_javascript")
        return {
            "result": result,
            "executed": True,
        }
    except ValueError:
        raise
    except Exception as e:
        raise RuntimeError(f"Failed to execute JavaScript: {e}")


__all__ = [
    "start_performance_trace",
    "stop_performance_trace",
    "get_network_logs",
    "get_console_logs",
    "get_page_metrics",
    "evaluate_javascript",
]
