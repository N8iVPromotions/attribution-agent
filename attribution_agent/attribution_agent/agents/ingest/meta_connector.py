from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
import logging

import pandas as pd
import pyarrow as pa
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from agents.ingest.batches import batches_to_dataframe, records_to_batches
from utils.secrets import redact_secrets
from utils.raw_archive import archive_raw_page

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


def _error_detail(response: requests.Response | None) -> str:
    if response is None:
        return ""
    body = (response.text or "").strip()
    if not body:
        return ""
    return f" | response={redact_secrets(body[:1200])}"


class MetaConnector:
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

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True
    )
    def _get(self, url: str, params: dict) -> dict:
        # The token rides in the URL (query param on the first request, baked
        # into Meta's paging.next URLs after that), and requests copies the
        # full URL into exception messages — re-raise with it scrubbed so it
        # never reaches logs or the source_failures ops record.
        params["access_token"] = self.access_token
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
        except requests.RequestException as exc:
            message = redact_secrets(str(exc))
            message += _error_detail(getattr(exc, "response", None))
            raise type(exc)(message) from None
        self.page_number += 1
        archive_raw_page(
            source="meta",
            client_id=self.client_id,
            run_id=self.run_id,
            page_number=self.page_number,
            body=response.content,
            endpoint="insights",
        )
        return response.json()

    def pull_campaign_insights(
        self, ad_account_id, start_date, end_date, level="campaign"
    ) -> pd.DataFrame:
        return batches_to_dataframe(
            self.iter_campaign_insight_batches(
                ad_account_id=ad_account_id,
                start_date=start_date,
                end_date=end_date,
                level=level,
            )
        )

    def iter_campaign_insight_batches(
        self, ad_account_id, start_date, end_date, level="campaign"
    ) -> Iterator[pa.RecordBatch]:
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

        def iter_records():
            nonlocal url, params
            while url:
                data = self._get(url, params)
                yield from self._normalize_rows(data.get("data", []), ad_account_id)
                url = data.get("paging", {}).get("next")
                params = {}

        total_rows = 0
        for batch in records_to_batches(iter_records()):
            total_rows += batch.num_rows
            yield batch
        logger.info(f"[Meta] Retrieved {total_rows} rows")

    @staticmethod
    def _normalize_rows(rows, ad_account_id) -> Iterator[dict]:
        for row in rows:
            base = {
                "ad_account_id": ad_account_id,
                "campaign_id": row.get("campaign_id"),
                "campaign_name": row.get("campaign_name"),
                "adset_id": row.get("adset_id"),
                "adset_name": row.get("adset_name"),
                "date": pd.to_datetime(row.get("date_start")),
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
            yield base


def pull_meta_data(
    ad_account_id: str,
    lookback_days: int = 30,
    access_token: str = "",
    client_id: str = "",
    run_id: str = "",
) -> pd.DataFrame:
    return batches_to_dataframe(
        iter_meta_data_batches(
            ad_account_id=ad_account_id,
            lookback_days=lookback_days,
            access_token=access_token,
            client_id=client_id,
            run_id=run_id,
        )
    )


def iter_meta_data_batches(
    ad_account_id: str,
    lookback_days: int = 30,
    access_token: str = "",
    client_id: str = "",
    run_id: str = "",
) -> Iterator[pa.RecordBatch]:
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=lookback_days - 1)
    connector = MetaConnector(
        access_token=access_token,
        client_id=client_id,
        run_id=run_id,
    )
    yield from connector.iter_campaign_insight_batches(
        ad_account_id=ad_account_id,
        start_date=start_date,
        end_date=end_date,
    )
