"""
agents/ingest/stripe_connector.py
-----------------------------------
Pulls Stripe payment intents (with charges and refunds) for the lookback period.
Normalizes into a flat DataFrame for Databricks upsert.

Matches to HubSpot deals via customer_email for the revenue attribution join.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import pyarrow as pa
from tenacity import retry, stop_after_attempt, wait_exponential

from agents.ingest.batches import batches_to_dataframe, records_to_batches
from utils.raw_archive import archive_raw_page

logger = logging.getLogger(__name__)


class StripeConnector:
    """
    Fetches Stripe payment data and returns a clean pandas DataFrame.

    Usage:
        connector = StripeConnector(access_token="sk_live_...")
        df = connector.pull_payments(lookback_days=30)
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

    def _get_client(self):
        import stripe

        return stripe.StripeClient(self.access_token)

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True
    )
    def _list_payment_intents(self, created_gte: int, created_lte: int) -> list:
        return [
            item
            for page in self._iter_payment_intent_pages(created_gte, created_lte)
            for item in page
        ]

    def _iter_payment_intent_pages(
        self, created_gte: int, created_lte: int
    ) -> Iterator[list]:
        client = self._get_client()
        params = {
            "created": {"gte": created_gte, "lte": created_lte},
            "limit": 100,
            "expand": ["data.customer", "data.latest_charge"],
        }
        page = client.payment_intents.list(params)
        self._archive_page(page.data, 1)
        if page.data:
            yield page.data
        page_number = 1
        while page.has_more:
            params["starting_after"] = page.data[-1].id
            page = client.payment_intents.list(params)
            page_number += 1
            self._archive_page(page.data, page_number)
            if page.data:
                yield page.data

    def _archive_page(self, objects: list, page_number: int) -> None:
        records = []
        for value in objects:
            if hasattr(value, "to_dict_recursive"):
                records.append(value.to_dict_recursive())
            elif isinstance(value, dict):
                records.append(value)
            else:
                records.append(str(value))
        archive_raw_page(
            source="stripe",
            client_id=self.client_id,
            run_id=self.run_id,
            page_number=page_number,
            body=json.dumps(records, default=str).encode(),
            endpoint="payment-intents",
        )

    def pull_payments(self, lookback_days: int = 30) -> pd.DataFrame:
        return batches_to_dataframe(self.iter_payment_batches(lookback_days))

    def iter_payment_batches(self, lookback_days: int = 30) -> Iterator[pa.RecordBatch]:
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=lookback_days)

        logger.info(
            f"[Stripe] Pulling payments | {start_dt.strftime('%Y-%m-%d')} "
            f"to {end_dt.strftime('%Y-%m-%d')}"
        )

        pages = self._iter_payment_intent_pages(
            created_gte=int(start_dt.timestamp()), created_lte=int(end_dt.timestamp())
        )
        records = (record for page in pages for record in self._normalize_rows(page))
        total_rows = 0
        for batch in records_to_batches(records):
            total_rows += batch.num_rows
            yield batch
        logger.info(f"[Stripe] Retrieved {total_rows} payment intents")

    def _normalize(self, payment_intents: list) -> pd.DataFrame:
        return pd.DataFrame(self._normalize_rows(payment_intents))

    @staticmethod
    def _normalize_rows(payment_intents: list) -> Iterator[dict]:
        for pi in payment_intents:
            customer_id = ""
            customer_email = ""
            customer = pi.get("customer")
            if isinstance(customer, str):
                customer_id = customer
            elif customer:
                customer_id = customer.get("id", "")
                customer_email = customer.get("email", "") or ""

            refunded = False
            refund_amount = 0.0
            charge = pi.get("latest_charge")
            if isinstance(charge, str):
                pass  # not expanded — skip charge detail
            elif charge:
                refunded = bool(charge.get("refunded", False))
                refund_amount = (charge.get("amount_refunded") or 0) / 100.0
                if not customer_email:
                    billing = charge.get("billing_details") or {}
                    customer_email = billing.get("email", "") or ""

            metadata = pi.get("metadata") or {}
            hubspot_deal_id = (
                metadata.get("hubspot_deal_id") or metadata.get("deal_id") or ""
            )

            yield {
                "payment_id": pi.get("id", ""),
                "customer_id": customer_id,
                "customer_email": customer_email.lower() if customer_email else "",
                "amount_paid": (pi.get("amount_received") or 0) / 100.0,
                "currency": (pi.get("currency") or "usd").upper(),
                "status": pi.get("status", ""),
                "refunded": refunded,
                "refund_amount": refund_amount,
                "created_at": pd.Timestamp(pi["created"], unit="s", tz="UTC"),
                "description": pi.get("description") or "",
                "hubspot_deal_id": hubspot_deal_id,
                "source": "stripe",
            }


def pull_stripe_data(
    lookback_days: int = 30,
    access_token: str = "",
    client_id: str = "",
    run_id: str = "",
) -> pd.DataFrame:
    return batches_to_dataframe(
        iter_stripe_batches(
            lookback_days=lookback_days,
            access_token=access_token,
            client_id=client_id,
            run_id=run_id,
        )
    )


def iter_stripe_batches(
    lookback_days: int = 30,
    access_token: str = "",
    client_id: str = "",
    run_id: str = "",
) -> Iterator[pa.RecordBatch]:
    connector = StripeConnector(
        access_token=access_token,
        client_id=client_id,
        run_id=run_id,
    )
    yield from connector.iter_payment_batches(lookback_days=lookback_days)
