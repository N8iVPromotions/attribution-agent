"""
api/auth.py
-----------
API key authentication dependency for FastAPI.

Key lookup: env var  API_KEY_ADMIN / API_KEY_{UPPER_CLIENT_ID}
(Cloud Run injects these from Secret Manager via --set-secrets.)

Header: X-API-Key: <key>
"""

from __future__ import annotations
import os
import hmac
from dataclasses import dataclass
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from config.rbac_config import Role

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(frozen=True)
class AuthPrincipal:
    role: Role
    client_id: str | None = None


def _resolve_key(api_key: str) -> AuthPrincipal | None:
    """Return the authenticated principal for an API key."""

    def _matches(env_name: str) -> bool:
        env_val = os.environ.get(env_name, "")
        return bool(env_val and hmac.compare_digest(api_key, env_val))

    if _matches("API_KEY_ADMIN"):
        return AuthPrincipal(Role.ADMIN)

    from config.client_config import CLIENT_REGISTRY

    for client_id in CLIENT_REGISTRY:
        env_name = f"API_KEY_{client_id.upper()}"
        if _matches(env_name):
            return AuthPrincipal(Role.ANALYST, client_id=client_id)

    return None


async def require_auth(
    api_key: str | None = Security(_api_key_header),
) -> AuthPrincipal:
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header required",
        )
    principal = _resolve_key(api_key)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return principal


async def require_admin(
    principal: AuthPrincipal = Depends(require_auth),
) -> AuthPrincipal:
    if principal.role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return principal


def require_permission(principal: AuthPrincipal, permission) -> None:
    from config.rbac_config import check_permission

    if not check_permission(principal.role, permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission '{permission.value}' required",
        )


def require_client_access(principal: AuthPrincipal, client_id: str) -> None:
    if principal.role != Role.ADMIN and principal.client_id != client_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client access denied",
        )
