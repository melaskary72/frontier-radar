"""Shared HTTP machinery for every collector.

Guarantees, in order:
1. Polite pacing: a minimum interval between requests per source (config/sources.yaml).
2. Retry with exponential backoff on 429, 5xx, and transport errors; honours Retry-After.
3. Caching: every response is written to raw_snapshots (fetched_at, SHA-256) and reused
   while fresh, so reruns cost nothing and every derived row has provenance.
4. Offline mode (RADAR_OFFLINE=1): never touches the network; cache or fixture only.
5. Cassettes: tests replay recorded real responses through httpx.MockTransport.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from radar.config import sources
from radar.store.snapshots import SnapshotStore, request_key

log = logging.getLogger(__name__)

USER_AGENT = "FrontierRadar/0.1 (+https://github.com/melaskary72/frontier-radar)"


class OfflineMiss(RuntimeError):
    """Raised in offline mode when a request is not in the cache."""


class RetryableStatus(Exception):
    def __init__(self, response: httpx.Response):
        self.response = response
        super().__init__(f"HTTP {response.status_code} for {response.request.url}")


@dataclass
class Fetched:
    snapshot_id: int
    status: int
    payload: Any
    from_cache: bool


class RateLimiter:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, RetryableStatus | httpx.TransportError)


def _wait_with_retry_after(state: RetryCallState) -> float:
    exc = state.outcome.exception() if state.outcome else None
    if isinstance(exc, RetryableStatus):
        ra = exc.response.headers.get("retry-after")
        if ra and ra.isdigit():
            return min(float(ra), 120.0)
    return wait_exponential_jitter(initial=2, max=60)(state)


def full_url(url: str, params: dict[str, Any] | None) -> str:
    if not params:
        return url
    clean = {k: v for k, v in sorted(params.items()) if v is not None}
    return f"{url}?{urlencode(clean, doseq=True)}"


class HttpCollector:
    """Base class. Subclasses set `source` and call `self.get_json` / `self.get_text`."""

    source: str = "base"
    rate_key: str | None = None
    default_ttl = timedelta(hours=20)

    def __init__(
        self,
        session: Session,
        *,
        client: httpx.Client | None = None,
        offline: bool | None = None,
        ttl: timedelta | None = None,
        min_interval: float | None = None,
    ):
        self.session = session
        self.store = SnapshotStore(session)
        self.offline = offline if offline is not None else os.environ.get("RADAR_OFFLINE") == "1"
        self.ttl = ttl if ttl is not None else self.default_ttl
        interval = float(sources()["rate_limits"].get(self.rate_key or self.source, 1.0))
        self.limiter = RateLimiter(interval if min_interval is None else min_interval)
        self.min_interval_override = min_interval
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            transport=cassette_transport_from_env(),
        )
        self.requests_made = 0

    # Subclasses override to add auth headers.
    def headers(self) -> dict[str, str]:
        return {}

    def fetch(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        method: str = "GET",
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        parse: str = "json",
        ttl: timedelta | None = None,
        ok_statuses: tuple[int, ...] = (200,),
    ) -> Fetched:
        """Fetch with cache. Non-OK statuses in `ok_statuses` are cached too (e.g. 404)."""
        url_full = full_url(url, params)
        key = request_key(method, url_full, json_body)
        cached = self.store.latest(key, max_age=None if self.offline else (ttl or self.ttl))
        if cached is not None:
            return Fetched(cached.id, cached.http_status, cached.payload_json, True)
        if self.offline:
            raise OfflineMiss(f"offline and not cached: {method} {url_full}")

        resp = self._send(method, url_full, json_body, {**self.headers(), **(headers or {})})
        if resp.status_code not in ok_statuses:
            resp.raise_for_status()
        raw = resp.content
        payload: Any
        if parse == "json":
            try:
                payload = resp.json() if raw.strip() else None
            except ValueError:  # error pages are often HTML or plain text
                payload = {"text": resp.text, "content_type": resp.headers.get("content-type")}
        else:
            payload = {"text": resp.text, "content_type": resp.headers.get("content-type")}
        snap = self.store.put(
            source=self.source, url=url_full, key=key, raw=raw, payload=payload,
            http_status=resp.status_code,
        )
        return Fetched(snap.id, resp.status_code, payload, False)

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=_wait_with_retry_after,
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _send(self, method: str, url: str, body: Any, headers: dict[str, str]) -> httpx.Response:
        self.limiter.wait()
        self.requests_made += 1
        resp = self.client.request(method, url, json=body, headers=headers)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise RetryableStatus(resp)
        self.after_response(resp)
        return resp

    def after_response(self, resp: httpx.Response) -> None:
        """Hook for budget tracking (GitHub rate-limit headers)."""


# ---------------------------------------------------------------------------
# Cassettes: record real responses once, replay them in unit tests.
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def _cassette_name(request: httpx.Request) -> str:
    body = request.content.decode() if request.content else None
    body_obj = json.loads(body) if body else None
    return request_key(request.method, str(request.url), body_obj)[:24]


class RecordingTransport(httpx.BaseTransport):
    """Passes requests through and writes each response into a cassette directory."""

    KEEP_HEADERS = ("content-type", "retry-after", "x-ratelimit-remaining", "x-ratelimit-reset",
                    "x-ratelimit-resource")

    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.inner = httpx.HTTPTransport(retries=0)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        resp = self.inner.handle_request(request)
        resp.read()
        record = {
            "request": {"method": request.method, "url": str(request.url)},
            "status": resp.status_code,
            "headers": {k: v for k, v in resp.headers.items() if k.lower() in self.KEEP_HEADERS},
            "body": resp.content.decode("utf-8", errors="replace"),
        }
        (self.directory / f"{_cassette_name(request)}.json").write_text(json.dumps(record, indent=1))
        # The body is already decoded; drop encoding headers so httpx does not decode twice.
        headers = [(k, v) for k, v in resp.headers.items()
                   if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        return httpx.Response(resp.status_code, headers=headers, content=resp.content,
                              request=request)


class ReplayTransport(httpx.BaseTransport):
    def __init__(self, directory: Path):
        self.directory = directory

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = self.directory / f"{_cassette_name(request)}.json"
        if not path.exists():
            raise OfflineMiss(f"no cassette for {request.method} {request.url} in {self.directory}")
        rec = json.loads(path.read_text())
        return httpx.Response(rec["status"], headers=rec["headers"], content=rec["body"].encode(),
                              request=request)


def cassette_transport_from_env() -> httpx.BaseTransport | None:
    rec = os.environ.get("RADAR_RECORD_DIR")
    if rec:
        return RecordingTransport(Path(rec))
    rep = os.environ.get("RADAR_REPLAY_DIR")
    if rep:
        return ReplayTransport(Path(rep))
    return None


def replay_client(source_dir: str) -> httpx.Client:
    return httpx.Client(transport=ReplayTransport(FIXTURES / source_dir),
                        headers={"User-Agent": USER_AGENT})
