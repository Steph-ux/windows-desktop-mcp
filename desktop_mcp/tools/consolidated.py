"""Dynamic consolidated MCP tool registration via action dispatch."""
from __future__ import annotations
import functools
import inspect
import time
import traceback
from typing import Any

import anyio

from ..app import mcp

from . import browser_sessions as _bs
from . import browser_headless as _bh
from . import capture as _cap
from . import chrome_devtools as _cdp
from . import input as _inp
from . import ocr as _ocr
from . import system as _sys
from . import windows as _win
from . import runtime as _rt
from . import video as _vid
from . import monitors as _mon
from . import workflows as _wf
from . import operator as _op
from . import smart_ocr as _socr
from . import agent_browser as _ab
from . import social_media as _sm
from .. import tools_ai as _ai
from .. import tools_runtime as _trt
from ..tool_policy import evaluate_host_interaction_guard

# ═══ SAFE DISPATCH WITH ERROR HANDLING & TIMEOUT ═══════════════════
DEFAULT_TIMEOUT_MS = 30000


def _d(actions, action, tool_name="", timeout_ms=None, **kw):
    """Dispatch to an action with structured error handling and optional timeout."""
    fn = actions.get(action)
    if not fn:
        return {"ok": False, "error": f"Unknown action: {action!r}",
                "available_actions": sorted(actions.keys())}

    confirmed = bool(kw.pop("_mcp_confirmed", False) or kw.get("confirmed", False))
    confirmation_source = str(kw.pop("_mcp_confirmation_source", "") or kw.get("confirmation_source", ""))
    if tool_name:
        host_guard = evaluate_host_interaction_guard(
            tool=tool_name,
            action=action,
            confirmed=confirmed,
            confirmation_source=confirmation_source,
        )
        if not host_guard["ok"]:
            return host_guard
        if host_guard.get("host_interactive"):
            kw.pop("confirmed", None)
            kw.pop("confirmation_source", None)

    # Filter kwargs to match function signature
    sig = inspect.signature(fn)
    params = sig.parameters
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    if not has_var_kw:
        kw = {k: v for k, v in kw.items() if k in params}

    t0 = time.monotonic()
    timeout_s = (timeout_ms or DEFAULT_TIMEOUT_MS) / 1000

    try:
        result = fn(**kw)
        elapsed = round(time.monotonic() - t0, 3)
        # If result is already a dict with ok/error, return as-is
        if isinstance(result, dict):
            result.setdefault("_elapsed_ms", int(elapsed * 1000))
            return result
        return {"ok": True, "result": result, "_elapsed_ms": int(elapsed * 1000)}
    except TimeoutError:
        return {"ok": False, "error": "Action timed out",
                "action": action, "timeout_ms": timeout_ms or DEFAULT_TIMEOUT_MS}
    except Exception as e:
        return {"ok": False, "error": str(e), "action": action,
                "type": type(e).__name__,
                "trace": traceback.format_exc(limit=3)}


R: dict[str, tuple[str, dict[str, Any]]] = {}

# ═══ BROWSER (7) ════════════════════════════════════════════════════

R["browser_session"] = (
    "Manage browser lifecycle, instances, profiles, and presets.\n"
    "Actions: open, user_open, close, list, cleanup, "
    "start, stop, stop_all, resume, get, list_instances, delete, "
    "attach_cdp, attach_existing, launch, debug, endpoints, "
    "profile_create, profile_get, profile_update, profile_list, profile_delete, profile_cleanup, profile_import, profile_export, "
    "preset_save, preset_get, preset_list, preset_delete", {
    "open": _bs.browser_open_session, "user_open": _bs.browser_user_open, "close": _bs.browser_close_session,
    "list": _bs.browser_list_sessions, "cleanup": _bs.browser_cleanup_sessions,
    "start": _bs.browser_start_instance, "stop": _bs.browser_stop_instance,
    "stop_all": _bs.browser_stop_instance_and_browser, "resume": _bs.browser_resume_instance,
    "get": _bs.browser_get_instance, "list_instances": _bs.browser_list_instances,
    "delete": _bs.browser_delete_instance,
    "attach_cdp": _bs.browser_attach_cdp, "attach_existing": _bs.browser_attach_existing,
    "launch": _bs.browser_launch_and_attach, "debug": _bs.browser_launch_debug_browser,
    "endpoints": _bs.browser_list_endpoints,
    "profile_create": _bs.browser_create_profile, "profile_get": _bs.browser_get_profile,
    "profile_update": _bs.browser_update_profile, "profile_list": _bs.browser_list_profiles,
    "profile_delete": _bs.browser_delete_profile, "profile_cleanup": _bs.browser_cleanup_profiles,
    "profile_import": _bs.browser_import_profile_config, "profile_export": _bs.browser_export_profile_config,
    "preset_save": _bs.browser_save_preset, "preset_get": _bs.browser_get_preset,
    "preset_list": _bs.browser_list_presets, "preset_delete": _bs.browser_delete_preset,
})

R["browser_navigate"] = (
    "Navigate browser pages.\n"
    "Actions: goto, reload, back, forward, scroll, new_page, switch_page, close_page, list_pages", {
    "goto": _bs.browser_navigate, "reload": _bs.browser_reload,
    "back": _bs.browser_go_back, "forward": _bs.browser_go_forward,
    "scroll": _bs.browser_scroll_page,
    "new_page": _bs.browser_new_page, "switch_page": _bs.browser_switch_page,
    "close_page": _bs.browser_close_page, "list_pages": _bs.browser_list_pages,
})

R["browser_content"] = (
    "Read DOM, execute JS, annotate elements, interact with frames/shadow DOM.\n"
    "Actions: get, dump, text, summary, count, a11y, eval, "
    "annotate, interactive, forms, downloads, "
    "frame_list, frame_eval, frame_click, frame_fill, shadow_query", {
    "get": _bs.browser_get_dom, "dump": _bh.browser_dump_dom,
    "text": _bs.browser_get_text, "summary": _bs.browser_get_page_summary,
    "count": _bs.browser_count_selectors,
    "a11y": _bs.browser_get_accessibility_snapshot, "eval": _bs.browser_eval,
    "annotate": _ai.browser_annotate_page,
    "interactive": _bs.browser_list_interactive_elements,
    "forms": _bs.browser_list_form_fields, "downloads": _bs.browser_list_downloads,
    "frame_list": _bs.browser_list_frames, "frame_eval": _bs.browser_frame_eval,
    "frame_click": _bs.browser_frame_click, "frame_fill": _bs.browser_frame_fill,
    "shadow_query": _bs.browser_shadow_query,
})

R["browser_interact"] = (
    "Click, type, and fill in browser.\n"
    "Actions: click, click_text, click_interactive, hover, focus, "
    "click_download, click_text_download, "
    "type, press, fill_field, fill_form, toggle, upload", {
    "click": _bs.browser_click_selector, "click_text": _bs.browser_click_text,
    "click_interactive": _bs.browser_click_interactive,
    "hover": _bs.browser_hover_selector, "focus": _bs.browser_focus_selector,
    "click_download": _bs.browser_click_selector_and_wait_download,
    "click_text_download": _bs.browser_click_text_and_wait_download,
    "type": _bs.browser_type_selector, "press": _bs.browser_press_key,
    "fill_field": _bs.browser_fill_form_field, "fill_form": _bs.browser_fill_form,
    "toggle": _bs.browser_toggle_form_field, "upload": _bs.browser_set_input_files,
})

R["browser_observe"] = (
    "Capture screenshots and wait for conditions.\n"
    "Actions: capture, capture_element, capture_session, capture_live, save, save_live, "
    "wait_selector, wait_text, wait_load, wait_dom, wait_url, wait_visual, wait_download", {
    "capture": _bh.browser_capture_page, "capture_element": _bs.browser_capture_element,
    "capture_session": _bs.browser_capture_session, "capture_live": _bs.browser_capture_live_page,
    "save": _bh.browser_save_screenshot, "save_live": _bs.browser_save_live_page_screenshot,
    "wait_selector": _bs.browser_wait_for_selector, "wait_text": _bs.browser_wait_for_text,
    "wait_load": _bs.browser_wait_for_load_state, "wait_dom": _bs.browser_wait_for_dom_change,
    "wait_url": _bs.browser_wait_for_url, "wait_visual": _bs.browser_wait_for_visual_change,
    "wait_download": _bs.browser_wait_for_download,
})

R["browser_network"] = (
    "Network monitoring, cookies, storage, console, interception, and permissions.\n"
    "Actions: net_list, net_summary, net_errors, net_detail, net_har, "
    "intercept_set, intercept_list, intercept_clear, "
    "console_logs, dialogs, "
    "cookie_get, cookie_set, cookie_delete, storage_export, "
    "perm_grant, perm_revoke, perm_clear", {
    "net_list": _bs.browser_list_network_requests, "net_summary": _bs.browser_get_network_summary,
    "net_errors": _bs.browser_get_network_errors, "net_detail": _bs.browser_get_network_request,
    "net_har": _bs.browser_export_network_har,
    "intercept_set": _bs.browser_intercept_requests, "intercept_list": _bs.browser_list_intercepts,
    "intercept_clear": _bs.browser_clear_intercepts,
    "console_logs": _bs.browser_get_console_logs, "dialogs": _bs.browser_get_dialogs,
    "cookie_get": _bs.browser_get_cookies, "cookie_set": _bs.browser_set_cookies,
    "cookie_delete": _bs.browser_delete_cookies, "storage_export": _bs.browser_storage_state_export,
    "perm_grant": _bs.browser_grant_permissions, "perm_revoke": _bs.browser_revoke_permissions,
    "perm_clear": _bs.browser_clear_permissions,
})

R["browser_debug"] = (
    "Browser debugging, tracing, coverage, and DevTools.\n"
    "Actions: bundle, report, snapshot, metrics, viewport, state, "
    "trace_start, trace_stop, cov_start, cov_stop, cov_export, "
    "cdp_eval, cdp_trace_start, cdp_trace_stop, cdp_network, cdp_console, cdp_metrics", {
    "bundle": _bs.browser_debug_bundle, "report": _bs.browser_debug_report,
    "snapshot": _bs.browser_debug_snapshot, "metrics": _bs.browser_get_performance_metrics,
    "viewport": _bs.browser_get_viewport_state, "state": _bs.browser_snapshot_state,
    "trace_start": _bs.browser_start_trace, "trace_stop": _bs.browser_stop_trace,
    "cov_start": _bs.browser_start_coverage, "cov_stop": _bs.browser_stop_coverage,
    "cov_export": _bs.browser_export_coverage_json,
    "cdp_eval": _cdp.evaluate_javascript, "cdp_trace_start": _cdp.start_performance_trace,
    "cdp_trace_stop": _cdp.stop_performance_trace, "cdp_network": _cdp.get_network_logs,
    "cdp_console": _cdp.get_console_logs, "cdp_metrics": _cdp.get_page_metrics,
})

# ═══ DESKTOP (4) ════════════════════════════════════════════════════

R["agent_browser"] = (
    "Dedicated model-controlled browser profiles and instances that do not use host mouse/keyboard input.\n"
    "Actions: ensure_profile, start, status, stop", {
    "ensure_profile": _ab.agent_browser_ensure_profile,
    "start": _ab.agent_browser_start,
    "status": _ab.agent_browser_status,
    "stop": _ab.agent_browser_stop,
})

R["social_media"] = (
    "Read-only social media workflows using the dedicated agent browser and DOM extraction.\n"
    "Actions: platform_url, supported_platforms, search, extract", {
    "platform_url": _sm.social_platform_url,
    "supported_platforms": _sm.social_supported_platforms,
    "search": _sm.social_search,
    "extract": _sm.social_extract,
})

R["desktop_interact"] = (
    "All desktop interaction: click, mouse, keyboard, clipboard, macros.\n"
    "Actions: click, double_click, right_click, click_text, click_ocr, click_ui, click_intent, click_visible, "
    "mouse_move, mouse_drag, mouse_position, mouse_scroll, "
    "kb_type, kb_unicode, kb_press, kb_hotkey, "
    "clip_get, clip_set, "
    "macro_record, macro_replay, macro_list, macro_clear", {
    "click": _inp.click, "double_click": _inp.double_click, "right_click": _inp.right_click,
    "click_text": _win.click_text, "click_ocr": _ocr.click_ocr_text,
    "click_ui": _win.click_ui_element, "click_intent": _ai.intent_click,
    "click_visible": _ocr.click_visible_text,
    "mouse_move": _inp.move_mouse, "mouse_drag": _inp.drag_mouse,
    "mouse_position": _inp.get_cursor_position, "mouse_scroll": _inp.scroll,
    "kb_type": _inp.type_text, "kb_unicode": _inp.type_text_unicode,
    "kb_press": _inp.press_key, "kb_hotkey": _inp.hotkey,
    "clip_get": _rt.clipboard_get, "clip_set": _rt.clipboard_set,
    "macro_record": _rt.macro_record_action, "macro_replay": _rt.macro_replay,
    "macro_list": _rt.macro_list, "macro_clear": _rt.macro_clear,
})

R["desktop_window"] = (
    "Window management and UI element inspection.\n"
    "Actions: list, focus, active, focused, minimize, maximize, close, resize, "
    "summary, focused_summary, text_map, ui_find, ui_inspect", {
    "list": _win.list_windows, "focus": _win.focus_window,
    "active": _win.get_active_window, "focused": _win.get_focused_window,
    "minimize": _win.minimize_window, "maximize": _win.maximize_window,
    "close": _win.close_window, "resize": _win.move_resize_window,
    "summary": _win.window_summary, "focused_summary": _win.focused_window_summary,
    "text_map": _ocr.focused_window_text_map,
    "ui_find": _win.find_ui_elements, "ui_inspect": _win.inspect_ui_tree,
})

R["desktop_observe"] = (
    "Screenshots, streaming, OCR, visual analysis, video recording, and multi-monitor.\n"
    "Actions: capture, capture_window, capture_focused, capture_region, save_desktop, save_window, "
    "stream_start, stream_stop, stream_status, "
    "ocr_window, ocr_region, ocr_file, ocr_find, "
    "ocr_smart, screen_understand, suggest_actions, "
    "annotate, watch, describe, overview, perception, snapshot, find_image, diff, compare, "
    "record_start, record_stop, record_status, record_list, "
    "list_monitors, capture_monitor, capture_all_monitors, monitor_at_point", {
    # Capture
    "capture": _cap.capture_desktop, "capture_window": _cap.capture_window,
    "capture_focused": _cap.capture_focused_window, "capture_region": _cap.capture_region,
    "save_desktop": _cap.save_desktop_screenshot, "save_window": _cap.save_window_screenshot,
    # Streaming
    "stream_start": _cap.start_mjpeg_stream, "stream_stop": _cap.stop_mjpeg_stream,
    "stream_status": _cap.get_mjpeg_status,
    # OCR
    "ocr_window": _ocr.ocr_window, "ocr_region": _ocr.ocr_region,
    "ocr_file": _ocr.ocr_image_file, "ocr_find": _ocr.find_ocr_text,
    # Smart OCR (NEW)
    "ocr_smart": _socr.ocr_smart, "screen_understand": _socr.screen_understand,
    "suggest_actions": _socr.suggest_actions,
    # Visual analysis
    "annotate": _ai.screen_annotate, "watch": _cap.screen_watch,
    "describe": _cap.describe_screen, "overview": _cap.desktop_overview,
    "perception": _cap.desktop_perception_snapshot,
    "snapshot": _cap.desktop_snapshot_state,
    "find_image": _cap.find_image_on_screen, "diff": _cap.diff_screenshots,
    "compare": _cap.compare_capture_images,
    # Video recording (NEW)
    "record_start": _vid.desktop_record_start, "record_stop": _vid.desktop_record_stop,
    "record_status": _vid.desktop_record_status, "record_list": _vid.desktop_record_list,
    # Multi-monitor (NEW)
    "list_monitors": _mon.list_monitors, "capture_monitor": _mon.capture_monitor,
    "capture_all_monitors": _mon.capture_all_monitors, "monitor_at_point": _mon.get_monitor_at_point,
})

R["desktop_monitor"] = (
    "Desktop change monitoring and waiting.\n"
    "Actions: watch_start, watch_stop, watch_list, watch_states, watch_latest, "
    "watch_summary, watch_compare, watch_wait, watch_until, watch_goal, "
    "wait_window, wait_text, wait_ui, wait_focus, wait_desktop, wait_content", {
    "watch_start": _cap.desktop_watch_start, "watch_stop": _cap.desktop_watch_stop,
    "watch_list": _cap.desktop_watch_list, "watch_states": _cap.desktop_watch_get_states,
    "watch_latest": _cap.desktop_watch_get_latest_capture,
    "watch_summary": _cap.desktop_watch_get_change_summary,
    "watch_compare": _cap.desktop_watch_compare_latest_frames,
    "watch_wait": _cap.desktop_watch_wait_change, "watch_until": _ai.desktop_watch_until,
    "watch_goal": _cap.watch_until_goal,
    "wait_window": _win.wait_for_window, "wait_text": _win.wait_for_text,
    "wait_ui": _win.wait_for_ui_element, "wait_focus": _win.wait_for_focus_change,
    "wait_desktop": _cap.wait_for_desktop_change, "wait_content": _win.wait_for_window_content_change,
})

# ═══ SYSTEM (2) ═════════════════════════════════════════════════════

R["system_info"] = (
    "System info, environment, network, power, services, registry, and misc.\n"
    "Actions: info, uptime, battery, disk, network, "
    "env_get, env_list, env_set, "
    "ping, port, resolve, "
    "notify, wallpaper, "
    "shutdown, restart, sleep, lock, "
    "svc_list, svc_status, svc_start, svc_stop, svc_restart, "
    "reg_read, reg_write, reg_delete", {
    "info": _sys.get_system_info, "uptime": _sys.get_system_uptime,
    "battery": _sys.get_battery_info, "disk": _sys.get_disk_usage,
    "network": _sys.get_network_info,
    "env_get": _sys.get_environment_variable, "env_list": _sys.get_environment_variables,
    "env_set": _sys.set_environment_variable,
    "ping": _sys.ping_host, "port": _sys.check_port, "resolve": _sys.resolve_hostname,
    "notify": _sys.show_notification, "wallpaper": _sys.set_desktop_wallpaper,
    "shutdown": _sys.shutdown_computer, "restart": _sys.restart_computer,
    "sleep": _sys.sleep_computer, "lock": _sys.lock_computer,
    "svc_list": _sys.list_windows_services, "svc_status": _sys.get_service_status,
    "svc_start": _sys.start_service, "svc_stop": _sys.stop_service,
    "svc_restart": _sys.restart_service,
    "reg_read": _sys.read_registry_key, "reg_write": _sys.write_registry_value,
    "reg_delete": _sys.delete_registry_value,
})

R["system_ops"] = (
    "File operations, archives, and process management.\n"
    "Actions: read, write, delete, list, search, mkdir, symlink, hash, "
    "compress, decompress, "
    "ps_list, ps_detail, ps_kill, run", {
    "read": _sys.read_file, "write": _sys.write_file, "delete": _sys.delete_file,
    "list": _sys.list_directory, "search": _sys.search_files,
    "mkdir": _sys.create_directory, "symlink": _sys.create_symbolic_link,
    "hash": _sys.calculate_file_hash,
    "compress": _sys.compress_files, "decompress": _sys.decompress_files,
    "ps_list": _sys.list_processes, "ps_detail": _sys.get_process_details,
    "ps_kill": _sys.kill_process, "run": _rt.run_command,
})

# ═══ RUNTIME (1) ════════════════════════════════════════════════════

R["runtime"] = (
    "MCP runtime status, analysis, and health.\n"
    "Actions: status, events, clear, health, manifest, analysis_export, analysis_latest, ping", {
    "status": _trt.runtime_get_status, "events": _trt.runtime_get_recent_events,
    "clear": _trt.runtime_clear_events, "health": _trt.runtime_healthcheck,
    "manifest": _trt.runtime_tool_manifest,
    "analysis_export": _cap.export_analysis_history, "analysis_latest": _cap.get_latest_analysis,
    "ping": _rt.ping,
})

from . import workflow_templates as _wt
from .plugins import discover_plugins as _discover_plugins

R["operator"] = (
    "Model operator task sessions with goal, steps, risk, and evidence.\n"
    "Actions: start, step, finish, session", {
    "start": _op.operator_start,
    "step": _op.operator_step,
    "finish": _op.operator_finish,
    "session": _op.operator_session,
})

R["workflow"] = (
    "Autonomous workflow engine — chain actions into reusable sequences.\n"
    "Actions: run, record_start, record_step, record_stop, list, load, delete, "
    "observe, risk, act_verify, "
    "template_list, template_get, template_instantiate", {
    "run": _wf.workflow_run, "record_start": _wf.workflow_record_start,
    "record_step": _wf.workflow_record_step, "record_stop": _wf.workflow_record_stop,
    "list": _wf.workflow_list, "load": _wf.workflow_load, "delete": _wf.workflow_delete,
    "observe": _wf.workflow_observe,
    "risk": _wf.workflow_risk,
    "act_verify": _wf.workflow_act_verify,
    # Templates
    "template_list": _wt.template_list,
    "template_get": _wt.template_get,
    "template_instantiate": _wt.template_instantiate,
})

# ═══ PLUGINS (AUTO-DISCOVERED) ═════════════════════════════════════
from .plugins import discover_plugins as _discover_plugins

for _pname, (_pdoc, _pactions) in _discover_plugins().items():
    if _pname not in R:
        R[_pname] = (_pdoc, _pactions)

# ═══ DYNAMIC REGISTRATION ══════════════════════════════════════════
import json as _json

for _name, (_doc, _actions) in R.items():
    def _make(name=_name, doc=_doc, actions=_actions):
        async def tool_fn(action: str = "", kwargs: str = "{}", timeout_ms: int = None) -> dict[str, Any]:
            try:
                kw = _json.loads(kwargs) if isinstance(kwargs, str) else kwargs
            except _json.JSONDecodeError:
                return {"ok": False, "error": f"Invalid kwargs JSON: {kwargs!r}"}
            if not isinstance(kw, dict):
                return {"ok": False, "error": f"kwargs must be a JSON object, got {type(kw).__name__}"}
            # Offload to thread so sync Playwright calls don't conflict with asyncio loop
            return await anyio.to_thread.run_sync(
                functools.partial(_d, actions, action, tool_name=name, timeout_ms=timeout_ms, **kw)
            )
        tool_fn.__name__ = name
        tool_fn.__qualname__ = name
        tool_fn.__doc__ = doc
        return tool_fn
    mcp.tool()(_make())
