"""
utils/secrets.py
----------------
Single source of truth for secret retrieval.

Secrets arrive as environment variables: GCP Secret Manager values are
injected by Cloud Run via --set-secrets, and local dev loads .env through
python-dotenv at module import in each entry point.
"""

from __future__ import annotations

import os
import re

# Query-string parameters whose values are credentials. Meta (and any
# connector that authenticates via URL params) embeds these in request URLs,
# which requests then copies into exception messages.
_SECRET_PARAM_RE = re.compile(
    r"(access_token|api_key|apikey|client_secret|refresh_token)=[^&\s'\"]+",
    re.IGNORECASE,
)


def get_secret(key: str, default: str = "") -> str:
    """Read a secret from the environment."""
    return os.environ.get(key, default)


def redact_secrets(text: str) -> str:
    """Strip credential values from text bound for logs or ops tables."""
    return _SECRET_PARAM_RE.sub(r"\1=REDACTED", text)
