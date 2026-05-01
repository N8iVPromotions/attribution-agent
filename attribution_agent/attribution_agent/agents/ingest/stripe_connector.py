"""
agents/ingest/stripe_connector.py
-----------------------------------
Pulls Stripe payment intents (with charges and refunds) for the lookback period.
Normalizes into a flat DataFrame for Databricks upsert.

Matches to HubSpot deals via customer_email for the revenue attribution join.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class StripeConnector:
    """
    Fetches Stripe payment data and returns a clean pandas DataFrame.

    Usage:
        connector = StripeConnector(access_token="sk_live_...")
        df = connector.pull_payments(lookback_days=30)
    """

    def __init__(self, access_token: str = "") -> None:
        self.access_token = access_token

    def _get_client(self):
        import stripe
        return stripe.StripeClient(self.access_token)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
    def _list_payment_intents(self, created_gte: int, created_lte: int) -> list:
        client = self._get_client()
        results = []
        params = {
            "created": {"gte": created_gte, "lte": created_lte},
            "limit": 100,
            "expand": ["data.customer", "data.latest_charge"],
        }
        page = client.payment_intents.list(params)
        results.extend(page.data)
        while page.has_more:
            params["starting_after"] = page.data[-1].id
            page = client.payment_intents.list(params)
            results.extend(page.data)
        return results

    def pull_payments(self, lookback_days: int = 30) -> pd.DataFrame:
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=lookback_days)

        logger.info(
            f"[Stripe] Pulling payments | {start_dt.strftime('%Y-%m-%d')} "
            f"to {end_dt.strftime('%Y-%m-%d')}"
        )

        payment_intents = self._list_payment_intents(
            created_gte=int(start_dt.timestamp()),
            created_lte=int(end_dt.timestamp()),
        )
        logger.info(f"[Stripe] Retrieved {len(payment_intents)} payment intents")

        if not payment_intents:
            return pd.DataFrame()

        return self._normalize(payment_intents)

    def _normalize(self, payment_intents: list) -> pd.DataFrame:
        rows = []
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
                metadata.get("hubspot_deal_id")
                or metadata.get("deal_id")
                or ""
            )

            rows.append({
                "payment_id":      pi.get("id", ""),
                "customer_id":     customer_id,
                "customer_email":  customer_email.lower() if customer_email else "",
                "amount_paid":     (pi.get("amount_received") or 0) / 100.0,
                "currency":        (pi.get("currency") or "usd").upper(),
                "status":          pi.get("status", ""),
                "refunded":        refunded,
                "refund_amount":   refund_amount,
                "created_at":      pd.Timestamp(pi["created"], unit="s", tz="UTC"),
                "description":     pi.get("description") or "",
                "hubspot_deal_id": hubspot_deal_id,
                "source":          "stripe",
            })

        df = pd.DataFrame(rows)
        logger.info(f"[Stripe] Normalized DataFrame shape: {df.shape}")
        return df


def pull_stripe_data(lookback_days: int = 30, access_token: str = "") -> pd.DataFrame:
    connector = StripeConnector(access_token=access_token)
    return connector.pull_payments(lookback_days=lookback_days)
