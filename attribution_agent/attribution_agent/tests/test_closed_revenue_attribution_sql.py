from __future__ import annotations

import re
from pathlib import Path


SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "transforms"
    / "closed_revenue_attribution.sql"
)
SQL = SQL_PATH.read_text(encoding="utf-8")
COMPACT_SQL = re.sub(r"\s+", " ", SQL.lower())


def _section(start: str, end: str) -> str:
    return COMPACT_SQL.split(start, 1)[1].split(end, 1)[0]


def test_sql_only_selects_valid_closed_won_deals_from_the_current_run():
    closed_deals = _section("with closed_deals as (", "unique_closed_deal_emails as (")

    assert "regexp_replace(" in closed_deals
    assert "'[^a-z0-9]+'" in closed_deals
    assert ") in ('closedwon', 'won')" in closed_deals
    assert "like '%closedwon%'" not in closed_deals
    assert "coalesce(try_cast(amount as double), 0.0) > 0" in closed_deals
    assert "try_cast(close_date as timestamp)" in closed_deals
    assert "try_cast(create_date as timestamp)" in closed_deals
    assert ") is not null" in closed_deals
    assert "ingested_at >= cast('{run_started_at}' as timestamp)" in closed_deals


def test_stripe_resolution_prefers_deal_id_and_uses_only_unique_email_fallback():
    unique_email = _section("unique_closed_deal_emails as (", "prepared_payments as (")
    prepared = _section("prepared_payments as (", "resolved_payments as (")
    resolved = _section("resolved_payments as (", "payments_by_deal as (")

    assert "having count(distinct deal_id) = 1" in unique_email
    assert "p.hubspot_deal_id_key <> ''" in resolved
    assert "p.hubspot_deal_id_key = id_match.deal_id" in resolved
    assert "p.hubspot_deal_id_key = ''" in resolved
    assert "p.customer_email_key = email_match.contact_email_key" in resolved
    assert (
        "sum(greatest(amount_paid - refund_amount, 0.0)) as collected_revenue"
        in COMPACT_SQL
    )
    assert "ingested_at >= cast('{run_started_at}' as timestamp)" in prepared
    assert (
        COMPACT_SQL.count("ingested_at >= cast('{run_started_at}' as timestamp)") == 4
    )


def test_lead_milestone_is_observed_and_w_shape_without_it_uses_u_shape():
    touchpoints = _section("deal_touchpoints as (", "deduped_touchpoints as (")
    weighted = _section("weighted as (", "normalized as (")

    assert "lead_create_date as touchpoint_at" in touchpoints
    assert "where lead_create_date is not null" in touchpoints
    assert "coalesce(lead_create_date" not in touchpoints
    assert "'w_shape' and has_lead_touch = 0 and touch_count = 2 then 0.5" in weighted
    assert "'w_shape' and has_lead_touch = 0 then case" in weighted
    assert "when touch_index = 1 then 0.4" in weighted
    assert "when touch_index = touch_count then 0.4" in weighted


def test_campaign_matching_is_strict_aggregated_and_time_bounded():
    ad_inputs = _section("ad_spend_inputs as (", "ad_spend_daily as (")
    matched = _section("matched_touchpoints as (", ") select date_format(")

    assert "ingested_at >= cast('{run_started_at}' as timestamp)" in ad_inputs
    assert "a.source_platform = n.source_platform" in matched
    assert (
        "(n.crm_campaign_key <> '' and a.campaign_key = n.crm_campaign_key) "
        "or n.crm_campaign_key = ''"
    ) in matched
    assert "sum(a.total_spend)" in matched
    assert "group by" in matched
    assert "date_sub(cast(n.close_date as date), {lookback_days})" in matched
    assert "n.channel = a.channel" not in COMPACT_SQL


def test_unmatched_rows_are_explicit_and_tiktok_is_a_paid_social_source():
    assert "when m.has_ad_match = 0 then 'unattributed'" in COMPACT_SQL
    assert (
        "case when m.has_ad_match = 1 then m.source_platform "
        "else 'unattributed' end as source_platform"
    ) in COMPACT_SQL
    assert "when m.crm_campaign_key = '' then '(platform only)'" in COMPACT_SQL
    assert "m.source_platform in ('meta', 'linkedin', 'tiktok')" in COMPACT_SQL
    assert "utm_source_key in ('tiktok', 'tik_tok', 'tt')" in COMPACT_SQL


def test_scorecard_counts_current_run_spend_once_per_month_and_platform():
    performance = _section(
        "create or replace table {schema}.channel_performance as",
        "create or replace table {schema}.channel_performance_v2 as",
    )

    assert "current_run_ad_spend_by_platform as (" in performance
    assert "group by report_month, source_platform" in performance
    assert "sum(spend) as total_spend" in performance
    assert "full outer join current_run_ad_spend_by_platform" in performance
    assert "'' as utm_campaign" in performance
    assert "max(total_spend)" not in performance
    assert "ingested_at >= cast('{run_started_at}' as timestamp)" in performance


def test_cash_scorecard_uses_net_revenue_and_gross_refund_denominator():
    cash_performance = COMPACT_SQL.split(
        "create or replace table {schema}.channel_performance_v2 as", 1
    )[1]

    assert "sum(attributed_revenue) as net_collected_revenue" in cash_performance
    assert "sum(attributed_refunds) as refunded_revenue" in cash_performance
    assert (
        "coalesce(cash.refunded_revenue, 0.0) as refunded_revenue" in cash_performance
    )
    assert (
        "coalesce(cash.net_collected_revenue, 0.0) + "
        "coalesce(cash.refunded_revenue, 0.0)"
    ) in cash_performance
    assert (
        "coalesce(cash.net_collected_revenue, 0.0) / cp.total_spend" in cash_performance
    )
    assert "cp.utm_campaign = ar.campaign" not in cash_performance


def test_template_renders_all_runtime_bounds():
    rendered = SQL.format(
        schema="workspace.attribution_acme",
        attribution_model="w_shape",
        lookback_days=45,
        run_started_at="2026-09-03T12:34:56+00:00",
    )
    compact = re.sub(r"\s+", " ", rendered.lower())

    assert "{schema}" not in rendered
    assert "{attribution_model}" not in rendered
    assert "{lookback_days}" not in rendered
    assert "{run_started_at}" not in rendered
    assert "date_sub(cast(n.close_date as date), 45)" in compact
    assert "ingested_at >= cast('2026-09-03t12:34:56+00:00' as timestamp)" in compact
