"""Bearer-token authentication for the FalconEye API.

Two roles are configured through the environment:

- ``FALCONEYE_STAFF_TOKEN`` — may read stored operational data.
- ``FALCONEYE_PRIVILEGED_TOKEN`` — may additionally call Sabre, restore
  snapshots, and queue ingestion or comparison jobs.

An unset or empty token never matches, so a missing configuration rejects
every request instead of opening the API.
"""

import hmac
import os
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

STAFF_TOKEN_ENV = "FALCONEYE_STAFF_TOKEN"
PRIVILEGED_TOKEN_ENV = "FALCONEYE_PRIVILEGED_TOKEN"

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class StaffIdentity:
    role: Literal["staff", "privileged"]


def _token_matches(presented: str, env_name: str) -> bool:
    expected = os.environ.get(env_name, "")
    if not expected:
        return False
    return hmac.compare_digest(presented.encode(), expected.encode())


def require_staff(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> StaffIdentity:
    token = credentials.credentials if credentials else ""
    if token:
        if _token_matches(token, PRIVILEGED_TOKEN_ENV):
            return StaffIdentity(role="privileged")
        if _token_matches(token, STAFF_TOKEN_ENV):
            return StaffIdentity(role="staff")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_privileged(
    identity: StaffIdentity = Depends(require_staff),
) -> StaffIdentity:
    if identity.role != "privileged":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Privileged role required",
        )
    return identity
