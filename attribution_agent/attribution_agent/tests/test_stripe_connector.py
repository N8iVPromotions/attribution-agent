from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import pytest

from agents.ingest import stripe_connector as stripe_module
from agents.ingest.stripe_connector import StripeConnector


class StripeObject:
    """Attribute-only stand-in for Stripe SDK resources."""

    def __init__(self, **fields: Any) -> None:
        for name, value in fields.items():
            setattr(self, name, value)

    def to_dict_recursive(self) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, StripeObject):
                return {name: convert(item) for name, item in vars(value).items()}
            if isinstance(value, list):
                return [convert(item) for item in value]
            return value

        return {name: convert(value) for name, value in vars(self).items()}


class FakePaymentIntentService:
    def __init__(self, listed_pages: list[Any], retrieved: dict[str, Any]) -> None:
        self.listed_pages = listed_pages
        self.retrieved = retrieved
        self.list_calls: list[dict] = []
        self.retrieve_calls: list[tuple[str, dict]] = []

    def list(self, params: dict) -> Any:
        self.list_calls.append(params)
        return self.listed_pages[len(self.list_calls) - 1]

    def retrieve(self, payment_intent_id: str, params: dict) -> Any:
        self.retrieve_calls.append((payment_intent_id, params))
        return self.retrieved[payment_intent_id]


class FakeRefundService:
    def __init__(self, pages: list[Any]) -> None:
        self.pages = pages
        self.list_calls: list[dict] = []

    def list(self, params: dict) -> Any:
        self.list_calls.append(params)
        return self.pages[len(self.list_calls) - 1]


class FakeStripeClient:
    def __init__(
        self,
        payment_intents: FakePaymentIntentService,
        refunds: FakeRefundService,
    ) -> None:
        self.v1 = StripeObject(
            payment_intents=payment_intents,
            refunds=refunds,
        )


def _payment_intent(
    payment_id: str,
    *,
    created: int,
    refunded: bool = False,
    amount_refunded: int = 0,
    livemode: bool = True,
) -> StripeObject:
    return StripeObject(
        id=payment_id,
        customer=StripeObject(id=f"cus_{payment_id}", email="BUYER@EXAMPLE.TEST"),
        amount_received=10_000,
        currency="usd",
        livemode=livemode,
        status="succeeded",
        latest_charge=StripeObject(
            refunded=refunded,
            amount_refunded=amount_refunded,
            billing_details=StripeObject(email="billing@example.test"),
        ),
        metadata=StripeObject(hubspot_deal_id="deal-123"),
        created=created,
        description="Annual plan",
    )


def test_refund_events_refresh_old_payment_intents_once_and_archive(monkeypatch):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 31, 23, 59, tzinfo=timezone.utc)
    old_created = int((start - timedelta(days=180)).timestamp())

    recent = _payment_intent("pi_recent", created=int(start.timestamp()))
    recent_two = {
        **_payment_intent(
            "pi_recent_two", created=int((start + timedelta(days=1)).timestamp())
        ).to_dict_recursive()
    }
    old_refunded = _payment_intent(
        "pi_old", created=old_created, refunded=True, amount_refunded=2_500
    )
    old_refunded_two = {
        **_payment_intent(
            "pi_old_two", created=old_created, refunded=True, amount_refunded=1_000
        ).to_dict_recursive()
    }

    payment_service = FakePaymentIntentService(
        listed_pages=[
            StripeObject(data=[recent], has_more=True),
            {"data": [recent_two], "has_more": False},
        ],
        retrieved={"pi_old": old_refunded, "pi_old_two": old_refunded_two},
    )
    refund_service = FakeRefundService(
        pages=[
            {
                "data": [
                    {"id": "re_old", "payment_intent": "pi_old"},
                    StripeObject(
                        id="re_recent",
                        payment_intent=StripeObject(id="pi_recent"),
                    ),
                ],
                "has_more": True,
            },
            StripeObject(
                data=[
                    StripeObject(id="re_old_again", payment_intent="pi_old"),
                    {"id": "re_old_two", "payment_intent": {"id": "pi_old_two"}},
                    {"id": "re_charge_only", "payment_intent": None},
                ],
                has_more=False,
            ),
        ]
    )
    client = FakeStripeClient(payment_service, refund_service)
    connector = StripeConnector(
        access_token="sk_test_redacted", client_id="client-1", run_id="run-1"
    )
    monkeypatch.setattr(connector, "_get_client", lambda: client)

    archives: list[dict] = []

    def capture_archive(**kwargs):
        archives.append(kwargs)
        return "gs://archive/object"

    monkeypatch.setattr(stripe_module, "archive_raw_page", capture_archive)

    batches = list(
        connector.iter_payment_batches(
            start_datetime=start,
            end_datetime=end,
        )
    )
    frame = pd.concat([batch.to_pandas() for batch in batches], ignore_index=True)

    assert frame["payment_id"].tolist() == [
        "pi_recent",
        "pi_recent_two",
        "pi_old",
        "pi_old_two",
    ]
    refreshed = frame.set_index("payment_id").loc["pi_old"]
    assert bool(refreshed["refunded"]) is True
    assert refreshed["refund_amount"] == pytest.approx(25.0)
    assert refreshed["customer_email"] == "buyer@example.test"
    assert refreshed["hubspot_deal_id"] == "deal-123"
    assert refreshed["created_at"] == pd.Timestamp(old_created, unit="s", tz="UTC")

    expected_window = {"gte": int(start.timestamp()), "lte": int(end.timestamp())}
    assert payment_service.list_calls == [
        {
            "created": expected_window,
            "limit": 100,
            "expand": ["data.customer", "data.latest_charge"],
        },
        {
            "created": expected_window,
            "limit": 100,
            "expand": ["data.customer", "data.latest_charge"],
            "starting_after": "pi_recent",
        },
    ]
    assert refund_service.list_calls == [
        {"created": expected_window, "limit": 100},
        {
            "created": expected_window,
            "limit": 100,
            "starting_after": "re_recent",
        },
    ]
    assert payment_service.retrieve_calls == [
        ("pi_old", {"expand": ["customer", "latest_charge"]}),
        ("pi_old_two", {"expand": ["customer", "latest_charge"]}),
    ]

    assert [(item["endpoint"], item["page_number"]) for item in archives] == [
        ("payment-intents", 1),
        ("payment-intents", 2),
        ("refunds", 1),
        ("refunded-payment-intents", 1),
        ("refunds", 2),
        ("refunded-payment-intents", 2),
    ]
    archived_refunds = json.loads(
        next(item["body"] for item in archives if item["endpoint"] == "refunds")
    )
    assert archived_refunds[0]["id"] == "re_old"


def test_lookback_only_call_remains_compatible(monkeypatch):
    fixed_now = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz else fixed_now.replace(tzinfo=None)

    connector = StripeConnector()
    observed: dict[str, int] = {}

    def no_payment_intents(*, created_gte: int, created_lte: int):
        observed.update(created_gte=created_gte, created_lte=created_lte)
        return iter(())

    monkeypatch.setattr(stripe_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(
        connector, "_iter_payment_intents_for_window", no_payment_intents
    )

    assert list(connector.iter_payment_batches(7)) == []
    assert observed == {
        "created_gte": int((fixed_now - timedelta(days=7)).timestamp()),
        "created_lte": int(fixed_now.timestamp()),
    }


def test_pagination_fails_closed_when_stripe_omits_cursor_data(monkeypatch):
    connector = StripeConnector()
    monkeypatch.setattr(
        connector,
        "_list_refund_page",
        lambda _params: {"data": [], "has_more": True},
    )
    monkeypatch.setattr(connector, "_archive_page", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="has_more without data"):
        list(connector._iter_refund_pages(1, 2))


def test_explicit_window_rejects_reverse_dates():
    with pytest.raises(ValueError, match="start_datetime"):
        StripeConnector._resolve_window(
            lookback_days=30,
            start_datetime=datetime(2026, 2, 1, tzinfo=timezone.utc),
            end_datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


def test_account_context_accepts_exact_configured_account(monkeypatch):
    connector = StripeConnector(
        access_token="sk_live_redacted",
        expected_account_id="acct_expected",
        require_live_mode=True,
    )
    monkeypatch.setattr(
        connector,
        "_retrieve_current_account",
        lambda: StripeObject(id="acct_expected"),
    )

    connector._validate_account_context()


def test_account_context_rejects_wrong_account(monkeypatch):
    connector = StripeConnector(
        access_token="sk_live_redacted",
        expected_account_id="acct_expected",
        require_live_mode=True,
    )
    monkeypatch.setattr(
        connector,
        "_retrieve_current_account",
        lambda: StripeObject(id="acct_other"),
    )

    with pytest.raises(ValueError, match="acct_expected.*acct_other"):
        connector._validate_account_context()


def test_live_account_context_rejects_test_key_before_api_call(monkeypatch):
    connector = StripeConnector(
        access_token="sk_test_redacted",
        expected_account_id="acct_expected",
        require_live_mode=True,
    )
    monkeypatch.setattr(
        connector,
        "_retrieve_current_account",
        lambda: pytest.fail("test-mode keys must be rejected before an API request"),
    )

    with pytest.raises(ValueError, match="live-mode secret key"):
        connector._validate_account_context()


def test_live_connector_rejects_test_mode_payment_intent():
    connector = StripeConnector(require_live_mode=True)
    payment = _payment_intent(
        "pi_test_mode",
        created=int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()),
        livemode=False,
    )

    with pytest.raises(ValueError, match="test-mode PaymentIntent"):
        connector._validate_payment_intent_mode(payment)


@pytest.mark.parametrize("livemode", [True, False])
def test_normalize_preserves_payment_livemode(livemode):
    payment = _payment_intent(
        "pi_mode",
        created=int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()),
        livemode=livemode,
    )

    row = StripeConnector()._normalize([payment]).iloc[0]

    assert bool(row["livemode"]) is livemode
    assert row["currency"] == "USD"
