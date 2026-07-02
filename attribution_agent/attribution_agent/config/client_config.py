"""
config/client_config.py
-----------------------
Central config for each client.  Add a new dict entry per client —
the rest of the pipeline picks it up automatically.
"""

from __future__ import annotations
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import Literal
import os
from pathlib import Path

logger = logging.getLogger(__name__)


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
    client_id: str  # short slug, e.g. "acme_co"
    client_name: str  # display name
    attribution_model: Literal[
        "last_touch", "first_touch", "linear", "time_decay", "u_shape", "w_shape"
    ] = "last_touch"

    # ── Meta ──────────────────────────────────────────────────────────────────
    meta_enabled: bool = False
    meta_ad_account_id: str = ""  # format: act_123456789

    # Google Ads
    google_ads_enabled: bool = False
    google_ads_customer_id: str = ""  # numeric customer ID, no dashes preferred

    # LinkedIn Ads
    linkedin_ads_enabled: bool = False
    linkedin_ads_account_id: str = ""  # sponsored account ID or URN

    # TikTok Ads
    tiktok_ads_enabled: bool = False
    tiktok_ads_advertiser_id: str = ""  # TikTok advertiser (ad account) ID

    # ── HubSpot ───────────────────────────────────────────────────────────────
    hubspot_enabled: bool = False
    hubspot_pipeline_id: str = ""  # leave blank for default pipeline

    # ── Databricks destination ─────────────────────────────────────────────────
    databricks_schema: str = ""  # e.g. "attribution_acme_co"

    # ── Reporting window ──────────────────────────────────────────────────────
    lookback_days: int = 30

    # ── Alert thresholds ──────────────────────────────────────────────────────
    spend_drop_pct_alert: float = 0.30
    zero_spend_days_allowed: int = 1

    # ── Stripe ────────────────────────────────────────────────────────────────
    stripe_enabled: bool = False
    stripe_account_id: str = ""  # Stripe account ID for reference

    # ── Agency ────────────────────────────────────────────────────────────────
    agency_id: str = ""  # links client to an agency ("" = direct)
    client_report_email: str = ""  # where this client's report gets sent
    client_display_name: str = ""  # name shown in agency dashboard

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

    @property
    def google_ads_refresh_token(self) -> str:
        return _get_secret("GOOGLE_ADS_REFRESH_TOKEN")

    @property
    def linkedin_access_token(self) -> str:
        return _get_secret("LINKEDIN_ACCESS_TOKEN")

    @property
    def tiktok_access_token(self) -> str:
        return _get_secret("TIKTOK_ACCESS_TOKEN")


# ─── CLIENT REGISTRY ──────────────────────────────────────────────────────────

CLIENT_REGISTRY_PATH = Path(
    os.environ.get(
        "ATTRIBUTION_CLIENT_REGISTRY_PATH",
        Path(__file__).with_name("client_registry.local.json"),
    )
)
CLIENT_FIELDS = set(ClientConfig.__dataclass_fields__.keys())


def slugify_client_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "new_client"


def default_client_schema(client_id: str) -> str:
    # main catalog is writable in all Databricks workspaces.
    # Override ATTRIBUTION_CATALOG to use a different catalog (e.g. hive_metastore).
    catalog = os.environ.get("ATTRIBUTION_CATALOG", "workspace")
    return f"{catalog}.attribution_{slugify_client_id(client_id)}"


def _config_from_dict(data: dict) -> ClientConfig:
    payload = {k: v for k, v in data.items() if k in CLIENT_FIELDS}
    if not payload.get("client_id"):
        payload["client_id"] = slugify_client_id(
            payload.get("client_name", "new_client")
        )
    if not payload.get("client_name"):
        payload["client_name"] = payload["client_id"].replace("_", " ").title()
    if not payload.get("databricks_schema"):
        payload["databricks_schema"] = default_client_schema(payload["client_id"])
    return ClientConfig(**payload)


def _registry_backend() -> str:
    """Where custom clients persist: "delta" (Databricks table) or "local" (JSON).

    Defaults to auto-detection: the Delta backend when running inside Databricks
    (App or Job), local JSON otherwise. Override with
    ATTRIBUTION_CLIENT_REGISTRY_BACKEND=delta|local (e.g. delta locally to share
    the production registry).
    """
    mode = os.environ.get("ATTRIBUTION_CLIENT_REGISTRY_BACKEND", "auto").lower()
    if mode in ("delta", "local"):
        return mode
    in_databricks = bool(
        os.environ.get("DATABRICKS_RUNTIME_VERSION")  # notebooks / jobs
        or os.environ.get("DATABRICKS_APP_PORT")  # Databricks Apps
        or os.environ.get("DATABRICKS_CLIENT_ID")  # app service principal
    )
    return "delta" if in_databricks else "local"


# Streamlit reruns call into the registry on every interaction — cache Delta
# reads briefly so the warehouse isn't hit each time. Writes invalidate.
_REGISTRY_CACHE_TTL_SECONDS = 60.0
_registry_cache: dict[str, ClientConfig] | None = None
_registry_cache_at: float = 0.0


def _invalidate_registry_cache() -> None:
    global _registry_cache
    _registry_cache = None


def _load_local_clients() -> dict[str, ClientConfig]:
    if not CLIENT_REGISTRY_PATH.exists():
        return {}
    data = json.loads(CLIENT_REGISTRY_PATH.read_text(encoding="utf-8"))
    raw_clients = data.get("clients", data if isinstance(data, dict) else {})
    clients: dict[str, ClientConfig] = {}
    for client_id, raw in raw_clients.items():
        if not isinstance(raw, dict):
            continue
        config = _config_from_dict({"client_id": client_id, **raw})
        clients[config.client_id] = config
    return clients


def _load_delta_clients() -> dict[str, ClientConfig]:
    from utils.databricks_writer import fetch_client_registry_rows

    clients: dict[str, ClientConfig] = {}
    for row in fetch_client_registry_rows():
        try:
            raw = json.loads(row.get("config_json") or "{}")
        except json.JSONDecodeError:
            logger.warning(
                f"[ClientRegistry] Skipping malformed config_json for "
                f"'{row.get('client_id')}'"
            )
            continue
        config = _config_from_dict({**raw, "client_id": row["client_id"]})
        clients[config.client_id] = config
    return clients


def _load_custom_clients() -> dict[str, ClientConfig]:
    global _registry_cache, _registry_cache_at
    if _registry_backend() == "local":
        return _load_local_clients()
    now = time.monotonic()
    if (
        _registry_cache is not None
        and now - _registry_cache_at < _REGISTRY_CACHE_TTL_SECONDS
    ):
        return dict(_registry_cache)
    try:
        clients = _load_delta_clients()
    except Exception as exc:
        logger.warning(
            f"[ClientRegistry] Delta registry unavailable, falling back to "
            f"local JSON: {exc!r}"
        )
        return _load_local_clients()
    _registry_cache = dict(clients)
    _registry_cache_at = now
    return clients


def _write_custom_clients(clients: dict[str, ClientConfig]) -> None:
    CLIENT_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "clients": {
            client_id: asdict(config) for client_id, config in sorted(clients.items())
        }
    }
    CLIENT_REGISTRY_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _catalog() -> str:
    return os.environ.get("ATTRIBUTION_CATALOG", "workspace")


BASE_CLIENT_REGISTRY: dict[str, ClientConfig] = {
    "demo_client": ClientConfig(
        client_id="demo_client",
        client_name="Demo Client LLC",
        attribution_model="last_touch",
        meta_enabled=True,
        meta_ad_account_id="155554968273585",  # ← replace with real Meta ad account ID
        google_ads_enabled=False,
        google_ads_customer_id="",
        linkedin_ads_enabled=False,
        linkedin_ads_account_id="",
        hubspot_enabled=True,
        hubspot_pipeline_id="",
        databricks_schema=f"{_catalog()}.attribution_demo_client",
        lookback_days=30,
        client_report_email="Zajen@n8ivpromotions.com",
    ),
    "n8iv_promotions": ClientConfig(
        client_id="n8iv_promotions",
        client_name="N8iV Promotions",
        attribution_model="last_touch",
        meta_enabled=True,
        meta_ad_account_id="",  # ← add Meta ad account ID
        hubspot_enabled=True,
        hubspot_pipeline_id="",  # ← add HubSpot pipeline ID if not default
        stripe_enabled=True,
        databricks_schema=f"{_catalog()}.attribution_n8iv_promotions",
        lookback_days=30,
        agency_id="n8iv_promotions",
        client_report_email="zajen@n8ivpromotions.com",
        client_display_name="N8iV Promotions (Internal)",
    ),
}

CLIENT_REGISTRY: dict[str, ClientConfig] = {}


def reload_client_registry() -> dict[str, ClientConfig]:
    CLIENT_REGISTRY.clear()
    CLIENT_REGISTRY.update(BASE_CLIENT_REGISTRY)
    CLIENT_REGISTRY.update(_load_custom_clients())
    return CLIENT_REGISTRY


def save_client_config(config: ClientConfig) -> ClientConfig:
    if _registry_backend() == "delta":
        # No JSON fallback on write: a failed save must surface to the admin
        # rather than land on the app's ephemeral local disk.
        from utils.databricks_writer import upsert_client_registry_entry

        upsert_client_registry_entry(
            config.client_id,
            json.dumps(asdict(config), sort_keys=True),
            is_active=True,
        )
        _invalidate_registry_cache()
    else:
        custom_clients = _load_local_clients()
        custom_clients[config.client_id] = config
        _write_custom_clients(custom_clients)
    reload_client_registry()
    return config


def delete_client_config(client_id: str) -> None:
    if _registry_backend() == "delta":
        if client_id not in _load_custom_clients():
            return
        from utils.databricks_writer import upsert_client_registry_entry

        # Soft delete — keeps the row (and its history) for audit.
        upsert_client_registry_entry(client_id, "{}", is_active=False)
        _invalidate_registry_cache()
        reload_client_registry()
        return
    custom_clients = _load_local_clients()
    if client_id in custom_clients:
        del custom_clients[client_id]
        _write_custom_clients(custom_clients)
        reload_client_registry()


def is_custom_client(client_id: str) -> bool:
    return client_id in _load_custom_clients()


reload_client_registry()


def get_client(client_id: str) -> ClientConfig:
    if client_id not in CLIENT_REGISTRY:
        raise ValueError(
            f"Client '{client_id}' not found. "
            f"Available clients: {list(CLIENT_REGISTRY.keys())}"
        )
    return CLIENT_REGISTRY[client_id]


def list_clients() -> list[str]:
    return list(CLIENT_REGISTRY.keys())
