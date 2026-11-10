"""Throttled, cached HTTP client. One instance per process.

- ≤ 1 request per `min_interval` seconds per host (default 2.5 s)
- browser-like User-Agent
- exponential backoff on 403/429/5xx, max 5 tries, then raises FetchError (caller logs + skips)
- every raw response body cached on disk under CACHE_DIR/<hh>/<sha256>, indexed in http_cache
- NSE: warms a session cookie from the homepage first; re-warms on 401/403 or after idle
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import sqlite3
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

import requests

from . import config, db

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
NSE_HOME = "https://www.nseindia.com/"
NSE_HOSTS = ("www.nseindia.com", "nseindia.com")
RETRY_STATUS = {403, 429, 500, 502, 503, 504}


class FetchError(Exception):
    pass


@dataclass
class Response:
    url: str
    status: int
    content: bytes
    content_hash: str
    from_cache: bool

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)


class Client:
    def __init__(self, conn: sqlite3.Connection, min_interval: float = 2.5, max_tries: int = 5,
                 nse_cookie_ttl: float = 600.0):
        self.conn = conn
        self.min_interval = min_interval
        self.max_tries = max_tries
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/html, */*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9",
            "Referer": NSE_HOME,
        })
        self._last: dict[str, float] = {}
        self._nse_cookie_at = 0.0
        self._nse_cookie_ttl = nse_cookie_ttl

    # -- throttle / cookies ---------------------------------------------------
    def _throttle(self, host: str) -> None:
        last = self._last.get(host, 0.0)
        wait = self.min_interval + random.uniform(0, 0.5) - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def warm_nse(self, force: bool = False) -> None:
        if not force and time.monotonic() - self._nse_cookie_at < self._nse_cookie_ttl:
            return
        self._throttle("www.nseindia.com")
        try:
            r = self.s.get(NSE_HOME, timeout=30)
            log.debug("NSE cookie warm: %s cookies=%d", r.status_code, len(self.s.cookies))
        except requests.RequestException as e:  # pragma: no cover
            log.warning("NSE cookie warm failed: %s", e)
        self._nse_cookie_at = time.monotonic()

    # -- cache ----------------------------------------------------------------
    @staticmethod
    def _key(url: str, params: dict | None) -> str:
        q = urlencode(sorted((params or {}).items()))
        return hashlib.sha256(f"GET {url}?{q}".encode()).hexdigest()

    def _cache_path(self, content_hash: str):
        return config.CACHE_DIR / content_hash[:2] / content_hash

    def _cache_get(self, key: str, max_age_s: float | None) -> Response | None:
        row = self.conn.execute("SELECT * FROM http_cache WHERE url_hash=?", (key,)).fetchone()
        if not row or not row["content_hash"]:
            return None
        if max_age_s is not None:
            age = time.time() - _iso_to_ts(row["fetched_at"])
            if age > max_age_s:
                return None
        p = self._cache_path(row["content_hash"])
        if not p.exists():
            return None
        return Response(row["url"], row["status_code"], p.read_bytes(), row["content_hash"], True)

    def _cache_put(self, key: str, url: str, status: int, content: bytes) -> str:
        h = hashlib.sha256(content).hexdigest()
        p = self._cache_path(h)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_bytes(content)
        self.conn.execute(
            "INSERT INTO http_cache(url_hash,url,fetched_at,status_code,content_hash,bytes) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(url_hash) DO UPDATE SET fetched_at=excluded.fetched_at, status_code=excluded.status_code, "
            "content_hash=excluded.content_hash, bytes=excluded.bytes",
            (key, url, db.now_iso(), status, h, len(content)))
        return h

    # -- fetch ----------------------------------------------------------------
    def get(self, url: str, params: dict | None = None, max_age_s: float | None = 0,
            timeout: int = 60) -> Response:
        """max_age_s: None = any cached copy is fine; 0 = always refetch (still cached after)."""
        key = self._key(url, params)
        if max_age_s is None or max_age_s > 0:
            cached = self._cache_get(key, max_age_s)
            if cached:
                return cached
        host = urlparse(url).netloc
        is_nse = host in NSE_HOSTS
        delay = 2.0
        last_err: str = ""
        for attempt in range(1, self.max_tries + 1):
            if is_nse:
                self.warm_nse()
            self._throttle(host)
            try:
                r = self.s.get(url, params=params, timeout=timeout)
            except requests.RequestException as e:
                last_err = f"{type(e).__name__}: {e}"
                log.warning("GET %s attempt %d failed: %s", url, attempt, last_err)
            else:
                if r.status_code < 400:
                    h = self._cache_put(key, r.url, r.status_code, r.content)
                    return Response(r.url, r.status_code, r.content, h, False)
                last_err = f"HTTP {r.status_code}"
                if r.status_code not in RETRY_STATUS:
                    raise FetchError(f"{url}: {last_err}")
                if is_nse and r.status_code in (401, 403):
                    self.warm_nse(force=True)
                log.warning("GET %s attempt %d: %s", url, attempt, last_err)
            time.sleep(delay + random.uniform(0, 1))
            delay = min(delay * 2, 60)
        raise FetchError(f"{url}: giving up after {self.max_tries} tries ({last_err})")

    def download(self, url: str, dest_dir=None, max_age_s: float | None = None) -> tuple[str, "config.Path"]:
        """Fetch a file (PDF/XBRL), store under FILES_DIR/<hh>/<sha256><ext>. Returns (sha256, path)."""
        resp = self.get(url, max_age_s=max_age_s, timeout=120)
        ext = ""
        path_part = urlparse(url).path.lower()
        for e in (".pdf", ".xml", ".xbrl", ".zip", ".csv", ".html"):
            if path_part.endswith(e):
                ext = e
                break
        dest_dir = dest_dir or config.FILES_DIR
        p = dest_dir / resp.content_hash[:2] / f"{resp.content_hash}{ext}"
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_bytes(resp.content)
        return resp.content_hash, p


def _iso_to_ts(s: str) -> float:
    from datetime import datetime
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=config.IST).timestamp()
