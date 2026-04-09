from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
import uuid
from types import SimpleNamespace
from pathlib import Path

import anyio
import mcp
import pytest
import numpy as np
from PIL import Image as PILImage

from desktop_mcp import browser_core, browser_sessions, desktop_core, runtime, tools_browser, tools_desktop
from desktop_mcp.helpers import wait_until
from desktop_mcp.ocr_core import find_ocr_text_spans
from desktop_mcp.shared.playwright_utils import validate_js_expression
from desktop_mcp.tools import browser_sessions as session_tools
from desktop_mcp.tools import capture as capture_tools
from desktop_mcp.tools import input as input_tools
from desktop_mcp.tools import runtime as runtime_tools


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_path() -> Path:
    root = Path(tempfile.gettempdir()) / "windows-desktop-mcp-tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"case-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=False)
    yield path


def test_build_browser_screenshot_args_chromium() -> None:
    target = browser_core.BROWSER_CAPTURE_ROOT / "shot.png"
    args = browser_core.build_browser_screenshot_args(
        browser="chrome",
        url="https://example.com",
        width=1200,
        height=800,
        wait_ms=1500,
        target_path=target,
    )
    assert f"--window-size=1200,800" in args
    assert f"--virtual-time-budget=1500" in args
    assert f"--screenshot={target}" in args


def test_build_browser_screenshot_args_firefox() -> None:
    target = browser_core.BROWSER_CAPTURE_ROOT / "shot.png"
    args = browser_core.build_browser_screenshot_args(
        browser="firefox",
        url="https://example.com",
        width=1200,
        height=800,
        wait_ms=1500,
        target_path=target,
    )
    assert "--virtual-time-budget=1500" not in args
    assert args[-1] == "https://example.com"
    assert "--screenshot" in args


def test_validate_screen_point_rejects_out_of_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop_core, "virtual_screen_bounds", lambda: {
        "left": -1920,
        "top": 0,
        "right": 1920,
        "bottom": 1080,
        "width": 3840,
        "height": 1080,
    })
    with pytest.raises(ValueError):
        desktop_core.validate_screen_point(2500, 42)


def test_window_capture_bounds_supports_virtual_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop_core, "ensure_windows", lambda: None)
    monkeypatch.setattr(desktop_core, "virtual_screen_bounds", lambda: {
        "left": -1920,
        "top": -200,
        "right": 3200,
        "bottom": 1440,
        "width": 5120,
        "height": 1640,
    })
    region = desktop_core.window_capture_bounds(
        {"left": 2390, "top": -10, "right": 4810, "bottom": 1300},
        padding=0,
    )
    assert region == {"left": 2390, "top": -10, "width": 810, "height": 1310}


def test_move_resize_window_data_falls_back_to_win32(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeWindow:
        handle = 123

        def move_window(self, **kwargs):
            raise AttributeError("no move_window")

    moved: dict[str, int] = {}
    monkeypatch.setattr(desktop_core, "find_window", lambda handle=None, title_regex=None, visible_only=True: FakeWindow())
    monkeypatch.setattr(desktop_core, "window_info", lambda window: {"handle": 123})
    monkeypatch.setattr(
        desktop_core.win32gui,
        "MoveWindow",
        lambda handle, x, y, width, height, repaint: moved.update(
            {"handle": handle, "x": x, "y": y, "width": width, "height": height, "repaint": int(repaint)}
        ),
    )
    result = desktop_core.move_resize_window_data(handle=123, x=10, y=20, width=300, height=200)
    assert result["handle"] == 123
    assert moved["handle"] == 123
    assert moved["width"] == 300


def test_cleanup_stale_playwright_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    closed_ids: list[str] = []
    original_sessions = browser_core.PLAYWRIGHT_SESSIONS.copy()
    browser_core.PLAYWRIGHT_SESSIONS.clear()
    browser_core.PLAYWRIGHT_SESSIONS.update(
        {
            "stale": {"last_used_at": 0.0, "created_at": 0.0},
            "fresh": {"last_used_at": 10_450.0, "created_at": 10_450.0},
        }
    )

    monkeypatch.setattr(browser_core.time, "time", lambda: 10_600.0)

    def fake_close(session_id: str) -> bool:
        closed_ids.append(session_id)
        browser_core.PLAYWRIGHT_SESSIONS.pop(session_id, None)
        return True

    monkeypatch.setattr(browser_sessions, "close_playwright_session", fake_close)
    try:
        result = browser_core.cleanup_stale_playwright_sessions(max_age_minutes=5)
        assert "fresh" in browser_core.PLAYWRIGHT_SESSIONS
    finally:
        browser_core.PLAYWRIGHT_SESSIONS.clear()
        browser_core.PLAYWRIGHT_SESSIONS.update(original_sessions)

    assert result["checked"] == 2
    assert result["closed"] == 1
    assert closed_ids == ["stale"]


def test_desktop_mcp_server_exposes_entry_symbols() -> None:
    server = importlib.import_module("desktop_mcp.server")
    package = importlib.import_module("desktop_mcp")
    assert package.main is server.main
    assert server.BROWSER_CAPTURE_ROOT.name == "browser-captures"


def test_desktop_mcp_package_exposes_main() -> None:
    package = importlib.import_module("desktop_mcp")
    assert callable(package.main)


def test_cleanup_stale_browser_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stale = tmp_path / "pm-browser-mcp-stale"
    fresh = tmp_path / "pm-browser-mcp-fresh"
    other = tmp_path / "keep-me"
    stale.mkdir()
    fresh.mkdir()
    other.mkdir()

    monkeypatch.setattr(browser_core, "BROWSER_PROFILE_ROOT", tmp_path)
    monkeypatch.setattr(browser_core.time, "time", lambda: 10_000.0)
    stale_time = 1_000.0
    fresh_time = 9_900.0
    os.utime(stale, (stale_time, stale_time))
    os.utime(fresh, (fresh_time, fresh_time))

    result = browser_core.cleanup_stale_browser_profiles(max_age_hours=1)

    assert result["checked"] == 2
    assert result["removed"] == 1
    assert stale.exists() is False
    assert fresh.exists() is True
    assert other.exists() is True


def test_runtime_event_log_records_and_clears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    event_log = tmp_path / "events.jsonl"
    monkeypatch.setattr(runtime, "RUNTIME_EVENT_LOG", event_log)
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: None)
    runtime.clear_events()

    event = runtime.record_event("test_event", foo="bar")
    assert event["type"] == "test_event"
    assert event_log.exists() is True
    status = runtime.runtime_status()
    assert status["recent_event_count"] >= 1
    recent = runtime.recent_events(limit=5)
    assert recent[-1]["foo"] == "bar"

    cleared = runtime.clear_events()
    assert cleared["cleared"] >= 1


def test_runtime_health_check_reports_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: None)
    monkeypatch.setattr(
        runtime,
        "browser_availability",
        lambda include_firefox=True: {
            "chrome_available": True,
            "chrome_executable": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "edge_available": False,
            "edge_executable": None,
            "firefox_available": False,
            "firefox_executable": None,
            "any_browser_available": True,
        },
    )
    monkeypatch.setattr(
        runtime,
        "ocr_availability",
        lambda: {
            "pytesseract_available": True,
            "tesseract_available": True,
            "tesseract_executable": r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            "ocr_ready": True,
        },
    )
    monkeypatch.setattr(runtime.importlib.util, "find_spec", lambda name: object())
    result = runtime.runtime_health_check()
    assert result["status"] == "ok"
    assert "status" in result
    assert "checks" in result
    assert "playwright_sessions" in result["checks"]
    assert result["checks"]["chrome_available"] is True
    assert result["checks"]["any_browser_available"] is True
    assert result["checks"]["playwright_installed"] is True
    assert result["checks"]["ocr_ready"] is True


def test_runtime_health_check_degrades_without_browser_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: None)
    monkeypatch.setattr(
        runtime,
        "browser_availability",
        lambda include_firefox=True: {
            "chrome_available": False,
            "chrome_executable": None,
            "edge_available": False,
            "edge_executable": None,
            "firefox_available": False,
            "firefox_executable": None,
            "any_browser_available": False,
        },
    )
    monkeypatch.setattr(
        runtime,
        "ocr_availability",
        lambda: {
            "pytesseract_available": False,
            "tesseract_available": False,
            "tesseract_executable": None,
            "ocr_ready": False,
        },
    )
    monkeypatch.setattr(runtime.importlib.util, "find_spec", lambda name: object())
    result = runtime.runtime_health_check()
    assert result["status"] == "degraded"
    assert result["checks"]["any_browser_available"] is False


def test_runtime_health_check_degrades_without_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "ensure_runtime_dirs", lambda: None)
    monkeypatch.setattr(
        runtime,
        "browser_availability",
        lambda include_firefox=True: {
            "chrome_available": True,
            "chrome_executable": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "edge_available": False,
            "edge_executable": None,
            "firefox_available": True,
            "firefox_executable": r"C:\Program Files\Mozilla Firefox\firefox.exe",
            "any_browser_available": True,
        },
    )
    monkeypatch.setattr(
        runtime,
        "ocr_availability",
        lambda: {
            "pytesseract_available": True,
            "tesseract_available": True,
            "tesseract_executable": r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            "ocr_ready": True,
        },
    )
    monkeypatch.setattr(runtime.importlib.util, "find_spec", lambda name: None)
    result = runtime.runtime_health_check()
    assert result["status"] == "degraded"
    assert result["checks"]["playwright_installed"] is False


def test_browser_availability_uses_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        browser_core,
        "browser_candidates",
        lambda browser="auto", include_firefox=False: [
            ("chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            ("firefox", r"C:\Program Files\Mozilla Firefox\firefox.exe"),
        ],
    )
    result = browser_core.browser_availability(include_firefox=True)
    assert result["chrome_available"] is True
    assert result["firefox_available"] is True
    assert result["edge_available"] is False
    assert result["any_browser_available"] is True


def test_attach_playwright_page_observers_records_events() -> None:
    session: dict[str, object] = {}
    handlers: dict[str, object] = {}

    class FakePage:
        def on(self, event_name: str, handler) -> None:
            handlers[event_name] = handler

    page = FakePage()
    browser_core.attach_playwright_page_observers(session, "page1", page)
    assert "console" in handlers
    assert "pageerror" in handlers
    assert "requestfailed" in handlers

    class FakeMessage:
        type = "error"
        text = "boom"
        location = {"url": "https://example.com"}

    class FakeRequest:
        url = "https://example.com/api"
        method = "GET"

        def failure(self):
            return {"errorText": "net::ERR_FAILED"}

    handlers["console"](FakeMessage())
    handlers["pageerror"](RuntimeError("js broke"))
    handlers["requestfailed"](FakeRequest())

    buffers = browser_core.get_playwright_page_event_buffers(session, "page1")
    assert buffers["console"][-1]["text"] == "boom"
    assert buffers["page_errors"][-1]["message"] == "js broke"
    assert buffers["request_failures"][-1]["error_text"] == "net::ERR_FAILED"


def test_ocr_availability_shape() -> None:
    from desktop_mcp.ocr_core import ocr_availability

    result = ocr_availability()
    assert "pytesseract_available" in result
    assert "tesseract_available" in result
    assert "ocr_ready" in result


def test_find_ocr_text_spans_merges_words() -> None:
    words = [
        {"text": "Hello", "confidence": 90, "left": 10, "top": 20, "width": 30, "height": 10},
        {"text": "world", "confidence": 88, "left": 45, "top": 20, "width": 35, "height": 10},
    ]
    matches = find_ocr_text_spans(words, text="hello world", exact=True)
    assert len(matches) == 1
    assert matches[0]["left"] == 10
    assert matches[0]["width"] == 70
    assert matches[0]["word_count"] == 2


def test_find_ocr_text_spans_partial_match() -> None:
    words = [
        {"text": "Open", "confidence": 92, "left": 10, "top": 20, "width": 25, "height": 10},
        {"text": "Settings", "confidence": 91, "left": 40, "top": 20, "width": 50, "height": 10},
    ]
    matches = find_ocr_text_spans(words, text="settings", exact=False)
    assert len(matches) >= 1
    assert any("settings" in match["text"] for match in matches)


def test_watch_change_summary_detects_hash_changes() -> None:
    states = [
        {"ts": "t1", "hash": "a", "summary": {"window": {"title": "One"}, "ocr_excerpt": "alpha"}, "capture_path": "one.png"},
        {"ts": "t2", "hash": "a", "summary": {"window": {"title": "One"}, "ocr_excerpt": "alpha"}, "capture_path": "one.png"},
        {"ts": "t3", "hash": "b", "summary": {"window": {"title": "Two"}, "ocr_excerpt": "beta"}, "capture_path": "two.png"},
    ]
    summary = tools_desktop._watch_change_summary(states)
    assert summary["count"] == 1
    assert summary["changes"][0]["from_title"] == "One"
    assert summary["changes"][0]["to_title"] == "Two"


def test_compare_image_paths_detects_visual_delta(tmp_path: Path) -> None:
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    image_before = PILImage.new("RGB", (20, 20), color="white")
    image_after = PILImage.new("RGB", (20, 20), color="white")
    image_after.putpixel((5, 5), (255, 0, 0))
    image_before.save(before)
    image_after.save(after)
    result = tools_desktop._compare_image_paths(str(before), str(after))
    assert result["same_size"] is True
    assert result["changed_pixels"] >= 1
    assert result["diff_bbox"] is not None


def test_get_playwright_session_error_is_explicit() -> None:
    with pytest.raises(ValueError, match="expired|cleaned up|never existed"):
        browser_core.get_playwright_session("missing-session")


def test_wait_until_includes_last_error_context() -> None:
    with pytest.raises(TimeoutError, match="Last error: RuntimeError: boom"):
        wait_until(time.time() + 0.02, 0.0, lambda: (_ for _ in ()).throw(RuntimeError("boom")), description="test wait")


def test_browser_open_session_handles_pre_session_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeContext:
        def new_page(self):
            raise AssertionError("should not get here")

    class FakeEngine:
        def new_context(self, **kwargs):
            raise RuntimeError("context failed")

        def close(self):
            return None

    class FakePlaywrightCM:
        def __exit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(
        tools_browser,
        "open_playwright_runtime",
        lambda browser, headless=True: (FakePlaywrightCM(), object(), FakeEngine(), "chrome"),
    )
    monkeypatch.setattr(tools_browser, "cleanup_stale_playwright_sessions", lambda *args, **kwargs: {"checked": 0, "closed": 0})
    monkeypatch.setattr(tools_browser, "record_event", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="context failed"):
        tools_browser.browser_open_session("https://example.com")


def test_browser_wait_for_dom_change_uses_browser_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePage:
        url = "https://example.com"

        def content(self):
            return "<html>before</html>"

        def evaluate(self, expression, *args):
            return 0

        def wait_for_function(self, expression, arg, timeout):
            assert "pmMcpDomRevision" in expression
            assert arg == 0
            assert timeout == 500

    monkeypatch.setattr(tools_browser, "get_playwright_page", lambda session_id, page_id=None: ({}, "page1", FakePage()))
    result = tools_browser.browser_wait_for_dom_change("session1", timeout_ms=500)
    assert result["changed"] is True


def test_browser_wait_for_visual_change_uses_browser_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeLocator:
        def wait_for(self, timeout, state):
            assert timeout == 500
            assert state == "visible"

        def screenshot(self):
            return b"after-image"

    class FakePage:
        def __init__(self):
            self.locator_instance = FakeLocator()

        def locator(self, selector):
            assert selector == "#target"
            return type("LocatorWrapper", (), {"first": self.locator_instance})()

        def evaluate(self, expression, *args):
            return '{"text":"before"}'

        def wait_for_function(self, expression, arg, timeout):
            assert "current !== baseline" in expression
            assert arg[0] == "#target"
            assert timeout == 500

    monkeypatch.setattr(tools_browser, "get_playwright_page", lambda session_id, page_id=None: ({}, "page1", FakePage()))
    result = tools_browser.browser_wait_for_visual_change("session1", selector="#target", timeout_ms=500)
    assert result["changed"] is True


def test_intent_click_falls_back_to_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_desktop, "_uia_click_by_text", lambda **kwargs: (_ for _ in ()).throw(ValueError("no uia")))
    monkeypatch.setattr(
        tools_desktop,
        "click_ocr_text",
        lambda **kwargs: {"ok": True, "match": {"center_x": 10, "center_y": 20}},
    )
    result = tools_desktop.intent_click("clique sur le bouton Enregistrer")
    assert result["source"] == "ocr"
    assert result["query"] == "Enregistrer"


def test_watch_until_goal_matches_uia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tools_desktop,
        "focused_window_summary",
        lambda **kwargs: {
            "window": {"title": "App"},
            "uia_titles": ["Accueil", "Enregistrer"],
            "ocr_excerpt": "",
        },
    )
    result = tools_desktop.watch_until_goal("text: Enregistrer", max_seconds=0.2, interval_seconds=0.01)
    assert result["matched"] is True
    assert result["source"] == "uia"


def test_screen_annotate_writes_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sample = PILImage.new("RGB", (200, 120), "white")
    buffer = Path(tmp_path / "sample.png")
    sample.save(buffer)
    monkeypatch.setattr(tools_desktop, "_uia_annotation_items", lambda **kwargs: ({"handle": 1}, {"left": 0, "top": 0, "width": 200, "height": 120}, [{"label": "Save", "left": 10, "top": 10, "width": 80, "height": 24, "screen_left": 10, "screen_top": 10, "handle": 1}]))
    monkeypatch.setattr(tools_desktop, "grab_png_bytes", lambda region=None: (buffer.read_bytes(), {"left": 0, "top": 0, "width": 200, "height": 120}))
    result = tools_desktop.screen_annotate(mode="uia", path=str(tmp_path / "annotated.png"))
    assert Path(result["path"]).exists()
    assert result["count"] == 1


def test_browser_intercept_requests_registers_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    session = {"session_id": "abc", "intercept_rules": [], "context": object()}
    monkeypatch.setattr(tools_browser, "get_playwright_session", lambda session_id: session)
    monkeypatch.setattr(tools_browser, "apply_playwright_intercepts", lambda s: {"session_id": "abc", "applied": len(s["intercept_rules"]), "rule_ids": [rule["rule_id"] for rule in s["intercept_rules"]]})
    result = tools_browser.browser_intercept_requests("abc", pattern="**/api/**", action="abort", methods=["GET"])
    assert result["action"] == "abort"
    assert result["applied"] == 1
    assert session["intercept_rules"][0]["methods"] == ["GET"]


def test_browser_eval_rejects_blocked_patterns() -> None:
    with pytest.raises(ValueError, match="bloquee"):
        tools_browser.browser_eval("session", "process.exit()", page_id="page")


def test_validate_js_expression_allows_legit_process_words() -> None:
    validate_js_expression("document.querySelectorAll('.process-step').length")
    validate_js_expression("el.getAttribute('data-process')")


def test_browser_fill_form_executes_supported_field_types(monkeypatch: pytest.MonkeyPatch) -> None:
    actions: list[tuple[str, str, object]] = []

    class FakeLocator:
        def __init__(self, selector: str):
            self.selector = selector
            self.checked = False

        @property
        def first(self):
            return self

        def fill(self, value):
            actions.append(("fill", self.selector, value))

        def select_option(self, value=None):
            actions.append(("select", self.selector, value))

        def is_checked(self):
            return self.checked

        def click(self):
            actions.append(("click", self.selector, None))
            self.checked = not self.checked

    class FakePage:
        def locator(self, selector: str):
            return FakeLocator(selector)

    monkeypatch.setattr(tools_browser, "get_playwright_page", lambda session_id, page_id=None: ({}, "page1", FakePage()))
    result = tools_browser.browser_fill_form(
        "session1",
        [
            {"selector": "#name", "value": "Alice", "type": "text"},
            {"selector": "#plan", "value": "pro", "type": "select"},
            {"selector": "#agree", "checked": True, "type": "checkbox"},
        ],
        submit_selector="#submit",
    )
    assert result["submitted"] is True
    assert ("fill", "#name", "Alice") in actions
    assert ("select", "#plan", "pro") in actions
    assert ("click", "#agree", None) in actions
    assert ("click", "#submit", None) in actions


def test_type_text_requires_expected_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools_desktop, "focused_window_data", lambda: {"handle": 7})
    with pytest.raises(RuntimeError, match="attendue"):
        tools_desktop.type_text("hello", require_handle=8)


def test_type_text_unicode_uses_clipboard_and_paste(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(input_tools, "focused_window_data", lambda: {"handle": 7})
    monkeypatch.setattr(input_tools.win32clipboard, "OpenClipboard", lambda: calls.append(("open", None)))
    monkeypatch.setattr(input_tools.win32clipboard, "EmptyClipboard", lambda: calls.append(("empty", None)))
    monkeypatch.setattr(
        input_tools.win32clipboard,
        "SetClipboardData",
        lambda fmt, text: calls.append(("set", (fmt, text))),
    )
    monkeypatch.setattr(input_tools.win32clipboard, "CloseClipboard", lambda: calls.append(("close", None)))
    monkeypatch.setattr(input_tools.pyautogui, "hotkey", lambda *keys: calls.append(("hotkey", keys)))
    result = input_tools.type_text_unicode("éà🙂", require_handle=7)
    assert result["method"] == "clipboard"
    assert ("hotkey", ("ctrl", "v")) in calls


def test_macro_record_list_replay_and_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    actions: list[tuple[str, object]] = []
    runtime_tools.macro_clear()
    monkeypatch.setattr(runtime_tools.pyautogui, "click", lambda **kwargs: actions.append(("click", kwargs)))
    monkeypatch.setattr(runtime_tools.pyautogui, "write", lambda text, interval=0.0: actions.append(("type", (text, interval))))
    monkeypatch.setattr(runtime_tools.pyautogui, "hotkey", lambda *keys, interval=0.0: actions.append(("hotkey", (keys, interval))))
    runtime_tools.macro_record_action("demo", {"action": "click", "x": 10, "y": 20})
    runtime_tools.macro_record_action("demo", {"action": "type", "text": "Hello"})
    runtime_tools.macro_record_action("demo", {"action": "hotkey", "keys": ["ctrl", "s"]})
    listed = runtime_tools.macro_list()
    assert listed["macros"]["demo"] == 3
    replayed = runtime_tools.macro_replay("demo")
    assert replayed["steps_executed"] == 3
    assert actions[0][0] == "click"
    assert actions[1][0] == "type"
    assert actions[2][0] == "hotkey"
    cleared = runtime_tools.macro_clear("demo")
    assert cleared["cleared"] == 1


def test_browser_open_session_supports_auto_viewport(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePage:
        url = "https://example.com"

        def goto(self, url, wait_until="networkidle"):
            self.url = url

        def title(self):
            return "Example"

    class FakeContext:
        def __init__(self):
            self.kwargs = None

        def new_page(self):
            return FakePage()

    class FakeEngine:
        def __init__(self):
            self.context = FakeContext()

        def new_context(self, **kwargs):
            self.context.kwargs = kwargs
            return self.context

        def close(self):
            return None

    class FakePlaywrightCM:
        def __exit__(self, exc_type, exc, tb):
            return None

    engine = FakeEngine()
    monkeypatch.setattr(session_tools, "cleanup_stale_playwright_sessions", lambda *args, **kwargs: {"checked": 0, "closed": 0})
    monkeypatch.setattr(
        session_tools,
        "open_playwright_runtime",
        lambda browser, headless=True: (FakePlaywrightCM(), object(), engine, "chrome"),
    )
    monkeypatch.setattr(session_tools, "store_playwright_session", lambda session: None)
    monkeypatch.setattr(session_tools, "register_playwright_page", lambda session, page, make_active=True: "page1")
    monkeypatch.setattr(session_tools, "record_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(session_tools.pyautogui, "size", lambda: SimpleNamespace(width=1280, height=800))
    async def scenario() -> None:
        result = await session_tools.browser_open_session("https://example.com", width="auto", height="auto")
        assert result["width"] == 1280
        assert result["height"] == 800
        assert engine.context.kwargs["viewport"] == {"width": 1280, "height": 800}

    anyio.run(scenario)


def test_browser_open_session_supports_persistent_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakePage:
        url = "https://example.com"
        viewport = None

        def goto(self, url, wait_until="networkidle"):
            self.url = url

        def title(self):
            return "Example"

        def set_viewport_size(self, viewport):
            self.viewport = viewport

    class FakeContext:
        def __init__(self):
            self.pages = [FakePage()]

        def new_page(self):
            page = FakePage()
            self.pages.append(page)
            return page

    class FakePlaywrightCM:
        def __exit__(self, exc_type, exc, tb):
            return None

    fake_context = FakeContext()
    monkeypatch.setattr(session_tools, "cleanup_stale_playwright_sessions", lambda *args, **kwargs: {"checked": 0, "closed": 0})
    monkeypatch.setattr(
        session_tools,
        "_launch_persistent_context",
        lambda browser, profile_name, headless=True: (FakePlaywrightCM(), object(), object(), fake_context, "chrome", tmp_path / profile_name),
    )
    monkeypatch.setattr(session_tools, "store_playwright_session", lambda session: None)
    monkeypatch.setattr(session_tools, "register_playwright_page", lambda session, page, make_active=True: "page1")
    monkeypatch.setattr(session_tools, "record_event", lambda *args, **kwargs: None)

    async def scenario() -> None:
        result = await session_tools.browser_open_session(
            "https://example.com",
            width=1200,
            height=800,
            profile_name="work",
            persistent_profile=True,
        )
        assert result["persistent_profile"] is True
        assert result["profile_name"] == "work"
        assert result["profile_path"].endswith("work")
        assert fake_context.pages[0].viewport == {"width": 1200, "height": 800}

    anyio.run(scenario)


def test_browser_open_session_applies_storage_state_and_init_scripts(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePage:
        def __init__(self) -> None:
            self.url = "https://example.com"
            self.viewport = None

        def goto(self, url: str, wait_until: str = "networkidle") -> None:
            self.url = url

        def title(self) -> str:
            return "Example Domain"

    class FakeContext:
        def __init__(self) -> None:
            self.pages: list[FakePage] = []
            self.init_scripts: list[str] = []
            self.permissions: list[str] = []

        def new_page(self) -> FakePage:
            page = FakePage()
            self.pages.append(page)
            return page

        def add_init_script(self, path: str) -> None:
            self.init_scripts.append(path)

        def grant_permissions(self, permissions: list[str]) -> None:
            self.permissions.extend(permissions)

    class FakeEngine:
        def __init__(self) -> None:
            self.context_kwargs = None

        def new_context(self, **kwargs):
            self.context_kwargs = kwargs
            return FakeContext()

        def close(self) -> None:
            return None

    fake_engine = FakeEngine()
    monkeypatch.setattr(session_tools, "cleanup_stale_playwright_sessions", lambda *args, **kwargs: None)
    monkeypatch.setattr(session_tools, "open_playwright_runtime", lambda browser="auto", headless=True: (SimpleNamespace(__exit__=lambda *a: None), object(), fake_engine, "chrome"))
    monkeypatch.setattr(session_tools, "store_playwright_session", lambda session: None)
    monkeypatch.setattr(session_tools, "register_playwright_page", lambda session, page, make_active=True: "page1")
    monkeypatch.setattr(session_tools.pyautogui, "size", lambda: SimpleNamespace(width=1440, height=960))

    init_script = r"C:\fake\init.js"
    storage_state = r"C:\fake\storage.json"

    async def scenario() -> None:
        result = await session_tools.browser_open_session(
            "https://example.com",
            storage_state_path=str(storage_state),
            init_script_paths=[str(init_script)],
            grant_permissions=["clipboard-read", "clipboard-write"],
        )
        assert result["storage_state_path"] == str(storage_state)
        assert result["init_script_paths"] == [str(init_script)]
        assert result["granted_permissions"] == ["clipboard-read", "clipboard-write"]
        assert fake_engine.context_kwargs["storage_state"] == str(storage_state)

    anyio.run(scenario)


def test_browser_list_profiles_reads_named_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeFile:
        def __init__(self, size: int) -> None:
            self._size = size

        def is_file(self) -> bool:
            return True

        def stat(self):
            return SimpleNamespace(st_size=self._size)

    class FakeProfileDir:
        def __init__(self, name: str, sizes: list[int]) -> None:
            self.name = name
            self._sizes = sizes

        def __lt__(self, other) -> bool:
            return self.name < other.name

        def is_dir(self) -> bool:
            return True

        def rglob(self, pattern: str):
            return [FakeFile(size) for size in self._sizes]

        def __str__(self) -> str:
            return f"C:/fake/{self.name}"

    class FakeNamedRoot:
        def exists(self) -> bool:
            return True

        def iterdir(self):
            return [FakeProfileDir("work", [1]), FakeProfileDir("personal", [2])]

    class FakeProfileRoot:
        def __truediv__(self, part: str):
            assert part == "named"
            return FakeNamedRoot()

    monkeypatch.setattr(session_tools, "BROWSER_PROFILE_ROOT", FakeProfileRoot())
    monkeypatch.setattr(session_tools, "_read_profile_manifest", lambda profile_name: None)

    async def scenario() -> None:
        result = await session_tools.browser_list_profiles()
        assert result["count"] == 2
        assert {item["name"] for item in result["profiles"]} == {"work", "personal"}

    anyio.run(scenario)


def test_browser_start_instance_reuses_running_session(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePage:
        url = "https://example.com"

    fake_session = {
        "pages": {"page1": FakePage()},
        "active_page_id": "page1",
        "browser_name": "chrome",
        "headless": True,
        "profile_name": "work",
        "profile_path": r"C:\profiles\work",
        "persistent_profile": True,
        "instance_name": "work-browser",
        "created_at": time.time(),
    }
    monkeypatch.setattr(session_tools, "list_playwright_sessions", lambda: [("sess1", fake_session)])
    monkeypatch.setattr(session_tools, "refresh_playwright_pages", lambda session: None)
    monkeypatch.setattr(session_tools, "page_title", lambda page: "Example Domain")
    monkeypatch.setattr(session_tools, "playwright_session_age_seconds", lambda session: 12.3)

    async def scenario() -> None:
        result = await session_tools.browser_start_instance("work-browser", url="https://ignored.example")
        assert result["reused"] is True
        assert result["session_id"] == "sess1"
        assert result["instance_name"] == "work-browser"

    anyio.run(scenario)


def test_browser_list_instances_merges_running_and_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePage:
        url = "https://example.com"

    fake_session = {
        "pages": {"page1": FakePage()},
        "active_page_id": "page1",
        "browser_name": "chrome",
        "headless": True,
        "profile_name": "work",
        "profile_path": r"C:\profiles\work",
        "persistent_profile": True,
        "instance_name": "work-browser",
        "created_at": time.time(),
    }
    monkeypatch.setattr(
        session_tools,
        "_list_instance_manifests",
        lambda: [{"instance_name": "notes-browser", "status": "stopped", "manifest_path": "C:/fake/notes-browser.json"}],
    )
    monkeypatch.setattr(session_tools, "list_playwright_sessions", lambda: [("sess1", fake_session)])
    monkeypatch.setattr(session_tools, "refresh_playwright_pages", lambda session: None)
    monkeypatch.setattr(session_tools, "page_title", lambda page: "Example Domain")
    monkeypatch.setattr(session_tools, "playwright_session_age_seconds", lambda session: 12.3)

    async def scenario() -> None:
        result = await session_tools.browser_list_instances()
        names = {item["instance_name"] for item in result["instances"]}
        assert names == {"notes-browser", "work-browser"}
        live = next(item for item in result["instances"] if item["instance_name"] == "work-browser")
        assert live["running"] is True
        stopped = next(item for item in result["instances"] if item["instance_name"] == "notes-browser")
        assert stopped["running"] is False

    anyio.run(scenario)


def test_browser_update_profile_merges_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_tools, "_named_profile_path", lambda name: SimpleNamespace(exists=lambda: True))
    monkeypatch.setattr(
        session_tools,
        "_read_profile_manifest",
        lambda name: {"browser": "chrome", "description": "old", "tags": ["a"], "preferred_url": "https://old.example"},
    )
    written: dict[str, Any] = {}
    monkeypatch.setattr(
        session_tools,
        "_write_profile_manifest",
        lambda name, payload: written.update({"name": name, **payload}) or {"profile_name": name, **payload},
    )
    monkeypatch.setattr(
        session_tools,
        "_profile_payload",
        lambda name, path, browser="auto": {"name": name, "browser": browser, "metadata": written},
    )

    async def scenario() -> None:
        result = await session_tools.browser_update_profile("work", description="new", tags=["x", "y"])
        assert result["updated"] is True
        assert written["description"] == "new"
        assert written["tags"] == ["x", "y"]
        assert written["preferred_url"] == "https://old.example"

    anyio.run(scenario)


def test_browser_export_profile_config_writes_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_tools, "_named_profile_path", lambda name: SimpleNamespace(exists=lambda: True))
    monkeypatch.setattr(
        session_tools,
        "_read_profile_manifest",
        lambda name: {"browser": "chrome", "description": "desc", "tags": ["a"], "preferred_url": "https://example.com"},
    )
    written: dict[str, str] = {}
    monkeypatch.setattr(Path, "mkdir", lambda self, parents=False, exist_ok=False: None)
    monkeypatch.setattr(Path, "write_text", lambda self, text, encoding="utf-8": written.update({"path": str(self), "text": text}))
    monkeypatch.setattr(Path, "exists", lambda self: True)

    async def scenario() -> None:
        result = await session_tools.browser_export_profile_config("work", path=r"C:\fake\profile.json")
        assert result["path"] == r"C:\fake\profile.json"
        assert '"profile_name": "work"' in written["text"]

    anyio.run(scenario)


def test_browser_import_profile_config_writes_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    config_data = {
        "profile_name": "work",
        "browser": "chrome",
        "description": "desc",
        "tags": ["a", "b"],
        "preferred_url": "https://example.com",
    }
    monkeypatch.setattr(Path, "read_text", lambda self, encoding="utf-8": json.dumps(config_data))
    monkeypatch.setattr(Path, "mkdir", lambda self, parents=False, exist_ok=False: None)
    written: dict[str, Any] = {}
    monkeypatch.setattr(session_tools, "_write_profile_manifest", lambda name, payload: written.update({"name": name, **payload}) or {"profile_name": name, **payload})

    async def scenario() -> None:
        result = await session_tools.browser_import_profile_config(r"C:\fake\profile.json", profile_name_override="override")
        assert result["imported"] is True
        assert written["name"] == "override"
        assert written["preferred_url"] == "https://example.com"

    anyio.run(scenario)


def test_browser_save_and_get_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    written: dict[str, Any] = {}
    monkeypatch.setattr(session_tools, "_write_preset", lambda name, payload: written.update({"preset_name": name, **payload}) or {"preset_name": name, **payload})
    monkeypatch.setattr(session_tools, "_preset_path", lambda name: Path(rf"C:\fake\{name}.json"))
    monkeypatch.setattr(session_tools, "_read_preset", lambda name: {"preset_name": name, "browser": "chrome"})

    async def scenario() -> None:
        saved = await session_tools.browser_save_preset("daily", browser="chrome", headless=True, expected_title="Inbox")
        assert saved["saved"] is True
        assert written["expected_title"] == "Inbox"
        fetched = await session_tools.browser_get_preset("daily")
        assert fetched["preset"]["browser"] == "chrome"

    anyio.run(scenario)


def test_browser_open_session_applies_preset_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    original = session_tools._browser_open_session_impl

    def fake_impl(*args, **kwargs):
        captured.update(kwargs)
        return {"ok": True, **kwargs}

    monkeypatch.setattr(session_tools, "_browser_open_session_impl", fake_impl)

    async def scenario() -> None:
        result = await session_tools.browser_open_session("https://example.com", preset_name="daily")
        assert result["ok"] is True
        assert captured["preset_name"] == "daily"

    anyio.run(scenario)
    monkeypatch.setattr(session_tools, "_browser_open_session_impl", original)


def test_browser_list_and_get_network_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = {"page_event_buffers": {}}

    monkeypatch.setattr(
        session_tools,
        "get_playwright_page",
        lambda session_id, page_id=None: (fake_session, "page1", SimpleNamespace()),
    )
    monkeypatch.setattr(
        session_tools,
        "get_playwright_page_event_buffers",
        lambda session, page_id: {
            "requests": [
                {
                    "request_id": "req1",
                    "url": "https://example.com/api/data",
                    "method": "GET",
                    "resource_type": "xhr",
                    "headers": {"accept": "application/json"},
                    "post_data": None,
                    "timestamp": 1.0,
                }
            ],
            "responses": [
                {
                    "request_id": "req1",
                    "url": "https://example.com/api/data",
                    "status": 200,
                    "status_text": "OK",
                    "ok": True,
                    "headers": {"content-type": "application/json"},
                    "timestamp": 1.2,
                }
            ],
            "request_failures": [],
        },
    )

    async def scenario() -> None:
        listed = await session_tools.browser_list_network_requests("sess1", include_headers=True)
        assert listed["count"] == 1
        assert listed["entries"][0]["request_id"] == "req1"
        assert listed["entries"][0]["status"] == 200
        assert listed["entries"][0]["request_headers"]["accept"] == "application/json"

        detail = await session_tools.browser_get_network_request("sess1", request_id="req1")
        assert detail["request"]["url"] == "https://example.com/api/data"
        assert detail["response"]["status"] == 200
        assert detail["failure"] is None

    anyio.run(scenario)


def test_browser_list_network_requests_filters_and_export_har(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_session = {"page_event_buffers": {}}
    written_har: dict[str, str] = {}

    monkeypatch.setattr(
        session_tools,
        "get_playwright_page",
        lambda session_id, page_id=None: (fake_session, "page1", SimpleNamespace()),
    )
    monkeypatch.setattr(
        session_tools,
        "get_playwright_page_event_buffers",
        lambda session, page_id: {
            "requests": [
                {
                    "request_id": "req1",
                    "url": "https://example.com/api/data",
                    "method": "GET",
                    "resource_type": "xhr",
                    "headers": {"accept": "application/json"},
                    "post_data": None,
                    "timestamp": 1.0,
                },
                {
                    "request_id": "req2",
                    "url": "https://example.com/app.js",
                    "method": "GET",
                    "resource_type": "script",
                    "headers": {},
                    "post_data": None,
                    "timestamp": 2.0,
                },
            ],
            "responses": [
                {"request_id": "req1", "url": "https://example.com/api/data", "status": 200, "status_text": "OK", "ok": True, "headers": {"content-type": "application/json"}, "timestamp": 1.5},
                {"request_id": "req2", "url": "https://example.com/app.js", "status": 304, "status_text": "Not Modified", "ok": True, "headers": {"content-type": "application/javascript"}, "timestamp": 2.1},
            ],
            "request_failures": [],
        },
    )
    monkeypatch.setattr(Path, "mkdir", lambda self, parents=False, exist_ok=False: None)
    monkeypatch.setattr(
        Path,
        "write_text",
        lambda self, text, encoding="utf-8": written_har.update({"path": str(self), "text": text}),
    )

    async def scenario() -> None:
        listed = await session_tools.browser_list_network_requests(
            "sess1",
            resource_type="xhr",
            status=200,
            mime_contains="json",
            sort_by="url",
            sort_order="asc",
            offset=0,
            limit=10,
        )
        assert listed["count"] == 1
        assert listed["total_count"] == 1
        assert listed["sort_by"] == "url"
        assert listed["entries"][0]["request_id"] == "req1"

        exported = await session_tools.browser_export_network_har("sess1", path=str(tmp_path / "out.har.json"))
        assert exported["entry_count"] == 2
        payload = json.loads(written_har["text"])
        assert payload["log"]["entries"][0]["_requestId"] == "req1"
        assert payload["log"]["entries"][0]["pageref"] == "page1"
        assert "content" in payload["log"]["entries"][0]["response"]
        summary = await session_tools.browser_get_network_summary("sess1")
        assert summary["request_count"] == 2
        assert summary["by_status"]["200"] == 1
        assert summary["by_resource_type"]["xhr"] == 1

    anyio.run(scenario)


def test_browser_performance_metrics_and_trace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    writes: list[str] = []

    class FakeTracing:
        def __init__(self) -> None:
            self.started = None
            self.stopped = None

        def start(self, **kwargs) -> None:
            self.started = kwargs

        def stop(self, path: str) -> None:
            self.stopped = path
            writes.append(path)

    fake_page = SimpleNamespace(
        evaluate=lambda script: {
            "url": "https://example.com",
            "title": "Example",
            "domContentLoadedMs": 10,
            "loadEventMs": 20,
            "responseEndMs": 5,
            "domInteractiveMs": 8,
            "firstPaintMs": 3,
            "firstContentfulPaintMs": 4,
            "resourceCount": 2,
            "transferSize": 100,
            "encodedBodySize": 90,
            "decodedBodySize": 200,
            "jsHeapUsedSize": 50,
            "jsHeapTotalSize": 75,
            "resourceCategories": {"script": {"count": 1, "transferSize": 50, "encodedBodySize": 45, "decodedBodySize": 100, "durationMs": 12}},
            "slowestResources": [{"name": "https://example.com/app.js", "initiatorType": "script", "durationMs": 12, "transferSize": 50}],
            "navigationTimings": {"responseEnd": 5, "loadEventEnd": 20},
            "longTaskCount": 0,
        },
    )
    fake_tracing = FakeTracing()
    fake_session = {"context": SimpleNamespace(tracing=fake_tracing)}

    monkeypatch.setattr(session_tools, "get_playwright_page", lambda session_id, page_id=None: ({}, "page1", fake_page))
    monkeypatch.setattr(session_tools, "get_playwright_session", lambda session_id: fake_session)
    monkeypatch.setattr(Path, "mkdir", lambda self, parents=False, exist_ok=False: None)

    async def scenario() -> None:
        metrics = await session_tools.browser_get_performance_metrics("sess1")
        assert metrics["resourceCount"] == 2
        assert "navigationTimings" in metrics
        assert "resourceCategories" in metrics
        started = await session_tools.browser_start_trace("sess1", screenshots=False, snapshots=True, sources=False, trace_name="debug-run")
        assert started["active"] is True
        assert started["trace_name"] == "debug-run"
        stopped = await session_tools.browser_stop_trace("sess1", path=str(tmp_path / "trace.zip"))
        assert stopped["path"].endswith("trace.zip")
        assert writes and writes[0].endswith("trace.zip")

    anyio.run(scenario)


def test_browser_coverage_start_and_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCDPSession:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any] | None]] = []

        def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
            self.calls.append((method, params))
            if method == "Profiler.takePreciseCoverage":
                return {
                    "result": [
                        {
                            "url": "https://example.com/app.js",
                            "scriptId": "1",
                            "functions": [
                                {"ranges": [{"startOffset": 0, "endOffset": 100, "count": 1}]},
                                {"ranges": [{"startOffset": 100, "endOffset": 200, "count": 0}]},
                            ],
                        }
                    ]
                }
            if method == "CSS.stopRuleUsageTracking":
                return {
                    "ruleUsage": [
                        {"styleSheetId": "s1", "startOffset": 0, "endOffset": 10, "used": True},
                        {"styleSheetId": "s1", "startOffset": 10, "endOffset": 20, "used": False},
                    ]
                }
            return {}

    fake_cdp = FakeCDPSession()
    fake_page = SimpleNamespace(context=SimpleNamespace(new_cdp_session=lambda page: fake_cdp))
    fake_session = {"browser_name": "chrome"}

    monkeypatch.setattr(session_tools, "get_playwright_page", lambda session_id, page_id=None: (fake_session, "page1", fake_page))

    async def scenario() -> None:
        started = await session_tools.browser_start_coverage("sess1", include_js=True, include_css=True, call_count=True)
        assert started["active"] is True
        stopped = await session_tools.browser_stop_coverage("sess1")
        assert stopped["js_summary"]["script_count"] == 1
        assert stopped["js_summary"]["used_bytes"] == 100
        assert stopped["css_summary"]["rule_count"] == 2
        assert stopped["css_summary"]["used_rule_count"] == 1

    anyio.run(scenario)


def test_browser_debug_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_page = SimpleNamespace(url="https://example.com")
    fake_session = {"coverage_state": {"page1": {"started_at": 1.0}}, "trace_state": {"active": True}}

    monkeypatch.setattr(session_tools, "get_playwright_page", lambda session_id, page_id=None: (fake_session, "page1", fake_page))
    monkeypatch.setattr(session_tools, "page_title", lambda page: "Example")
    monkeypatch.setattr(session_tools, "browser_get_page_summary", lambda session_id, page_id=None: {"title": "Example", "url": "https://example.com"})
    monkeypatch.setattr(session_tools, "browser_get_performance_metrics", lambda session_id, page_id=None: {"resourceCount": 2})
    monkeypatch.setattr(session_tools, "browser_get_network_summary", lambda session_id, page_id=None: {"request_count": 3})
    monkeypatch.setattr(session_tools, "browser_get_console_logs", lambda session_id, page_id=None, limit=20: {"count": 1, "entries": [{"text": "ok"}]})
    monkeypatch.setattr(session_tools, "browser_get_network_errors", lambda session_id, page_id=None, limit=20: {"request_failure_count": 0, "page_error_count": 0})
    monkeypatch.setattr(session_tools, "browser_get_viewport_state", lambda session_id, page_id=None: {"width": 1280, "height": 720})

    async def scenario() -> None:
        result = await session_tools.browser_debug_snapshot("sess1")
        assert result["title"] == "Example"
        assert result["coverage_active"] is True
        assert result["trace_active"] is True
        assert result["network"]["request_count"] == 3

    anyio.run(scenario)


def test_browser_export_coverage_json_and_debug_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    written: dict[str, str] = {}

    monkeypatch.setattr(
        session_tools,
        "browser_stop_coverage",
        lambda session_id, page_id=None: {
            "page_id": "page1",
            "js_summary": {"script_count": 1},
            "css_summary": {"rule_count": 2},
            "js_entries": [],
            "css_entries": [],
        },
    )
    monkeypatch.setattr(Path, "mkdir", lambda self, parents=False, exist_ok=False: None)
    monkeypatch.setattr(Path, "write_text", lambda self, text, encoding="utf-8": written.update({"path": str(self), "text": text}))
    monkeypatch.setattr(
        session_tools,
        "browser_debug_snapshot",
        lambda session_id, page_id=None: {
            "page_id": "page1",
            "title": "Example",
            "url": "https://example.com",
            "page_summary": {"readyState": "complete"},
            "performance": {"firstContentfulPaintMs": 2200, "domContentLoadedMs": 1200, "loadEventMs": 4100, "longTaskCount": 1},
            "network": {"request_count": 10, "failure_count": 2},
            "console": {"count": 1},
            "errors": {"page_error_count": 1, "request_failure_count": 2},
            "viewport": {"width": 1280, "height": 720},
            "coverage_active": False,
            "trace_active": True,
        },
    )

    async def scenario() -> None:
        exported = await session_tools.browser_export_coverage_json("sess1", path=str(tmp_path / "coverage.json"))
        assert exported["js_script_count"] == 1
        assert '"page_id": "page1"' in written["text"]
        report = await session_tools.browser_debug_report("sess1")
        assert "slow load event" in report["report"]
        assert len(report["issues"]) >= 3

    anyio.run(scenario)


def test_browser_resume_instance_uses_manifest_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        session_tools,
        "_read_instance_manifest",
        lambda name: {
            "instance_name": name,
            "profile_name": "work",
            "browser": "chrome",
            "headless": False,
            "last_url": "https://example.com/dashboard",
        },
    )
    called: dict[str, Any] = {}
    monkeypatch.setattr(session_tools, "_browser_start_instance_impl", lambda **kwargs: called.update(kwargs) or {"ok": True, **kwargs})

    async def scenario() -> None:
        result = await session_tools.browser_resume_instance("work-browser")
        assert result["ok"] is True
        assert called["instance_name"] == "work-browser"
        assert called["url"] == "https://example.com/dashboard"
        assert called["profile_name"] == "work"
        assert called["browser"] == "chrome"
        assert called["headless"] is False

    anyio.run(scenario)


def test_browser_delete_instance_closes_running_session_when_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = {"instance_name": "work-browser"}
    monkeypatch.setattr(session_tools, "_running_instance_session", lambda name: ("sess1", fake_session))
    closed: list[str] = []
    monkeypatch.setattr(session_tools, "close_playwright_session", lambda session_id: closed.append(session_id) or True)

    class FakeManifestPath:
        def __init__(self) -> None:
            self.deleted = False

        def exists(self) -> bool:
            return not self.deleted

        def unlink(self) -> None:
            self.deleted = True

        def __str__(self) -> str:
            return "C:/fake/work-browser.json"

    fake_path = FakeManifestPath()
    monkeypatch.setattr(session_tools, "_instance_manifest_path", lambda name: fake_path)

    async def scenario() -> None:
        result = await session_tools.browser_delete_instance("work-browser", force=True)
        assert result["deleted"] is True
        assert closed == ["sess1"]
        assert fake_path.deleted is True

    anyio.run(scenario)


def test_browser_launch_debug_browser_returns_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        session_tools,
        "launch_debug_browser_process",
        lambda browser, port, url="about:blank", profile_path=None: (
            "chrome",
            SimpleNamespace(pid=12345),
            f"http://127.0.0.1:{port}",
        ),
    )
    monkeypatch.setattr(session_tools, "wait_for_cdp_endpoint", lambda endpoint, timeout_seconds=10.0: {"endpoint": endpoint, "Browser": "Chrome"})
    monkeypatch.setattr(session_tools, "_named_profile_path", lambda name: Path(f"C:/profiles/{name}"))
    monkeypatch.setattr(session_tools, "_write_profile_manifest", lambda profile_name, payload: {"profile_name": profile_name, **payload})
    monkeypatch.setattr(Path, "mkdir", lambda self, parents=False, exist_ok=False: None)

    async def scenario() -> None:
        result = await session_tools.browser_launch_debug_browser(browser="chrome", port=9333, url="https://example.com", profile_name="work")
        assert result["endpoint"] == "http://127.0.0.1:9333"
        assert result["pid"] == 12345
        assert result["profile_name"] == "work"
        assert result["endpoint_ready"] is True

    anyio.run(scenario)


def test_browser_attach_cdp_uses_existing_page(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePage:
        def __init__(self) -> None:
            self.url = "https://example.com"
            self.viewport = None

        def set_viewport_size(self, viewport):
            self.viewport = viewport

    class FakeContext:
        def __init__(self) -> None:
            self.pages = [FakePage()]

    class FakeBrowser:
        def __init__(self) -> None:
            self.contexts = [FakeContext()]

        def close(self):
            return None

    monkeypatch.setattr(session_tools, "open_playwright_cdp_runtime", lambda endpoint, browser="chrome", timeout_ms=30000: (SimpleNamespace(__exit__=lambda *a: None), object(), FakeBrowser(), "chrome"))
    monkeypatch.setattr(session_tools, "store_playwright_session", lambda session: None)
    monkeypatch.setattr(session_tools, "register_playwright_page", lambda session, page, make_active=True: "page1")
    monkeypatch.setattr(session_tools, "page_title", lambda page: "Example Domain")
    manifests: list[dict[str, Any]] = []
    monkeypatch.setattr(session_tools, "_write_instance_manifest", lambda instance_name, payload: manifests.append(payload) or payload)

    async def scenario() -> None:
        result = await session_tools.browser_attach_cdp(
            endpoint="http://127.0.0.1:9222",
            browser="chrome",
            instance_name="work-browser",
            profile_name="work",
            width=1200,
            height=800,
        )
        assert result["attached"] is True
        assert result["instance_name"] == "work-browser"
        assert result["cdp_endpoint"] == "http://127.0.0.1:9222"
        assert manifests[-1]["attached"] is True

    anyio.run(scenario)


def test_browser_attach_existing_returns_discovered_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        session_tools,
        "_merge_browser_preset",
        lambda preset_name, payload: payload,
    )
    chosen = {
        "endpoint": "http://127.0.0.1:9222",
        "targets": [
            {"type": "page", "title": "Tab A", "url": "https://a.example"},
            {"type": "page", "title": "Tab B", "url": "https://b.example"},
        ],
    }
    monkeypatch.setattr(session_tools, "detect_cdp_endpoints", lambda ports=None, host="127.0.0.1": [chosen])
    monkeypatch.setattr(session_tools, "_resolve_attach_existing_endpoint", lambda **kwargs: chosen)
    monkeypatch.setattr(
        session_tools,
        "_browser_attach_cdp_impl",
        lambda **kwargs: {"session_id": "sess1", "page_id": "page1", "endpoint": kwargs["endpoint"]},
    )

    async def scenario() -> None:
        result = await session_tools.browser_attach_existing(page_index=1)
        assert result["discovered_target"]["title"] == "Tab B"

    anyio.run(scenario)


def test_browser_attach_existing_matches_expected_tab(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_tools, "_merge_browser_preset", lambda preset_name, payload: payload)
    chosen = {
        "endpoint": "http://127.0.0.1:9222",
        "targets": [
            {"type": "page", "title": "Inbox", "url": "https://mail.example"},
            {"type": "page", "title": "Example Domain", "url": "https://example.com"},
        ],
    }
    monkeypatch.setattr(session_tools, "detect_cdp_endpoints", lambda ports=None, host="127.0.0.1": [chosen])
    monkeypatch.setattr(session_tools, "_resolve_attach_existing_endpoint", lambda **kwargs: chosen)
    monkeypatch.setattr(session_tools, "_browser_attach_cdp_impl", lambda **kwargs: {"session_id": "sess1", "page_id": "page1"})

    async def scenario() -> None:
        result = await session_tools.browser_attach_existing(expected_tab_title="Example")
        assert result["discovered_target"]["url"] == "https://example.com"

    anyio.run(scenario)


def test_browser_debug_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_session = {"trace_state": {"active": True}, "coverage_state": {"page1": {"started_at": 1.0}}}
    monkeypatch.setattr(session_tools, "get_playwright_page", lambda session_id, page_id=None: (fake_session, "page1", SimpleNamespace()))
    monkeypatch.setattr(session_tools, "browser_debug_report", lambda session_id, page_id=None: {"page_id": "page1", "report": "ok"})
    monkeypatch.setattr(session_tools, "browser_export_network_har", lambda session_id, page_id=None, path=None: {"path": path})
    monkeypatch.setattr(session_tools, "browser_stop_trace", lambda session_id, path=None: {"path": path})
    monkeypatch.setattr(session_tools, "browser_export_coverage_json", lambda session_id, page_id=None, path=None: {"path": path})
    monkeypatch.setattr(Path, "mkdir", lambda self, parents=False, exist_ok=False: None)
    written: dict[str, str] = {}
    monkeypatch.setattr(Path, "write_text", lambda self, text, encoding="utf-8": written.update({"path": str(self), "text": text}))

    async def scenario() -> None:
        result = await session_tools.browser_debug_bundle("sess1", bundle_dir=str(tmp_path))
        assert result["artifacts"]["report"].endswith("debug-report.json")
        assert result["artifacts"]["har"].endswith("network.har.json")
        assert result["artifacts"]["trace"].endswith("trace.zip")
        assert result["artifacts"]["coverage"].endswith("coverage.json")

    anyio.run(scenario)


def test_browser_attach_existing_uses_first_detected_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        session_tools,
        "detect_cdp_endpoints",
        lambda ports=None, host="127.0.0.1": [
            {"endpoint": "http://127.0.0.1:9222", "port": 9222, "browser": "Chrome/1.0"}
        ],
    )
    called: dict[str, Any] = {}
    monkeypatch.setattr(session_tools, "_browser_attach_cdp_impl", lambda **kwargs: called.update(kwargs) or {"session_id": "sess1", **kwargs})

    async def scenario() -> None:
        result = await session_tools.browser_attach_existing(browser="chrome", instance_name="work-browser")
        assert result["session_id"] == "sess1"
        assert called["endpoint"] == "http://127.0.0.1:9222"
        assert result["discovered_endpoint"]["port"] == 9222

    anyio.run(scenario)


def test_browser_attach_existing_uses_profile_manifest_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        session_tools,
        "detect_cdp_endpoints",
        lambda ports=None, host="127.0.0.1": [
            {"endpoint": "http://127.0.0.1:9222", "port": 9222, "browser": "Chrome/1.0"}
        ],
    )
    monkeypatch.setattr(
        session_tools,
        "_read_profile_manifest",
        lambda profile_name: {"cdp_endpoint": "http://127.0.0.1:9222", "browser_pid": 4321, "debug_browser_running": True},
    )
    called: dict[str, Any] = {}
    monkeypatch.setattr(session_tools, "_browser_attach_cdp_impl", lambda **kwargs: called.update(kwargs) or {"session_id": "sess1", **kwargs})

    async def scenario() -> None:
        result = await session_tools.browser_attach_existing(browser="chrome", instance_name="work-browser", profile_name="work")
        assert result["session_id"] == "sess1"
        assert called["browser_pid"] == 4321
        assert called["launched_debug_browser"] is True

    anyio.run(scenario)


def test_browser_attach_existing_prefers_profile_matched_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        session_tools,
        "detect_cdp_endpoints",
        lambda ports=None, host="127.0.0.1": [
            {"endpoint": "http://127.0.0.1:9222", "port": 9222, "browser": "Chrome/1.0"},
            {"endpoint": "http://127.0.0.1:9333", "port": 9333, "browser": "Chrome/1.0"},
        ],
    )
    monkeypatch.setattr(
        session_tools,
        "_read_profile_manifest",
        lambda profile_name: {"cdp_endpoint": "http://127.0.0.1:9333", "debug_port": 9333},
    )
    called: dict[str, Any] = {}
    monkeypatch.setattr(session_tools, "_browser_attach_cdp_impl", lambda **kwargs: called.update(kwargs) or {"session_id": "sess1", **kwargs})

    async def scenario() -> None:
        result = await session_tools.browser_attach_existing(browser="chrome", profile_name="work")
        assert called["endpoint"] == "http://127.0.0.1:9333"
        assert result["discovered_endpoint"]["port"] == 9333

    anyio.run(scenario)


def test_browser_attach_existing_prefers_endpoint_with_real_page_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        session_tools,
        "detect_cdp_endpoints",
        lambda ports=None, host="127.0.0.1": [
            {
                "endpoint": "http://127.0.0.1:9222",
                "port": 9222,
                "browser": "Chrome/1.0",
                "targets": [{"type": "page", "url": "chrome://intro/", "title": "Welcome"}],
            },
            {
                "endpoint": "http://127.0.0.1:9333",
                "port": 9333,
                "browser": "Chrome/1.0",
                "targets": [{"type": "page", "url": "https://example.com", "title": "Example Domain"}],
            },
        ],
    )
    monkeypatch.setattr(session_tools, "_read_profile_manifest", lambda profile_name: {})
    called: dict[str, Any] = {}
    monkeypatch.setattr(session_tools, "_browser_attach_cdp_impl", lambda **kwargs: called.update(kwargs) or {"session_id": "sess1", **kwargs})

    async def scenario() -> None:
        result = await session_tools.browser_attach_existing(browser="chrome")
        assert called["endpoint"] == "http://127.0.0.1:9333"
        assert result["discovered_endpoint"]["port"] == 9333

    anyio.run(scenario)


def test_browser_attach_existing_prefers_expected_title(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        session_tools,
        "detect_cdp_endpoints",
        lambda ports=None, host="127.0.0.1": [
            {
                "endpoint": "http://127.0.0.1:9222",
                "port": 9222,
                "browser": "Chrome/1.0",
                "targets": [{"type": "page", "url": "https://foo.example", "title": "Foo"}],
            },
            {
                "endpoint": "http://127.0.0.1:9333",
                "port": 9333,
                "browser": "Chrome/1.0",
                "targets": [{"type": "page", "url": "https://bar.example", "title": "Target App"}],
            },
        ],
    )
    monkeypatch.setattr(session_tools, "_read_profile_manifest", lambda profile_name: {})
    called: dict[str, Any] = {}
    monkeypatch.setattr(session_tools, "_browser_attach_cdp_impl", lambda **kwargs: called.update(kwargs) or {"session_id": "sess1", **kwargs})

    async def scenario() -> None:
        result = await session_tools.browser_attach_existing(browser="chrome", expected_title="Target App")
        assert called["endpoint"] == "http://127.0.0.1:9333"
        assert result["discovered_endpoint"]["port"] == 9333

    anyio.run(scenario)


def test_browser_attach_existing_prefers_expected_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        session_tools,
        "detect_cdp_endpoints",
        lambda ports=None, host="127.0.0.1": [
            {
                "endpoint": "http://127.0.0.1:9222",
                "port": 9222,
                "browser": "Chrome/1.0",
                "targets": [{"type": "page", "url": "https://foo.example", "title": "Foo"}],
            },
            {
                "endpoint": "http://127.0.0.1:9333",
                "port": 9333,
                "browser": "Chrome/1.0",
                "targets": [{"type": "page", "url": "https://app.example/dashboard", "title": "Bar"}],
            },
        ],
    )
    monkeypatch.setattr(session_tools, "_read_profile_manifest", lambda profile_name: {})
    called: dict[str, Any] = {}
    monkeypatch.setattr(session_tools, "_browser_attach_cdp_impl", lambda **kwargs: called.update(kwargs) or {"session_id": "sess1", **kwargs})

    async def scenario() -> None:
        result = await session_tools.browser_attach_existing(browser="chrome", expected_url_contains="/dashboard")
        assert called["endpoint"] == "http://127.0.0.1:9333"
        assert result["discovered_endpoint"]["port"] == 9333

    anyio.run(scenario)


def test_browser_attach_existing_applies_preset_expectations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        session_tools,
        "_read_preset",
        lambda preset_name: {
            "preset_name": preset_name,
            "browser": "chrome",
            "expected_title": "Target App",
        },
    )
    monkeypatch.setattr(
        session_tools,
        "detect_cdp_endpoints",
        lambda ports=None, host="127.0.0.1": [
            {
                "endpoint": "http://127.0.0.1:9222",
                "port": 9222,
                "browser": "Chrome/1.0",
                "targets": [{"type": "page", "url": "https://foo.example", "title": "Foo"}],
            },
            {
                "endpoint": "http://127.0.0.1:9333",
                "port": 9333,
                "browser": "Chrome/1.0",
                "targets": [{"type": "page", "url": "https://bar.example", "title": "Target App"}],
            },
        ],
    )
    monkeypatch.setattr(session_tools, "_read_profile_manifest", lambda profile_name: {})
    called: dict[str, Any] = {}
    monkeypatch.setattr(session_tools, "_browser_attach_cdp_impl", lambda **kwargs: called.update(kwargs) or {"session_id": "sess1", **kwargs})

    async def scenario() -> None:
        result = await session_tools.browser_attach_existing(preset_name="daily")
        assert called["endpoint"] == "http://127.0.0.1:9333"
        assert result["preset_name"] == "daily"

    anyio.run(scenario)


def test_browser_list_endpoints_returns_detected_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        session_tools,
        "detect_cdp_endpoints",
        lambda ports=None, host="127.0.0.1": [
            {"endpoint": "http://127.0.0.1:9222", "port": 9222},
            {"endpoint": "http://127.0.0.1:9333", "port": 9333},
        ],
    )

    async def scenario() -> None:
        result = await session_tools.browser_list_endpoints()
        assert result["count"] == 2
        assert result["endpoints"][0]["port"] == 9222

    anyio.run(scenario)


def test_browser_storage_state_export_writes_path(monkeypatch: pytest.MonkeyPatch) -> None:
    exported: dict[str, str] = {}

    class FakeContext:
        def storage_state(self, path: str) -> None:
            exported["path"] = path

    monkeypatch.setattr(session_tools, "get_playwright_session", lambda session_id: {"context": FakeContext()})
    monkeypatch.setattr(Path, "mkdir", lambda self, parents=False, exist_ok=False: None)

    async def scenario() -> None:
        result = await session_tools.browser_storage_state_export("sess1", path=r"C:\fake\state.json")
        assert result["path"] == r"C:\fake\state.json"
        assert exported["path"] == r"C:\fake\state.json"

    anyio.run(scenario)


def test_browser_grant_permissions_updates_session(monkeypatch: pytest.MonkeyPatch) -> None:
    granted: list[list[str]] = []

    class FakeContext:
        def grant_permissions(self, permissions: list[str]) -> None:
            granted.append(list(permissions))

    session = {"context": FakeContext(), "granted_permissions": ["clipboard-read"]}
    monkeypatch.setattr(session_tools, "get_playwright_session", lambda session_id: session)

    async def scenario() -> None:
        result = await session_tools.browser_grant_permissions("sess1", ["geolocation", "clipboard-read"])
        assert result["granted_permissions"] == ["clipboard-read", "geolocation"]
        assert granted[-1] == ["clipboard-read", "geolocation"]

    anyio.run(scenario)


def test_browser_clear_permissions_resets_session(monkeypatch: pytest.MonkeyPatch) -> None:
    cleared = {"called": False}

    class FakeContext:
        def clear_permissions(self) -> None:
            cleared["called"] = True

    session = {"context": FakeContext(), "granted_permissions": ["clipboard-read"]}
    monkeypatch.setattr(session_tools, "get_playwright_session", lambda session_id: session)

    async def scenario() -> None:
        result = await session_tools.browser_clear_permissions("sess1")
        assert result["granted_permissions"] == []
        assert session["granted_permissions"] == []
        assert cleared["called"] is True

    anyio.run(scenario)


def test_browser_revoke_permissions_keeps_remaining(monkeypatch: pytest.MonkeyPatch) -> None:
    granted: list[list[str]] = []
    cleared = {"called": False}

    class FakeContext:
        def clear_permissions(self) -> None:
            cleared["called"] = True

        def grant_permissions(self, permissions: list[str]) -> None:
            granted.append(list(permissions))

    session = {"context": FakeContext(), "granted_permissions": ["clipboard-read", "geolocation", "notifications"]}
    monkeypatch.setattr(session_tools, "get_playwright_session", lambda session_id: session)

    async def scenario() -> None:
        result = await session_tools.browser_revoke_permissions("sess1", ["geolocation"])
        assert cleared["called"] is True
        assert result["granted_permissions"] == ["clipboard-read", "notifications"]
        assert granted[-1] == ["clipboard-read", "notifications"]

    anyio.run(scenario)


def test_browser_resume_instance_uses_cdp_when_manifest_attached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        session_tools,
        "_read_instance_manifest",
        lambda name: {
            "instance_name": name,
            "attached": True,
            "cdp_endpoint": "http://127.0.0.1:9222",
            "browser": "chrome",
            "profile_name": "work",
        },
    )
    called: dict[str, Any] = {}
    monkeypatch.setattr(session_tools, "_browser_attach_cdp_impl", lambda **kwargs: called.update(kwargs) or {"ok": True, **kwargs})

    async def scenario() -> None:
        result = await session_tools.browser_resume_instance("work-browser")
        assert result["ok"] is True
        assert called["endpoint"] == "http://127.0.0.1:9222"
        assert called["instance_name"] == "work-browser"
        assert called["profile_name"] == "work"

    anyio.run(scenario)


def test_browser_launch_and_attach_combines_launch_and_attach(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        session_tools,
        "_browser_launch_debug_browser_impl",
        lambda **kwargs: {"pid": 123, "endpoint": "http://127.0.0.1:9222", "browser": "chrome"},
    )
    monkeypatch.setattr(session_tools.time, "sleep", lambda seconds: None)
    called: dict[str, Any] = {}
    monkeypatch.setattr(session_tools, "_browser_attach_cdp_impl", lambda **kwargs: called.update(kwargs) or {"attached": True, **kwargs})

    async def scenario() -> None:
        result = await session_tools.browser_launch_and_attach(browser="chrome", instance_name="work-browser", profile_name="work")
        assert result["attached"] is True
        assert result["browser_pid"] == 123
        assert called["browser_pid"] == 123
        assert called["launched_debug_browser"] is True

    anyio.run(scenario)


def test_browser_stop_instance_and_browser_kills_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        session_tools,
        "_read_instance_manifest",
        lambda name: {"instance_name": name, "browser_pid": 456, "launched_debug_browser": True},
    )
    monkeypatch.setattr(
        session_tools,
        "_browser_stop_instance_impl",
        lambda name: {"instance_name": name, "closed": True, "session_id": "sess1", "browser_pid": 456},
    )
    killed: list[list[str]] = []
    monkeypatch.setattr(session_tools.subprocess, "run", lambda command, **kwargs: killed.append(command) or SimpleNamespace(returncode=0))

    async def scenario() -> None:
        result = await session_tools.browser_stop_instance_and_browser("work-browser")
        assert result["browser_closed"] is True
        assert killed and killed[0][0].lower() == "taskkill"

    anyio.run(scenario)


def test_desktop_watch_loop_applies_error_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    class FakeStopEvent:
        def is_set(self) -> bool:
            return False

        def wait(self, seconds: float) -> bool:
            waits.append(seconds)
            return True

    watch_id = "watch1"
    original = dict(capture_tools.DESKTOP_WATCH_SESSIONS)
    capture_tools.DESKTOP_WATCH_SESSIONS.clear()
    capture_tools.DESKTOP_WATCH_SESSIONS[watch_id] = {
        "stop_event": FakeStopEvent(),
        "mode": "desktop",
        "handle": None,
        "title_regex": None,
        "region": None,
        "use_ocr": False,
        "uia_depth": 1,
        "max_nodes": 10,
        "interval_seconds": 0.1,
        "capture": False,
        "sample_count": 0,
        "change_count": 0,
        "last_hash": None,
        "last_error": None,
        "history": [],
    }
    monkeypatch.setattr(capture_tools, "_desktop_watch_sample", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        capture_tools._desktop_watch_loop(watch_id)
    finally:
        capture_tools.DESKTOP_WATCH_SESSIONS.clear()
        capture_tools.DESKTOP_WATCH_SESSIONS.update(original)
    assert waits[0] == 2


def test_describe_screen_calls_claude_vision(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sample = PILImage.new("RGB", (32, 32), "white")
    sample_path = tmp_path / "screen.png"
    sample.save(sample_path)

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"content": [{"type": "text", "text": "A white square screen."}]}

    fake_httpx = SimpleNamespace(post=lambda *args, **kwargs: FakeResponse())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    monkeypatch.setattr(capture_tools, "grab_png_bytes", lambda region=None: (sample_path.read_bytes(), {"left": 0, "top": 0, "width": 32, "height": 32}))
    result = capture_tools.describe_screen(capture_path=str(tmp_path / "captured.png"))
    assert "white square" in result["description"].lower()
    assert Path(result["capture_path"]).exists()


def test_find_image_on_screen_returns_match(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    template_path = tmp_path / "template.png"
    PILImage.new("L", (10, 10), color=255).save(template_path)
    fake_cv2 = SimpleNamespace(
        IMREAD_GRAYSCALE=0,
        TM_CCOEFF_NORMED=5,
        imread=lambda path, mode: np.ones((10, 10), dtype=np.uint8),
        imdecode=lambda buffer, mode: np.ones((50, 50), dtype=np.uint8),
        matchTemplate=lambda screen, template, method: np.array([[0.95]], dtype=float),
        minMaxLoc=lambda result: (0.0, 0.95, (0, 0), (7, 9)),
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    monkeypatch.setattr(capture_tools, "grab_png_bytes", lambda region=None: (template_path.read_bytes(), {"left": 100, "top": 200, "width": 50, "height": 50}))
    result = capture_tools.find_image_on_screen(str(template_path), confidence=0.8)
    assert result["found"] is True
    assert result["x"] == 112
    assert result["y"] == 214


def test_diff_screenshots_detects_change_and_writes_diff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = tmp_path / "before.png"
    after = tmp_path / "after.png"
    monkeypatch.setattr(capture_tools, "SCREENSHOT_DIR", tmp_path)
    PILImage.new("RGB", (20, 20), "white").save(before)
    img = PILImage.new("RGB", (20, 20), "white")
    img.putpixel((4, 4), (255, 0, 0))
    img.save(after)
    result = tools_desktop.diff_screenshots(str(before), str(after), threshold=0.0001)
    assert result["changed"] is True
    assert result["diff_path"]


def test_run_command_wait_returns_output() -> None:
    result = tools_desktop.run_command(["python", "-c", "print('ok')"], wait=True, timeout_seconds=5)
    assert result["returncode"] == 0
    assert "ok" in result["stdout"]


def test_mcp_stdio_server_round_trip() -> None:
    async def scenario() -> None:
        params = mcp.StdioServerParameters(
            command="python",
            args=["-m", "desktop_mcp"],
            cwd=str(REPO_ROOT),
        )
        async with mcp.stdio_client(params) as streams:
            async with mcp.ClientSession(*streams) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert "ping" in names
                assert "describe_screen" in names
                ping_result = await session.call_tool("ping", {})
                assert ping_result.isError is False
                snapshot_result = await session.call_tool("desktop_snapshot_state", {"include_windows": False})
                assert snapshot_result.isError is False

    try:
        anyio.run(scenario)
    except PermissionError as exc:
        pytest.skip(f"stdio spawn unavailable in this Windows test environment: {exc}")


def test_mcp_stdio_browser_session_flow() -> None:
    def parse_tool_json(result) -> dict[str, Any]:
        texts = [getattr(item, "text", None) for item in result.content]
        text = next((item for item in texts if item), None)
        assert text is not None
        return json.loads(text)

    async def scenario() -> None:
        params = mcp.StdioServerParameters(
            command="python",
            args=["-m", "desktop_mcp"],
            cwd=str(REPO_ROOT),
        )
        async with mcp.stdio_client(params) as streams:
            async with mcp.ClientSession(*streams) as session:
                await session.initialize()
                opened = parse_tool_json(
                    await session.call_tool(
                        "browser_open_session",
                        {"url": "https://example.com", "width": "auto", "height": "auto", "headless": True},
                    )
                )
                session_id = opened["session_id"]
                summary = parse_tool_json(await session.call_tool("browser_get_page_summary", {"session_id": session_id}))
                assert summary["title"] == "Example Domain"
                pages = parse_tool_json(await session.call_tool("browser_list_pages", {"session_id": session_id}))
                assert pages["pages"]
                captured = parse_tool_json(
                    await session.call_tool(
                        "browser_capture_session",
                        {"session_id": session_id, "full_page": False},
                    )
                )
                assert Path(captured["path"]).exists()
                closed = parse_tool_json(await session.call_tool("browser_close_session", {"session_id": session_id}))
                assert closed["closed"] is True

    try:
        anyio.run(scenario)
    except PermissionError as exc:
        pytest.skip(f"stdio spawn unavailable in this Windows test environment: {exc}")


def test_mcp_stdio_browser_profile_instance_flow() -> None:
    def parse_tool_json(result) -> dict[str, Any]:
        texts = [getattr(item, "text", None) for item in result.content]
        text = next((item for item in texts if item), None)
        assert text is not None
        return json.loads(text)

    async def scenario() -> None:
        params = mcp.StdioServerParameters(
            command="python",
            args=["-m", "desktop_mcp"],
            cwd=str(REPO_ROOT),
        )
        profile_name = f"stdio-profile-{int(time.time())}"
        instance_name = f"stdio-instance-{int(time.time())}"
        async with mcp.stdio_client(params) as streams:
            async with mcp.ClientSession(*streams) as session:
                await session.initialize()
                created = parse_tool_json(
                    await session.call_tool(
                        "browser_create_profile",
                        {
                            "profile_name": profile_name,
                            "description": "stdio flow profile",
                            "tags": ["test", "stdio"],
                            "preferred_url": "https://example.com",
                            "browser": "chrome",
                        },
                    )
                )
                assert created["name"] == profile_name
                listed_profiles = parse_tool_json(await session.call_tool("browser_list_profiles", {}))
                assert any(item["name"] == profile_name for item in listed_profiles["profiles"])
                started = parse_tool_json(
                    await session.call_tool(
                        "browser_start_instance",
                        {
                            "instance_name": instance_name,
                            "profile_name": profile_name,
                            "url": "https://example.com",
                            "headless": True,
                            "width": "auto",
                            "height": "auto",
                        },
                    )
                )
                assert started["instance_name"] == instance_name
                fetched = parse_tool_json(await session.call_tool("browser_get_instance", {"instance_name": instance_name}))
                assert fetched["running"] is True
                instances = parse_tool_json(await session.call_tool("browser_list_instances", {}))
                assert any(item["instance_name"] == instance_name for item in instances["instances"])
                stopped = parse_tool_json(await session.call_tool("browser_stop_instance", {"instance_name": instance_name}))
                assert stopped["closed"] is True
                deleted_instance = parse_tool_json(await session.call_tool("browser_delete_instance", {"instance_name": instance_name}))
                assert deleted_instance["deleted"] is True
                deleted_profile = parse_tool_json(
                    await session.call_tool("browser_delete_profile", {"profile_name": profile_name, "force": True})
                )
                assert deleted_profile["deleted"] is True

    try:
        anyio.run(scenario)
    except PermissionError as exc:
        pytest.skip(f"stdio spawn unavailable in this Windows test environment: {exc}")


def test_mcp_stdio_desktop_watch_flow() -> None:
    def parse_tool_json(result) -> dict[str, Any]:
        texts = [getattr(item, "text", None) for item in result.content]
        text = next((item for item in texts if item), None)
        assert text is not None
        return json.loads(text)

    async def scenario() -> None:
        params = mcp.StdioServerParameters(
            command="python",
            args=["-m", "desktop_mcp"],
            cwd=str(REPO_ROOT),
        )
        async with mcp.stdio_client(params) as streams:
            async with mcp.ClientSession(*streams) as session:
                await session.initialize()
                started = parse_tool_json(
                    await session.call_tool(
                        "desktop_watch_start",
                        {"mode": "desktop", "interval_seconds": 0.2, "history_limit": 5, "capture": False},
                    )
                )
                watch_id = started["watch_id"]
                listed = parse_tool_json(await session.call_tool("desktop_watch_list", {}))
                assert any(item["watch_id"] == watch_id for item in listed["watches"])
                states = parse_tool_json(await session.call_tool("desktop_watch_get_states", {"watch_id": watch_id, "limit": 3}))
                assert states["watch_id"] == watch_id
                stopped = parse_tool_json(await session.call_tool("desktop_watch_stop", {"watch_id": watch_id}))
                assert stopped["stopped"] is True

    try:
        anyio.run(scenario)
    except PermissionError as exc:
        pytest.skip(f"stdio spawn unavailable in this Windows test environment: {exc}")
