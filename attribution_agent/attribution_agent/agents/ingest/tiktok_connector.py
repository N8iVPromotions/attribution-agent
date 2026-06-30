"""
TikTok Ads connector.

Uses the TikTok Marketing API synchronous reporting endpoint to pull daily
campaign-level metrics. Spend is returned in the ad account's currency. The
connector normalizes nothing here — `ad_sources.normalize_tiktok_ads` maps the
raw shape onto the shared normalized schema.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

TIKTOK_BASE_URL = "https://business-api.tiktok.com/open_api/v1.3"
PAGE_SIZE = 1000


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
def _get(url: str, headers: dict, params: dict) -> dict:
    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    # TikTok returns HTTP 200 with a non-zero `code` on API errors.
    if payload.get("code", 0) != 0:
        raise RuntimeError(
            f"TikTok API error {payload.get('code')}: {payload.get('message')}"
        )
    return payload


def pull_tiktok_ads_data(
    advertiser_id: str,
    lookback_days: int = 30,
    access_token: str = "",
) -> pd.DataFrame:
    """Pull daily campaign-level TikTok Ads metrics for the lookback window."""
    if not advertiser_id:
        return pd.DataFrame()
    if not access_token:
        raise ValueError("TIKTOK_ACCESS_TOKEN is required for TikTok Ads ingestion")

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=lookback_days - 1)

    headers = {"Access-Token": access_token}
    base_params = {
        "advertiser_id": advertiser_id,
        "report_type": "BASIC",
        "data_level": "AUCTION_CAMPAIGN",
        "service_type": "AUCTION",
        "dimensions": '["campaign_id","stat_time_day"]',
        "metrics": '["campaign_name","spend","impressions","clicks","conversion"]',
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "page_size": PAGE_SIZE,
    }

    logger.info(
        f"[TikTok] Pulling report for advertiser {advertiser_id} | "
        f"{start_date} to {end_date}"
    )

    rows: list[dict] = []
    page = 1
    while True:
        params = {**base_params, "page": page}
        data = _get(
            f"{TIKTOK_BASE_URL}/report/integrated/get/", headers=headers, params=params
        )
        body = data.get("data", {})
        elements = body.get("list", [])
        for item in elements:
            dims = item.get("dimensions", {})
            metrics = item.get("metrics", {})
            rows.append(
                {
                    "date": dims.get("stat_time_day", ""),
                    "advertiser_id": advertiser_id,
                    "campaign_id": dims.get("campaign_id", ""),
                    "campaign_name": metrics.get("campaign_name", ""),
                    "spend": float(metrics.get("spend") or 0),
                    "impressions": int(float(metrics.get("impressions") or 0)),
                    "clicks": int(float(metrics.get("clicks") or 0)),
                    "conversions": float(metrics.get("conversion") or 0),
                }
            )
        page_info = body.get("page_info", {})
        total_pages = page_info.get("total_page", 1)
        if page >= total_pages or not elements:
            break
        page += 1

    logger.info(f"[TikTok] Retrieved {len(rows)} rows")

    df = pd.DataFrame(rows)
    if not df.empty:
        # TikTok day stamps arrive as "YYYY-MM-DD HH:MM:SS"
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    return df
