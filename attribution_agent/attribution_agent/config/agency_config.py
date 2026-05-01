"""
config/agency_config.py
-----------------------
Agency tier configuration. An agency manages multiple clients under one umbrella.
Agency reports are white-labeled with agency branding.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgencyConfig:
    agency_id: str                          # short slug, e.g. "acme_media"
    agency_name: str                        # display name, e.g. "Acme Media Group"
    client_ids: list[str] = field(default_factory=list)  # client_ids from CLIENT_REGISTRY

    # ── Branding ──────────────────────────────────────────────────────────────
    brand_color: str = "1A1A1A"            # hex color (no #) for email header
    brand_logo_url: str = ""               # URL to agency logo image
    sender_name: str = ""                  # e.g. "Acme Media Analytics"
    sender_email: str = ""                 # overrides GMAIL_SENDER env var
    reply_to: str = ""                     # where client replies are routed

    # ── Dashboard ─────────────────────────────────────────────────────────────
    powerbi_workspace_url: str = ""        # agency Power BI workspace link


# ─── AGENCY REGISTRY ──────────────────────────────────────────────────────────

AGENCY_REGISTRY: dict[str, AgencyConfig] = {
    "demo_agency": AgencyConfig(
        agency_id="demo_agency",
        agency_name="Demo Agency Group",
        client_ids=["demo_client"],
        brand_color="1A1A1A",
        sender_name="Demo Agency Analytics",
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


def get_agency(agency_id: str) -> AgencyConfig:
    if agency_id not in AGENCY_REGISTRY:
        raise ValueError(
            f"Agency '{agency_id}' not found. "
            f"Available agencies: {list(AGENCY_REGISTRY.keys())}"
        )
    return AGENCY_REGISTRY[agency_id]


def list_agencies() -> list[str]:
    return list(AGENCY_REGISTRY.keys())
