"""Read-only social media helpers backed by the dedicated agent browser."""

from __future__ import annotations

import re
import time
import unicodedata
from typing import Any
from urllib.parse import quote_plus

from ..browser_core import get_playwright_page
from ..runtime import record_event
from ..state import PLAYWRIGHT_SESSIONS, PLAYWRIGHT_SESSIONS_LOCK
from . import browser_sessions as _bs
from .agent_browser import agent_browser_start, agent_browser_stop

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
_DEFAULT_SCROLL_PAUSE_MS = 500

_SCROLL_SCRIPT = r"""
() => {
  const beforeY = window.scrollY || document.documentElement.scrollTop || 0;
  const beforeHeight = Math.max(document.body.scrollHeight || 0, document.documentElement.scrollHeight || 0);
  const delta = Math.max(Math.floor((window.innerHeight || 900) * 0.85), 600);
  window.scrollBy({ top: delta, left: 0, behavior: 'instant' });
  const afterY = window.scrollY || document.documentElement.scrollTop || 0;
  const afterHeight = Math.max(document.body.scrollHeight || 0, document.documentElement.scrollHeight || 0);
  return { beforeY, afterY, beforeHeight, afterHeight, delta };
}
"""

_METRIC_NUMBER = r"(?P<value>\d[\d\s.,]*)(?P<suffix>\s*[kmb])?"
_METRIC_PATTERNS = {
    "replies": [
        rf"{_METRIC_NUMBER}\s*(?:reponses?|replies?|responses?|comments?)\b",
    ],
    "reposts": [
        rf"{_METRIC_NUMBER}\s*(?:reposts?|retweets?|shares?)\b",
    ],
    "likes": [
        rf"{_METRIC_NUMBER}\s*(?:j\s*aime|likes?)\b",
    ],
    "bookmarks": [
        rf"{_METRIC_NUMBER}\s*(?:signets?|bookmarks?|saves?)\b",
    ],
    "views": [
        rf"{_METRIC_NUMBER}\s*(?:vues?|views?)\b",
    ],
}
_RANK_WEIGHTS = {
    "views": 0.05,
    "likes": 3.0,
    "replies": 2.0,
    "reposts": 4.0,
    "bookmarks": 5.0,
}


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
    scroll_steps: int = 0,
    scroll_pause_ms: int = _DEFAULT_SCROLL_PAUSE_MS,
    rank: bool = True,
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
            scroll_steps=scroll_steps,
            scroll_pause_ms=scroll_pause_ms,
            rank=rank,
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
            scroll_steps=scroll_steps,
            scroll_pause_ms=scroll_pause_ms,
            rank=rank,
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
    scroll_steps: int,
    scroll_pause_ms: int,
    rank: bool,
    automation: str,
) -> dict[str, Any]:
    items, attempts, waited_ms, scrolls_performed = _evaluate_items_with_wait(
        page=page,
        target=target,
        safe_limit=safe_limit,
        wait_ms=wait_ms,
        poll_ms=poll_ms,
        scroll_steps=scroll_steps,
        scroll_pause_ms=scroll_pause_ms,
    )
    if rank:
        items = _rank_items(items)
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
        "scroll_steps": scrolls_performed,
        "ranked": bool(rank),
    }


def _evaluate_items_with_wait(
    page: Any,
    target: str,
    safe_limit: int,
    wait_ms: int,
    poll_ms: int,
    scroll_steps: int,
    scroll_pause_ms: int,
) -> tuple[list[dict[str, Any]], int, int, int]:
    script = _EXTRACTORS[target]
    deadline = time.monotonic() + max(int(wait_ms), 0) / 1000
    poll_seconds = max(int(poll_ms), 0) / 1000
    attempts = 0
    scrolls_performed = 0
    started_at = time.monotonic()
    items: list[dict[str, Any]] = []

    while True:
        attempts += 1
        raw_items = page.evaluate(script, safe_limit)
        items = _merge_items(items, _normalize_items(raw_items, target, safe_limit), safe_limit)
        if items or time.monotonic() >= deadline:
            break
        sleep_seconds = min(poll_seconds, max(deadline - time.monotonic(), 0))
        if sleep_seconds <= 0:
            break
        time.sleep(sleep_seconds)
    for _ in range(max(int(scroll_steps), 0)):
        if len(items) >= safe_limit:
            break
        scroll_result = _scroll_page(page)
        scrolls_performed += 1
        pause_seconds = max(int(scroll_pause_ms), 0) / 1000
        if pause_seconds:
            time.sleep(pause_seconds)
        attempts += 1
        raw_items = page.evaluate(script, safe_limit)
        before_count = len(items)
        items = _merge_items(items, _normalize_items(raw_items, target, safe_limit), safe_limit)
        if len(items) == before_count and _scroll_is_exhausted(scroll_result):
            break
    waited_ms = int((time.monotonic() - started_at) * 1000)
    return items[:safe_limit], attempts, waited_ms, scrolls_performed


def _scroll_page(page: Any) -> dict[str, Any]:
    try:
        result = page.evaluate(_SCROLL_SCRIPT)
    except TypeError:
        result = page.evaluate(_SCROLL_SCRIPT, None)
    return result if isinstance(result, dict) else {}


def _scroll_is_exhausted(scroll_result: dict[str, Any]) -> bool:
    before_y = float(scroll_result.get("beforeY") or 0)
    after_y = float(scroll_result.get("afterY") or 0)
    before_height = float(scroll_result.get("beforeHeight") or 0)
    after_height = float(scroll_result.get("afterHeight") or 0)
    return after_y <= before_y and after_height <= before_height


def _merge_items(existing: list[dict[str, Any]], new_items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    merged = list(existing)
    seen = {_item_key(item) for item in merged}
    for item in new_items:
        key = _item_key(item)
        if key in seen:
            continue
        seen.add(key)
        item.setdefault("source_index", len(merged))
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def _item_key(item: dict[str, Any]) -> str:
    for key in ("url", "author_url"):
        value = str(item.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    text = str(item.get("text") or item.get("title") or "").strip().lower()
    return f"text:{text[:180]}"


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
    scroll_steps: int,
    scroll_pause_ms: int,
    rank: bool,
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
        scroll_steps=scroll_steps,
        scroll_pause_ms=scroll_pause_ms,
        rank=rank,
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
    keep_open: bool = True,
    scroll_steps: int | None = None,
    scroll_pause_ms: int = _DEFAULT_SCROLL_PAUSE_MS,
    rank: bool = True,
) -> dict[str, Any]:
    """Open a read-only social search in the agent browser and extract DOM results."""
    target = _platform(platform)
    safe_limit = max(1, min(int(limit), 100))
    resolved_scroll_steps = _resolve_scroll_steps(safe_limit, scroll_steps)
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
    browser_stop: dict[str, Any] | None = None
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
    try:
        extracted = social_extract(
            platform=target,
            session_id=started["session_id"],
            page_id=started.get("page_id"),
            limit=safe_limit,
            scroll_steps=resolved_scroll_steps,
            scroll_pause_ms=scroll_pause_ms,
            rank=rank,
        )
    finally:
        if not keep_open:
            browser_stop = agent_browser_stop(instance_name=started.get("instance_name") or instance_name, platform=target)
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
        "keep_open": bool(keep_open),
        "browser_stop": browser_stop,
    }


def _resolve_scroll_steps(limit: int, scroll_steps: int | None) -> int:
    if scroll_steps is not None:
        return max(int(scroll_steps), 0)
    if limit <= 10:
        return 0
    return min(10, max(1, (limit - 1) // 5))


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


def _rank_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for fallback_index, item in enumerate(items):
        metrics = _extract_metrics(item)
        ranked = dict(item)
        ranked["metrics"] = metrics
        ranked["rank_score"] = _rank_score(metrics)
        ranked.setdefault("source_index", fallback_index)
        enriched.append(ranked)
    ranked_items = sorted(enriched, key=lambda item: (-float(item.get("rank_score") or 0), int(item.get("source_index") or 0)))
    for position, item in enumerate(ranked_items, start=1):
        item["rank_position"] = position
    return ranked_items


def _extract_metrics(item: dict[str, Any]) -> dict[str, int]:
    text = " | ".join(
        str(item.get(key) or "")
        for key in ("metrics_text", "metadata", "text", "title")
    )
    normalized = _normalize_metric_text(text)
    metrics = {key: 0 for key in _METRIC_PATTERNS}
    for metric, patterns in _METRIC_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue
            metrics[metric] = _parse_metric_number(match.group("value"), match.groupdict().get("suffix"))
            break
    return metrics


def _normalize_metric_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = re.sub(r"['`]+", " ", ascii_text)
    return re.sub(r"\s+", " ", ascii_text).strip().lower()


def _parse_metric_number(value: str, suffix: str | None = None) -> int:
    compact = re.sub(r"\s+", "", str(value or ""))
    suffix_key = str(suffix or "").strip().lower()
    if suffix_key:
        compact = compact.replace(",", ".")
    elif "," in compact and "." in compact:
        compact = compact.replace(",", "")
    elif "," in compact:
        parts = compact.split(",")
        compact = "".join(parts) if len(parts) > 1 and all(len(part) == 3 for part in parts[1:]) else compact.replace(",", ".")
    elif "." in compact:
        parts = compact.split(".")
        if len(parts) > 1 and all(len(part) == 3 for part in parts[1:]):
            compact = "".join(parts)
    try:
        number = float(compact)
    except ValueError:
        return 0
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix_key, 1)
    return int(number * multiplier)


def _rank_score(metrics: dict[str, int]) -> float:
    return round(sum(float(metrics.get(key) or 0) * weight for key, weight in _RANK_WEIGHTS.items()), 2)


__all__ = [
    "social_extract",
    "social_platform_url",
    "social_search",
    "social_supported_platforms",
]
