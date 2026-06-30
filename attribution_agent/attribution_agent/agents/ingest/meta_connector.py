from __future__ import annotations

from datetime import date, timedelta
import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential
import logging

logger = logging.getLogger(__name__)

META_API_VERSION = "v19.0"
META_BASE_URL = f"https://graph.facebook.com/{META_API_VERSION}"

INSIGHT_FIELDS = [
    "campaign_id",
    "campaign_name",
    "adset_id",
    "adset_name",
    "spend",
    "impressions",
    "clicks",
    "reach",
    "cpm",
    "cpc",
    "ctr",
    "actions",
    "cost_per_action_type",
    "date_start",
    "date_stop",
]

CONVERSION_ACTIONS = {
    "lead",
    "offsite_conversion.fb_pixel_lead",
    "offsite_conversion.fb_pixel_purchase",
    "offsite_conversion.fb_pixel_complete_registration",
}


class MetaConnector:
    def __init__(self, access_token: str = "") -> None:
        self.access_token = access_token
        self.session = requests.Session()

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True
    )
    def _get(self, url: str, params: dict) -> dict:
        params["access_token"] = self.access_token
        response = self.session.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def pull_campaign_insights(
        self, ad_account_id, start_date, end_date, level="campaign"
    ):
        logger.info(
            f"[Meta] Pulling insights for {ad_account_id} | {start_date} to {end_date}"
        )
        account = (
            ad_account_id
            if ad_account_id.startswith("act_")
            else f"act_{ad_account_id}"
        )
        url = f"{META_BASE_URL}/{account}/insights"
        params = {
            "fields": ",".join(INSIGHT_FIELDS),
            "time_range": f'{{"since":"{start_date}","until":"{end_date}"}}',
            "time_increment": 1,
            "level": level,
            "limit": 500,
        }
        rows = []
        while url:
            data = self._get(url, params)
            rows.extend(data.get("data", []))
            url = data.get("paging", {}).get("next")
            params = {}
        logger.info(f"[Meta] Retrieved {len(rows)} rows")
        if not rows:
            return pd.DataFrame()
        return self._normalize(rows, ad_account_id)

    def _normalize(self, rows, ad_account_id):
        normalized = []
        for row in rows:
            base = {
                "ad_account_id": ad_account_id,
                "campaign_id": row.get("campaign_id"),
                "campaign_name": row.get("campaign_name"),
                "adset_id": row.get("adset_id"),
                "adset_name": row.get("adset_name"),
                "date": row.get("date_start"),
                "spend": float(row.get("spend", 0)),
                "impressions": int(row.get("impressions", 0)),
                "clicks": int(row.get("clicks", 0)),
                "reach": int(row.get("reach", 0)),
                "cpm": float(row.get("cpm", 0)),
                "cpc": float(row.get("cpc", 0)),
                "ctr": float(row.get("ctr", 0)),
                "source": "meta",
            }
            for action in row.get("actions", []):
                atype = action.get("action_type", "")
                if atype in CONVERSION_ACTIONS:
                    base[f"conversions_{atype.replace('.', '_')}"] = int(
                        action.get("value", 0)
                    )
            normalized.append(base)
        df = pd.DataFrame(normalized)
        df["date"] = pd.to_datetime(df["date"])
        return df


def pull_meta_data(
    ad_account_id: str, lookback_days: int = 30, access_token: str = ""
) -> pd.DataFrame:
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=lookback_days - 1)
    connector = MetaConnector(access_token=access_token)
    return connector.pull_campaign_insights(
        ad_account_id=ad_account_id,
        start_date=start_date,
        end_date=end_date,
    )
