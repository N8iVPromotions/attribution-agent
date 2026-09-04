"""
agents/ingest/stripe_connector.py
-----------------------------------
Pulls Stripe payment intents (with charges and refunds) for the lookback period.
Normalizes into a flat DataFrame for Databricks upsert.

Matches to HubSpot deals via metadata.hubspot_deal_id for the revenue attribution
join. Customer email is retained only as optional diagnostic context.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

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
        expected_account_id: str = "",
        require_live_mode: bool = False,
    ) -> None:
        self.access_token = access_token
        self.client_id = client_id
        self.run_id = run_id
        self.expected_account_id = expected_account_id.strip()
        self.require_live_mode = require_live_mode

    def _get_client(self):
        import stripe

        return stripe.StripeClient(self.access_token)

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True
    )
    def _retrieve_current_account(self) -> Any:
        return self._get_client().v1.accounts.retrieve_current()

    def _validate_account_context(self) -> None:
        if not self.expected_account_id:
            if self.require_live_mode:
                raise ValueError(
                    "stripe_account_id is required for live Stripe delivery"
                )
            return
        if self.require_live_mode and not self.access_token.startswith(
            ("sk_live_", "rk_live_")
        ):
            raise ValueError(
                "Live report delivery requires a Stripe live-mode secret key"
            )
        account = self._retrieve_current_account()
        actual_account_id = self._object_id(account)
        if actual_account_id != self.expected_account_id:
            raise ValueError(
                "Stripe key account does not match configured stripe_account_id: "
                f"expected {self.expected_account_id!r}, received "
                f"{actual_account_id or '(missing id)'!r}"
            )

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True
    )
    def _list_payment_intents(self, created_gte: int, created_lte: int) -> list:
        return [
            item
            for page in self._iter_payment_intent_pages(created_gte, created_lte)
            for item in page
        ]

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True
    )
    def _list_payment_intent_page(self, params: dict) -> Any:
        return self._get_client().v1.payment_intents.list(params)

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True
    )
    def _list_refund_page(self, params: dict) -> Any:
        return self._get_client().v1.refunds.list(params)

    @retry(
        stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True
    )
    def _retrieve_payment_intent(self, payment_intent_id: str) -> Any:
        return self._get_client().v1.payment_intents.retrieve(
            payment_intent_id,
            {"expand": ["customer", "latest_charge"]},
        )

    def _iter_payment_intent_pages(
        self, created_gte: int, created_lte: int
    ) -> Iterator[list]:
        params = {
            "created": {"gte": created_gte, "lte": created_lte},
            "limit": 100,
            "expand": ["data.customer", "data.latest_charge"],
        }
        yield from self._iter_pages(
            params=params,
            list_page=self._list_payment_intent_page,
            endpoint="payment-intents",
        )

    def _iter_refund_pages(self, created_gte: int, created_lte: int) -> Iterator[list]:
        params = {
            "created": {"gte": created_gte, "lte": created_lte},
            "limit": 100,
        }
        yield from self._iter_pages(
            params=params,
            list_page=self._list_refund_page,
            endpoint="refunds",
        )

    def _iter_pages(self, *, params: dict, list_page, endpoint: str) -> Iterator[list]:
        page_number = 0
        while True:
            page = list_page(params.copy())
            page_number += 1
            objects = self._page_data(page)
            self._archive_page(objects, page_number, endpoint=endpoint)
            if objects:
                yield objects

            if not bool(self._field(page, "has_more", False)):
                return
            if not objects:
                raise RuntimeError(
                    f"Stripe {endpoint} pagination returned has_more without data"
                )
            cursor = self._object_id(objects[-1])
            if not cursor:
                raise RuntimeError(
                    f"Stripe {endpoint} pagination returned an item without an id"
                )
            params["starting_after"] = cursor

    def _archive_page(
        self, objects: list, page_number: int, *, endpoint: str = "payment-intents"
    ) -> None:
        records = []
        for value in objects:
            if hasattr(value, "to_dict_recursive"):
                records.append(value.to_dict_recursive())
            elif isinstance(value, Mapping):
                records.append(dict(value))
            else:
                records.append(str(value))
        archive_raw_page(
            source="stripe",
            client_id=self.client_id,
            run_id=self.run_id,
            page_number=page_number,
            body=json.dumps(records, default=str).encode(),
            endpoint=endpoint,
        )

    def pull_payments(
        self,
        lookback_days: int = 30,
        *,
        start_datetime: datetime | None = None,
        end_datetime: datetime | None = None,
    ) -> pd.DataFrame:
        return batches_to_dataframe(
            self.iter_payment_batches(
                lookback_days,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
            )
        )

    def iter_payment_batches(
        self,
        lookback_days: int = 30,
        *,
        start_datetime: datetime | None = None,
        end_datetime: datetime | None = None,
    ) -> Iterator[pa.RecordBatch]:
        self._validate_account_context()
        start_dt, end_dt = self._resolve_window(
            lookback_days=lookback_days,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )

        logger.info(
            f"[Stripe] Pulling payments | {start_dt.strftime('%Y-%m-%d')} "
            f"to {end_dt.strftime('%Y-%m-%d')}"
        )

        payment_intents = self._iter_payment_intents_for_window(
            created_gte=int(start_dt.timestamp()),
            created_lte=int(end_dt.timestamp()),
        )
        records = self._normalize_rows(payment_intents)
        total_rows = 0
        for batch in records_to_batches(records):
            total_rows += batch.num_rows
            yield batch
        logger.info(f"[Stripe] Retrieved {total_rows} payment intents")

    def _iter_payment_intents_for_window(
        self, created_gte: int, created_lte: int
    ) -> Iterator[Any]:
        seen_payment_ids: set[str] = set()
        for page in self._iter_payment_intent_pages(created_gte, created_lte):
            for payment_intent in page:
                payment_intent_id = self._object_id(payment_intent)
                if payment_intent_id:
                    seen_payment_ids.add(payment_intent_id)
                self._validate_payment_intent_mode(payment_intent)
                yield payment_intent

        refreshed_count = 0
        for page in self._iter_refund_pages(created_gte, created_lte):
            for refund in page:
                payment_intent_id = self._payment_intent_id_from_refund(refund)
                if not payment_intent_id:
                    logger.warning(
                        "[Stripe] Refund %s has no PaymentIntent; skipping refresh",
                        self._object_id(refund) or "(unknown)",
                    )
                    continue
                if payment_intent_id in seen_payment_ids:
                    continue

                payment_intent = self._retrieve_payment_intent(payment_intent_id)
                retrieved_id = self._object_id(payment_intent)
                if retrieved_id != payment_intent_id:
                    raise RuntimeError(
                        "Stripe returned an unexpected PaymentIntent while refreshing "
                        f"refund activity: requested {payment_intent_id!r}, "
                        f"received {retrieved_id or '(missing id)'!r}"
                    )

                seen_payment_ids.add(payment_intent_id)
                refreshed_count += 1
                self._validate_payment_intent_mode(payment_intent)
                self._archive_page(
                    [payment_intent],
                    refreshed_count,
                    endpoint="refunded-payment-intents",
                )
                yield payment_intent

        if refreshed_count:
            logger.info(
                "[Stripe] Refreshed %s older payment intents from refund events",
                refreshed_count,
            )

    @classmethod
    def _payment_intent_id_from_refund(cls, refund: Any) -> str:
        return cls._object_id(cls._field(refund, "payment_intent"))

    def _validate_payment_intent_mode(self, payment_intent: Any) -> None:
        if self.require_live_mode and not bool(
            self._field(payment_intent, "livemode", False)
        ):
            raise ValueError(
                "Stripe returned a test-mode PaymentIntent during live delivery"
            )

    @staticmethod
    def _resolve_window(
        *,
        lookback_days: int,
        start_datetime: datetime | None,
        end_datetime: datetime | None,
    ) -> tuple[datetime, datetime]:
        end_dt = StripeConnector._as_utc(end_datetime or datetime.now(timezone.utc))
        start_dt = StripeConnector._as_utc(
            start_datetime or end_dt - timedelta(days=lookback_days)
        )
        if start_dt > end_dt:
            raise ValueError("start_datetime must be before or equal to end_datetime")
        return start_dt, end_dt

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _page_data(cls, page: Any) -> list:
        data = cls._field(page, "data", [])
        if data is None:
            return []
        if isinstance(data, (str, bytes, Mapping)):
            raise RuntimeError("Stripe list response contained invalid page data")
        try:
            return list(data)
        except TypeError as exc:
            raise RuntimeError(
                "Stripe list response contained invalid page data"
            ) from exc

    @staticmethod
    def _field(value: Any, field: str, default: Any = None) -> Any:
        if value is None:
            return default
        if isinstance(value, Mapping):
            return value.get(field, default)
        getter = getattr(value, "get", None)
        if callable(getter):
            try:
                return getter(field, default)
            except (AttributeError, TypeError):
                pass
        return getattr(value, field, default)

    @classmethod
    def _object_id(cls, value: Any) -> str:
        if isinstance(value, str):
            return value
        return str(cls._field(value, "id", "") or "")

    def _normalize(self, payment_intents: list) -> pd.DataFrame:
        return pd.DataFrame(self._normalize_rows(payment_intents))

    @staticmethod
    def _normalize_rows(payment_intents) -> Iterator[dict]:
        for pi in payment_intents:
            customer_id = ""
            customer_email = ""
            customer = StripeConnector._field(pi, "customer")
            if isinstance(customer, str):
                customer_id = customer
            elif customer:
                customer_id = StripeConnector._field(customer, "id", "")
                customer_email = StripeConnector._field(customer, "email", "") or ""

            refunded = False
            refund_amount = 0.0
            charge = StripeConnector._field(pi, "latest_charge")
            if isinstance(charge, str):
                pass  # not expanded — skip charge detail
            elif charge:
                refunded = bool(StripeConnector._field(charge, "refunded", False))
                refund_amount = (
                    StripeConnector._field(charge, "amount_refunded", 0) or 0
                ) / 100.0
                if not customer_email:
                    billing = StripeConnector._field(charge, "billing_details") or {}
                    customer_email = StripeConnector._field(billing, "email", "") or ""

            metadata = StripeConnector._field(pi, "metadata") or {}
            hubspot_deal_id = (
                StripeConnector._field(metadata, "hubspot_deal_id")
                or StripeConnector._field(metadata, "deal_id")
                or ""
            )

            created = StripeConnector._field(pi, "created")
            if created is None:
                raise ValueError("Stripe PaymentIntent is missing created")

            yield {
                "payment_id": StripeConnector._object_id(pi),
                "customer_id": customer_id,
                "customer_email": customer_email.lower() if customer_email else "",
                "amount_paid": (StripeConnector._field(pi, "amount_received", 0) or 0)
                / 100.0,
                "currency": (
                    StripeConnector._field(pi, "currency", "usd") or "usd"
                ).upper(),
                "livemode": bool(StripeConnector._field(pi, "livemode", False)),
                "status": StripeConnector._field(pi, "status", ""),
                "refunded": refunded,
                "refund_amount": refund_amount,
                "created_at": pd.Timestamp(created, unit="s", tz="UTC"),
                "description": StripeConnector._field(pi, "description") or "",
                "hubspot_deal_id": hubspot_deal_id,
                "source": "stripe",
            }


def pull_stripe_data(
    lookback_days: int = 30,
    access_token: str = "",
    client_id: str = "",
    run_id: str = "",
    *,
    start_datetime: datetime | None = None,
    end_datetime: datetime | None = None,
    expected_account_id: str = "",
    require_live_mode: bool = False,
) -> pd.DataFrame:
    return batches_to_dataframe(
        iter_stripe_batches(
            lookback_days=lookback_days,
            access_token=access_token,
            client_id=client_id,
            run_id=run_id,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            expected_account_id=expected_account_id,
            require_live_mode=require_live_mode,
        )
    )


def iter_stripe_batches(
    lookback_days: int = 30,
    access_token: str = "",
    client_id: str = "",
    run_id: str = "",
    *,
    start_datetime: datetime | None = None,
    end_datetime: datetime | None = None,
    expected_account_id: str = "",
    require_live_mode: bool = False,
) -> Iterator[pa.RecordBatch]:
    connector = StripeConnector(
        access_token=access_token,
        client_id=client_id,
        run_id=run_id,
        expected_account_id=expected_account_id,
        require_live_mode=require_live_mode,
    )
    yield from connector.iter_payment_batches(
        lookback_days=lookback_days,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
    )
