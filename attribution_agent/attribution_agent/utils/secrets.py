"""
utils/secrets.py
----------------
Single source of truth for secret retrieval.

Global platform secrets can still arrive as environment variables through
Cloud Run --set-secrets. Client-specific credentials are stored in GCP Secret
Manager and referenced by secret ID from the client registry.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

# Query-string parameters whose values are credentials. Meta (and any
# connector that authenticates via URL params) embeds these in request URLs,
# which requests then copies into exception messages.
_SECRET_PARAM_RE = re.compile(
    r"(access_token|api_key|apikey|client_secret|refresh_token)=[^&\s'\"]+",
    re.IGNORECASE,
)
_SAFE_SECRET_ID_RE = re.compile(r"[^a-z0-9]+")
_PROJECT_ENV_KEYS = (
    "GOOGLE_CLOUD_PROJECT",
    "GCP_PROJECT",
    "PROJECT_ID",
    "CLOUDSDK_CORE_PROJECT",
)


def get_secret(key: str, default: str = "") -> str:
    """Read a secret from the environment."""
    return os.environ.get(key, default).strip()


def _project_id() -> str:
    for key in _PROJECT_ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def _slug(value: str) -> str:
    return _SAFE_SECRET_ID_RE.sub("-", value.lower()).strip("-") or "value"


def secret_id_for_client(
    client_id: str,
    credential_key: str,
    *,
    prefix: str | None = None,
) -> str:
    """Build a stable Secret Manager ID for one client's credential."""
    env = _slug(os.environ.get("ATTRIBUTION_ENV", "prod"))
    secret_prefix = _slug(
        prefix or os.environ.get("ATTRIBUTION_CLIENT_SECRET_PREFIX", "attr")
    )
    client = _slug(client_id)
    credential = _slug(credential_key)
    return "-".join(part for part in (secret_prefix, env, client, credential) if part)


def _secret_version_name(secret_ref: str) -> str:
    secret_ref = secret_ref.strip()
    if not secret_ref:
        return ""
    if secret_ref.startswith("projects/"):
        if "/versions/" in secret_ref:
            return secret_ref
        return f"{secret_ref}/versions/latest"
    project_id = _project_id()
    if not project_id:
        return ""
    return f"projects/{project_id}/secrets/{secret_ref}/versions/latest"


def read_secret(secret_ref: str, default: str = "") -> str:
    """Read a Secret Manager value by secret ID or full resource name.

    If GCP is not configured locally, falls back to an env var with the same
    name so tests and local smoke runs can continue without ADC.
    """
    secret_ref = (secret_ref or "").strip()
    if not secret_ref:
        return default
    env_value = os.environ.get(secret_ref)
    if env_value is not None:
        return env_value
    version_name = _secret_version_name(secret_ref)
    if not version_name:
        return default
    try:
        from google.cloud import secretmanager
    except Exception as exc:  # pragma: no cover - depends on optional local deps
        logger.warning("Secret Manager client unavailable: %r", exc)
        return default
    try:
        client = secretmanager.SecretManagerServiceClient()
        response = client.access_secret_version(request={"name": version_name})
        return response.payload.data.decode("utf-8").strip()
    except Exception as exc:
        logger.warning("Secret Manager read failed for %s: %r", secret_ref, exc)
        return default


def resolve_secret(secret_ref: str, env_key: str, default: str = "") -> str:
    """Prefer a client-specific Secret Manager ref, then fall back to env."""
    if secret_ref:
        value = read_secret(secret_ref, default="")
        if value:
            return value
    return get_secret(env_key, default)


def write_secret(secret_id: str, value: str) -> str:
    """Create a Secret Manager secret if needed and add a latest version."""
    secret_id = _slug(secret_id)
    secret_value = (value or "").strip()
    if not secret_value:
        raise ValueError("Secret value cannot be empty.")
    project_id = _project_id()
    if not project_id:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT, GCP_PROJECT, PROJECT_ID, or "
            "CLOUDSDK_CORE_PROJECT must be set before writing secrets."
        )
    try:
        from google.api_core.exceptions import AlreadyExists
        from google.cloud import secretmanager
    except Exception as exc:  # pragma: no cover - depends on deployment deps
        raise RuntimeError(
            "google-cloud-secret-manager is required to write client secrets."
        ) from exc

    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{project_id}"
    secret_name = f"{parent}/secrets/{secret_id}"
    try:
        client.create_secret(
            request={
                "parent": parent,
                "secret_id": secret_id,
                "secret": {"replication": {"automatic": {}}},
            }
        )
    except AlreadyExists:
        pass
    client.add_secret_version(
        request={
            "parent": secret_name,
            "payload": {"data": secret_value.encode("utf-8")},
        }
    )
    return secret_id


def redact_secrets(text: str) -> str:
    """Strip credential values from text bound for logs or ops tables."""
    return _SECRET_PARAM_RE.sub(r"\1=REDACTED", text)
