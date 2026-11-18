"""
Browser origin policy for the FalconEye API.

Only the FalconEye web app may call the API from a browser. The allowed
origins come from ``CORS_ALLOWED_ORIGINS`` (comma-separated, e.g.
``https://falconeye.example.com,http://localhost:3000``). Wildcards and the
opaque ``null`` origin are never accepted.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")

_REJECTED_ENTRIES = {"*", "null"}


def _normalize(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


def get_allowed_origins() -> list[str]:
    raw = os.environ.get("CORS_ALLOWED_ORIGINS")
    entries = raw.split(",") if raw is not None else DEFAULT_ALLOWED_ORIGINS

    origins: list[str] = []
    for entry in entries:
        origin = _normalize(entry)
        if not origin:
            continue
        if origin in _REJECTED_ENTRIES or "*" in origin:
            logger.warning("Ignoring disallowed CORS origin entry %r", entry)
            continue
        if not origin.startswith(("http://", "https://")):
            logger.warning("Ignoring malformed CORS origin entry %r", entry)
            continue
        if origin not in origins:
            origins.append(origin)
    return origins


class BrowserOriginGuardMiddleware:
    """Refuse browser requests issued by pages outside the allowed origins.

    CORS headers only decide whether a page may *read* a response; a
    cross-origin "simple" request (e.g. a bodiless POST) still reaches the
    handler. This middleware stops such requests before any route runs:

    * a request carrying an ``Origin`` header must come from an allowed origin;
    * a request without one that the browser marks as cross-site or same-site
      (``Sec-Fetch-Site``) is only let through for top-level GET navigations.

    Non-browser clients (curl, server-side fetches) send neither header and are
    unaffected.
    """

    def __init__(self, app, allowed_origins: list[str]):
        self.app = app
        self.allowed_origins = frozenset(_normalize(o) for o in allowed_origins)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self._is_permitted(scope):
            await self.app(scope, receive, send)
            return

        body = json.dumps({"detail": "Cross-origin request not allowed"}).encode()
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"vary", b"Origin"),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    def _is_permitted(self, scope) -> bool:
        origin = None
        fetch_site = None
        fetch_mode = None
        for name, value in scope.get("headers", []):
            if name == b"origin":
                origin = value.decode("latin-1")
            elif name == b"sec-fetch-site":
                fetch_site = value.decode("latin-1").lower()
            elif name == b"sec-fetch-mode":
                fetch_mode = value.decode("latin-1").lower()

        if origin is not None:
            return _normalize(origin) in self.allowed_origins

        if fetch_site in ("cross-site", "same-site"):
            return fetch_mode == "navigate" and scope.get("method") in ("GET", "HEAD")

        return True
