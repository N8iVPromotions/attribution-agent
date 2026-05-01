"""
agents/ingest/hubspot_connector.py
------------------------------------
Pulls deals + associated contacts with UTM / source properties from HubSpot.
Joins them so every deal row carries its lead's original traffic source.

API Docs: https://developers.hubspot.com/docs/api/crm/deals
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

# ─── CONSTANTS ────────────────────────────────────────────────────────────────
HS_BASE_URL = "https://api.hubapi.com"

# Deal properties to fetch
DEAL_PROPERTIES = [
    "dealname",
    "dealstage",
    "pipeline",
    "amount",
    "closedate",
    "createdate",
    "hs_lastmodifieddate",
    "hubspot_owner_id",
    "hs_deal_stage_probability",
    "closed_won_reason",
]

# Contact properties to fetch (UTM fields are standard in HubSpot)
CONTACT_PROPERTIES = [
    "firstname",
    "lastname",
    "email",
    "hs_analytics_source",              # First touch source (e.g. PAID_SOCIAL)
    "hs_analytics_source_data_1",       # Source detail 1 (e.g. facebook)
    "hs_analytics_source_data_2",       # Source detail 2 (e.g. campaign name)
    "hs_analytics_first_url",
    "hs_analytics_last_url",
    "hs_analytics_num_visits",
    "utm_campaign",                     # Only present if you've set up custom props
    "utm_source",
    "utm_medium",
    "utm_content",
    "createdate",
    "lifecyclestage",
]

# Map HubSpot source values to readable labels
SOURCE_MAP = {
    "PAID_SOCIAL":      "Paid Social",
    "PAID_SEARCH":      "Paid Search",
    "ORGANIC_SEARCH":   "Organic Search",
    "EMAIL_MARKETING":  "Email",
    "SOCIAL_MEDIA":     "Organic Social",
    "REFERRALS":        "Referral",
    "DIRECT_TRAFFIC":   "Direct",
    "OTHER_CAMPAIGNS":  "Other",
    "OFFLINE":          "Offline",
}


# ─── CONNECTOR ────────────────────────────────────────────────────────────────

class HubSpotConnector:
    """
    Fetches HubSpot CRM data and returns a clean pandas DataFrame.

    Usage:
        connector = HubSpotConnector()
        df = connector.pull_deals(lookback_days=30, pipeline_id="")
    """

    def __init__(self, access_token: str = "") -> None:
        self.access_token = access_token
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        })

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _post(self, endpoint: str, payload: dict) -> dict:
        url = f"{HS_BASE_URL}{endpoint}"
        response = self.session.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        url = f"{HS_BASE_URL}{endpoint}"
        response = self.session.get(url, params=params or {}, timeout=30)
        response.raise_for_status()
        return response.json()

    # ── DEALS ──────────────────────────────────────────────────────────────────

    def _search_deals(
        self,
        after_timestamp_ms: int,
        pipeline_id: str = "",
    ) -> list[dict]:
        """Search deals modified after a given timestamp using the CRM Search API."""
        filters = [
            {
                "propertyName": "hs_lastmodifieddate",
                "operator": "GTE",
                "value": str(after_timestamp_ms),
            }
        ]
        if pipeline_id:
            filters.append({
                "propertyName": "pipeline",
                "operator": "EQ",
                "value": pipeline_id,
            })

        deals = []
        after_cursor = None

        while True:
            payload: dict[str, Any] = {
                "filterGroups": [{"filters": filters}],
                "properties": DEAL_PROPERTIES,
                "associations": ["contacts"],
                "limit": 100,
                "sorts": [{"propertyName": "hs_lastmodifieddate", "direction": "DESCENDING"}],
            }
            if after_cursor:
                payload["after"] = after_cursor

            data = self._post("/crm/v3/objects/deals/search", payload)
            deals.extend(data.get("results", []))

            paging = data.get("paging", {})
            after_cursor = paging.get("next", {}).get("after")
            if not after_cursor:
                break

        logger.info(f"[HubSpot] Retrieved {len(deals)} deals")
        return deals

    # ── CONTACTS ───────────────────────────────────────────────────────────────

    def _batch_get_contacts(self, contact_ids: list[str]) -> dict[str, dict]:
        """Batch fetch contacts by ID. Returns a dict keyed by contact ID."""
        if not contact_ids:
            return {}

        # HubSpot batch read limit is 100
        contact_map = {}
        for i in range(0, len(contact_ids), 100):
            batch = contact_ids[i : i + 100]
            payload = {
                "inputs": [{"id": cid} for cid in batch],
                "properties": CONTACT_PROPERTIES,
            }
            data = self._post("/crm/v3/objects/contacts/batch/read", payload)
            for contact in data.get("results", []):
                contact_map[contact["id"]] = contact.get("properties", {})

        logger.info(f"[HubSpot] Fetched {len(contact_map)} associated contacts")
        return contact_map

    # ── MAIN PULL ──────────────────────────────────────────────────────────────

    def pull_deals(
        self,
        lookback_days: int = 30,
        pipeline_id: str = "",
    ) -> pd.DataFrame:
        """
        Pull all deals modified in the last N days + their associated
        contact source data. Returns a single flat DataFrame.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        cutoff_ms = int(cutoff.timestamp() * 1000)

        logger.info(
            f"[HubSpot] Pulling deals modified since "
            f"{cutoff.strftime('%Y-%m-%d')} | pipeline='{pipeline_id or 'all'}'"
        )

        deals = self._search_deals(cutoff_ms, pipeline_id)
        if not deals:
            logger.warning("[HubSpot] No deals returned")
            return pd.DataFrame()

        # Collect all unique contact IDs across all deals
        contact_ids: list[str] = []
        for deal in deals:
            associations = deal.get("associations", {})
            for contact in associations.get("contacts", {}).get("results", []):
                contact_ids.append(contact["id"])
        contact_ids = list(set(contact_ids))

        contact_map = self._batch_get_contacts(contact_ids)
        return self._normalize(deals, contact_map)

    # ── NORMALIZATION ──────────────────────────────────────────────────────────

    def _normalize(
        self, deals: list[dict], contact_map: dict[str, dict]
    ) -> pd.DataFrame:
        rows = []
        for deal in deals:
            props = deal.get("properties", {})

            # Get the first associated contact (primary contact)
            contact_id = None
            associations = deal.get("associations", {})
            contacts_list = associations.get("contacts", {}).get("results", [])
            if contacts_list:
                contact_id = contacts_list[0]["id"]

            contact_props = contact_map.get(contact_id, {}) if contact_id else {}

            raw_source = contact_props.get("hs_analytics_source", "")
            row = {
                # Deal fields
                "deal_id":          deal["id"],
                "deal_name":        props.get("dealname"),
                "deal_stage":       props.get("dealstage"),
                "pipeline":         props.get("pipeline"),
                "amount":           _safe_float(props.get("amount")),
                "close_date":       _safe_date(props.get("closedate")),
                "create_date":      _safe_date(props.get("createdate")),
                "stage_probability":_safe_float(props.get("hs_deal_stage_probability")),

                # Contact / Attribution fields
                "contact_id":       contact_id,
                "contact_email":    contact_props.get("email"),
                "hs_source":        raw_source,
                "hs_source_label":  SOURCE_MAP.get(raw_source, raw_source),
                "hs_source_detail_1": contact_props.get("hs_analytics_source_data_1"),
                "hs_source_detail_2": contact_props.get("hs_analytics_source_data_2"),
                "utm_campaign":     contact_props.get("utm_campaign"),
                "utm_source":       contact_props.get("utm_source"),
                "utm_medium":       contact_props.get("utm_medium"),
                "utm_content":      contact_props.get("utm_content"),
                "first_page_url":   contact_props.get("hs_analytics_first_url"),
                "lifecycle_stage":  contact_props.get("lifecyclestage"),
                "lead_create_date": _safe_date(contact_props.get("createdate")),

                # Meta
                "source":           "hubspot",
            }
            rows.append(row)

        df = pd.DataFrame(rows)

        # Derived: days from lead creation to deal creation
        if "lead_create_date" in df.columns and "create_date" in df.columns:
            try:
                df["create_date"] = pd.to_datetime(df["create_date"], errors="coerce")
                df["lead_create_date"] = pd.to_datetime(df["lead_create_date"], errors="coerce")
                df["days_to_deal"] = (df["create_date"] - df["lead_create_date"]).dt.days
            except Exception:
                df["days_to_deal"] = None

        logger.info(f"[HubSpot] Normalized DataFrame shape: {df.shape}")
        return df


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _safe_float(val: Any) -> float | None:
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def _safe_date(val: Any) -> pd.Timestamp | None:
    try:
        return pd.to_datetime(val, unit="ms") if val else None
    except Exception:
        return None


# ─── CONVENIENCE FUNCTION (called by Prefect flow) ────────────────────────────

def pull_hubspot_data(
    lookback_days: int = 30,
    pipeline_id: str = "",
    access_token: str = "",
) -> pd.DataFrame:
    connector = HubSpotConnector(access_token=access_token)
    return connector.pull_deals(
        lookback_days=lookback_days,
        pipeline_id=pipeline_id,
    )
