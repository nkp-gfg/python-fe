"""Caller authentication and Sabre budgets for routes that open Sabre sessions.

Every route that constructs a ``SabreClient`` (directly, through
``run_feeder``, or by queuing a Celery batch) must depend on
``require_sabre_caller`` and call ``reserve_sabre_budget`` before the session
is opened or the job is written.

Configuration is read from the environment on every request:

- ``FALCONEYE_API_TOKENS``: comma-separated bearer tokens. With none
  configured, every Sabre route answers 401.
- ``FALCONEYE_SABRE_LIMIT_WINDOW_SECONDS``: sliding budget window.
- ``FALCONEYE_SABRE_SESSIONS_PER_CALLER`` / ``FALCONEYE_SABRE_SESSIONS_GLOBAL``
- ``FALCONEYE_FLIGHTS_PER_CALLER`` / ``FALCONEYE_FLIGHTS_GLOBAL``

Budgets are kept in Redis and reserved by one Lua script, so concurrent
requests and multiple API processes share the same counters.
"""

import hashlib
import hmac
import os
import uuid
from dataclasses import dataclass

import structlog
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.exceptions import RedisError

from backend.api.redis_client import get_redis

logger = structlog.get_logger(__name__)

_DEFAULTS = {
    "FALCONEYE_SABRE_LIMIT_WINDOW_SECONDS": 60.0,
    "FALCONEYE_SABRE_SESSIONS_PER_CALLER": 6,
    "FALCONEYE_SABRE_SESSIONS_GLOBAL": 20,
    "FALCONEYE_FLIGHTS_PER_CALLER": 100,
    "FALCONEYE_FLIGHTS_GLOBAL": 200,
}

# Hash tag keeps every budget key in one Redis Cluster slot for the script.
_KEY_PREFIX = "{sabre_budget}"

_RESERVE_SCRIPT = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local window_ms = tonumber(ARGV[1])
local member = ARGV[2]
local sessions = tonumber(ARGV[3])
local flights = tonumber(ARGV[4])
local caps = {tonumber(ARGV[5]), tonumber(ARGV[6]), tonumber(ARGV[7]), tonumber(ARGV[8])}
local amounts = {sessions, sessions, flights, flights}

local function used(key)
  redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window_ms)
  local total = 0
  for _, m in ipairs(redis.call('ZRANGE', key, 0, -1)) do
    total = total + tonumber(string.match(m, ':(%d+)$'))
  end
  return total
end

for i = 1, 4 do
  if amounts[i] > 0 and used(KEYS[i]) + amounts[i] > caps[i] then
    return 0
  end
end

for i = 1, 4 do
  if amounts[i] > 0 then
    redis.call('ZADD', KEYS[i], now, member .. ':' .. amounts[i])
    redis.call('PEXPIRE', KEYS[i], window_ms)
  end
end
return 1
"""

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class SabreCaller:
    """An authenticated caller. ``id`` is derived from the token, never the token itself."""

    id: str


def _configured_tokens() -> list[str]:
    raw = os.environ.get("FALCONEYE_API_TOKENS", "")
    return [token.strip() for token in raw.split(",") if token.strip()]


def _caller_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="A valid bearer token is required for Sabre operations",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_sabre_caller(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> SabreCaller:
    """Authenticate the caller of a route that opens a Sabre session."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    presented = credentials.credentials.encode("utf-8")
    matched = False
    for token in _configured_tokens():
        if hmac.compare_digest(presented, token.encode("utf-8")):
            matched = True
    if not matched:
        raise _unauthorized()
    return SabreCaller(id=_caller_id(credentials.credentials))


def _limit(name: str, cast):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return cast(_DEFAULTS[name])
    try:
        value = cast(raw)
    except ValueError:
        logger.warning("sabre_budget_invalid_setting", setting=name)
        return cast(_DEFAULTS[name])
    return max(value, 0)


def try_reserve_sabre_budget(caller_id: str, sessions: int, flights: int) -> bool:
    """Atomically reserve Sabre sessions and flights for a caller.

    Returns False, reserving nothing, when the caller's or the global budget
    for the current window would be exceeded. Raises ``RedisError`` if the
    budget store is unreachable.
    """
    window_ms = max(int(_limit("FALCONEYE_SABRE_LIMIT_WINDOW_SECONDS", float) * 1000), 1)
    keys = [
        f"{_KEY_PREFIX}:caller:{caller_id}:sessions",
        f"{_KEY_PREFIX}:global:sessions",
        f"{_KEY_PREFIX}:caller:{caller_id}:flights",
        f"{_KEY_PREFIX}:global:flights",
    ]
    args = [
        window_ms,
        uuid.uuid4().hex,
        int(sessions),
        int(flights),
        _limit("FALCONEYE_SABRE_SESSIONS_PER_CALLER", int),
        _limit("FALCONEYE_SABRE_SESSIONS_GLOBAL", int),
        _limit("FALCONEYE_FLIGHTS_PER_CALLER", int),
        _limit("FALCONEYE_FLIGHTS_GLOBAL", int),
    ]
    return bool(get_redis().eval(_RESERVE_SCRIPT, len(keys), *keys, *args))


def reserve_sabre_budget(caller: SabreCaller, sessions: int = 1, flights: int = 0) -> None:
    """Reserve budget for a request or raise 429 (over budget) / 503 (store down)."""
    try:
        admitted = try_reserve_sabre_budget(caller.id, sessions, flights)
    except RedisError:
        logger.exception("sabre_budget_unavailable")
        raise HTTPException(
            status_code=503,
            detail="Sabre budget store unavailable — please retry shortly",
        )
    if not admitted:
        logger.warning("sabre_budget_exceeded", caller=caller.id[:8],
                       sessions=sessions, flights=flights)
        window = _limit("FALCONEYE_SABRE_LIMIT_WINDOW_SECONDS", float)
        raise HTTPException(
            status_code=429,
            detail="Sabre request budget exceeded — please retry later",
            headers={"Retry-After": str(max(int(window), 1))},
        )
