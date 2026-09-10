"""Web research primitives: DuckDuckGo search (no API key) and a plain-text page fetcher."""

from __future__ import annotations

import html
import re

import httpx

_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)
_WS_RE = re.compile(r"[ \t]+")
_NL_RE = re.compile(r"\n\s*\n+")
USER_AGENT = "hackbot/0.2 (cybersecurity research demo)"


def web_search(query: str, max_results: int = 5) -> dict:
    from ddgs import DDGS  # lazy import keeps process start-up fast

    max_results = max(1, min(int(max_results or 5), 10))
    hits = DDGS().text(query, max_results=max_results) or []
    return {
        "query": query,
        "results": [{"title": h.get("title"), "url": h.get("href"), "snippet": h.get("body")} for h in hits],
    }


def fetch_page(url: str, max_chars: int = 4000) -> dict:
    if not url.lower().startswith(("http://", "https://")):
        return {"error": "only http(s) URLs are allowed", "url": url}
    resp = httpx.get(url, follow_redirects=True, timeout=20, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    text = html.unescape(_TAG_RE.sub(" ", resp.text))
    text = _NL_RE.sub("\n", _WS_RE.sub(" ", text)).strip()
    max_chars = max(500, min(int(max_chars or 4000), 12000))
    return {"url": str(resp.url), "status": resp.status_code, "truncated": len(text) > max_chars, "text": text[:max_chars]}
