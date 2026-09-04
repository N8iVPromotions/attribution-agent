"""
agents/ingest/hubspot_connector.py
------------------------------------
Pulls deals and HubSpot's calculated deal-level original traffic source.
Associated contacts are used only for diagnostic fields and lead timing.

API Docs: https://developers.hubspot.com/docs/api/crm/deals
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import pyarrow as pa
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from agents.ingest.batches import batches_to_dataframe, records_to_batches
from utils.raw_archive import archive_raw_page

# ─── CONSTANTS ────────────────────────────────────────────────────────────────
HS_BASE_URL = "https://api.hubapi.com"

# Deal properties to fetch
DEAL_PROPERTIES = [
    "dealname",
    "dealstage",
    "pipeline",
    "amount",
    "deal_currency_code",
    "closedate",
    "createdate",
    "hs_lastmodifieddate",
    "hubspot_owner_id",
    "hs_deal_stage_probability",
    "closed_won_reason",
    # HubSpot-calculated deal attribution uses the associated contact with the
    # earliest activity (or its company fallback), avoiding arbitrary contact
    # association order.
    "hs_analytics_source",
    "hs_analytics_source_data_1",
    "hs_analytics_source_data_2",
]

# Contact properties used only for diagnostics. Custom UTM properties are
# deliberately not requested: they do not exist in every portal, and an
# arbitrary associated contact must never determine a deal's attribution.
CONTACT_PROPERTIES = [
    "firstname",
    "lastname",
    "email",
    "hs_analytics_source",  # First touch source (e.g. PAID_SOCIAL)
    "hs_analytics_source_data_1",  # Source detail 1 (e.g. facebook)
    "hs_analytics_source_data_2",  # Source detail 2 (e.g. campaign name)
    "hs_analytics_first_url",
    "hs_analytics_last_url",
    "hs_analytics_num_visits",
    "createdate",
    "lifecyclestage",
]

# Map HubSpot source values to readable labels
SOURCE_MAP = {
    "PAID_SOCIAL": "Paid Social",
    "PAID_SEARCH": "Paid Search",
    "ORGANIC_SEARCH": "Organic Search",
    "EMAIL_MARKETING": "Email",
    "SOCIAL_MEDIA": "Organic Social",
    "REFERRALS": "Referral",
    "DIRECT_TRAFFIC": "Direct",
    "OTHER_CAMPAIGNS": "Other",
    "OFFLINE": "Offline",
}


# ─── CONNECTOR ────────────────────────────────────────────────────────────────


class HubSpotConnector:
    """
    Fetches HubSpot CRM data and returns a clean pandas DataFrame.

    Usage:
        connector = HubSpotConnector()
        df = connector.pull_deals(lookback_days=30, pipeline_id="")
    """

    def __init__(
        self,
        access_token: str = "",
        client_id: str = "",
        run_id: str = "",
    ) -> None:
        self.access_token = access_token
        self.client_id = client_id
        self.run_id = run_id
        self.page_number = 0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _post(self, endpoint: str, payload: dict) -> dict:
        url = f"{HS_BASE_URL}{endpoint}"
        response = self.session.post(url, json=payload, timeout=30)
        response.raise_for_status()
        self._archive_response(endpoint, response.content)
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
        self._archive_response(endpoint, response.content)
        return response.json()

    def _archive_response(self, endpoint: str, body: bytes) -> None:
        self.page_number += 1
        archive_raw_page(
            source="hubspot",
            client_id=self.client_id,
            run_id=self.run_id,
            page_number=self.page_number,
            body=body,
            endpoint=endpoint.strip("/").replace("/", "-"),
        )

    # ── DEALS ──────────────────────────────────────────────────────────────────

    def _search_deals(
        self,
        after_timestamp_ms: int,
        pipeline_id: str = "",
    ) -> list[dict]:
        """Search deals modified after a given timestamp using the CRM Search API."""
        deals = [
            deal
            for page in self._iter_deal_pages(after_timestamp_ms, pipeline_id)
            for deal in page
        ]
        logger.info(f"[HubSpot] Retrieved {len(deals)} deals")
        return deals

    def _iter_deal_pages(
        self,
        after_timestamp_ms: int,
        pipeline_id: str = "",
    ) -> Iterator[list[dict]]:
        filters = [
            {
                "propertyName": "hs_lastmodifieddate",
                "operator": "GTE",
                "value": str(after_timestamp_ms),
            }
        ]
        if pipeline_id:
            filters.append(
                {
                    "propertyName": "pipeline",
                    "operator": "EQ",
                    "value": pipeline_id,
                }
            )

        after_cursor = None

        while True:
            payload: dict[str, Any] = {
                "filterGroups": [{"filters": filters}],
                "properties": DEAL_PROPERTIES,
                "associations": ["contacts"],
                "limit": 100,
                "sorts": [
                    {"propertyName": "hs_lastmodifieddate", "direction": "DESCENDING"}
                ],
            }
            if after_cursor:
                payload["after"] = after_cursor

            data = self._post("/crm/v3/objects/deals/search", payload)
            results = data.get("results", [])
            if results:
                yield results

            paging = data.get("paging", {})
            after_cursor = paging.get("next", {}).get("after")
            if not after_cursor:
                break

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
        return batches_to_dataframe(
            self.iter_deal_batches(
                lookback_days=lookback_days,
                pipeline_id=pipeline_id,
            )
        )

    def iter_deal_batches(
        self,
        lookback_days: int = 30,
        pipeline_id: str = "",
    ) -> Iterator[pa.RecordBatch]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        cutoff_ms = int(cutoff.timestamp() * 1000)
        logger.info(
            f"[HubSpot] Pulling deals modified since "
            f"{cutoff.strftime('%Y-%m-%d')} | pipeline='{pipeline_id or 'all'}'"
        )

        def iter_records():
            for deals in self._iter_deal_pages(cutoff_ms, pipeline_id):
                contact_ids = {
                    contact["id"]
                    for deal in deals
                    for contact in deal.get("associations", {})
                    .get("contacts", {})
                    .get("results", [])
                }
                contact_map = self._batch_get_contacts(sorted(contact_ids))
                yield from self._normalize_rows(deals, contact_map)

        total_rows = 0
        for batch in records_to_batches(iter_records()):
            total_rows += batch.num_rows
            yield batch
        logger.info(f"[HubSpot] Retrieved {total_rows} deals")

    # ── NORMALIZATION ──────────────────────────────────────────────────────────

    def _normalize(
        self, deals: list[dict], contact_map: dict[str, dict]
    ) -> pd.DataFrame:
        return pd.DataFrame(self._normalize_rows(deals, contact_map))

    def _normalize_rows(
        self, deals: list[dict], contact_map: dict[str, dict]
    ) -> Iterator[dict]:
        for deal in deals:
            props = deal.get("properties", {})
            close_datetime = _safe_date(props.get("closedate"))

            # Retain one deterministic associated contact for diagnostic fields
            # only. Revenue attribution uses HubSpot's deal-level calculated
            # source properties below, never association order.
            contact_id = None
            associations = deal.get("associations", {})
            contacts_list = associations.get("contacts", {}).get("results", [])
            if contacts_list:
                contact_id = min(str(contact["id"]) for contact in contacts_list)

            contact_props = contact_map.get(contact_id, {}) if contact_id else {}

            raw_source = props.get("hs_analytics_source", "")
            source_key = str(raw_source or "").strip().upper()
            source_detail_1 = props.get("hs_analytics_source_data_1")
            source_detail_2 = props.get("hs_analytics_source_data_2")

            # Preserve the legacy normalized UTM-shaped columns using only the
            # same trustworthy deal-level identity used by warehouse SQL.
            # HubSpot defines Paid Search detail 1 as campaign name and Paid
            # Social detail 1/2 as network/campaign respectively.
            if source_key == "PAID_SEARCH":
                derived_utm_source = "google"
                derived_utm_campaign = source_detail_1
                derived_utm_medium = "paid_search"
            elif source_key == "PAID_SOCIAL":
                derived_utm_source = source_detail_1
                derived_utm_campaign = source_detail_2
                derived_utm_medium = "paid_social"
            else:
                derived_utm_source = None
                derived_utm_campaign = None
                derived_utm_medium = None
            row = {
                # Deal fields
                "deal_id": deal["id"],
                "deal_name": props.get("dealname"),
                "deal_stage": props.get("dealstage"),
                "pipeline": props.get("pipeline"),
                "amount": _safe_float(props.get("amount")),
                "deal_currency_code": str(
                    props.get("deal_currency_code") or ""
                ).upper(),
                # Preserve the legacy DATE field while retaining the exact UTC
                # instant for business-timezone reporting boundaries.
                "close_date": close_datetime,
                "close_datetime": close_datetime,
                "create_date": _safe_date(props.get("createdate")),
                "stage_probability": _safe_float(
                    props.get("hs_deal_stage_probability")
                ),
                # Contact / Attribution fields
                "contact_id": contact_id,
                "contact_email": contact_props.get("email"),
                "hs_source": raw_source,
                "hs_source_label": SOURCE_MAP.get(raw_source, raw_source),
                "hs_source_detail_1": source_detail_1,
                "hs_source_detail_2": source_detail_2,
                "utm_campaign": derived_utm_campaign,
                "utm_source": derived_utm_source,
                "utm_medium": derived_utm_medium,
                "utm_content": None,
                "first_page_url": contact_props.get("hs_analytics_first_url"),
                "lifecycle_stage": contact_props.get("lifecyclestage"),
                "lead_create_date": _safe_date(contact_props.get("createdate")),
                # Meta
                "source": "hubspot",
            }
            create_date = row["create_date"]
            lead_create_date = row["lead_create_date"]
            row["days_to_deal"] = (
                (create_date - lead_create_date).days
                if create_date is not None and lead_create_date is not None
                else None
            )
            yield row


# ─── HELPERS ──────────────────────────────────────────────────────────────────


def _safe_float(val: Any) -> float | None:
    try:
        return float(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def _safe_date(val: Any) -> pd.Timestamp | None:
    """Parse HubSpot ISO timestamps and legacy epoch-millisecond values."""
    if val is None or isinstance(val, bool):
        return None

    value = val
    unit = None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            value = int(value)
            unit = "ms"
        except ValueError:
            pass
    elif isinstance(value, (int, float)):
        unit = "ms"

    try:
        parsed = pd.to_datetime(value, unit=unit, errors="coerce", utc=True)
    except Exception:
        return None
    if not isinstance(parsed, pd.Timestamp) or pd.isna(parsed):
        return None
    return parsed.tz_convert(None)


# ─── CONVENIENCE FUNCTION (called by Prefect flow) ────────────────────────────


def pull_hubspot_data(
    lookback_days: int = 30,
    pipeline_id: str = "",
    access_token: str = "",
    client_id: str = "",
    run_id: str = "",
) -> pd.DataFrame:
    return batches_to_dataframe(
        iter_hubspot_batches(
            lookback_days=lookback_days,
            pipeline_id=pipeline_id,
            access_token=access_token,
            client_id=client_id,
            run_id=run_id,
        )
    )


def iter_hubspot_batches(
    lookback_days: int = 30,
    pipeline_id: str = "",
    access_token: str = "",
    client_id: str = "",
    run_id: str = "",
) -> Iterator[pa.RecordBatch]:
    connector = HubSpotConnector(
        access_token=access_token,
        client_id=client_id,
        run_id=run_id,
    )
    yield from connector.iter_deal_batches(
        lookback_days=lookback_days,
        pipeline_id=pipeline_id,
    )
