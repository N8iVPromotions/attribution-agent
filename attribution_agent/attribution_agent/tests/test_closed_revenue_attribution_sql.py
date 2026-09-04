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


def test_closed_deals_are_current_exact_wins_inside_the_explicit_period():
    prepared = _section("with prepared_deals as (", "closed_deals as (")
    closed = _section("closed_deals as (", "deal_identity_inputs as (")

    assert "ingested_at >= cast('{run_started_at}' as timestamp)" in prepared
    assert "row_number() over (" in prepared
    assert "partition by trim(cast(deal_id as string))" in prepared
    assert "try_cast(close_datetime as timestamp) as close_date" in prepared
    assert "deal_rank = 1" in closed
    assert "deal_stage_key in ({hubspot_closed_won_stage_keys})" in closed
    assert "pipeline_value > 0" in closed
    assert "close_date >= cast('{period_start}' as timestamp)" in closed
    assert "close_date < cast('{period_end}' as timestamp)" in closed


def test_each_deal_has_one_non_weighted_source_match_row():
    assert "'source_match' as touchpoint_role" in COMPACT_SQL
    assert "1.0 as credit" in COMPACT_SQL
    assert "m.pipeline_value as attributed_pipeline" in COMPACT_SQL
    assert "union all" not in COMPACT_SQL
    assert "touch_index" not in COMPACT_SQL
    assert "raw_credit" not in COMPACT_SQL
    assert "lead_create_date" not in COMPACT_SQL


def test_ad_evidence_is_current_globally_bounded_and_pre_close():
    inputs = _section("ad_evidence_inputs as (", "ad_evidence_daily as (")
    matched = _section("matched_deals as (", "stripe_payment_history as (")

    assert "ingested_at >= cast('{run_started_at}' as timestamp)" in inputs
    assert (
        "try_cast(date as date) >= date_sub( cast('{period_start}' as date), "
        "{lookback_days} )"
    ) in inputs
    assert "try_cast(date as date) < cast('{period_end}' as date)" in inputs
    assert "a.spend_date >= date_sub(" in matched
    assert (
        "to_date(from_utc_timestamp(d.close_date, '{report_timezone}')), "
        "{lookback_days}"
    ) in matched
    assert (
        "a.spend_date <= to_date( from_utc_timestamp(d.close_date, "
        "'{report_timezone}') )"
    ) in matched


def test_campaign_matching_is_strict_and_blank_campaign_is_platform_only():
    matched = _section("matched_deals as (", "stripe_payment_history as (")

    assert "a.source_platform = d.source_platform" in matched
    assert (
        "(d.crm_campaign_key <> '' and a.campaign_key = d.crm_campaign_key) "
        "or d.crm_campaign_key = ''"
    ) in matched
    assert "when m.has_ad_match = 0 then 'unattributed'" in COMPACT_SQL
    assert "when m.crm_campaign_key = '' then '(platform only)'" in COMPACT_SQL
    assert (
        "when m.has_ad_match = 1 then m.source_platform else 'unattributed'"
        in COMPACT_SQL
    )


def test_stripe_uses_current_validated_history_and_exact_deal_ids_only():
    history = _section("stripe_payment_history as (", "payments_by_deal as (")
    payments = _section("payments_by_deal as (", ") select '{report_month}'")

    assert "from {schema}.stripe_payments_raw" in history
    assert "ingested_at >= cast('{run_started_at}' as timestamp)" in history
    assert "upper(trim(coalesce(currency, ''))) = '{reporting_currency}'" in history
    assert "partition by trim(cast(payment_id as string))" in history
    assert "payment_rank = 1" in payments
    assert "hubspot_deal_id_key <> ''" in payments
    assert "hubspot_deal_id_key as deal_id" in payments
    assert "customer_email" not in COMPACT_SQL
    assert "contact_email" not in COMPACT_SQL
    assert (
        "sum(greatest(amount_paid - refund_amount, 0.0)) as collected_revenue"
        in payments
    )
    assert "sum(greatest(refund_amount, 0.0)) as refunded_revenue" in payments


def test_report_month_is_explicit_and_not_inferred_from_source_dates():
    assert COMPACT_SQL.count("'{report_month}' as report_month") == 2
    assert "date_format(" not in COMPACT_SQL
    assert "where report_month = '{report_month}'" in COMPACT_SQL


def test_target_period_is_atomically_replaced_without_erasing_history():
    assert "insert overwrite" not in COMPACT_SQL
    assert "create or replace table" not in COMPACT_SQL
    assert "delete from" not in COMPACT_SQL
    assert COMPACT_SQL.count("create table if not exists") == 3
    assert (
        "insert into {schema}.attributed_revenue replace where report_month = "
        "'{report_month}' and attribution_model = '{attribution_model}'" in COMPACT_SQL
    )
    assert (
        "insert into {schema}.channel_performance replace where report_month = "
        "'{report_month}'" in COMPACT_SQL
    )
    assert (
        "insert into {schema}.channel_performance_v2 replace where report_month = "
        "'{report_month}'" in COMPACT_SQL
    )
    assert (
        COMPACT_SQL.count(
            "where report_month = '{report_month}' and attribution_model = "
            "'{attribution_model}'"
        )
        == 3
    )


def test_scorecard_uses_only_current_run_spend_inside_the_period():
    performance = _section(
        "insert into {schema}.channel_performance replace where",
        "create table if not exists {schema}.channel_performance_v2 (",
    )

    assert "period_ad_spend_by_platform as (" in performance
    assert "sum(spend) as total_spend" in performance
    assert "full outer join period_ad_spend_by_platform" in performance
    assert "ingested_at >= cast('{run_started_at}' as timestamp)" in performance
    assert "try_cast(date as date) >= cast('{period_start}' as date)" in performance
    assert "try_cast(date as date) < cast('{period_end}' as date)" in performance
    assert "sum(total_spend)" not in performance


def test_tiktok_is_paid_social_and_cash_scorecard_uses_net_revenue():
    cash_performance = COMPACT_SQL.split(
        "insert into {schema}.channel_performance_v2 replace where", 1
    )[1]

    assert "hs_source_key = 'paid_social'" in COMPACT_SQL
    assert "source_detail_key in ('tiktok', 'tiktokads')" in COMPACT_SQL
    assert "source_detail_key like" not in COMPACT_SQL
    assert "source_platform in ('meta', 'linkedin', 'tiktok')" in COMPACT_SQL
    assert "sum(attributed_revenue) as net_collected_revenue" in cash_performance
    assert "sum(attributed_refunds) as refunded_revenue" in cash_performance
    assert (
        "coalesce(cash.net_collected_revenue, 0.0) / cp.total_spend" in cash_performance
    )
    assert "where cp.report_month = '{report_month}'" in cash_performance


def test_template_renders_every_runtime_boundary():
    rendered = SQL.format(
        schema="workspace.attribution_acme",
        attribution_model="last_touch",
        report_month="2026-08",
        period_start="2026-08-01T04:00:00+00:00",
        period_end="2026-09-01T04:00:00+00:00",
        lookback_days=45,
        run_started_at="2026-09-03T12:34:56+00:00",
        hubspot_closed_won_stage_keys="'closedwon', 'customstage123'",
        reporting_currency="USD",
        report_timezone="America/New_York",
    )
    compact = re.sub(r"\s+", " ", rendered.lower())

    for parameter in (
        "{schema}",
        "{attribution_model}",
        "{report_month}",
        "{period_start}",
        "{period_end}",
        "{lookback_days}",
        "{run_started_at}",
        "{hubspot_closed_won_stage_keys}",
        "{reporting_currency}",
        "{report_timezone}",
    ):
        assert parameter not in rendered
    assert "'2026-08' as report_month" in compact
    assert "close_date >= cast('2026-08-01t04:00:00+00:00' as timestamp)" in compact
    assert "close_date < cast('2026-09-01t04:00:00+00:00' as timestamp)" in compact
    assert (
        "to_date(from_utc_timestamp(d.close_date, 'america/new_york')), 45" in compact
    )
    assert "ingested_at >= cast('2026-09-03t12:34:56+00:00' as timestamp)" in compact
    assert "deal_stage_key in ('closedwon', 'customstage123')" in compact
    assert "upper(trim(coalesce(currency, ''))) = 'usd'" in compact
