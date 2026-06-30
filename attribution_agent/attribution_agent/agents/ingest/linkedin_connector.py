"""
LinkedIn Ads connector.

Uses LinkedIn Marketing API ad analytics. Account IDs may be configured either
as a numeric ID or as an URN suffix; this connector normalizes to sponsored
account URNs for the API call.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

LINKEDIN_BASE_URL = "https://api.linkedin.com/rest"
PAGE_SIZE = 1000


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
def _get(url: str, headers: dict, params: dict) -> dict:
    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def pull_linkedin_ads_data(
    account_id: str,
    lookback_days: int = 30,
    access_token: str = "",
) -> pd.DataFrame:
    if not account_id:
        return pd.DataFrame()
    if not access_token:
        raise ValueError("LINKEDIN_ACCESS_TOKEN is required for LinkedIn Ads ingestion")

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=lookback_days - 1)
    account_urn = (
        account_id
        if account_id.startswith("urn:li:sponsoredAccount:")
        else f"urn:li:sponsoredAccount:{account_id}"
    )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": "202405",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    logger.info(
        f"[LinkedIn] Pulling analytics for {account_id} | {start_date} to {end_date}"
    )
    base_params = {
        "q": "analytics",
        "pivot": "CAMPAIGN",
        "timeGranularity": "DAILY",
        "accounts": f"List({account_urn})",
        "dateRange.start.year": start_date.year,
        "dateRange.start.month": start_date.month,
        "dateRange.start.day": start_date.day,
        "dateRange.end.year": end_date.year,
        "dateRange.end.month": end_date.month,
        "dateRange.end.day": end_date.day,
        "fields": "dateRange,pivotValues,impressions,clicks,costInLocalCurrency,externalWebsiteConversions",
        "count": PAGE_SIZE,
    }

    rows = []
    start_index = 0
    while True:
        params = {**base_params, "start": start_index}
        data = _get(f"{LINKEDIN_BASE_URL}/adAnalytics", headers=headers, params=params)
        elements = data.get("elements", [])
        for item in elements:
            start = item.get("dateRange", {}).get("start", {})
            campaign_urn = (item.get("pivotValues") or [""])[0]
            campaign_id = campaign_urn.split(":")[-1] if campaign_urn else ""
            rows.append(
                {
                    "date": f"{start.get('year')}-{start.get('month'):02d}-{start.get('day'):02d}",
                    "account_id": account_id,
                    "campaign_id": campaign_id,
                    "campaign_name": campaign_id,
                    "creative_id": "",
                    "creative_name": "",
                    "spend": float(item.get("costInLocalCurrency") or 0),
                    "impressions": int(item.get("impressions") or 0),
                    "clicks": int(item.get("clicks") or 0),
                    "conversions": float(item.get("externalWebsiteConversions") or 0),
                }
            )
        if len(elements) < PAGE_SIZE:
            break
        start_index += PAGE_SIZE
    logger.info(f"[LinkedIn] Retrieved {len(rows)} rows")

    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df
