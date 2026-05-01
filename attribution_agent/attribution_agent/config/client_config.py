"""
config/client_config.py
-----------------------
Central config for each client.  Add a new dict entry per client —
the rest of the pipeline picks it up automatically.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal
import os
from pathlib import Path


def _get_secret(key: str) -> str:
    """
    Reads a secret from Databricks Secrets when running inside Databricks.
    Falls back to environment variables for local testing.
    """
    try:
        from databricks.sdk.runtime import dbutils
        return dbutils.secrets.get(scope="attribution", key=key)
    except Exception:
        return os.environ.get(key, "")


@dataclass
class ClientConfig:
    client_id: str                          # short slug, e.g. "acme_co"
    client_name: str                        # display name
    attribution_model: Literal[
        "last_touch", "linear", "time_decay"
    ] = "last_touch"

    # ── Meta ──────────────────────────────────────────────────────────────────
    meta_enabled: bool = False
    meta_ad_account_id: str = ""            # format: act_123456789

    # ── HubSpot ───────────────────────────────────────────────────────────────
    hubspot_enabled: bool = False
    hubspot_pipeline_id: str = ""           # leave blank for default pipeline

    # ── Databricks destination ─────────────────────────────────────────────────
    databricks_schema: str = ""         # e.g. "attribution_acme_co"

    # ── Reporting window ──────────────────────────────────────────────────────
    lookback_days: int = 30

    # ── Alert thresholds ──────────────────────────────────────────────────────
    spend_drop_pct_alert: float = 0.30
    zero_spend_days_allowed: int = 1

    # ── Stripe ────────────────────────────────────────────────────────────────
    stripe_enabled: bool = False
    stripe_account_id: str = ""            # Stripe account ID for reference

    # ── Agency ────────────────────────────────────────────────────────────────
    agency_id: str = ""                    # links client to an agency ("" = direct)
    client_report_email: str = ""          # where this client's report gets sent
    client_display_name: str = ""          # name shown in agency dashboard

    # ── Credentials (read from Databricks Secrets at runtime) ─────────────────
    @property
    def meta_access_token(self) -> str:
        return _get_secret("META_ACCESS_TOKEN")

    @property
    def hubspot_access_token(self) -> str:
        return _get_secret("HUBSPOT_ACCESS_TOKEN")

    @property
    def stripe_secret_key(self) -> str:
        return _get_secret("STRIPE_SECRET_KEY")


# ─── CLIENT REGISTRY ──────────────────────────────────────────────────────────

CLIENT_REGISTRY: dict[str, ClientConfig] = {
    "demo_client": ClientConfig(
        client_id="demo_client",
        client_name="Demo Client LLC",
        attribution_model="last_touch",
        meta_enabled=True,
        meta_ad_account_id="155554968273585",  # ← replace with real ID
        hubspot_enabled=True,
        hubspot_pipeline_id="",
        databricks_schema="workspace.attribution_demo_client",
        lookback_days=30,
    ),
    # "second_client": ClientConfig(...)
}


def get_client(client_id: str) -> ClientConfig:
    if client_id not in CLIENT_REGISTRY:
        raise ValueError(
            f"Client '{client_id}' not found. "
            f"Available clients: {list(CLIENT_REGISTRY.keys())}"
        )
    return CLIENT_REGISTRY[client_id]


def list_clients() -> list[str]:
    return list(CLIENT_REGISTRY.keys())