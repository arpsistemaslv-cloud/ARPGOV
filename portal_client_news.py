"""
Agrega notícias por RSS para a página inicial da área do cliente.
URLs configuráveis em CLIENT_NEWS_RSS_URLS (separadas por ; ou quebra de linha).
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import feedparser
import requests

# Fontes padrão: mundo + Brasil (CNN costuma falhar SSL em alguns ambientes; Guardian costuma ser estável)
DEFAULT_FEED_URLS: tuple[str, ...] = (
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.theguardian.com/world/rss",
    "https://g1.globo.com/dynamo/mundo/rss2.xml",
)

_CACHE: dict[str, Any] = {"t": 0.0, "items": [], "errors": []}
CACHE_TTL_SEC = 15 * 60
REQUEST_TIMEOUT = 12
MAX_PER_FEED = 12
MAX_TOTAL = 36

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": (
            "ARPGOV/1.0 (+https://www.example.com; área do cliente; RSS agregador)"
        ),
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }
)


def _feed_urls_from_env() -> list[str]:
    raw = (os.environ.get("CLIENT_NEWS_RSS_URLS") or "").strip()
    if not raw:
        return list(DEFAULT_FEED_URLS)
    parts = []
    for line in raw.replace(";", "\n").splitlines():
        u = line.strip()
        if u and not u.startswith("#"):
            parts.append(u)
    return parts or list(DEFAULT_FEED_URLS)


def _source_label(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return "Notícias"
    if "bbc" in host:
        return "BBC"
    if "cnn" in host:
        return "CNN"
    if "theguardian" in host:
        return "The Guardian"
    if "g1." in host or "globo" in host:
        return "G1"
    if "uol" in host:
        return "UOL"
    return host.replace("www.", "")[:24] or "Notícias"


def _entry_datetime(entry: Any) -> datetime | None:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if not t:
        return None
    try:
        return datetime(
            t.tm_year,
            t.tm_mon,
            t.tm_mday,
            t.tm_hour,
            t.tm_min,
            t.tm_sec,
            tzinfo=timezone.utc,
        )
    except (TypeError, ValueError):
        return None


def _entry_link(entry: Any) -> str:
    link = entry.get("link")
    if link:
        return str(link).strip()
    links = entry.get("links") or []
    if links and isinstance(links, list):
        h = links[0].get("href") if isinstance(links[0], dict) else None
        if h:
            return str(h).strip()
    return ""


def _fetch_one_feed(url: str) -> tuple[list[dict], str | None]:
    err: str | None = None
    out: list[dict] = []
    try:
        r = _SESSION.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        parsed = feedparser.parse(r.content)
    except requests.RequestException as e:
        return [], f"{_source_label(url)}: {e!s}"[:200]
    except Exception as e:
        return [], f"{_source_label(url)}: {e!s}"[:200]

    if getattr(parsed, "bozo", False) and not parsed.entries:
        bozo = getattr(parsed, "bozo_exception", None)
        return [], f"{_source_label(url)}: feed inválido ({bozo!s})"[:200]

    label = _source_label(url)
    for entry in parsed.entries[:MAX_PER_FEED]:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        link = _entry_link(entry)
        if not link:
            continue
        dt = _entry_datetime(entry)
        out.append(
            {
                "title": title[:300],
                "link": link,
                "source": label,
                "feed_url": url,
                "published": dt,
            }
        )
    return out, err


def get_client_news_items(*, force_refresh: bool = False) -> tuple[list[dict], list[str]]:
    """
    Retorna (itens ordenados por data desc, lista de avisos/erros de feeds).
    Cada item: title, link, source, published (datetime ou None).
    """
    now = time.monotonic()
    if (
        not force_refresh
        and _CACHE["items"]
        and (now - float(_CACHE["t"])) < CACHE_TTL_SEC
    ):
        return list(_CACHE["items"]), list(_CACHE["errors"])

    all_items: list[dict] = []
    errors: list[str] = []
    for url in _feed_urls_from_env():
        items, e = _fetch_one_feed(url)
        all_items.extend(items)
        if e:
            errors.append(e)

    def sort_key(it: dict) -> float:
        d = it.get("published")
        if d is None:
            return 0.0
        return d.timestamp()

    all_items.sort(key=sort_key, reverse=True)
    all_items = all_items[:MAX_TOTAL]

    _CACHE["t"] = now
    _CACHE["items"] = all_items
    _CACHE["errors"] = errors
    return all_items, errors
