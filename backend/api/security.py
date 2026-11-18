"""Bearer-token authentication and per-caller rate limiting for API routes."""

import hashlib
import hmac
import os
import threading
import time
from collections import deque
from typing import Optional

from fastapi import Header, HTTPException

API_TOKEN_ENV = "FALCONEYE_API_TOKEN"


def _configured_tokens() -> list[str]:
    raw = os.environ.get(API_TOKEN_ENV, "")
    return [t.strip() for t in raw.split(",") if t.strip()]


def require_api_token(
    authorization: Optional[str] = Header(default=None),
) -> str:
    """Return a stable caller id for a valid ``Authorization: Bearer`` token.

    Tokens are read from ``FALCONEYE_API_TOKEN`` (comma-separated) on every
    request. When no token is configured every request is refused.
    """
    scheme, _, presented = (authorization or "").partition(" ")
    presented = presented.strip()
    if scheme.lower() != "bearer" or not presented:
        raise HTTPException(
            status_code=401,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    presented_bytes = presented.encode()
    matched = False
    for token in _configured_tokens():
        if hmac.compare_digest(token.encode(), presented_bytes):
            matched = True
    if not matched:
        raise HTTPException(
            status_code=401,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return hashlib.sha256(presented_bytes).hexdigest()


class SlidingWindowLimiter:
    """Allow at most ``limit`` acquisitions per caller in any ``window_s`` seconds.

    State is per process; the API runs as a single uvicorn process.
    """

    def __init__(self, limit: int, window_s: float):
        self.limit = limit
        self.window_s = window_s
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def try_acquire(self, caller: str) -> Optional[float]:
        """Record a hit and return None, or return seconds until one is allowed."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(caller, deque())
            while hits and now - hits[0] >= self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(0.0, self.window_s - (now - hits[0]))
            hits.append(now)
            return None


def enforce_rate_limit(limiter: SlidingWindowLimiter, caller: str) -> None:
    retry_after = limiter.try_acquire(caller)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many analytics requests; retry later",
            headers={"Retry-After": str(max(1, int(retry_after + 0.999)))},
        )
