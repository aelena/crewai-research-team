"""Source registry: every URL the search and scrape tools actually returned during a run.

A citation is only trustworthy if a tool saw it. The writers' guardrails check every URL they cite
against this registry, which is the cheapest reliable defence against invented references.
"""

from __future__ import annotations

import re
import threading
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

_URL = re.compile(r"https?://[^\s<>()\[\]{}\"'`|\\^]+")
_TRAILING = ".,;:!?*_)]}'\""
_TRACKING = ("utm_", "fbclid", "gclid", "mc_", "ref_src")


def extract_urls(text: str) -> list[str]:
    """URLs in order of appearance, deduplicated, trailing punctuation stripped."""
    seen: dict[str, None] = {}
    for m in _URL.findall(text or ""):
        seen.setdefault(m.rstrip(_TRAILING), None)
    return list(seen)


def normalize(url: str) -> str:
    """Comparable form: lowercase host without www, no fragment, no tracking params, no trailing slash."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(_TRACKING)])
    path = parts.path.rstrip("/")
    return urlunsplit(("https", host, path, query, ""))


def _no_query(url: str) -> str:
    return url.split("?", 1)[0]


class Source(BaseModel):
    url: str
    origins: list[str] = Field(default_factory=list)
    first_seen: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))


class SourceRegistry:
    """Thread-safe: the research tasks run in parallel and all write here."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sources: dict[str, Source] = {}

    def observe(self, text: str, origin: str) -> int:
        """Record every URL in ``text``; returns how many were new."""
        new = 0
        with self._lock:
            for url in extract_urls(text):
                key = normalize(url)
                src = self._sources.get(key)
                if src is None:
                    self._sources[key] = src = Source(url=url)
                    new += 1
                if origin not in src.origins:
                    src.origins.append(origin)
        return new

    def __len__(self) -> int:
        return len(self._sources)

    def __contains__(self, url: str) -> bool:
        key = normalize(url)
        with self._lock:
            return key in self._sources or _no_query(key) in {_no_query(k) for k in self._sources}

    def unknown(self, urls: list[str]) -> list[str]:
        return [u for u in urls if u not in self]

    def sources(self) -> list[Source]:
        with self._lock:
            return list(self._sources.values())
