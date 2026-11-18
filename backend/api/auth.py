"""Bearer-token authentication for operator-only API routes."""

import hmac
import logging
import os

from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)

API_TOKEN_ENV = "FALCONEYE_API_TOKEN"


def require_api_token(authorization: str | None = Header(default=None)) -> None:
    """Require ``Authorization: Bearer <FALCONEYE_API_TOKEN>``.

    The token is read on every request so rotation does not need a restart.
    When the variable is unset the route is closed rather than open.
    """
    expected = os.environ.get(API_TOKEN_ENV, "")
    if not expected:
        logger.error("%s is not configured; refusing protected request", API_TOKEN_ENV)
        raise HTTPException(
            status_code=401,
            detail="Authentication is not configured",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        presented.strip().encode(), expected.encode()
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
