"""Read-only social media helpers backed by the dedicated agent browser."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote_plus

from ..browser_core import get_playwright_page
from ..runtime import record_event
from ..state import PLAYWRIGHT_SESSIONS, PLAYWRIGHT_SESSIONS_LOCK
from . import browser_sessions as _bs
from .agent_browser import agent_browser_start

_PLATFORM_ALIASES = {
    "twitter": "x",
    "youtube_studio": "youtube_studio",
    "youtube-studio": "youtube_studio",
    "yt_studio": "youtube_studio",
    "yt": "youtube",
}

_SUPPORTED_PLATFORMS = {"x", "youtube", "youtube_studio", "tiktok", "instagram"}

_X_EXTRACTOR = r"""
(limit) => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const absolute = (href) => {
    try { return new URL(href, location.href).href; } catch { return href || null; }
  };
  const links = (root) => Array.from(root.querySelectorAll('a[href]')).map((a) => absolute(a.getAttribute('href'))).filter(Boolean);
  return Array.from(document.querySelectorAll('article')).slice(0, limit).map((article, index) => {
    const hrefs = links(article);
    const statusUrl = hrefs.find((href) => /\/status\/\d+/.test(href)) || null;
    const authorUrl = hrefs.find((href) => /^https?:\/\/(x|twitter)\.com\/[^/?#]+\/?$/.test(href) && !/\/(home|search|notifications|messages|i)\b/.test(href)) || null;
    const texts = Array.from(article.querySelectorAll('[data-testid="tweetText"], div[lang]'))
      .map((node) => clean(node.innerText || node.textContent))
      .filter(Boolean);
    const time = article.querySelector('time');
    const metricLabels = Array.from(article.querySelectorAll('[role="group"], [aria-label]'))
      .map((node) => clean(node.getAttribute('aria-label')))
      .filter(Boolean);
    return {
      platform: 'x',
      index,
      text: texts.join('\n'),
      url: statusUrl,
      author_url: authorUrl,
      time: time ? (time.getAttribute('datetime') || clean(time.textContent)) : null,
      metrics_text: metricLabels.join(' | '),
      media_count: article.querySelectorAll('img, video').length,
      source: 'dom'
    };
  }).filter((item) => item.text || item.url);
}
"""

_YOUTUBE_EXTRACTOR = r"""
(limit) => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const absolute = (href) => {
    try { return new URL(href, location.href).href; } catch { return href || null; }
  };
  const containers = Array.from(document.querySelectorAll('ytd-video-renderer, ytd-grid-video-renderer, ytd-rich-item-renderer, ytd-channel-renderer, a#video-title'));
  const seen = new Set();
  return containers.map((node, index) => {
    const root = node.closest('ytd-video-renderer, ytd-grid-video-renderer, ytd-rich-item-renderer, ytd-channel-renderer') || node;
    const titleNode = root.querySelector('#video-title, a#video-title, yt-formatted-string#video-title, a[href*="/watch"], a[href*="/shorts"]') || root;
    const href = titleNode && titleNode.getAttribute ? titleNode.getAttribute('href') : '';
    const url = href ? absolute(href) : null;
    const title = clean(titleNode.getAttribute && titleNode.getAttribute('title') || titleNode.textContent);
    const channel = clean(root.querySelector('ytd-channel-name, #channel-name, .ytd-channel-name')?.textContent);
    const metadata = clean(root.querySelector('#metadata-line, #metadata, #video-info, yt-formatted-string')?.textContent);
    const key = url || `${title}:${index}`;
    if (!title || seen.has(key)) return null;
    seen.add(key);
    return { platform: 'youtube', index, title, text: title, url, channel, metadata, source: 'dom' };
  }).filter(Boolean).slice(0, limit);
}
"""

_YOUTUBE_STUDIO_EXTRACTOR = r"""
(limit) => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const rows = Array.from(document.querySelectorAll('ytcp-video-row, ytcp-video-list-cell-video, tr, [role="row"]'));
  return rows.map((row, index) => {
    const text = clean(row.innerText || row.textContent);
    const link = row.querySelector('a[href]');
    let url = null;
    if (link) {
      try { url = new URL(link.getAttribute('href'), location.href).href; } catch { url = link.getAttribute('href'); }
    }
    return { platform: 'youtube_studio', index, text, url, source: 'dom' };
  }).filter((item) => item.text).slice(0, limit);
}
"""

_TIKTOK_EXTRACTOR = r"""
(limit) => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const absolute = (href) => {
    try { return new URL(href, location.href).href; } catch { return href || null; }
  };
  const cards = Array.from(document.querySelectorAll('a[href*="/video/"], div[data-e2e*="search"], div[data-e2e*="user-post-item"]'));
  const seen = new Set();
  return cards.map((node, index) => {
    const root = node.closest('div') || node;
    const link = node.matches && node.matches('a[href]') ? node : root.querySelector('a[href*="/video/"]');
    const url = link ? absolute(link.getAttribute('href')) : null;
    const text = clean(root.innerText || root.textContent);
    if (!text && !url) return null;
    const key = url || `${text}:${index}`;
    if (seen.has(key)) return null;
    seen.add(key);
    return { platform: 'tiktok', index, text, url, source: 'dom' };
  }).filter(Boolean).slice(0, limit);
}
"""

_INSTAGRAM_EXTRACTOR = r"""
(limit) => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const absolute = (href) => {
    try { return new URL(href, location.href).href; } catch { return href || null; }
  };
  const links = Array.from(document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"], a[href*="/tv/"]'));
  const seen = new Set();
  return links.map((link, index) => {
    const url = absolute(link.getAttribute('href'));
    const root = link.closest('article, main, div') || link;
    const text = clean(root.innerText || root.textContent || link.getAttribute('aria-label') || '');
    const image_alt = clean(link.querySelector('img')?.getAttribute('alt'));
    if (!url || seen.has(url)) return null;
    seen.add(url);
    return { platform: 'instagram', index, text: text || image_alt, url, image_alt, source: 'dom' };
  }).filter(Boolean).slice(0, limit);
}
"""

_EXTRACTORS = {
    "x": _X_EXTRACTOR,
    "youtube": _YOUTUBE_EXTRACTOR,
    "youtube_studio": _YOUTUBE_STUDIO_EXTRACTOR,
    "tiktok": _TIKTOK_EXTRACTOR,
    "instagram": _INSTAGRAM_EXTRACTOR,
}

_DEFAULT_EXTRACT_WAIT_MS = 10000
_DEFAULT_EXTRACT_POLL_MS = 250


def _platform(platform: str) -> str:
    key = str(platform or "").strip().lower().replace(" ", "_")
    key = _PLATFORM_ALIASES.get(key, key)
    if key not in _SUPPORTED_PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform!r}. Use one of: {', '.join(sorted(_SUPPORTED_PLATFORMS))}.")
    return key


def _query(value: str) -> str:
    return quote_plus(str(value or "").strip())


def social_platform_url(platform: str, query: str = "", mode: str = "search") -> dict[str, Any]:
    """Build a read-only social media URL for a model-planned browser workflow."""
    target = _platform(platform)
    encoded = _query(query)
    if mode != "search":
        raise ValueError("Only read-only search mode is currently supported.")
    if target == "x":
        url = f"https://x.com/search?q={encoded}&src=typed_query&f=top" if encoded else "https://x.com/explore"
    elif target == "youtube":
        url = f"https://www.youtube.com/results?search_query={encoded}" if encoded else "https://www.youtube.com/"
    elif target == "youtube_studio":
        url = "https://studio.youtube.com/"
    elif target == "tiktok":
        url = f"https://www.tiktok.com/search?q={encoded}" if encoded else "https://www.tiktok.com/explore"
    else:
        url = f"https://www.instagram.com/explore/search/keyword/?q={encoded}" if encoded else "https://www.instagram.com/explore/"
    return {
        "ok": True,
        "platform": target,
        "query": query,
        "mode": mode,
        "url": url,
        "read_only": True,
        "host_interactive": False,
    }


def social_supported_platforms() -> dict[str, Any]:
    """List social media platforms supported by the read-only DOM extractors."""
    return {
        "ok": True,
        "platforms": sorted(_SUPPORTED_PLATFORMS),
        "read_only": True,
        "extraction_method": "dom",
    }


def social_extract(
    platform: str,
    session_id: str,
    page_id: str | None = None,
    limit: int = 10,
    wait_ms: int = _DEFAULT_EXTRACT_WAIT_MS,
    poll_ms: int = _DEFAULT_EXTRACT_POLL_MS,
) -> dict[str, Any]:
    """Extract visible social media items from the current page through DOM/CDP."""
    target = _platform(platform)
    safe_limit = max(1, min(int(limit), 100))
    snapshot = _playwright_session_snapshot(session_id)
    try:
        session, resolved_page_id, page = get_playwright_page(session_id, page_id=page_id)
        return _extract_from_page(
            target=target,
            session_id=session_id,
            page_id=resolved_page_id,
            page=page,
            safe_limit=safe_limit,
            wait_ms=wait_ms,
            poll_ms=poll_ms,
            automation="cdp" if session.get("cdp_endpoint") else "playwright",
        )
    except Exception as exc:
        if not _is_stale_cdp_thread_error(exc) or not snapshot.get("cdp_endpoint"):
            raise
        return _extract_from_reattached_cdp(
            target=target,
            original_session_id=session_id,
            page_id=page_id,
            safe_limit=safe_limit,
            wait_ms=wait_ms,
            poll_ms=poll_ms,
            snapshot=snapshot,
        )


def _extract_from_page(
    target: str,
    session_id: str,
    page_id: str,
    page: Any,
    safe_limit: int,
    wait_ms: int,
    poll_ms: int,
    automation: str,
) -> dict[str, Any]:
    items, attempts, waited_ms = _evaluate_items_with_wait(
        page=page,
        target=target,
        safe_limit=safe_limit,
        wait_ms=wait_ms,
        poll_ms=poll_ms,
    )
    record_event("social_media_extract", platform=target, session_id=session_id, page_id=page_id, item_count=len(items))
    return {
        "ok": True,
        "platform": target,
        "session_id": session_id,
        "page_id": page_id,
        "url": getattr(page, "url", ""),
        "read_only": True,
        "browser_context": "agent_dedicated",
        "automation": automation,
        "host_interactive": False,
        "extraction_method": "dom",
        "source": "dom",
        "items": items,
        "item_count": len(items),
        "extract_attempts": attempts,
        "extract_waited_ms": waited_ms,
    }


def _evaluate_items_with_wait(
    page: Any,
    target: str,
    safe_limit: int,
    wait_ms: int,
    poll_ms: int,
) -> tuple[list[dict[str, Any]], int, int]:
    script = _EXTRACTORS[target]
    deadline = time.monotonic() + max(int(wait_ms), 0) / 1000
    poll_seconds = max(int(poll_ms), 0) / 1000
    attempts = 0
    started_at = time.monotonic()
    items: list[dict[str, Any]] = []

    while True:
        attempts += 1
        raw_items = page.evaluate(script, safe_limit)
        items = _normalize_items(raw_items, target, safe_limit)
        if items or time.monotonic() >= deadline:
            waited_ms = int((time.monotonic() - started_at) * 1000)
            return items, attempts, waited_ms
        sleep_seconds = min(poll_seconds, max(deadline - time.monotonic(), 0))
        if sleep_seconds <= 0:
            waited_ms = int((time.monotonic() - started_at) * 1000)
            return items, attempts, waited_ms
        time.sleep(sleep_seconds)


def _playwright_session_snapshot(session_id: str) -> dict[str, Any]:
    with PLAYWRIGHT_SESSIONS_LOCK:
        session = PLAYWRIGHT_SESSIONS.get(session_id)
        if not session:
            return {}
        return {
            "session_id": session.get("session_id"),
            "browser_name": session.get("browser_name"),
            "profile_name": session.get("profile_name"),
            "instance_name": session.get("instance_name"),
            "cdp_endpoint": session.get("cdp_endpoint"),
            "browser_pid": session.get("browser_pid"),
            "launched_debug_browser": session.get("launched_debug_browser"),
            "init_script_paths": list(session.get("init_script_paths") or []),
            "granted_permissions": list(session.get("granted_permissions") or []),
        }


def _is_stale_cdp_thread_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "greenlet" in text or "cannot switch to a different thread" in text


def _extract_from_reattached_cdp(
    target: str,
    original_session_id: str,
    page_id: str | None,
    safe_limit: int,
    wait_ms: int,
    poll_ms: int,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    attached = _bs.browser_attach_cdp(
        endpoint=str(snapshot["cdp_endpoint"]),
        browser=str(snapshot.get("browser_name") or "chrome"),
        instance_name=snapshot.get("instance_name"),
        profile_name=snapshot.get("profile_name"),
        browser_pid=snapshot.get("browser_pid"),
        launched_debug_browser=bool(snapshot.get("launched_debug_browser")),
        init_script_paths=snapshot.get("init_script_paths"),
        grant_permissions=snapshot.get("granted_permissions"),
    )
    session_id = attached["session_id"]
    _session, resolved_page_id, page = get_playwright_page(session_id, page_id=attached.get("page_id") or page_id)
    result = _extract_from_page(
        target=target,
        session_id=session_id,
        page_id=resolved_page_id,
        page=page,
        safe_limit=safe_limit,
        wait_ms=wait_ms,
        poll_ms=poll_ms,
        automation="cdp",
    )
    result["cdp_reattached"] = True
    result["original_session_id"] = original_session_id
    return result


def social_search(
    platform: str,
    query: str,
    limit: int = 10,
    profile_name: str = "",
    instance_name: str = "",
    browser: str = "chrome",
    browser_engine: str = "playwright",
    debug_port: int = 9333,
    headless: bool = True,
    width: int | str = "auto",
    height: int | str = "auto",
    wait_until: str = "domcontentloaded",
) -> dict[str, Any]:
    """Open a read-only social search in the agent browser and extract DOM results."""
    target = _platform(platform)
    url_info = social_platform_url(platform=target, query=query)
    started = agent_browser_start(
        platform=target,
        url=url_info["url"],
        profile_name=profile_name,
        instance_name=instance_name,
        browser=browser,
        browser_engine=browser_engine,
        debug_port=debug_port,
        headless=headless,
        width=width,
        height=height,
        wait_until=wait_until,
    )
    if not started.get("ok", True):
        return {
            **started,
            "ok": False,
            "platform": target,
            "query": query,
            "url": url_info["url"],
            "read_only": True,
            "browser_context": "agent_dedicated",
            "host_interactive": False,
        }
    extracted = social_extract(
        platform=target,
        session_id=started["session_id"],
        page_id=started.get("page_id"),
        limit=limit,
    )
    return {
        **extracted,
        "ok": bool(extracted.get("ok", True)),
        "platform": target,
        "query": query,
        "search_url": url_info["url"],
        "url": extracted.get("url") or started.get("url") or url_info["url"],
        "browser": started,
        "browser_context": "agent_dedicated",
        "automation": started.get("automation") or extracted.get("automation") or "playwright",
        "host_interactive": False,
        "read_only": True,
    }


def _normalize_items(raw_items: Any, platform: str, limit: int) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    items: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items[:limit]):
        if not isinstance(item, dict):
            continue
        normalized = {str(key): value for key, value in item.items()}
        normalized.setdefault("platform", platform)
        normalized.setdefault("index", index)
        normalized.setdefault("source", "dom")
        if normalized.get("text") is not None:
            normalized["text"] = str(normalized["text"]).strip()
        if normalized.get("title") is not None:
            normalized["title"] = str(normalized["title"]).strip()
        if not normalized.get("text") and normalized.get("title"):
            normalized["text"] = normalized["title"]
        if normalized.get("text") or normalized.get("url") or normalized.get("title"):
            items.append(normalized)
    return items


__all__ = [
    "social_extract",
    "social_platform_url",
    "social_search",
    "social_supported_platforms",
]
