"""Headless and static browser capture MCP tools."""

from __future__ import annotations

import functools
import nest_asyncio
from pathlib import Path
from typing import Any

import anyio
from mcp.server.fastmcp import Image

from ..app import mcp
from ..browser_core import build_browser_screenshot_args, dump_dom_fallback, run_browser_command
from ..helpers import now_stamp
from ..paths import BROWSER_CAPTURE_ROOT
from ..runtime import record_event

# Allow nested event loops for Playwright async API
nest_asyncio.apply()


def threaded_tool(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
    return wrapper


def _headless_capture(
    url: str,
    width: int,
    height: int,
    wait_ms: int,
    browser: str,
    output_path: Path,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = build_browser_screenshot_args(browser, url, width, height, wait_ms, output_path)
    result = run_browser_command(browser, args, include_firefox=True)
    return {
        "path": str(output_path),
        "url": url,
        "browser": result["browser"],
        "executable": result["executable"],
        "width": max(width, 320),
        "height": max(height, 240),
        "wait_ms": max(wait_ms, 0),
    }
def browser_capture_page(url: str, width: int = 1440, height: int = 960, wait_ms: int = 1200, browser: str = "auto", path: str | None = None) -> Image:
    target_path = Path(path) if path else BROWSER_CAPTURE_ROOT / f"browser-{now_stamp()}.png"
    _headless_capture(url, width, height, wait_ms, browser, target_path)
    record_event("browser_capture_page", url=url, browser=browser, path=str(target_path))
    return Image(data=target_path.read_bytes(), format="png")
def browser_save_screenshot(url: str, width: int = 1440, height: int = 960, wait_ms: int = 1200, browser: str = "auto", prefix: str = "browser") -> dict[str, Any]:
    target_path = BROWSER_CAPTURE_ROOT / f"{prefix}-{now_stamp()}.png"
    return _headless_capture(url, width, height, wait_ms, browser, target_path)
def browser_dump_dom(url: str, wait_ms: int = 1200, browser: str = "auto") -> dict[str, Any]:
    rendered = True
    try:
        result = run_browser_command(browser, [f"--virtual-time-budget={max(wait_ms, 0)}", "--dump-dom", url])
        html = result["stdout"]
    except Exception:
        fallback = dump_dom_fallback(url)
        html = fallback["html"]
        result = fallback
        rendered = False
    return {"url": url, "browser": result["browser"], "executable": result["executable"], "html": html, "length": len(html), "rendered": rendered}
async def browser_capture_page_exact(url: str, width: int = 1440, height: int = 960, browser: str = "auto", path: str | None = None, full_page: bool = True) -> dict[str, Any]:
    from playwright.async_api import async_playwright
    
    target_path = Path(path) if path else BROWSER_CAPTURE_ROOT / f"playwright-{now_stamp()}.png"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    
    async def capture():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": max(width, 320), "height": max(height, 240)}, device_scale_factor=1)
            await page.goto(url, wait_until="networkidle")
            await page.screenshot(path=str(target_path), full_page=full_page)
            title = await page.title()
            await browser.close()
            return title
    
    title = await capture()
    record_event("browser_capture_page_exact", url=url, browser="playwright-async", path=str(target_path), full_page=full_page)
    return {"path": str(target_path), "url": url, "browser": "playwright-async", "title": title, "width": max(width, 320), "height": max(height, 240), "full_page": full_page}
async def browser_dump_dom_exact(url: str, width: int = 1440, height: int = 960, browser: str = "auto") -> dict[str, Any]:
    from playwright.async_api import async_playwright
    
    async def dump():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": max(width, 320), "height": max(height, 240)}, device_scale_factor=1)
            await page.goto(url, wait_until="networkidle")
            html = await page.content()
            title = await page.title()
            await browser.close()
            return title, html
    
    title, html = await dump()
    return {"url": url, "browser": "playwright-async", "title": title, "html": html, "length": len(html), "rendered": True}


__all__ = [
    "browser_capture_page",
    "browser_capture_page_exact",
    "browser_dump_dom",
    "browser_dump_dom_exact",
    "browser_save_screenshot",
]
