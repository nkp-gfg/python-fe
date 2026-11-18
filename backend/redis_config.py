"""Redis connection settings shared by the Celery app and the API client.

Redis is the Celery broker and result backend, so anyone who can talk to it
can enqueue background tasks. Both sides must authenticate with the same
password; a URL without one is refused instead of silently connecting.
"""

import os
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

_PASSWORD_SCHEMES = {"redis", "rediss"}


class RedisConfigError(RuntimeError):
    pass


def get_redis_url() -> str:
    """Return REDIS_URL with a password, taking it from REDIS_PASSWORD if needed."""
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        raise RedisConfigError(
            "REDIS_URL is not set. Set it to redis://:<password>@<host>:<port>/<db> "
            "or set REDIS_URL without a password plus REDIS_PASSWORD."
        )

    parts = urlsplit(url)
    if parts.scheme not in _PASSWORD_SCHEMES:
        raise RedisConfigError(f"REDIS_URL must use redis:// or rediss://, got {parts.scheme}://")

    if parts.password or parse_qs(parts.query).get("password", [""])[0]:
        return url

    password = os.environ.get("REDIS_PASSWORD", "")
    if not password:
        raise RedisConfigError(
            "REDIS_URL has no password and REDIS_PASSWORD is not set; "
            "Redis must require authentication."
        )

    userinfo = quote(parts.username, safe="") if parts.username else ""
    netloc = f"{userinfo}:{quote(password, safe='')}@{parts.netloc.rsplit('@', 1)[-1]}"
    return urlunsplit(parts._replace(netloc=netloc))


def redact_redis_url(url: str) -> str:
    """Return the URL with any password removed, for logging."""
    parts = urlsplit(url)
    host = parts.netloc.rsplit("@", 1)[-1]
    userinfo = f"{parts.username or ''}:***@" if (parts.username or parts.password) else ""
    return urlunsplit(parts._replace(netloc=f"{userinfo}{host}", query=""))
