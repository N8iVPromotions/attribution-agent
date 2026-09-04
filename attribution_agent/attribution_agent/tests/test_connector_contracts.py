"""
tests/test_connector_contracts.py
-----------------------------------
Schema contract tests for each connector's output DataFrame.

These run without any live API credentials. They use fixture DataFrames that
mirror the exact shape each connector produces, then assert that:
  1. All required columns are present after normalization.
  2. Column dtypes are correct (spend is numeric, date is parseable, etc.).
  3. The validator passes on well-formed data and catches bad data.

If a connector changes its output schema (e.g. Meta renames a field), these
tests will catch the break before it reaches Databricks.
"""

from __future__ import annotations

import pandas as pd
import pytest

from agents.ingest.ad_sources import (
    NORMALIZED_AD_COLUMNS,
    normalize_google_ads,
    normalize_linkedin_ads,
    normalize_meta_ads,
)
from agents.ingest.hubspot_connector import HubSpotConnector
from agents.ingest.validator import HubSpotValidator, MetaValidator, StripeValidator


# ─── FIXTURES — mirror exact connector output schemas ─────────────────────────


@pytest.fixture
def meta_raw() -> pd.DataFrame:
    """Shape produced by MetaConnector._normalize()"""
    return pd.DataFrame(
        {
            "ad_account_id": ["act_123"] * 3,
            "campaign_id": ["c1", "c2", "c3"],
            "campaign_name": ["Camp A", "Camp B", "Camp C"],
            "adset_id": ["as1", "as2", "as3"],
            "adset_name": ["AS A", "AS B", "AS C"],
            "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            "spend": [100.0, 200.0, 300.0],
            "impressions": [1000, 2000, 3000],
            "clicks": [50, 100, 150],
            "reach": [900, 1800, 2700],
            "cpm": [100.0, 100.0, 100.0],
            "cpc": [2.0, 2.0, 2.0],
            "ctr": [0.05, 0.05, 0.05],
            "source": ["meta"] * 3,
        }
    )


@pytest.fixture
def google_raw() -> pd.DataFrame:
    """Shape produced by GoogleAdsConnector (after cost_micros → spend conversion)"""
    return pd.DataFrame(
        {
            "customer_id": ["cust_1"] * 3,
            "campaign_id": ["gc1", "gc2", "gc3"],
            "campaign_name": ["GCamp A", "GCamp B", "GCamp C"],
            "ad_group_id": ["ag1", "ag2", "ag3"],
            "ad_group_name": ["AG A", "AG B", "AG C"],
            "date": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "spend": [50.0, 75.0, 100.0],
            "impressions": [500, 750, 1000],
            "clicks": [25, 37, 50],
            "conversions": [2.0, 3.0, 4.0],
        }
    )


@pytest.fixture
def linkedin_raw() -> pd.DataFrame:
    """Shape produced by LinkedInConnector (after externalWebsiteConversions → conversions)"""
    return pd.DataFrame(
        {
            "account_id": ["acc_1"] * 3,
            "campaign_id": ["lc1", "lc2", "lc3"],
            "campaign_name": ["LCamp A", "LCamp B", "LCamp C"],
            "date": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "spend": [30.0, 45.0, 60.0],
            "impressions": [300, 450, 600],
            "clicks": [15, 22, 30],
            "conversions": [1.0, 2.0, 3.0],
        }
    )


@pytest.fixture
def hubspot_raw() -> pd.DataFrame:
    """Shape produced by HubSpotConnector._normalize()"""
    return pd.DataFrame(
        {
            "deal_id": ["d1", "d2", "d3"],
            "deal_name": ["Deal A", "Deal B", "Deal C"],
            "deal_stage": ["closedwon"] * 3,
            "pipeline": ["default"] * 3,
            "amount": [5000.0, 10000.0, 7500.0],
            "close_date": pd.to_datetime(["2026-01-15", "2026-01-20", "2026-01-25"]),
            "create_date": pd.to_datetime(["2026-01-01", "2026-01-05", "2026-01-10"]),
            "stage_probability": [1.0, 1.0, 1.0],
            "contact_id": ["ct1", "ct2", "ct3"],
            "contact_email": ["a@x.com", "b@x.com", "c@x.com"],
            "hs_source": ["PAID_SOCIAL", "PAID_SEARCH", "ORGANIC_SEARCH"],
            "hs_source_label": ["Paid Social", "Paid Search", "Organic Search"],
            "hs_source_detail_1": ["facebook", "google", None],
            "hs_source_detail_2": ["camp_a", "camp_b", None],
            "utm_campaign": ["camp_a", "camp_b", None],
            "utm_source": ["facebook", "google", None],
            "utm_medium": ["paid_social", "cpc", None],
            "utm_content": [None, None, None],
            "first_page_url": [None, None, None],
            "lifecycle_stage": ["customer"] * 3,
            "lead_create_date": pd.to_datetime(
                ["2025-12-01", "2025-12-15", "2025-12-20"]
            ),
            "days_to_deal": [31, 21, 21],
            "source": ["hubspot"] * 3,
        }
    )


@pytest.fixture
def stripe_raw() -> pd.DataFrame:
    """Shape produced by StripeConnector.pull_payments()"""
    return pd.DataFrame(
        {
            "payment_id": ["pi_1", "pi_2", "pi_3"],
            "customer_id": ["cus_1", "cus_2", "cus_3"],
            "customer_email": ["a@x.com", "b@x.com", "c@x.com"],
            "amount_paid": [5000.0, 10000.0, 7500.0],
            "currency": ["usd"] * 3,
            "status": ["succeeded"] * 3,
            "refunded": [False, False, True],
            "refund_amount": [0.0, 0.0, 500.0],
            "created_at": pd.to_datetime(["2026-01-15", "2026-01-20", "2026-01-25"]),
            "description": [None, None, None],
            "hubspot_deal_id": ["d1", "d2", "d3"],
            "source": ["stripe"] * 3,
        }
    )


def _hubspot_api_payload(
    *,
    close_date: object,
    deal_create_date: object,
    contact_create_date: object,
) -> tuple[list[dict], dict[str, dict]]:
    deals = [
        {
            "id": "deal-1",
            "properties": {
                "dealname": "Deal One",
                "dealstage": "closedwon",
                "pipeline": "default",
                "amount": "1000",
                "closedate": close_date,
                "createdate": deal_create_date,
            },
            "associations": {"contacts": {"results": [{"id": "contact-1"}]}},
        }
    ]
    contacts = {
        "contact-1": {
            "email": "buyer@example.test",
            "createdate": contact_create_date,
        }
    }
    return deals, contacts


# ─── META CONNECTOR CONTRACT ──────────────────────────────────────────────────


class TestMetaConnectorContract:
    def test_normalized_output_has_all_required_columns(self, meta_raw):
        result = normalize_meta_ads(meta_raw, "test_client")
        assert set(NORMALIZED_AD_COLUMNS).issubset(set(result.columns))

    def test_spend_is_numeric(self, meta_raw):
        result = normalize_meta_ads(meta_raw, "test_client")
        assert pd.api.types.is_numeric_dtype(result["spend"])

    def test_impressions_and_clicks_are_integer(self, meta_raw):
        result = normalize_meta_ads(meta_raw, "test_client")
        assert result["impressions"].dtype == "int64"
        assert result["clicks"].dtype == "int64"

    def test_date_is_parseable(self, meta_raw):
        result = normalize_meta_ads(meta_raw, "test_client")
        parsed = pd.to_datetime(result["date"], errors="coerce")
        assert parsed.notna().all()

    def test_source_platform_is_meta(self, meta_raw):
        result = normalize_meta_ads(meta_raw, "test_client")
        assert (result["source_platform"] == "meta").all()

    def test_missing_spend_column_returns_zeros(self):
        """Connector contract: absent numeric columns default to 0, not crash."""
        bare = pd.DataFrame(
            {
                "ad_account_id": ["act_1"] * 2,
                "campaign_id": ["c1", "c2"],
                "campaign_name": ["A", "B"],
                "adset_id": ["as1", "as2"],
                "adset_name": ["AS1", "AS2"],
                "date": ["2026-01-01", "2026-01-02"],
            }
        )
        result = normalize_meta_ads(bare, "test_client")
        assert (result["spend"] == 0).all()
        assert (result["impressions"] == 0).all()

    def test_validator_passes_on_clean_data(self, meta_raw):
        _, report = MetaValidator("test").validate(meta_raw)
        assert report.passed

    def test_validator_catches_missing_required_columns(self):
        bad = pd.DataFrame({"spend": [100.0], "date": ["2026-01-01"]})
        _, report = MetaValidator("test").validate(bad)
        assert not report.passed
        assert any("Missing required columns" in e for e in report.errors)

    def test_validator_flags_zero_spend_days(self):
        zero = pd.DataFrame(
            {
                "ad_account_id": ["act_1"] * 5,
                "campaign_id": ["c1"] * 5,
                "date": pd.date_range("2026-01-01", periods=5)
                .strftime("%Y-%m-%d")
                .tolist(),
                "spend": [0.0] * 5,
                "impressions": [0] * 5,
                "clicks": [0] * 5,
            }
        )
        _, report = MetaValidator("test", zero_spend_days_allowed=1).validate(zero)
        assert not report.passed

    def test_validator_warns_on_spend_drop(self):
        drop = pd.DataFrame(
            {
                "ad_account_id": ["act_1"] * 6,
                "campaign_id": ["c1"] * 6,
                "date": pd.date_range("2026-01-01", periods=6)
                .strftime("%Y-%m-%d")
                .tolist(),
                "spend": [1000.0, 1000.0, 1000.0, 100.0, 100.0, 100.0],
                "impressions": [10000] * 6,
                "clicks": [500] * 6,
            }
        )
        _, report = MetaValidator("test", spend_drop_pct_alert=0.3).validate(drop)
        assert len(report.warnings) > 0


# ─── GOOGLE ADS CONNECTOR CONTRACT ───────────────────────────────────────────


class TestGoogleAdsConnectorContract:
    def test_normalized_output_has_all_required_columns(self, google_raw):
        result = normalize_google_ads(google_raw, "test_client")
        assert set(NORMALIZED_AD_COLUMNS).issubset(set(result.columns))

    def test_spend_is_numeric(self, google_raw):
        result = normalize_google_ads(google_raw, "test_client")
        assert pd.api.types.is_numeric_dtype(result["spend"])

    def test_source_platform_is_google(self, google_raw):
        result = normalize_google_ads(google_raw, "test_client")
        assert (result["source_platform"] == "google").all()

    def test_missing_spend_column_returns_zeros(self):
        bare = pd.DataFrame(
            {
                "customer_id": ["cust_1"] * 2,
                "campaign_id": ["gc1", "gc2"],
                "campaign_name": ["A", "B"],
                "ad_group_id": ["ag1", "ag2"],
                "ad_group_name": ["AG1", "AG2"],
                "date": ["2026-01-01", "2026-01-02"],
            }
        )
        result = normalize_google_ads(bare, "test_client")
        assert (result["spend"] == 0).all()
        assert (result["conversions"] == 0).all()


# ─── LINKEDIN CONNECTOR CONTRACT ─────────────────────────────────────────────


class TestLinkedInConnectorContract:
    def test_normalized_output_has_all_required_columns(self, linkedin_raw):
        result = normalize_linkedin_ads(linkedin_raw, "test_client")
        assert set(NORMALIZED_AD_COLUMNS).issubset(set(result.columns))

    def test_spend_is_numeric(self, linkedin_raw):
        result = normalize_linkedin_ads(linkedin_raw, "test_client")
        assert pd.api.types.is_numeric_dtype(result["spend"])

    def test_source_platform_is_linkedin(self, linkedin_raw):
        result = normalize_linkedin_ads(linkedin_raw, "test_client")
        assert (result["source_platform"] == "linkedin").all()

    def test_missing_conversions_column_returns_zeros(self):
        bare = pd.DataFrame(
            {
                "account_id": ["acc_1"] * 2,
                "campaign_id": ["lc1", "lc2"],
                "campaign_name": ["A", "B"],
                "date": ["2026-01-01", "2026-01-02"],
                "spend": [10.0, 20.0],
                "impressions": [100, 200],
                "clicks": [5, 10],
            }
        )
        result = normalize_linkedin_ads(bare, "test_client")
        assert (result["conversions"] == 0).all()


# ─── HUBSPOT CONNECTOR + VALIDATOR CONTRACT ───────────────────────────────────


class TestHubSpotConnectorDateContract:
    def test_normalize_accepts_iso_8601_property_dates(self):
        deals, contacts = _hubspot_api_payload(
            close_date="2026-01-15T18:30:00.000Z",
            deal_create_date="2026-01-05T12:00:00.000Z",
            contact_create_date="2026-01-01T12:00:00.000Z",
        )

        row = HubSpotConnector()._normalize(deals, contacts).iloc[0]

        assert row["close_date"] == pd.Timestamp("2026-01-15 18:30:00")
        assert row["create_date"] == pd.Timestamp("2026-01-05 12:00:00")
        assert row["lead_create_date"] == pd.Timestamp("2026-01-01 12:00:00")
        assert row["days_to_deal"] == 4

    def test_normalize_accepts_numeric_epoch_milliseconds(self):
        close_date = pd.Timestamp("2026-01-15T18:30:00Z")
        deal_create_date = pd.Timestamp("2026-01-05T12:00:00Z")
        contact_create_date = pd.Timestamp("2026-01-01T12:00:00Z")
        deals, contacts = _hubspot_api_payload(
            close_date=int(close_date.timestamp() * 1000),
            deal_create_date=str(int(deal_create_date.timestamp() * 1000)),
            contact_create_date=int(contact_create_date.timestamp() * 1000),
        )

        row = HubSpotConnector()._normalize(deals, contacts).iloc[0]

        assert row["close_date"] == close_date.tz_convert(None)
        assert row["create_date"] == deal_create_date.tz_convert(None)
        assert row["lead_create_date"] == contact_create_date.tz_convert(None)
        assert row["days_to_deal"] == 4

    def test_normalize_keeps_invalid_dates_missing(self):
        deals, contacts = _hubspot_api_payload(
            close_date="not-a-date",
            deal_create_date="",
            contact_create_date="also-not-a-date",
        )

        row = HubSpotConnector()._normalize(deals, contacts).iloc[0]

        assert row["close_date"] is None
        assert row["create_date"] is None
        assert row["lead_create_date"] is None
        assert row["days_to_deal"] is None


class TestHubSpotValidatorContract:
    def test_passes_on_clean_data(self, hubspot_raw):
        _, report = HubSpotValidator("test").validate(hubspot_raw)
        assert report.passed

    def test_catches_missing_required_columns(self):
        bad = pd.DataFrame({"deal_name": ["Deal A"]})
        _, report = HubSpotValidator("test").validate(bad)
        assert not report.passed
        assert any("Missing required columns" in e for e in report.errors)

    def test_deduplicates_duplicate_deal_ids(self, hubspot_raw):
        duped = pd.concat([hubspot_raw, hubspot_raw.iloc[:1]], ignore_index=True)
        result_df, report = HubSpotValidator("test").validate(duped)
        assert len(result_df) == len(hubspot_raw)
        assert any("duplicate" in w.lower() for w in report.warnings)

    def test_warns_on_low_utm_coverage(self):
        no_utm = pd.DataFrame(
            {
                "deal_id": [f"d{i}" for i in range(10)],
                "deal_stage": ["closedwon"] * 10,
                "amount": [1000.0] * 10,
                "create_date": pd.to_datetime(["2026-01-01"] * 10),
                "hs_source": ["PAID_SOCIAL"] * 10,
                "utm_campaign": [None] * 10,
            }
        )
        _, report = HubSpotValidator("test").validate(no_utm)
        assert any("utm_campaign" in w for w in report.warnings)


# ─── STRIPE VALIDATOR CONTRACT ────────────────────────────────────────────────


class TestStripeValidatorContract:
    def test_passes_on_clean_data(self, stripe_raw):
        _, report = StripeValidator("test").validate(stripe_raw)
        assert report.passed

    def test_catches_missing_required_columns(self):
        bad = pd.DataFrame({"payment_id": ["pi_1"], "amount_paid": [100.0]})
        _, report = StripeValidator("test").validate(bad)
        assert not report.passed

    def test_catches_negative_amount(self):
        neg = pd.DataFrame(
            {
                "payment_id": ["pi_1"],
                "customer_email": ["a@x.com"],
                "amount_paid": [-100.0],
                "status": ["succeeded"],
            }
        )
        _, report = StripeValidator("test").validate(neg)
        assert not report.passed

    def test_warns_on_high_refund_rate(self, stripe_raw):
        high_refund = stripe_raw.copy()
        high_refund["refund_amount"] = high_refund["amount_paid"] * 0.25
        _, report = StripeValidator("test").validate(high_refund)
        assert any("efund" in w for w in report.warnings)

    def test_deduplicates_duplicate_payment_ids(self, stripe_raw):
        duped = pd.concat([stripe_raw, stripe_raw.iloc[:1]], ignore_index=True)
        result_df, report = StripeValidator("test").validate(duped)
        assert len(result_df) == len(stripe_raw)
