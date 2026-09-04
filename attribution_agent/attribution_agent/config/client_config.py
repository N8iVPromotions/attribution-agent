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
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from collections.abc import Iterable
from typing import Literal
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_HUBSPOT_CLOSED_WON_STAGE_IDS: tuple[str, ...] = ("closedwon", "won")
SUPPORTED_REPORTING_CURRENCY = "USD"
_DATABRICKS_SCHEMA_PATTERN = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*"
)


def normalize_reporting_currency(value: str) -> str:
    currency = str(value or "").strip().upper()
    if currency != SUPPORTED_REPORTING_CURRENCY:
        raise ValueError(
            "Production reporting currently supports USD only; configure every "
            "ad, CRM, and payment account in USD before enabling delivery"
        )
    return currency


def normalize_databricks_schema(value: str) -> str:
    schema = str(value or "").strip()
    if schema and not _DATABRICKS_SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Unsafe Databricks schema identifier: {schema!r}")
    return schema


def normalize_hubspot_closed_won_stage_ids(
    values: Iterable[str],
) -> tuple[str, ...]:
    """Return unique HubSpot stage IDs in the same exact-key form used by SQL."""
    if isinstance(values, (str, bytes)):
        raw_values = (str(values),)
    else:
        try:
            raw_values = tuple(values)
        except TypeError:
            raise ValueError(
                "HubSpot closed-won stage IDs must be a collection"
            ) from None
    if len(raw_values) > 20:
        raise ValueError("No more than 20 HubSpot closed-won stage IDs are allowed")

    normalized: list[str] = []
    for raw_value in raw_values:
        if not isinstance(raw_value, str):
            raise ValueError("HubSpot closed-won stage IDs must be strings")
        if len(raw_value) > 100:
            raise ValueError(
                "HubSpot closed-won stage IDs cannot exceed 100 characters"
            )
        stage_key = re.sub(r"[^a-z0-9]+", "", raw_value.strip().lower())
        if not stage_key:
            raise ValueError("HubSpot closed-won stage IDs cannot be blank")
        if stage_key not in normalized:
            normalized.append(stage_key)

    if not normalized:
        raise ValueError("At least one HubSpot closed-won stage ID is required")
    return tuple(normalized)


def _get_secret(env_key: str, secret_ref: str = "") -> str:
    """Resolve one tenant secret without silently crossing account boundaries."""
    from utils.secrets import get_secret, read_secret

    if secret_ref:
        return read_secret(secret_ref, default="")
    allow_global = (
        os.environ.get("ARIE_ALLOW_GLOBAL_CONNECTOR_CREDENTIALS", "false")
        .strip()
        .lower()
        == "true"
    )
    return get_secret(env_key) if allow_global else ""


def stripe_history_start_datetime(
    value: str,
    *,
    now: datetime | None = None,
) -> datetime:
    """Validate a canonical Stripe history date and return UTC midnight."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("stripe_history_start_date is required when Stripe is enabled")
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        raise ValueError(
            "stripe_history_start_date must use a valid YYYY-MM-DD date"
        ) from None
    if parsed.isoformat() != raw:
        raise ValueError("stripe_history_start_date must use a valid YYYY-MM-DD date")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if parsed > current.astimezone(timezone.utc).date():
        raise ValueError("stripe_history_start_date cannot be in the future")
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)


@dataclass
class ClientConfig:
    client_id: str  # short slug, e.g. "acme_co"
    client_name: str  # display name
    attribution_model: Literal[
        "last_touch", "first_touch", "linear", "time_decay", "u_shape", "w_shape"
    ] = "last_touch"
    reporting_currency: str = SUPPORTED_REPORTING_CURRENCY

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
    hubspot_closed_won_stage_ids: tuple[str, ...] = DEFAULT_HUBSPOT_CLOSED_WON_STAGE_IDS

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
    stripe_history_start_date: str = ""  # earliest possible payment, YYYY-MM-DD

    # ── Agency ────────────────────────────────────────────────────────────────
    agency_id: str = ""  # links client to an agency ("" = direct)
    client_report_email: str = ""  # where this client's report gets sent
    client_display_name: str = ""  # name shown in agency dashboard

    # Client-specific Secret Manager IDs. Raw values are never stored here.
    meta_access_token_secret_name: str = ""
    google_ads_refresh_token_secret_name: str = ""
    linkedin_access_token_secret_name: str = ""
    tiktok_access_token_secret_name: str = ""
    hubspot_access_token_secret_name: str = ""
    stripe_secret_key_secret_name: str = ""

    # Optional ISO dates used for proactive operator alerts.
    meta_token_expires_at: str = ""
    google_ads_token_expires_at: str = ""
    linkedin_token_expires_at: str = ""
    tiktok_token_expires_at: str = ""
    hubspot_token_expires_at: str = ""
    stripe_token_expires_at: str = ""

    def __post_init__(self) -> None:
        self.reporting_currency = normalize_reporting_currency(self.reporting_currency)
        self.databricks_schema = normalize_databricks_schema(self.databricks_schema)
        if not 7 <= int(self.lookback_days) <= 365:
            raise ValueError("lookback_days must be between 7 and 365")
        self.hubspot_closed_won_stage_ids = normalize_hubspot_closed_won_stage_ids(
            self.hubspot_closed_won_stage_ids
        )

    # Tenant secrets never fall back to another credential. Global connector
    # credentials require the explicit legacy deployment opt-in.
    @property
    def meta_access_token(self) -> str:
        return _get_secret("META_ACCESS_TOKEN", self.meta_access_token_secret_name)

    @property
    def hubspot_access_token(self) -> str:
        return _get_secret(
            "HUBSPOT_ACCESS_TOKEN", self.hubspot_access_token_secret_name
        )

    @property
    def stripe_secret_key(self) -> str:
        return _get_secret("STRIPE_SECRET_KEY", self.stripe_secret_key_secret_name)

    @property
    def google_ads_refresh_token(self) -> str:
        return _get_secret(
            "GOOGLE_ADS_REFRESH_TOKEN", self.google_ads_refresh_token_secret_name
        )

    @property
    def linkedin_access_token(self) -> str:
        return _get_secret(
            "LINKEDIN_ACCESS_TOKEN", self.linkedin_access_token_secret_name
        )

    @property
    def tiktok_access_token(self) -> str:
        return _get_secret("TIKTOK_ACCESS_TOKEN", self.tiktok_access_token_secret_name)


# ─── CLIENT REGISTRY ──────────────────────────────────────────────────────────

CLIENT_REGISTRY_PATH = Path(
    os.environ.get(
        "ATTRIBUTION_CLIENT_REGISTRY_PATH",
        Path(__file__).with_name("client_registry.local.json"),
    )
)
CLIENT_FIELDS = set(ClientConfig.__dataclass_fields__.keys())
CLIENT_SECRET_VALUE_FIELDS = {
    "meta_access_token": ("meta_access_token_secret_name", "meta-access-token"),
    "google_ads_refresh_token": (
        "google_ads_refresh_token_secret_name",
        "google-ads-refresh-token",
    ),
    "linkedin_access_token": (
        "linkedin_access_token_secret_name",
        "linkedin-access-token",
    ),
    "tiktok_access_token": ("tiktok_access_token_secret_name", "tiktok-access-token"),
    "hubspot_access_token": (
        "hubspot_access_token_secret_name",
        "hubspot-access-token",
    ),
    "stripe_secret_key": ("stripe_secret_key_secret_name", "stripe-secret-key"),
}


def attach_client_secret_values(
    config: ClientConfig,
    secret_values: dict[str, str],
) -> ClientConfig:
    """Write supplied raw credentials and return config with secret refs set."""
    from utils.secrets import secret_id_for_client, write_secret

    updates: dict[str, str] = {}
    for value_key, raw_value in secret_values.items():
        if value_key not in CLIENT_SECRET_VALUE_FIELDS:
            continue
        secret_value = (raw_value or "").strip()
        if not secret_value:
            continue
        field_name, credential_key = CLIENT_SECRET_VALUE_FIELDS[value_key]
        secret_id = secret_id_for_client(config.client_id, credential_key)
        updates[field_name] = write_secret(secret_id, secret_value)
    if not updates:
        return config
    return replace(config, **updates)


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
        reporting_currency="USD",
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
        reporting_currency="USD",
        meta_enabled=True,
        meta_ad_account_id="155554968273585",
        hubspot_enabled=True,
        hubspot_pipeline_id="",  # ← add HubSpot pipeline ID if not default
        stripe_enabled=True,
        stripe_history_start_date="2010-01-01",
        databricks_schema=f"{_catalog()}.attribution_n8iv_promotions",
        lookback_days=90,
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
