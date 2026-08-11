"""
TikTok Ads connector.

Uses the TikTok Marketing API synchronous reporting endpoint to pull daily
campaign-level metrics. Spend is returned in the ad account's currency. The
connector normalizes nothing here — `ad_sources.normalize_tiktok_ads` maps the
raw shape onto the shared normalized schema.
"""

from __future__ import annotations

from collections.abc import Iterator
import logging
from datetime import date, timedelta

import pandas as pd
import pyarrow as pa
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from agents.ingest.batches import batches_to_dataframe, records_to_batches
from utils.raw_archive import archive_raw_page

logger = logging.getLogger(__name__)

TIKTOK_BASE_URL = "https://business-api.tiktok.com/open_api/v1.3"
PAGE_SIZE = 1000


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
def _get(
    url: str,
    headers: dict,
    params: dict,
    *,
    client_id: str = "",
    run_id: str = "",
    page_number: int = 1,
) -> dict:
    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    archive_raw_page(
        source="tiktok",
        client_id=client_id,
        run_id=run_id,
        page_number=page_number,
        body=response.content,
        endpoint="integrated-report",
    )
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
    client_id: str = "",
    run_id: str = "",
) -> pd.DataFrame:
    """Pull daily campaign-level TikTok Ads metrics for the lookback window."""
    return batches_to_dataframe(
        iter_tiktok_ads_batches(
            advertiser_id=advertiser_id,
            lookback_days=lookback_days,
            access_token=access_token,
            client_id=client_id,
            run_id=run_id,
        )
    )


def iter_tiktok_ads_batches(
    advertiser_id: str,
    lookback_days: int = 30,
    access_token: str = "",
    client_id: str = "",
    run_id: str = "",
) -> Iterator[pa.RecordBatch]:
    if not advertiser_id:
        return
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

    def iter_records():
        page = 1
        while True:
            params = {**base_params, "page": page}
            archive_context = (
                {"client_id": client_id, "run_id": run_id, "page_number": page}
                if client_id or run_id
                else {}
            )
            data = _get(
                f"{TIKTOK_BASE_URL}/report/integrated/get/",
                headers=headers,
                params=params,
                **archive_context,
            )
            body = data.get("data", {})
            elements = body.get("list", [])
            for item in elements:
                dims = item.get("dimensions", {})
                metrics = item.get("metrics", {})
                yield {
                    "date": pd.to_datetime(
                        dims.get("stat_time_day", ""), errors="coerce"
                    ).normalize(),
                    "advertiser_id": advertiser_id,
                    "campaign_id": dims.get("campaign_id", ""),
                    "campaign_name": metrics.get("campaign_name", ""),
                    "spend": float(metrics.get("spend") or 0),
                    "impressions": int(float(metrics.get("impressions") or 0)),
                    "clicks": int(float(metrics.get("clicks") or 0)),
                    "conversions": float(metrics.get("conversion") or 0),
                }
            page_info = body.get("page_info", {})
            total_pages = page_info.get("total_page", 1)
            if page >= total_pages or not elements:
                break
            page += 1

    total_rows = 0
    for batch in records_to_batches(iter_records()):
        total_rows += batch.num_rows
        yield batch
    logger.info(f"[TikTok] Retrieved {total_rows} rows")
