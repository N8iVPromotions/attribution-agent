"""
config/agency_config.py
-----------------------
Agency tier configuration. An agency manages multiple clients under one umbrella.
Agency reports are white-labeled with agency branding.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from config.client_config import _registry_backend

logger = logging.getLogger(__name__)


@dataclass
class AgencyConfig:
    agency_id: str  # short slug, e.g. "acme_media"
    agency_name: str  # display name, e.g. "Acme Media Group"
    client_ids: list[str] = field(
        default_factory=list
    )  # client_ids from CLIENT_REGISTRY

    # ── Branding ──────────────────────────────────────────────────────────────
    brand_color: str = "1A1A1A"  # hex color (no #) for email header
    brand_logo_url: str = ""  # URL to agency logo image
    sender_name: str = ""  # e.g. "Acme Media Analytics"
    sender_email: str = ""  # overrides GMAIL_SENDER env var
    reply_to: str = ""  # where client replies are routed

    # ── Dashboard ─────────────────────────────────────────────────────────────
    powerbi_workspace_url: str = ""  # agency Power BI workspace link


# ─── AGENCY REGISTRY ──────────────────────────────────────────────────────────

BASE_AGENCY_REGISTRY: dict[str, AgencyConfig] = {
    "demo_agency": AgencyConfig(
        agency_id="demo_agency",
        agency_name="Demo Agency Group",
        client_ids=["demo_client"],
        brand_color="1A1A1A",
        sender_name="Demo Agency Analytics",
    ),
    "n8iv_promotions": AgencyConfig(
        agency_id="n8iv_promotions",
        agency_name="N8iV Promotions",
        client_ids=["n8iv_promotions"],
        brand_color="2563EB",
        sender_name="N8iV Promotions Analytics",
        sender_email="zajen@n8ivpromotions.com",
        reply_to="zajen@n8ivpromotions.com",
    ),
    # "acme_media": AgencyConfig(
    #     agency_id="acme_media",
    #     agency_name="Acme Media Group",
    #     client_ids=["client_a", "client_b"],
    #     brand_color="2B5EA7",
    #     brand_logo_url="https://cdn.acmemedia.com/logo.png",
    #     sender_name="Acme Media Analytics",
    #     sender_email="reports@acmemedia.com",
    #     reply_to="analytics@acmemedia.com",
    # ),
}

AGENCY_REGISTRY: dict[str, AgencyConfig] = {}
_REGISTRY_CACHE_TTL_SECONDS = 60.0
_registry_cache: dict[str, AgencyConfig] | None = None
_registry_cache_at = 0.0


def _config_from_row(row: dict) -> AgencyConfig:
    try:
        payload = json.loads(row.get("config_json") or "{}")
    except json.JSONDecodeError:
        payload = {}
    fields = AgencyConfig.__dataclass_fields__
    config = {key: value for key, value in payload.items() if key in fields}
    config["agency_id"] = row["agency_id"]
    config["agency_name"] = (
        row.get("agency_name") or config.get("agency_name") or row["agency_id"]
    )
    return AgencyConfig(**config)


def _load_delta_agencies() -> dict[str, AgencyConfig]:
    from utils.databricks_writer import fetch_agency_registry_rows

    return {
        row["agency_id"]: _config_from_row(row) for row in fetch_agency_registry_rows()
    }


def _load_custom_agencies() -> dict[str, AgencyConfig]:
    global _registry_cache, _registry_cache_at
    if _registry_backend() != "delta":
        return {}
    now = time.monotonic()
    if (
        _registry_cache is not None
        and now - _registry_cache_at < _REGISTRY_CACHE_TTL_SECONDS
    ):
        return dict(_registry_cache)
    try:
        agencies = _load_delta_agencies()
    except Exception as exc:
        logger.warning("[AgencyRegistry] Delta registry unavailable: %r", exc)
        return {}
    _registry_cache = dict(agencies)
    _registry_cache_at = now
    return agencies


def reload_agency_registry() -> dict[str, AgencyConfig]:
    AGENCY_REGISTRY.clear()
    AGENCY_REGISTRY.update(BASE_AGENCY_REGISTRY)
    AGENCY_REGISTRY.update(_load_custom_agencies())
    return AGENCY_REGISTRY


def save_agency_config(config: AgencyConfig) -> AgencyConfig:
    if _registry_backend() != "delta":
        raise RuntimeError(
            "Dynamic agency persistence requires the Delta registry backend"
        )
    from dataclasses import asdict

    from utils.databricks_writer import upsert_agency_registry_entry

    upsert_agency_registry_entry(
        config.agency_id,
        config.agency_name,
        json.dumps(asdict(config), sort_keys=True),
        is_active=True,
    )
    global _registry_cache
    _registry_cache = None
    reload_agency_registry()
    return config


reload_agency_registry()


def get_agency(agency_id: str) -> AgencyConfig:
    if agency_id not in AGENCY_REGISTRY:
        raise ValueError(
            f"Agency '{agency_id}' not found. "
            f"Available agencies: {list(AGENCY_REGISTRY.keys())}"
        )
    return AGENCY_REGISTRY[agency_id]


def list_agencies() -> list[str]:
    return list(AGENCY_REGISTRY.keys())
