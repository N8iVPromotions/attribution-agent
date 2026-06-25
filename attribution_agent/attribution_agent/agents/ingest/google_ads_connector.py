"""
Google Ads connector.

Requires google-ads to be installed and these environment variables configured:
GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET,
GOOGLE_ADS_REFRESH_TOKEN, and optionally GOOGLE_ADS_LOGIN_CUSTOMER_ID.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
def _search_rows(service, customer_id: str, query: str) -> list[dict]:
    """Run the GAQL query and collect rows. Retried on transient API errors."""
    rows: list[dict] = []
    response = service.search_stream(customer_id=customer_id, query=query)
    for batch in response:
        for row in batch.results:
            rows.append({
                "date": row.segments.date,
                "customer_id": str(row.customer.id),
                "campaign_id": str(row.campaign.id),
                "campaign_name": row.campaign.name,
                "ad_group_id": str(row.ad_group.id),
                "ad_group_name": row.ad_group.name,
                "ad_id": "",
                "ad_name": "",
                "spend": float(row.metrics.cost_micros or 0) / 1_000_000,
                "impressions": int(row.metrics.impressions or 0),
                "clicks": int(row.metrics.clicks or 0),
                "conversions": float(row.metrics.conversions or 0),
                "landing_url": "",
            })
    return rows


def pull_google_ads_data(
    customer_id: str,
    lookback_days: int = 30,
    access_token: str = "",
) -> pd.DataFrame:
    if not customer_id:
        return pd.DataFrame()

    try:
        from google.ads.googleads.client import GoogleAdsClient
    except ImportError as exc:
        raise ImportError(
            "google-ads is required for Google Ads ingestion. "
            "Install google-ads>=24.0.0 and configure Google Ads OAuth env vars."
        ) from exc

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=lookback_days - 1)
    logger.info(f"[Google Ads] Pulling insights for {customer_id} | {start_date} to {end_date}")

    credentials = {
        "developer_token": os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
        "client_id": os.environ.get("GOOGLE_ADS_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_ADS_CLIENT_SECRET", ""),
        "refresh_token": access_token or os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", ""),
        "use_proto_plus": True,
    }
    login_customer_id = os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "")
    if login_customer_id:
        credentials["login_customer_id"] = login_customer_id

    client = GoogleAdsClient.load_from_dict(credentials)
    service = client.get_service("GoogleAdsService")

    query = f"""
        SELECT
          segments.date,
          customer.id,
          campaign.id,
          campaign.name,
          ad_group.id,
          ad_group.name,
          metrics.cost_micros,
          metrics.impressions,
          metrics.clicks,
          metrics.conversions
        FROM ad_group
        WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
    """

    rows = _search_rows(service, str(customer_id).replace("-", ""), query)
    logger.info(f"[Google Ads] Retrieved {len(rows)} rows")

    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df

