"""
config.py — Client configuration and environment management.
Each client gets a ClientConfig. Add new clients by appending to CLIENT_REGISTRY.
Store all secrets in .env or your secret manager — never hardcode.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ClientConfig:
    """All settings for a single client's attribution pipeline."""

    client_id: str  # Unique slug, e.g. "acme-corp"
    client_name: str  # Human-readable name
    meta_ad_account_id: str  # Format: act_XXXXXXXXXX
    meta_access_token: str  # Meta system user token
    hubspot_access_token: str  # HubSpot private app token
    lookback_days: int = 30  # How far back to pull data
    databricks_catalog: str = "main"  # Unity Catalog name
    databricks_schema: str = "bronze"  # Bronze = raw landing zone


@dataclass
class DatabricksConfig:
    """Shared Databricks connection settings (same for all clients)."""

    host: str
    http_path: str  # SQL warehouse HTTP path
    access_token: str


def get_databricks_config() -> DatabricksConfig:
    return DatabricksConfig(
        host=os.environ["DATABRICKS_HOST"],  # e.g. adb-XXXXX.azuredatabricks.net
        http_path=os.environ["DATABRICKS_HTTP_PATH"],  # /sql/1.0/warehouses/XXXXX
        access_token=os.environ["DATABRICKS_TOKEN"],
    )


# ─── CLIENT REGISTRY ────────────────────────────────────────────────────────
# Add a new dict entry per client. Pull secrets from env vars so nothing
# sensitive lives in this file.

CLIENT_REGISTRY: list[ClientConfig] = [
    ClientConfig(
        client_id="client-001",
        client_name="Example Client",
        meta_ad_account_id=os.environ.get("CLIENT_001_META_ACCOUNT_ID", ""),
        meta_access_token=os.environ.get("CLIENT_001_META_TOKEN", ""),
        hubspot_access_token=os.environ.get("CLIENT_001_HUBSPOT_TOKEN", ""),
        lookback_days=30,
        databricks_catalog="main",
        databricks_schema="bronze",
    ),
    # Add more clients here as you onboard them:
    # ClientConfig(
    #     client_id="client-002",
    #     client_name="Another Client",
    #     meta_ad_account_id=os.environ.get("CLIENT_002_META_ACCOUNT_ID", ""),
    #     ...
    # ),
]


def get_client(client_id: str) -> ClientConfig:
    """Retrieve a client config by ID. Raises if not found."""
    for client in CLIENT_REGISTRY:
        if client.client_id == client_id:
            return client
    raise ValueError(f"No client found with id: {client_id}")
