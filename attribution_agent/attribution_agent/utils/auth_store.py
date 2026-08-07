"""
Databricks-backed authentication for the ARIE Command Center.

The v1 login flow is intentionally simple for internal pilots:
- users, sessions, and auth events live in Databricks Delta tables
- passwords are stored as PBKDF2-SHA256 hashes
- the first admin can be bootstrapped from deployment secrets
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from config.rbac_config import Role
from utils import databricks_writer as db

HASH_ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 260_000
DEFAULT_BOOTSTRAP_EMAIL = "zajen@n8ivpromotions.com"


class AuthConfigurationError(RuntimeError):
    """Raised when auth is enabled but cannot bootstrap or reach its store."""


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    email: str
    display_name: str
    role: Role
    agency_id: str = ""
    client_ids: tuple[str, ...] = ()

    def to_session_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role.value,
            "agency_id": self.agency_id,
            "client_ids": list(self.client_ids),
        }


@dataclass(frozen=True)
class AuthResult:
    ok: bool
    message: str
    user: AuthUser | None = None
    session_id: str = ""


def auth_enabled() -> bool:
    return os.environ.get("ARIE_AUTH_ENABLED", "true").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def hash_password(password: str, *, iterations: int = DEFAULT_ITERATIONS) -> str:
    if not password:
        raise ValueError("password is required")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return "$".join(
        [
            HASH_ALGORITHM,
            str(iterations),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iteration_text, salt_text, digest_text = password_hash.split("$", 3)
        if algorithm != HASH_ALGORITHM:
            return False
        iterations = int(iteration_text)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected)


def login(email: str, password: str, *, user_agent: str = "streamlit") -> AuthResult:
    email = _normalize_email(email)
    if not email or not password:
        return AuthResult(False, "Email and password are required.")

    try:
        _bootstrap_admin_if_empty()
        row = db.fetch_auth_user_by_email(email)
    except Exception as exc:
        raise AuthConfigurationError(
            "Authentication database is unavailable. Check the Databricks SQL warehouse and credentials."
        ) from exc

    if not row:
        _write_event("login", email=email, outcome="unknown_user")
        return AuthResult(False, "Invalid email or password.")

    locked_until = _coerce_datetime(row.get("locked_until"))
    if locked_until and locked_until > _now():
        _write_event("login", row=row, outcome="locked")
        return AuthResult(False, "Account is temporarily locked.")

    if not bool(row.get("is_active", True)):
        _write_event("login", row=row, outcome="inactive")
        return AuthResult(False, "Account is inactive.")

    if not verify_password(password, str(row.get("password_hash", ""))):
        failed_count = int(row.get("failed_login_count") or 0) + 1
        row["failed_login_count"] = failed_count
        max_failed = int(os.environ.get("ARIE_AUTH_MAX_FAILED", "5") or "5")
        if failed_count >= max_failed:
            lock_minutes = int(os.environ.get("ARIE_AUTH_LOCK_MINUTES", "15") or "15")
            row["locked_until"] = _now() + timedelta(minutes=lock_minutes)
        db.upsert_auth_user(row)
        _write_event("login", row=row, outcome="bad_password")
        return AuthResult(False, "Invalid email or password.")

    user = _row_to_user(row)
    row["failed_login_count"] = 0
    row["locked_until"] = None
    row["last_login_at"] = _now()
    db.upsert_auth_user(row)

    ttl_hours = int(os.environ.get("ARIE_SESSION_TTL_HOURS", "12") or "12")
    session_id = secrets.token_urlsafe(32)
    db.write_auth_session(
        {
            "session_id": session_id,
            "user_id": user.user_id,
            "email": user.email,
            "role": user.role.value,
            "agency_id": user.agency_id,
            "created_at": _now(),
            "expires_at": _now() + timedelta(hours=ttl_hours),
            "user_agent": user_agent,
        }
    )
    _write_event("login", user=user, outcome="success", session_id=session_id)
    return AuthResult(True, "Signed in.", user=user, session_id=session_id)


def validate_session(session_id: str) -> AuthUser | None:
    try:
        session = db.fetch_auth_session(session_id)
    except Exception:
        return None
    if not session:
        return None
    user_row = db.fetch_auth_user_by_email(str(session.get("email", "")))
    if not user_row or not bool(user_row.get("is_active", True)):
        return None
    return _row_to_user(user_row)


def logout(session_id: str, user: dict[str, Any] | None = None) -> None:
    if not session_id:
        return
    try:
        db.revoke_auth_session(session_id)
        _write_event(
            "logout",
            email=(user or {}).get("email", ""),
            user_id=(user or {}).get("user_id", ""),
            role=(user or {}).get("role", ""),
            agency_id=(user or {}).get("agency_id", ""),
            outcome="success",
            session_id=session_id,
        )
    except Exception:
        return


def _bootstrap_admin_if_empty() -> None:
    if db.count_auth_users() > 0:
        return

    email = _normalize_email(
        os.environ.get("ARIE_BOOTSTRAP_ADMIN_EMAIL", DEFAULT_BOOTSTRAP_EMAIL)
    )
    password = os.environ.get("ARIE_BOOTSTRAP_ADMIN_PASSWORD") or os.environ.get(
        "API_KEY_ADMIN",
        "",
    )
    if not email or not password:
        raise AuthConfigurationError(
            "Set ARIE_BOOTSTRAP_ADMIN_EMAIL and ARIE_BOOTSTRAP_ADMIN_PASSWORD to create the first admin."
        )

    now = _now()
    user_id = uuid.uuid4().hex
    db.upsert_auth_user(
        {
            "user_id": user_id,
            "email": email,
            "display_name": "ARIE Operator",
            "role": Role.ADMIN.value,
            "agency_id": "",
            "client_ids": "",
            "password_hash": hash_password(password),
            "is_active": True,
            "failed_login_count": 0,
            "created_at": now,
            "updated_at": now,
        }
    )
    _write_event(
        "bootstrap_admin",
        email=email,
        user_id=user_id,
        role=Role.ADMIN.value,
        outcome="success",
        detail={"source": "env"},
    )


def _row_to_user(row: dict[str, Any]) -> AuthUser:
    role_value = str(row.get("role") or Role.VIEWER.value)
    try:
        role = Role(role_value)
    except ValueError:
        role = Role.VIEWER
    return AuthUser(
        user_id=str(row.get("user_id", "") or ""),
        email=_normalize_email(str(row.get("email", "") or "")),
        display_name=str(row.get("display_name", "") or ""),
        role=role,
        agency_id=str(row.get("agency_id", "") or ""),
        client_ids=tuple(
            c.strip()
            for c in str(row.get("client_ids", "") or "").split(",")
            if c.strip()
        ),
    )


def _write_event(
    event_type: str,
    *,
    row: dict[str, Any] | None = None,
    user: AuthUser | None = None,
    email: str = "",
    user_id: str = "",
    role: str = "",
    agency_id: str = "",
    outcome: str,
    detail: dict[str, Any] | None = None,
    session_id: str = "",
) -> None:
    row = row or {}
    try:
        db.write_auth_event(
            {
                "event_id": uuid.uuid4().hex,
                "event_time": _now(),
                "event_type": event_type,
                "email": email or (user.email if user else row.get("email", "")),
                "user_id": user_id or (user.user_id if user else row.get("user_id", "")),
                "role": role or (user.role.value if user else row.get("role", "")),
                "agency_id": agency_id
                or (user.agency_id if user else row.get("agency_id", "")),
                "outcome": outcome,
                "detail_json": json.dumps(detail or {}, sort_keys=True),
                "session_id": session_id,
            }
        )
    except Exception:
        return


def _coerce_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _normalize_email(value: str) -> str:
    return str(value or "").strip().lower()


def _now() -> datetime:
    return datetime.now(timezone.utc)
