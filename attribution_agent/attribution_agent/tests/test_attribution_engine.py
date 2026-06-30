import pandas as pd
import pytest

from attribution_engine import (
    UNATTRIBUTED,
    attribute_conversions,
    channel_performance,
    conversions_from_hubspot,
    conversions_from_stripe,
    reconcile_conversions,
    run_attribution,
)


CLIENT = "acme_co"


def _ads():
    """Two platforms, two campaigns, spend across the window."""
    return pd.DataFrame(
        [
            {
                "client_id": CLIENT,
                "source_platform": "meta",
                "campaign_name": "Spring Sale",
                "utm_campaign": "spring_sale",
                "date": "2026-05-01",
                "spend": 500.0,
            },
            {
                "client_id": CLIENT,
                "source_platform": "meta",
                "campaign_name": "Spring Sale",
                "utm_campaign": "spring_sale",
                "date": "2026-05-10",
                "spend": 500.0,
            },
            {
                "client_id": CLIENT,
                "source_platform": "tiktok",
                "campaign_name": "Awareness",
                "utm_campaign": "awareness",
                "date": "2026-05-05",
                "spend": 250.0,
            },
        ]
    )


def _hubspot():
    return pd.DataFrame(
        [
            # Closed/won, attributable to meta/spring_sale
            {
                "deal_id": "D1",
                "deal_stage": "closedwon",
                "amount": 2000.0,
                "close_date": "2026-05-15",
                "create_date": "2026-05-01",
                "contact_email": "buyer@acme.com",
                "utm_source": "facebook",
                "utm_campaign": "spring_sale",
                "utm_medium": "paid_social",
            },
            # Open deal — must be ignored
            {
                "deal_id": "D2",
                "deal_stage": "qualifiedtobuy",
                "amount": 9999.0,
                "close_date": "2026-05-16",
                "create_date": "2026-05-02",
                "contact_email": "lead@acme.com",
                "utm_source": "google",
                "utm_campaign": "brand",
                "utm_medium": "paid_search",
            },
        ]
    )


def _stripe():
    return pd.DataFrame(
        [
            # Same deal as D1, paid via Stripe — should dedupe, Stripe revenue wins
            {
                "payment_id": "P1",
                "status": "succeeded",
                "amount_paid": 2500.0,
                "refund_amount": 0.0,
                "created_at": "2026-05-16",
                "customer_email": "buyer@acme.com",
                "deal_id": "D1",
            },
            # Standalone checkout, no CRM record, no utm → unattributable
            {
                "payment_id": "P2",
                "status": "succeeded",
                "amount_paid": 300.0,
                "refund_amount": 50.0,
                "created_at": "2026-05-20",
                "customer_email": "walkin@acme.com",
                "deal_id": "",
            },
        ]
    )


def test_hubspot_only_closed_won_conversions():
    convs = conversions_from_hubspot(_hubspot(), CLIENT)
    assert len(convs) == 1
    assert convs[0].deal_id == "D1"
    assert convs[0].revenue == 2000.0


def test_stripe_net_of_refunds_and_status():
    convs = conversions_from_stripe(_stripe(), CLIENT)
    revenues = sorted(c.revenue for c in convs)
    assert revenues == [250.0, 2500.0]  # P2 is 300 - 50 refund


def test_reconcile_dedupes_and_prefers_stripe_revenue():
    hub = conversions_from_hubspot(_hubspot(), CLIENT)
    stripe = conversions_from_stripe(_stripe(), CLIENT)
    reconciled = reconcile_conversions(hub, stripe, prefer="stripe")
    # D1 collapses to one conversion (not two); standalone P2 remains
    ids = sorted(c.conversion_id for c in reconciled)
    assert ids == ["hubspot:D1", "stripe:P2"]
    d1 = next(c for c in reconciled if c.conversion_id == "hubspot:D1")
    assert d1.revenue == 2500.0  # Stripe cash beats CRM amount
    assert d1.utm_campaign == "spring_sale"  # inherited CRM identity


def test_attribute_routes_revenue_to_matched_platform():
    hub = conversions_from_hubspot(_hubspot(), CLIENT)
    rows = attribute_conversions(hub, _ads(), "last_touch")
    meta_rows = rows[rows["source_platform"] == "meta"]
    assert pytest.approx(meta_rows["attributed_revenue"].sum()) == 2000.0
    assert (rows["source_platform"] == UNATTRIBUTED).sum() == 0


def test_unattributed_bucket_for_unmatched_revenue():
    stripe = conversions_from_stripe(_stripe(), CLIENT)
    # Stripe-only convs carry no utm → cannot match any ad
    rows = attribute_conversions(stripe, _ads(), "last_touch")
    assert set(rows["source_platform"]) == {UNATTRIBUTED}


def test_channel_performance_computes_roas():
    hub = conversions_from_hubspot(_hubspot(), CLIENT)
    rows = attribute_conversions(hub, _ads(), "last_touch")
    perf = channel_performance(rows, _ads(), "last_touch", CLIENT)
    meta = perf[perf["source_platform"] == "meta"].iloc[0]
    assert meta["spend"] == 1000.0
    assert meta["attributed_revenue"] == 2000.0
    assert meta["roas"] == 2.0
    # tiktok had spend but no attributed revenue → roas 0
    tiktok = perf[perf["source_platform"] == "tiktok"].iloc[0]
    assert tiktok["spend"] == 250.0
    assert tiktok["attributed_revenue"] == 0.0


def test_run_attribution_end_to_end_balances_revenue():
    result = run_attribution(
        client_id=CLIENT,
        ads=_ads(),
        model="last_touch",
        hubspot_df=_hubspot(),
        stripe_df=_stripe(),
    )
    # D1 (2500 via Stripe) + standalone P2 (250) = 2750 total
    assert result.total_revenue == 2750.0
    assert result.attributed_revenue == 2500.0
    assert result.unattributed_revenue == 250.0
    assert (
        result.attributed_revenue + result.unattributed_revenue == result.total_revenue
    )


def test_multi_touch_splits_credit_across_campaigns():
    ads = pd.DataFrame(
        [
            {
                "client_id": CLIENT,
                "source_platform": "meta",
                "campaign_name": "A",
                "utm_campaign": "a",
                "date": "2026-05-01",
                "spend": 100.0,
            },
            {
                "client_id": CLIENT,
                "source_platform": "meta",
                "campaign_name": "B",
                "utm_campaign": "b",
                "date": "2026-05-10",
                "spend": 100.0,
            },
        ]
    )
    # utm_campaign empty → conversion matches all meta campaigns in window (2 touches)
    hub = pd.DataFrame(
        [
            {
                "deal_id": "D9",
                "deal_stage": "closedwon",
                "amount": 1000.0,
                "close_date": "2026-05-15",
                "create_date": "2026-05-01",
                "contact_email": "x@acme.com",
                "utm_source": "facebook",
                "utm_campaign": "",
                "utm_medium": "paid_social",
            },
        ]
    )
    convs = conversions_from_hubspot(hub, CLIENT)
    rows = attribute_conversions(convs, ads, "linear")
    assert len(rows) == 2
    assert pytest.approx(rows["attributed_revenue"].sum()) == 1000.0
    assert pytest.approx(sorted(rows["attributed_revenue"])) == [500.0, 500.0]
