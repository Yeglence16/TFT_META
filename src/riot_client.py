"""Rate-limited Riot API client.

Implements SPEC.md section 2:
  - A dual token bucket that tracks the two development-key rate limits
    (20 req/1s and 100 req/2min) independently; a request proceeds only once
    both buckets have room.
  - The error table from section 2, in particular the 403 behavior: a 403
    means the dev key has expired (this happens every 24h) and must stop the
    process immediately. It is NOT retried and NOT treated as a failed queue
    item, because "failed" would mean "this match doesn't exist" and would
    cause silent, permanent data loss for whatever is left in the queue.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RIOT_API_BASE = "https://{host}.api.riotgames.com"

# Not exhaustive of every Riot platform/region — just enough to catch the
# platform/regional host mixup the spec warns about (section 2: "Routing iki
# seviyeli, karıştırma").
PLATFORM_HOSTS = {
    "euw1", "eun1", "na1", "kr", "tr1", "br1", "jp1",
    "la1", "la2", "oc1", "ru", "sg2", "th2", "tw2", "vn2",
}
REGIONAL_HOSTS = {"europe", "americas", "asia", "sea"}

# Which regional host (for account/match endpoints) a platform host's accounts
# route through. Not exhaustive — extend if config.yaml lists other regions.
PLATFORM_TO_REGIONAL: dict[str, str] = {
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe",
    "na1": "americas", "br1": "americas", "la1": "americas", "la2": "americas", "oc1": "americas",
    "kr": "asia", "jp1": "asia",
    "sg2": "sea", "th2": "sea", "tw2": "sea", "vn2": "sea",
}


class RiotApiKeyExpiredError(RuntimeError):
    """Raised on HTTP 403.

    Callers must let this propagate and stop whatever scrape/queue is running.
    It must never be caught and recorded as "item failed" — the item was never
    attempted, it was refused because the key is dead.
    """


class RiotRateLimitExceededError(RuntimeError):
    """Raised when a single request is still getting 429'd after max_429_attempts.

    This should not happen in normal operation — the token buckets already
    throttle requests to Riot's advertised limits. Hitting this means either
    the buckets are misconfigured or something else is consuming the same
    key's quota concurrently.
    """


class _TokenBucket:
    """One sliding-window rate limit (max_requests per window_seconds).

    Thread-safe. wait_for_slot() blocks until a slot is free, then reserves it.
    """

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def wait_for_slot(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= self.window_seconds:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.max_requests:
                    self._timestamps.append(now)
                    return
                sleep_for = self.window_seconds - (now - self._timestamps[0])
            time.sleep(max(sleep_for, 0.01))


class RiotClient:
    """Rate-limited HTTP client for the Riot TFT API.

    Enforces both development-key rate limit windows at once (20/1s and
    100/2min by default) via two independent token buckets, and applies the
    retry contract from SPEC.md section 2.

    Use `platform_url` for league/summoner endpoints and `regional_url` for
    account/match endpoints — they hit different host families and are not
    interchangeable.
    """

    def __init__(
        self,
        api_key: str,
        *,
        requests_per_second: int = 20,
        requests_per_two_minutes: int = 100,
        timeout: float = 10.0,
        max_5xx_attempts: int = 3,
        max_429_attempts: int = 5,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required (set the RIOT_API_KEY environment variable)")
        self._api_key = api_key
        self._max_5xx_attempts = max_5xx_attempts
        self._max_429_attempts = max_429_attempts
        self._short_bucket = _TokenBucket(requests_per_second, 1.0)
        self._long_bucket = _TokenBucket(requests_per_two_minutes, 120.0)
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "RiotClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    @staticmethod
    def platform_url(platform: str, path: str) -> str:
        """Build a URL against a platform host (euw1, na1, kr, tr1, ...) for
        league / summoner endpoints."""
        if platform not in PLATFORM_HOSTS:
            logger.warning(
                "'%s' is not a recognized platform host — check you didn't pass "
                "a regional host (europe/americas/asia) by mistake",
                platform,
            )
        return RIOT_API_BASE.format(host=platform) + path

    @staticmethod
    def regional_url(region: str, path: str) -> str:
        """Build a URL against a regional host (europe, americas, asia) for
        account / match endpoints."""
        if region not in REGIONAL_HOSTS:
            logger.warning(
                "'%s' is not a recognized regional host — check you didn't pass "
                "a platform host (euw1/na1/kr/...) by mistake",
                region,
            )
        return RIOT_API_BASE.format(host=region) + path

    def get(self, url: str, *, params: dict[str, Any] | None = None) -> Any | None:
        """GET url, obeying both rate-limit windows.

        Returns the parsed JSON body, or None if the resource was a 404
        (caller should skip it and move on).

        Raises RiotApiKeyExpiredError on 403 — this must propagate and stop
        the caller's queue, per SPEC.md section 2.

        Raises RiotRateLimitExceededError if a request is still 429ing after
        max_429_attempts (default 5) — the 429 loop is bounded, not infinite.
        """
        attempts_5xx = 0
        attempts_429 = 0
        while True:
            self._short_bucket.wait_for_slot()
            self._long_bucket.wait_for_slot()

            response = self._http.get(url, params=params, headers={"X-Riot-Token": self._api_key})

            if response.status_code == 200:
                return response.json()

            if response.status_code == 429:
                attempts_429 += 1
                if attempts_429 >= self._max_429_attempts:
                    logger.error(
                        "429 on %s — still rate limited after %d attempts, giving up",
                        url, attempts_429,
                    )
                    raise RiotRateLimitExceededError(
                        f"429 on {url} persisted through {attempts_429} attempts. "
                        "The token buckets should prevent this under normal load — "
                        "check for another process sharing this API key."
                    )
                retry_after = float(response.headers.get("Retry-After", "1"))
                logger.warning(
                    "429 on %s — honoring Retry-After, sleeping %.1fs (attempt %d/%d)",
                    url, retry_after, attempts_429, self._max_429_attempts,
                )
                time.sleep(retry_after)
                continue

            if response.status_code == 403:
                logger.error(
                    "403 Forbidden on %s — Riot API key has expired or is invalid. "
                    "Stopping immediately. This item is unattempted, not failed.",
                    url,
                )
                raise RiotApiKeyExpiredError(
                    f"403 Forbidden on {url}: the Riot API key has expired or is invalid. "
                    "Refresh RIOT_API_KEY and resume from saved state — do not resume "
                    "by treating pending items as missing matches."
                )

            if response.status_code == 404:
                logger.info("404 on %s — skipping", url)
                return None

            if 500 <= response.status_code < 600:
                attempts_5xx += 1
                if attempts_5xx >= self._max_5xx_attempts:
                    logger.error(
                        "%d on %s — giving up after %d attempts",
                        response.status_code, url, attempts_5xx,
                    )
                    response.raise_for_status()
                backoff = 2 ** (attempts_5xx - 1)
                logger.warning(
                    "%d on %s — retrying (%d/%d) after %.1fs",
                    response.status_code, url, attempts_5xx, self._max_5xx_attempts, backoff,
                )
                time.sleep(backoff)
                continue

            response.raise_for_status()
