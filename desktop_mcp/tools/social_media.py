"""Read-only social media helpers backed by the dedicated agent browser."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import re
import time
import unicodedata
from typing import Any
from urllib.parse import quote_plus, urlparse

from ..browser_core import get_playwright_page
from ..cdp_client import (
    cdp_close_page_target,
    cdp_navigate,
    cdp_page_info as _cdp_page_info,
    js_call as _js_call,
    open_cdp_session as _open_cdp_session,
    select_cdp_page_target as _select_cdp_page_target,
)
from ..runtime import record_event
from ..state import PLAYWRIGHT_SESSIONS, PLAYWRIGHT_SESSIONS_LOCK
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
  const blockedProfiles = new Set([
    'about', 'accounts', 'api', 'blog', 'developer', 'direct', 'explore',
    'help', 'legal', 'meta', 'oauth', 'p', 'popular', 'privacy', 'reel', 'reels',
    'stories', 'terms', 'tv', 'web'
  ]);
  const genericProfileLabels = new Set([
    'accueil', 'create', 'créer', 'explore', 'explorer', 'home', 'messages',
    'notifications', 'popular', 'populaire', 'profile', 'profil', 'reels',
    'search', 'rechercher'
  ]);
  const typeForUrl = (url) => {
    try {
      const parsed = new URL(url, location.href);
      if (!/(^|\.)instagram\.com$/i.test(parsed.hostname)) return null;
      const parts = parsed.pathname.split('/').filter(Boolean);
      if (parts[0] === 'p') return 'post';
      if (parts[0] === 'reel') return 'reel';
      if (parts[0] === 'tv') return 'tv';
      if (parts.length === 1 && /^[A-Za-z0-9._]+$/.test(parts[0]) && !blockedProfiles.has(parts[0].toLowerCase())) return 'profile';
    } catch {}
    return null;
  };
  const links = Array.from(document.querySelectorAll('main a[href], article a[href], a[href]'));
  const seen = new Set();
  const items = [];
  links.forEach((link, index) => {
    const url = absolute(link.getAttribute('href'));
    const item_type = typeForUrl(url);
    if (!url || !item_type || seen.has(url)) return;
    const root = link.closest('article, [role="button"], main, div') || link;
    const image_alt = clean(link.querySelector('img')?.getAttribute('alt'));
    const aria = clean(link.getAttribute('aria-label'));
    const linkText = clean(link.innerText || link.textContent);
    const rootText = clean(root.innerText || root.textContent);
    const fallback = item_type === 'profile' ? url.split('instagram.com/')[1]?.split(/[/?#]/)[0] : '';
    const label = (linkText || aria || rootText || '').toLowerCase();
    if (item_type === 'profile' && genericProfileLabels.has(label)) return;
    seen.add(url);
    items.push({
      platform: 'instagram',
      index,
      text: linkText || aria || image_alt || rootText || fallback || '',
      url,
      image_alt,
      item_type,
      source: 'dom'
    });
  });
  const priority = { post: 0, reel: 1, tv: 2, profile: 3 };
  return items.sort((a, b) => (priority[a.item_type] ?? 9) - (priority[b.item_type] ?? 9) || a.index - b.index).slice(0, limit);
}
"""

_EXTRACTORS = {
    "x": _X_EXTRACTOR,
    "youtube": _YOUTUBE_EXTRACTOR,
    "youtube_studio": _YOUTUBE_STUDIO_EXTRACTOR,
    "tiktok": _TIKTOK_EXTRACTOR,
    "instagram": _INSTAGRAM_EXTRACTOR,
}

_X_DETAIL_EXTRACTOR = r"""
() => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const absolute = (href) => {
    try { return new URL(href, location.href).href; } catch { return href || null; }
  };
  const root = document.querySelector('article') || document.querySelector('main') || document.body;
  const textNodes = Array.from(root.querySelectorAll('[data-testid="tweetText"], div[lang]'))
    .map((node) => clean(node.innerText || node.textContent))
    .filter(Boolean);
  const links = Array.from(root.querySelectorAll('a[href]')).map((a) => absolute(a.getAttribute('href'))).filter(Boolean);
  const authorUrl = links.find((href) => /^https?:\/\/(x|twitter)\.com\/[^/?#]+\/?$/.test(href) && !/\/(home|search|notifications|messages|i)\b/.test(href)) || null;
  const userName = clean(root.querySelector('[data-testid="User-Name"]')?.innerText || '');
  const metricLabels = Array.from(root.querySelectorAll('[role="group"], [aria-label]'))
    .map((node) => clean(node.getAttribute('aria-label')))
    .filter(Boolean);
  const media = Array.from(root.querySelectorAll('img, video')).map((node) => ({
    tag: node.tagName.toLowerCase(),
    src: node.currentSrc || node.src || node.poster || null,
    alt: clean(node.getAttribute('alt') || '')
  })).filter((item) => item.src || item.alt);
  const allText = clean(root.innerText || root.textContent || document.body.innerText || '');
  const text = textNodes.join('\n') || allText;
  return {
    platform: 'x',
    url: location.href,
    title: document.title,
    author: userName.split('\n').find(Boolean) || null,
    author_url: authorUrl,
    text,
    full_text: allText,
    metrics_text: metricLabels.join(' | '),
    links: Array.from(new Set(links)),
    media,
    source: 'dom'
  };
}
"""

_YOUTUBE_DETAIL_EXTRACTOR = r"""
() => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const absolute = (href) => {
    try { return new URL(href, location.href).href; } catch { return href || null; }
  };
  const root = document.querySelector('ytd-watch-flexy, ytd-page-manager, main') || document.body;
  const title = clean(root.querySelector('h1, #title h1, ytd-watch-metadata h1')?.innerText || document.title);
  const authorNode = root.querySelector('ytd-channel-name a, #owner a, a[href^="/@"]');
  const description = clean(root.querySelector('#description-inline-expander, ytd-text-inline-expander, #description')?.innerText || '');
  const metadata = clean(root.querySelector('#info, #info-strings, ytd-watch-info-text')?.innerText || '');
  const links = Array.from(root.querySelectorAll('a[href]')).map((a) => absolute(a.getAttribute('href'))).filter(Boolean);
  return {
    platform: 'youtube',
    url: location.href,
    title,
    author: clean(authorNode?.innerText || authorNode?.textContent || ''),
    author_url: authorNode ? absolute(authorNode.getAttribute('href')) : null,
    text: description || title,
    full_text: clean([title, metadata, description].filter(Boolean).join('\n')),
    metrics_text: metadata,
    links: Array.from(new Set(links)),
    media: [],
    source: 'dom'
  };
}
"""

_TIKTOK_DETAIL_EXTRACTOR = r"""
() => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const absolute = (href) => {
    try { return new URL(href, location.href).href; } catch { return href || null; }
  };
  const root = document.querySelector('[data-e2e*="browse-video"], main, article') || document.body;
  const links = Array.from(root.querySelectorAll('a[href]')).map((a) => absolute(a.getAttribute('href'))).filter(Boolean);
  const authorLink = links.find((href) => /^https?:\/\/www\.tiktok\.com\/@[^/?#]+/.test(href)) || null;
  const text = clean(root.querySelector('[data-e2e*="video-desc"], [data-e2e*="browse-video-desc"], h1')?.innerText || root.innerText || '');
  const metricLabels = Array.from(root.querySelectorAll('[aria-label], strong, button'))
    .map((node) => clean(node.getAttribute('aria-label') || node.innerText || node.textContent))
    .filter(Boolean);
  const media = Array.from(root.querySelectorAll('video, img')).map((node) => ({
    tag: node.tagName.toLowerCase(),
    src: node.currentSrc || node.src || node.poster || null,
    alt: clean(node.getAttribute('alt') || '')
  })).filter((item) => item.src || item.alt);
  return {
    platform: 'tiktok',
    url: location.href,
    title: document.title,
    author: authorLink ? authorLink.split('/@')[1]?.split(/[/?#]/)[0] : null,
    author_url: authorLink,
    text,
    full_text: clean(root.innerText || document.body.innerText || ''),
    metrics_text: metricLabels.join(' | '),
    links: Array.from(new Set(links)),
    media,
    source: 'dom'
  };
}
"""

_INSTAGRAM_DETAIL_EXTRACTOR = r"""
() => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const absolute = (href) => {
    try { return new URL(href, location.href).href; } catch { return href || null; }
  };
  const root = document.querySelector('article, main') || document.body;
  const links = Array.from(root.querySelectorAll('a[href]')).map((a) => absolute(a.getAttribute('href'))).filter(Boolean);
  const authorLink = links.find((href) => /^https?:\/\/www\.instagram\.com\/[^/?#]+\/?$/.test(href) && !/\/(explore|reels|direct)\b/.test(href)) || null;
  const text = clean(root.querySelector('h1, span[dir="auto"], ul')?.innerText || root.innerText || '');
  const metricLabels = Array.from(root.querySelectorAll('[aria-label], button, span'))
    .map((node) => clean(node.getAttribute('aria-label') || node.innerText || node.textContent))
    .filter(Boolean);
  const media = Array.from(root.querySelectorAll('img, video')).map((node) => ({
    tag: node.tagName.toLowerCase(),
    src: node.currentSrc || node.src || node.poster || null,
    alt: clean(node.getAttribute('alt') || '')
  })).filter((item) => item.src || item.alt);
  return {
    platform: 'instagram',
    url: location.href,
    title: document.title,
    author: authorLink ? authorLink.split('instagram.com/')[1]?.split(/[/?#]/)[0] : null,
    author_url: authorLink,
    text,
    full_text: clean(root.innerText || document.body.innerText || ''),
    metrics_text: metricLabels.join(' | '),
    links: Array.from(new Set(links)),
    media,
    source: 'dom'
  };
}
"""

_DETAIL_EXTRACTORS = {
    "x": _X_DETAIL_EXTRACTOR,
    "youtube": _YOUTUBE_DETAIL_EXTRACTOR,
    "youtube_studio": _YOUTUBE_DETAIL_EXTRACTOR,
    "tiktok": _TIKTOK_DETAIL_EXTRACTOR,
    "instagram": _INSTAGRAM_DETAIL_EXTRACTOR,
}

_DEFAULT_EXTRACT_WAIT_MS = 10000
_DEFAULT_EXTRACT_POLL_MS = 250
_DEFAULT_SCROLL_PAUSE_MS = 500
_MAX_DETAIL_TEXT_CHARS = 8000
_X_DETAIL_NOISE_MARKERS = (
    "window.__INITIAL_STATE__",
    "webpackChunk_twitter_responsive_web",
    "window.__SCRIPTS_LOADED__",
    "document.createElement",
    "JavaScript n&#x27;est pas disponible",
    "JavaScript n'est pas disponible",
    "Loading chunk ",
    "<style>",
)
_X_DETAIL_GENERIC_CHROME_MARKERS = (
    "Conditions d’utilisation",
    "Politique de Confidentialité",
    "Politique relative aux cookies",
    "Informations sur les publicités",
    "Pied de page",
    "Fil d'actualités",
    "Voir de nouveaux posts",
)
_DEFAULT_SOCIAL_CDP_PROFILE = "agent-social-x-cdp"
_DEFAULT_SOCIAL_CDP_INSTANCE = "agent-social-x-cdp"
_INSTAGRAM_PROFILE_EXCLUSIONS = {
    "about",
    "accounts",
    "api",
    "blog",
    "developer",
    "direct",
    "explore",
    "help",
    "legal",
    "meta",
    "oauth",
    "p",
    "popular",
    "privacy",
    "reel",
    "reels",
    "stories",
    "terms",
    "tv",
    "web",
}

_INSTAGRAM_GENERIC_PROFILE_LABELS = {
    "accueil",
    "create",
    "creer",
    "explore",
    "explorer",
    "home",
    "messages",
    "notifications",
    "popular",
    "populaire",
    "profile",
    "profil",
    "rechercher",
    "reels",
    "search",
}

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


def _run_browser_call(fn, /, *args, **kwargs):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return fn(*args, **kwargs)
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: fn(*args, **kwargs)).result()


def _platform(platform: str) -> str:
    key = str(platform or "").strip().lower().replace(" ", "_")
    key = _PLATFORM_ALIASES.get(key, key)
    if key not in _SUPPORTED_PLATFORMS:
        raise ValueError(f"Unsupported platform: {platform!r}. Use one of: {', '.join(sorted(_SUPPORTED_PLATFORMS))}.")
    return key


def _is_cdp_engine(browser_engine: str) -> bool:
    return str(browser_engine or "").strip().lower() == "cdp"


def _resolve_social_browser_identity(
    profile_name: str,
    instance_name: str,
    browser_engine: str,
) -> tuple[str, str]:
    if not _is_cdp_engine(browser_engine):
        return profile_name, instance_name
    return (
        str(profile_name or "").strip() or _DEFAULT_SOCIAL_CDP_PROFILE,
        str(instance_name or "").strip() or _DEFAULT_SOCIAL_CDP_INSTANCE,
    )


def _query(value: str) -> str:
    return quote_plus(str(value or "").strip())


def _instagram_url_type(url: str) -> str:
    try:
        parsed = urlparse(str(url or ""))
    except Exception:
        return ""
    host = parsed.netloc.lower()
    if not (host == "instagram.com" or host.endswith(".instagram.com")):
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return ""
    if parts[0] == "p":
        return "post"
    if parts[0] == "reel":
        return "reel"
    if parts[0] == "tv":
        return "tv"
    if (
        len(parts) == 1
        and re.fullmatch(r"[A-Za-z0-9._]+", parts[0])
        and parts[0].lower() not in _INSTAGRAM_PROFILE_EXCLUSIONS
    ):
        return "profile"
    return ""


def _instagram_profile_name(url: str) -> str:
    try:
        parsed = urlparse(str(url or ""))
    except Exception:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    return parts[0] if len(parts) == 1 else ""


def _instagram_profile_item_is_generic_navigation(item: dict[str, Any]) -> bool:
    text = _ascii_lower(str(item.get("text") or item.get("title") or ""))
    image_alt = _ascii_lower(str(item.get("image_alt") or ""))
    if text in _INSTAGRAM_GENERIC_PROFILE_LABELS:
        return True
    return text in {"profil", "profile"} and "photo de profil" in image_alt


def _youtube_title_is_duration_chrome(title: str) -> bool:
    text = _ascii_lower(title)
    return bool(re.fullmatch(r"[\d:\s]+(?:en cours de lecture|now playing)?", text) or "en cours de lecture" in text)


def _ascii_lower(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text).strip().lower()


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


def _cdp_endpoint(started_or_session: dict[str, Any]) -> str:
    manifest = started_or_session.get("manifest") if isinstance(started_or_session.get("manifest"), dict) else {}
    return str(started_or_session.get("cdp_endpoint") or manifest.get("cdp_endpoint") or "").strip().rstrip("/")


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
    direct_cdp_error: Exception | None = None
    endpoint = _cdp_endpoint(snapshot)
    if endpoint:
        try:
            return _extract_from_cdp_endpoint(
                target=target,
                session_id=session_id,
                page_id=page_id,
                endpoint=endpoint,
                target_url=str(snapshot.get("url") or ""),
                safe_limit=safe_limit,
                wait_ms=wait_ms,
                poll_ms=poll_ms,
                scroll_steps=scroll_steps,
                scroll_pause_ms=scroll_pause_ms,
                rank=rank,
            )
        except Exception as exc:
            direct_cdp_error = exc
    try:
        session, resolved_page_id, page = _run_browser_call(get_playwright_page, session_id, page_id=page_id)
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
        if not _is_stale_cdp_thread_error(exc) or not endpoint:
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
            direct_cdp_error=direct_cdp_error,
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
    return _evaluate_items_with_wait_from_evaluator(
        evaluate_items=lambda script, limit: _run_browser_call(page.evaluate, script, limit),
        scroll=lambda: _scroll_page(page),
        target=target,
        safe_limit=safe_limit,
        wait_ms=wait_ms,
        poll_ms=poll_ms,
        scroll_steps=scroll_steps,
        scroll_pause_ms=scroll_pause_ms,
    )


def _evaluate_items_with_wait_from_evaluator(
    evaluate_items: Any,
    scroll: Any,
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
        raw_items = evaluate_items(script, safe_limit)
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
        scroll_result = scroll()
        scrolls_performed += 1
        pause_seconds = max(int(scroll_pause_ms), 0) / 1000
        if pause_seconds:
            time.sleep(pause_seconds)
        attempts += 1
        raw_items = evaluate_items(script, safe_limit)
        before_count = len(items)
        items = _merge_items(items, _normalize_items(raw_items, target, safe_limit), safe_limit)
        if len(items) == before_count and _scroll_is_exhausted(scroll_result):
            break
    waited_ms = int((time.monotonic() - started_at) * 1000)
    return items[:safe_limit], attempts, waited_ms, scrolls_performed


def _scroll_page(page: Any) -> dict[str, Any]:
    try:
        result = _run_browser_call(page.evaluate, _SCROLL_SCRIPT)
    except TypeError:
        result = _run_browser_call(page.evaluate, _SCROLL_SCRIPT, None)
    return result if isinstance(result, dict) else {}


def _scroll_cdp(cdp: Any) -> dict[str, Any]:
    result = cdp.evaluate(_js_call(_SCROLL_SCRIPT))
    return result if isinstance(result, dict) else {}


def _extract_from_cdp_endpoint(
    target: str,
    session_id: str,
    page_id: str | None,
    endpoint: str,
    target_url: str,
    safe_limit: int,
    wait_ms: int,
    poll_ms: int,
    scroll_steps: int,
    scroll_pause_ms: int,
    rank: bool,
) -> dict[str, Any]:
    selected = _select_cdp_page_target(endpoint, preferred_url=target_url, page_id=page_id)
    target_id = str(selected.get("id") or page_id or "")
    ws_url = str(selected.get("webSocketDebuggerUrl") or "")
    if not ws_url:
        raise RuntimeError(f"Selected CDP target at {endpoint!r} has no webSocketDebuggerUrl.")
    with _open_cdp_session(ws_url) as cdp:
        page_info = _cdp_page_info(cdp)
        items, attempts, waited_ms, scrolls_performed = _evaluate_items_with_wait_from_evaluator(
            evaluate_items=lambda script, limit: cdp.evaluate(_js_call(script, limit)),
            scroll=lambda: _scroll_cdp(cdp),
            target=target,
            safe_limit=safe_limit,
            wait_ms=wait_ms,
            poll_ms=poll_ms,
            scroll_steps=scroll_steps,
            scroll_pause_ms=scroll_pause_ms,
        )
    if rank:
        items = _rank_items(items)
    record_event("social_media_extract", platform=target, session_id=session_id, page_id=target_id, item_count=len(items), automation="cdp")
    return {
        "ok": True,
        "platform": target,
        "session_id": session_id,
        "page_id": target_id,
        "url": page_info.get("href") or selected.get("url") or target_url,
        "title": page_info.get("title") or selected.get("title"),
        "read_only": True,
        "browser_context": "agent_dedicated",
        "automation": "cdp",
        "browser_engine": "cdp",
        "host_interactive": False,
        "extraction_method": "dom",
        "source": "dom",
        "cdp_direct": True,
        "cdp_endpoint": endpoint,
        "cdp_target_id": target_id,
        "items": items,
        "item_count": len(items),
        "extract_attempts": attempts,
        "extract_waited_ms": waited_ms,
        "scroll_steps": scrolls_performed,
        "ranked": bool(rank),
    }


def _extract_detail_from_cdp_endpoint(
    target: str,
    session_id: str,
    page_id: str | None,
    endpoint: str,
    target_url: str,
    wait_ms: int,
    poll_ms: int = _DEFAULT_EXTRACT_POLL_MS,
    new_tab_if_needed: bool = False,
    force_new_tab: bool = False,
    close_after_extract: bool = False,
) -> dict[str, Any]:
    navigation = cdp_navigate(
        endpoint=endpoint,
        url=target_url,
        preferred_url=target_url,
        page_id=page_id,
        wait_ms=wait_ms,
        new_tab_if_needed=new_tab_if_needed,
        force_new_tab=force_new_tab,
    )
    selected = _select_cdp_page_target(endpoint, preferred_url=navigation.get("url") or target_url, page_id=navigation.get("cdp_target_id"))
    target_id = str(selected.get("id") or navigation.get("cdp_target_id") or page_id or "")
    ws_url = str(selected.get("webSocketDebuggerUrl") or "")
    if not ws_url:
        raise RuntimeError(f"Selected CDP target at {endpoint!r} has no webSocketDebuggerUrl.")
    script = _DETAIL_EXTRACTORS[target]
    deadline = time.monotonic() + max(int(wait_ms), 0) / 1000
    poll_seconds = max(int(poll_ms), 0) / 1000
    attempts = 0
    raw_detail: dict[str, Any] = {}
    page_info: dict[str, Any] = {}
    close_result: dict[str, Any] | None = None
    close_error: str | None = None
    started_at = time.monotonic()
    try:
        with _open_cdp_session(ws_url) as cdp:
            while True:
                attempts += 1
                page_info = _cdp_page_info(cdp)
                raw = cdp.evaluate(_js_call(script))
                raw_detail = raw if isinstance(raw, dict) else {}
                if _detail_is_meaningful(raw_detail, target) or time.monotonic() >= deadline:
                    break
                sleep_seconds = min(poll_seconds, max(deadline - time.monotonic(), 0))
                if sleep_seconds <= 0:
                    break
                time.sleep(sleep_seconds)
    finally:
        if close_after_extract and navigation.get("created_target") and target_id:
            try:
                close_result = cdp_close_page_target(endpoint, target_id)
            except Exception as exc:
                close_error = str(exc)
    detail = _normalize_detail(raw_detail, target, page_info.get("href") or navigation.get("url") or target_url)
    record_event("social_media_detail", platform=target, session_id=session_id, page_id=target_id, url=detail.get("url"))
    return {
        "ok": True,
        "platform": target,
        "session_id": session_id,
        "page_id": target_id,
        "url": detail.get("url") or page_info.get("href") or navigation.get("url") or target_url,
        "title": detail.get("title") or page_info.get("title") or navigation.get("title"),
        "author": detail.get("author"),
        "author_url": detail.get("author_url"),
        "text": detail.get("text") or "",
        "full_text": detail.get("full_text") or detail.get("text") or "",
        "metrics": detail.get("metrics") or {},
        "metrics_text": detail.get("metrics_text") or "",
        "links": detail.get("links") or [],
        "media": detail.get("media") or [],
        "quality": detail.get("quality") or "clean",
        "quality_notes": detail.get("quality_notes") or [],
        "read_only": True,
        "browser_context": "agent_dedicated",
        "automation": "cdp",
        "browser_engine": "cdp",
        "host_interactive": False,
        "extraction_method": "dom",
        "source": "dom",
        "cdp_direct": True,
        "cdp_endpoint": endpoint,
        "cdp_target_id": target_id,
        "created_target": bool(navigation.get("created_target")),
        "temporary_detail_tab": bool(force_new_tab and close_after_extract),
        "detail_tab_closed": bool(close_result and close_result.get("ok")),
        "detail_tab_close_error": close_error,
        "extract_attempts": attempts,
        "extract_waited_ms": int((time.monotonic() - started_at) * 1000),
    }


def _detail_is_meaningful(raw_detail: dict[str, Any], platform: str) -> bool:
    if not isinstance(raw_detail, dict):
        return False
    text = str(raw_detail.get("full_text") or raw_detail.get("text") or "").strip()
    metrics_text = str(raw_detail.get("metrics_text") or "").strip().lower()
    links = raw_detail.get("links")
    has_links = isinstance(links, list) and len(links) > 0
    if platform == "x":
        loading_markers = {
            "chargement",
            "loading",
            "pour voir les raccourcis clavier, appuyez sur le point d'interrogation. voir les raccourcis clavier",
        }
        if metrics_text in loading_markers or text.lower() in loading_markers:
            return False
        if _x_detail_is_noisy(raw_detail):
            return bool(_x_text_from_title(str(raw_detail.get("title") or "")))
        return bool(raw_detail.get("author_url") or has_links or _extract_metrics({"metrics_text": metrics_text})["views"] > 0)
    if platform == "youtube":
        return bool(raw_detail.get("title") or raw_detail.get("author") or text)
    if platform in {"tiktok", "instagram"}:
        media = raw_detail.get("media")
        return bool(raw_detail.get("author_url") or text or (isinstance(media, list) and media))
    return bool(text or has_links)


def _normalize_detail(raw_detail: dict[str, Any], platform: str, fallback_url: str) -> dict[str, Any]:
    detail = {str(key): value for key, value in raw_detail.items()} if isinstance(raw_detail, dict) else {}
    detail["platform"] = platform
    detail["url"] = str(detail.get("url") or fallback_url or "")
    for key in ("title", "author", "author_url", "text", "full_text", "metrics_text"):
        if detail.get(key) is not None:
            detail[key] = str(detail.get(key) or "").strip()
    if not detail.get("full_text") and detail.get("text"):
        detail["full_text"] = detail["text"]
    if not detail.get("text") and detail.get("full_text"):
        detail["text"] = str(detail["full_text"])[:4000]
    if platform == "x":
        _normalize_x_detail_quality(detail)
    else:
        detail.setdefault("quality", "clean")
        detail.setdefault("quality_notes", [])
    for key in ("text", "full_text"):
        if detail.get(key):
            detail[key] = str(detail[key])[:_MAX_DETAIL_TEXT_CHARS]
    links = detail.get("links")
    detail["links"] = _unique_strings(links if isinstance(links, list) else [])
    media = detail.get("media")
    detail["media"] = [item for item in media if isinstance(item, dict)] if isinstance(media, list) else []
    detail["metrics"] = _extract_metrics({
        "platform": platform,
        "text": detail.get("full_text") or detail.get("text") or "",
        "metrics_text": detail.get("metrics_text") or "",
    })
    return detail


def _normalize_x_detail_quality(detail: dict[str, Any]) -> None:
    """Prevent X bootstrap or generic shell text from becoming model-visible detail."""
    notes: list[str] = []
    title_text = _x_text_from_title(str(detail.get("title") or ""))
    text = str(detail.get("text") or "")
    full_text = str(detail.get("full_text") or "")
    if _x_text_is_noisy(text) or _x_text_is_noisy(full_text):
        notes.append("filtered_script_noise")
        fallback = title_text
        detail["text"] = fallback
        detail["full_text"] = fallback
    elif _x_text_is_generic_chrome(text) or _x_text_is_generic_chrome(full_text):
        notes.append("filtered_generic_page_chrome")
        fallback = title_text
        detail["text"] = fallback
        detail["full_text"] = fallback
    elif title_text and (not text or text.lower() in {"loading", "chargement"}):
        notes.append("used_title_fallback")
        detail["text"] = title_text
        detail["full_text"] = title_text
    if notes:
        detail["quality"] = "partial" if detail.get("text") else "noisy"
    else:
        detail["quality"] = "clean"
    detail["quality_notes"] = notes


def _x_detail_is_noisy(raw_detail: dict[str, Any]) -> bool:
    text = " ".join(
        str(raw_detail.get(key) or "")
        for key in ("title", "text", "full_text", "metrics_text")
    )
    return _x_text_is_noisy(text) or _x_text_is_generic_chrome(text)


def _x_text_is_noisy(text: str) -> bool:
    value = str(text or "")
    if not value:
        return False
    marker_hits = sum(1 for marker in _X_DETAIL_NOISE_MARKERS if marker in value)
    if marker_hits:
        return True
    if len(value) > 20000 and ("function(" in value or "=>{" in value or "var " in value):
        return True
    return False


def _x_text_is_generic_chrome(text: str) -> bool:
    value = str(text or "")
    if not value:
        return False
    lower_value = value.lower()
    marker_hits = sum(1 for marker in _X_DETAIL_GENERIC_CHROME_MARKERS if marker.lower() in lower_value)
    if marker_hits >= 2:
        return True
    if "post voir de nouveaux posts" in lower_value and "conditions d" in lower_value:
        return True
    if "fil d'actualités" in lower_value and "pied de page" in lower_value:
        return True
    return False


def _x_text_from_title(title: str) -> str:
    text = str(title or "").strip()
    if not text:
        return ""
    patterns = [
        r':\s*["“](.+?)["”]\s*/\s*X\s*$',
        r'on\s+X:\s*["“](.+?)["”]\s*/\s*X\s*$',
        r'sur\s+X\s*:\s*["“](.+?)["”]\s*/\s*X\s*$',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    return ""


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


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
    direct_cdp_error: Exception | None = None,
) -> dict[str, Any]:
    try:
        result = _extract_from_cdp_endpoint(
            target=target,
            session_id=original_session_id,
            page_id=page_id,
            endpoint=str(snapshot["cdp_endpoint"]),
            target_url=str(snapshot.get("url") or ""),
            safe_limit=safe_limit,
            wait_ms=wait_ms,
            poll_ms=poll_ms,
            scroll_steps=scroll_steps,
            scroll_pause_ms=scroll_pause_ms,
            rank=rank,
        )
    except Exception:
        if direct_cdp_error is not None:
            raise direct_cdp_error
        raise
    result["cdp_reattached"] = False
    result["original_session_id"] = original_session_id
    result["cdp_recovery"] = "direct_endpoint"
    return result


def social_detail(
    platform: str,
    url: str,
    session_id: str = "",
    page_id: str | None = None,
    profile_name: str = "",
    instance_name: str = "",
    browser: str = "chrome",
    browser_engine: str = "cdp",
    debug_port: int = 9333,
    keep_open: bool = True,
    wait_ms: int = _DEFAULT_EXTRACT_WAIT_MS,
    temporary_detail_tab: bool = False,
) -> dict[str, Any]:
    """Open a read-only social item and extract its full visible DOM detail."""
    target = _platform(platform)
    target_url = str(url or "").strip()
    if not target_url:
        raise ValueError("url is required.")
    started: dict[str, Any] | None = None
    browser_stop: dict[str, Any] | None = None
    resolved_session_id = str(session_id or "")
    resolved_page_id = page_id
    resolved_profile_name, resolved_instance_name = _resolve_social_browser_identity(profile_name, instance_name, browser_engine)
    endpoint = ""
    if resolved_session_id:
        snapshot = _playwright_session_snapshot(resolved_session_id)
        endpoint = _cdp_endpoint(snapshot)
    if not endpoint:
        started = agent_browser_start(
            platform=target,
            url=target_url,
            profile_name=resolved_profile_name,
            instance_name=resolved_instance_name,
            browser=browser,
            browser_engine=browser_engine,
            debug_port=debug_port,
            new_tab_if_needed=_is_cdp_engine(browser_engine),
            headless=False,
            wait_until="domcontentloaded",
        )
        if not started.get("ok", True):
            return {
                **started,
                "ok": False,
                "platform": target,
                "url": target_url,
                "read_only": True,
                "browser_context": "agent_dedicated",
                "host_interactive": False,
            }
        resolved_session_id = str(started["session_id"])
        resolved_page_id = started.get("page_id")
        endpoint = _cdp_endpoint(started)
    try:
        if endpoint:
            extracted = _extract_detail_from_cdp_endpoint(
                target=target,
                session_id=resolved_session_id,
                page_id=resolved_page_id,
                endpoint=endpoint,
                target_url=target_url,
                wait_ms=wait_ms,
                new_tab_if_needed=_is_cdp_engine(browser_engine),
                force_new_tab=bool(temporary_detail_tab and _is_cdp_engine(browser_engine)),
                close_after_extract=bool(temporary_detail_tab and _is_cdp_engine(browser_engine)),
            )
        else:
            extracted = _extract_detail_from_playwright(
                target=target,
                session_id=resolved_session_id,
                page_id=resolved_page_id,
                target_url=target_url,
                wait_ms=wait_ms,
            )
    finally:
        if not keep_open and (started or resolved_instance_name):
            browser_stop = agent_browser_stop(instance_name=(started or {}).get("instance_name") or resolved_instance_name, platform=target)
    return {
        **extracted,
        "ok": bool(extracted.get("ok", True)),
        "platform": target,
        "url": extracted.get("url") or target_url,
        "browser": started,
        "browser_context": "agent_dedicated",
        "host_interactive": False,
        "read_only": True,
        "keep_open": bool(keep_open),
        "browser_stop": browser_stop,
    }


def _extract_detail_from_playwright(
    target: str,
    session_id: str,
    page_id: str | None,
    target_url: str,
    wait_ms: int,
) -> dict[str, Any]:
    _session, resolved_page_id, page = _run_browser_call(get_playwright_page, session_id, page_id=page_id)
    if getattr(page, "url", "") != target_url:
        _run_browser_call(page.goto, target_url, wait_until="domcontentloaded", timeout=max(int(wait_ms), 1000))
    raw = _run_browser_call(page.evaluate, _DETAIL_EXTRACTORS[target])
    detail = _normalize_detail(raw if isinstance(raw, dict) else {}, target, getattr(page, "url", target_url))
    return {
        "ok": True,
        "platform": target,
        "session_id": session_id,
        "page_id": resolved_page_id,
        "url": detail.get("url") or getattr(page, "url", target_url),
        "title": detail.get("title"),
        "author": detail.get("author"),
        "author_url": detail.get("author_url"),
        "text": detail.get("text") or "",
        "full_text": detail.get("full_text") or detail.get("text") or "",
        "metrics": detail.get("metrics") or {},
        "metrics_text": detail.get("metrics_text") or "",
        "links": detail.get("links") or [],
        "media": detail.get("media") or [],
        "quality": detail.get("quality") or "clean",
        "quality_notes": detail.get("quality_notes") or [],
        "read_only": True,
        "browser_context": "agent_dedicated",
        "automation": "playwright",
        "host_interactive": False,
        "extraction_method": "dom",
        "source": "dom",
    }


def social_search(
    platform: str,
    query: str,
    limit: int = 10,
    profile_name: str = "",
    instance_name: str = "",
    browser: str = "chrome",
    browser_engine: str = "cdp",
    debug_port: int = 9333,
    headless: bool = True,
    width: int | str = "auto",
    height: int | str = "auto",
    wait_until: str = "domcontentloaded",
    keep_open: bool = True,
    scroll_steps: int | None = None,
    scroll_pause_ms: int = _DEFAULT_SCROLL_PAUSE_MS,
    rank: bool = True,
    include_details: bool = False,
    detail_limit: int = 3,
) -> dict[str, Any]:
    """Open a read-only social search in the agent browser and extract DOM results."""
    target = _platform(platform)
    safe_limit = max(1, min(int(limit), 100))
    resolved_scroll_steps = _resolve_scroll_steps(safe_limit, scroll_steps)
    resolved_profile_name, resolved_instance_name = _resolve_social_browser_identity(profile_name, instance_name, browser_engine)
    url_info = social_platform_url(platform=target, query=query)
    started = agent_browser_start(
        platform=target,
        url=url_info["url"],
        profile_name=resolved_profile_name,
        instance_name=resolved_instance_name,
        browser=browser,
        browser_engine=browser_engine,
        debug_port=debug_port,
        new_tab_if_needed=_is_cdp_engine(browser_engine),
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
    extraction_errors: list[dict[str, str]] = []
    search_fallbacks: list[str] = []
    try:
        endpoint = _cdp_endpoint(started)
        if (started.get("automation") == "cdp" or str(browser_engine).strip().lower() == "cdp") and endpoint:
            extracted = _extract_search_from_cdp_with_retry(
                target=target,
                started=started,
                endpoint=endpoint,
                target_url=url_info["url"],
                safe_limit=safe_limit,
                resolved_scroll_steps=resolved_scroll_steps,
                scroll_pause_ms=scroll_pause_ms,
                rank=rank,
                extraction_errors=extraction_errors,
            )
            extracted = _filter_extracted_items(extracted, target, safe_limit, rank=rank)
            if target == "instagram" and not extracted.get("items"):
                for fallback_url in _instagram_fallback_search_urls(query):
                    search_fallbacks.append(fallback_url)
                    try:
                        navigation = cdp_navigate(
                            endpoint=endpoint,
                            url=fallback_url,
                            preferred_url=fallback_url,
                            page_id=started.get("page_id"),
                            wait_ms=10000,
                            new_tab_if_needed=True,
                        )
                        fallback = _extract_search_from_cdp_with_retry(
                            target=target,
                            started=started,
                            endpoint=endpoint,
                            target_url=fallback_url,
                            safe_limit=safe_limit,
                            resolved_scroll_steps=max(resolved_scroll_steps, 1),
                            scroll_pause_ms=scroll_pause_ms,
                            rank=rank,
                            extraction_errors=extraction_errors,
                            page_id=navigation.get("cdp_target_id") or started.get("page_id"),
                        )
                        fallback = _filter_extracted_items(fallback, target, safe_limit, rank=rank)
                    except Exception as exc:
                        extraction_errors.append({"phase": "instagram_fallback", "url": fallback_url, "error": str(exc), "type": type(exc).__name__})
                        continue
                    if fallback.get("items"):
                        extracted = fallback
                        extracted["search_fallback_url"] = fallback_url
                        break
        else:
            extracted = social_extract(
                platform=target,
                session_id=started["session_id"],
                page_id=started.get("page_id"),
                limit=safe_limit,
                scroll_steps=resolved_scroll_steps,
                scroll_pause_ms=scroll_pause_ms,
                rank=rank,
            )
            extracted = _filter_extracted_items(extracted, target, safe_limit, rank=rank)
        if include_details:
            extracted["items"] = _enrich_items_with_details(
                items=list(extracted.get("items") or []),
                target=target,
                session_id=started["session_id"],
                page_id=extracted.get("page_id") or started.get("page_id"),
                detail_limit=detail_limit,
                profile_name=resolved_profile_name,
                instance_name=started.get("instance_name") or resolved_instance_name,
                browser=browser,
                browser_engine=browser_engine,
                debug_port=debug_port,
            )
            extracted["item_count"] = len(extracted["items"])
            extracted["details_included"] = True
            extracted["detail_limit"] = max(int(detail_limit), 0)
        if extraction_errors:
            extracted["extract_errors"] = extraction_errors
        if search_fallbacks:
            extracted["search_fallbacks"] = search_fallbacks
    finally:
        if not keep_open:
            browser_stop = agent_browser_stop(instance_name=started.get("instance_name") or resolved_instance_name, platform=target)
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


def _extract_search_from_cdp_with_retry(
    target: str,
    started: dict[str, Any],
    endpoint: str,
    target_url: str,
    safe_limit: int,
    resolved_scroll_steps: int,
    scroll_pause_ms: int,
    rank: bool,
    extraction_errors: list[dict[str, str]],
    page_id: str | None = None,
) -> dict[str, Any]:
    resolved_page_id = page_id or started.get("page_id")
    for attempt in range(2):
        try:
            return _extract_from_cdp_endpoint(
                target=target,
                session_id=started["session_id"],
                page_id=resolved_page_id,
                endpoint=endpoint,
                target_url=target_url,
                safe_limit=safe_limit,
                wait_ms=_DEFAULT_EXTRACT_WAIT_MS,
                poll_ms=_DEFAULT_EXTRACT_POLL_MS,
                scroll_steps=resolved_scroll_steps,
                scroll_pause_ms=scroll_pause_ms,
                rank=rank,
            )
        except Exception as exc:
            if not _is_transient_cdp_error(exc):
                raise
            extraction_errors.append({"phase": "search_extract", "attempt": str(attempt + 1), "error": str(exc), "type": type(exc).__name__})
            if attempt == 0:
                time.sleep(0.25)
                continue
            return _empty_extract_result(
                target=target,
                started=started,
                target_url=target_url,
                endpoint=endpoint,
                page_id=resolved_page_id,
                exc=exc,
            )
    raise RuntimeError("unreachable")


def _empty_extract_result(
    target: str,
    started: dict[str, Any],
    target_url: str,
    endpoint: str,
    page_id: str | None,
    exc: Exception,
) -> dict[str, Any]:
    return {
        "ok": True,
        "platform": target,
        "session_id": started.get("session_id"),
        "page_id": page_id,
        "url": target_url,
        "title": started.get("title"),
        "read_only": True,
        "browser_context": "agent_dedicated",
        "automation": "cdp",
        "browser_engine": "cdp",
        "host_interactive": False,
        "extraction_method": "dom",
        "source": "dom",
        "cdp_direct": True,
        "cdp_endpoint": endpoint,
        "cdp_target_id": page_id,
        "items": [],
        "item_count": 0,
        "extract_attempts": 2,
        "extract_waited_ms": 0,
        "scroll_steps": 0,
        "ranked": False,
        "extract_error": str(exc),
        "extract_error_type": type(exc).__name__,
    }


def _filter_extracted_items(extracted: dict[str, Any], target: str, limit: int, rank: bool) -> dict[str, Any]:
    items = extracted.get("items") if isinstance(extracted.get("items"), list) else []
    filtered = _filter_items_for_platform(items, target, limit)
    if rank:
        filtered = _rank_items(filtered)
    result = dict(extracted)
    result["items"] = filtered[:limit]
    result["item_count"] = len(result["items"])
    result["quality_filtered"] = len(items) - len(result["items"])
    return result


def _filter_items_for_platform(items: list[dict[str, Any]], target: str, limit: int) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if target == "instagram":
            item_type = str(item.get("item_type") or _instagram_url_type(str(item.get("url") or ""))).strip()
            if str(item.get("url") or "").strip() and not item_type:
                continue
            candidate = dict(item)
            if item_type:
                candidate["item_type"] = item_type
            if item_type == "profile" and _instagram_profile_item_is_generic_navigation(candidate):
                continue
            item = candidate
        filtered.append(item)
        if len(filtered) >= limit:
            break
    return filtered


def _instagram_fallback_search_urls(query: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_]+", str(query or "").lower())
    candidates: list[str] = []
    if tokens:
        compact = "".join(tokens[:3])
        first = tokens[0]
        for tag in (compact, first):
            if tag and tag not in candidates:
                candidates.append(tag)
    return [f"https://www.instagram.com/explore/tags/{tag}/" for tag in candidates]


def _is_transient_cdp_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return (
        "websockettimeoutexception" in text
        or "connection timed out" in text
        or ("timed out" in text and ("websocket" in text or "cdp" in text or "connection" in text))
    )


def _enrich_items_with_details(
    items: list[dict[str, Any]],
    target: str,
    session_id: str,
    page_id: str | None,
    detail_limit: int,
    profile_name: str,
    instance_name: str,
    browser: str,
    browser_engine: str,
    debug_port: int,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    remaining = max(int(detail_limit), 0)
    for item in items:
        current = dict(item)
        url = str(current.get("url") or "").strip()
        if remaining > 0 and url:
            try:
                detail = social_detail(
                    platform=target,
                    url=url,
                    session_id=session_id,
                    page_id=page_id,
                    profile_name=profile_name,
                    instance_name=instance_name,
                    browser=browser,
                    browser_engine=browser_engine,
                    debug_port=debug_port,
                    keep_open=True,
                    temporary_detail_tab=_is_cdp_engine(browser_engine),
                )
                current["detail"] = {key: value for key, value in detail.items() if key not in {"browser", "browser_stop"}}
                if not current.get("text") and detail.get("text"):
                    current["text"] = detail["text"]
                remaining -= 1
            except Exception as exc:
                current["detail_error"] = str(exc)
                current["detail"] = _fallback_detail_from_item(current, target, exc)
                remaining -= 1
        enriched.append(current)
    return enriched


def _fallback_detail_from_item(item: dict[str, Any], target: str, exc: Exception) -> dict[str, Any]:
    """Return a model-facing partial detail when the detail page cannot be opened."""
    raw = {
        "platform": target,
        "url": item.get("url") or "",
        "title": item.get("title") or item.get("text") or "",
        "author": item.get("channel") or item.get("author") or "",
        "author_url": item.get("author_url") or "",
        "text": item.get("text") or item.get("title") or item.get("metadata") or "",
        "full_text": " ".join(str(item.get(key) or "").strip() for key in ("title", "text", "metadata", "metrics_text") if item.get(key)),
        "metrics_text": item.get("metrics_text") or item.get("metadata") or "",
        "links": [item.get("url")] if item.get("url") else [],
        "media": [],
        "quality": "partial",
        "quality_notes": ["detail_error", "used_search_item_fallback"],
    }
    detail = _normalize_detail(raw, target, str(item.get("url") or ""))
    detail["ok"] = True
    detail["read_only"] = True
    detail["browser_context"] = "agent_dedicated"
    detail["automation"] = "cdp"
    detail["browser_engine"] = "cdp"
    detail["host_interactive"] = False
    detail["extraction_method"] = "dom"
    detail["source"] = "search_item_fallback"
    detail["cdp_direct"] = True
    detail["detail_error"] = str(exc)
    return detail


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
        if platform == "youtube" and _youtube_title_is_duration_chrome(str(normalized.get("title") or "")):
            metadata = str(normalized.get("metadata") or "").strip()
            if metadata:
                original_title = str(normalized.get("title") or "")
                normalized["title"] = metadata
                if not normalized.get("text") or normalized.get("text") == original_title:
                    normalized["text"] = metadata
        if platform == "instagram":
            item_type = str(normalized.get("item_type") or _instagram_url_type(str(normalized.get("url") or ""))).strip()
            if normalized.get("url") and not item_type:
                continue
            if item_type:
                normalized["item_type"] = item_type
            if not normalized.get("text") and normalized.get("image_alt"):
                normalized["text"] = str(normalized["image_alt"]).strip()
            if item_type == "profile" and not normalized.get("text"):
                normalized["text"] = _instagram_profile_name(str(normalized.get("url") or ""))
            if item_type == "profile" and _instagram_profile_item_is_generic_navigation(normalized):
                continue
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
    "social_detail",
    "social_extract",
    "social_platform_url",
    "social_search",
    "social_supported_platforms",
]
