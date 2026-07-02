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


def get_secret(key: str, default: str = "") -> str:
    """Read a secret from the environment."""
    return os.environ.get(key, default)
