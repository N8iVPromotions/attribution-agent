"""
api/auth.py
-----------
API key authentication dependency for FastAPI.

Key lookup order:
  1. env var  API_KEY_ADMIN / API_KEY_{UPPER_CLIENT_ID}
  2. Databricks Secrets scope "attribution": api_key_admin / api_key_{client_id}

Header: X-API-Key: <key>
"""

from __future__ import annotations
import os
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from config.rbac_config import Role

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _get_secret(key: str) -> str:
    try:
        import base64
        from databricks.sdk import WorkspaceClient

        resp = WorkspaceClient().secrets.get_secret(scope="attribution", key=key)
        val = resp.value or ""
        try:
            return base64.b64decode(val).decode("utf-8")
        except Exception:
            return val
    except Exception:
        return ""


def _resolve_key(api_key: str) -> Role | None:
    """Return the Role for an API key, or None if unrecognized."""

    def _matches(secret_name: str, env_name: str) -> bool:
        env_val = os.environ.get(env_name, "")
        if env_val and api_key == env_val:
            return True
        db_val = _get_secret(secret_name)
        return bool(db_val and api_key == db_val)

    if _matches("api_key_admin", "API_KEY_ADMIN"):
        return Role.ADMIN

    from config.client_config import CLIENT_REGISTRY

    for client_id in CLIENT_REGISTRY:
        secret_name = f"api_key_{client_id}"
        env_name = f"API_KEY_{client_id.upper()}"
        if _matches(secret_name, env_name):
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
