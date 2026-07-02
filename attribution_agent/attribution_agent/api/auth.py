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
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from config.rbac_config import Role

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _resolve_key(api_key: str) -> Role | None:
    """Return the Role for an API key, or None if unrecognized."""

    def _matches(env_name: str) -> bool:
        env_val = os.environ.get(env_name, "")
        return bool(env_val and api_key == env_val)

    if _matches("API_KEY_ADMIN"):
        return Role.ADMIN

    from config.client_config import CLIENT_REGISTRY

    for client_id in CLIENT_REGISTRY:
        env_name = f"API_KEY_{client_id.upper()}"
        if _matches(env_name):
            return Role.ANALYST

    return None


async def require_auth(api_key: str | None = Security(_api_key_header)) -> Role:
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header required",
        )
    role = _resolve_key(api_key)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return role


async def require_admin(role: Role = Depends(require_auth)) -> Role:
    if role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return role
