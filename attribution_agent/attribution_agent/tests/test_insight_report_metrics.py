from __future__ import annotations

import pytest

from agents.insight import insight_agent
from agents.intelligence import n8iv_agents
from config.client_config import ClientConfig


def test_generate_report_uses_governed_metrics_not_llm_values(monkeypatch):
    config = ClientConfig(
        client_id="acme",
        client_name="Acme",
        databricks_schema="workspace.attribution_acme",
        attribution_model="last_touch",
    )
    governed_rows = [
        {
            "report_month": "2026-08",
            "channel": "Meta",
            "pipeline_value": 100.0,
            "total_spend": 40.0,
            "collected_revenue": 80.0,
            "refunded_revenue": 20.0,
            "refund_rate": 0.25,
            "ingested_at": "2026-09-01T10:00:00Z",
        },
        {
            "report_month": "2026-08",
            "channel": "Paid Search",
            "pipeline_value": 900.0,
            "total_spend": 160.0,
            "collected_revenue": 320.0,
            "refunded_revenue": 40.0,
            "refund_rate": 0.125,
            "ingested_at": "2026-09-01T10:05:00Z",
        },
    ]
    inflated_llm_response = {
        "report_month": "2099-12",
        "narrative": "AI-authored narrative.",
        "key_findings": ["AI-authored finding."],
        "top_channel": "Imaginary Channel",
        "total_pipeline": 9_999_999.0,
        "total_spend": 1.0,
        "overall_roi": 9_999_999.0,
        "collected_revenue": 8_888_888.0,
        "refund_rate": 0.99,
        "true_roi": 8_888_888.0,
    }

    monkeypatch.setattr(insight_agent, "get_client", lambda _client_id: config)
    monkeypatch.setattr(
        insight_agent,
        "_fetch_channel_performance",
        lambda _config, _model, report_month: governed_rows,
    )
    monkeypatch.setattr(
        n8iv_agents,
        "run_revenue_analyst_agent",
        lambda **_kwargs: "Analyst output.",
    )
    monkeypatch.setattr(
        n8iv_agents,
        "run_executive_reporting_agent",
        lambda **_kwargs: inflated_llm_response,
    )

    report = insight_agent.generate_insight_report("acme", report_month="2026-08")

    assert report.narrative == "AI-authored narrative."
    assert report.key_findings == ["AI-authored finding."]
    assert report.report_month == "2026-08"
    assert report.top_channel == "Paid Search"
    assert report.total_pipeline == 1_000.0
    assert report.total_spend == 200.0
    assert report.overall_roi == 5.0
    assert report.collected_revenue == 400.0
    assert report.refund_rate == pytest.approx(60.0 / 460.0, abs=0.0001)
    assert report.true_roi == 2.0


def test_no_data_report_retains_requested_month(monkeypatch):
    config = ClientConfig(
        client_id="acme",
        client_name="Acme",
        databricks_schema="workspace.attribution_acme",
        attribution_model="last_touch",
    )
    monkeypatch.setattr(insight_agent, "get_client", lambda _client_id: config)
    monkeypatch.setattr(
        insight_agent,
        "_fetch_channel_performance",
        lambda _config, _model, report_month: [],
    )

    report = insight_agent.generate_insight_report("acme", report_month="2026-08")

    assert report.report_month == "2026-08"
    assert report.total_pipeline == 0.0


def test_governed_metrics_handle_spend_only_rows():
    metrics = insight_agent._derive_governed_metrics(
        [
            {
                "report_month": "2026-08",
                "channel": "Meta",
                "pipeline_value": 0.0,
                "total_spend": 25.0,
            },
            {
                "report_month": "2026-08",
                "channel": "Paid Search",
                "pipeline_value": 0.0,
                "total_spend": 60.0,
            },
        ]
    )

    assert metrics.report_month == "2026-08"
    assert metrics.top_channel == "Paid Search"
    assert metrics.total_pipeline == 0.0
    assert metrics.total_spend == 85.0
    assert metrics.overall_roi == 0.0
    assert metrics.collected_revenue == 0.0
    assert metrics.refund_rate == 0.0
    assert metrics.true_roi == 0.0


def test_unattributed_revenue_is_excluded_from_attributed_metrics():
    metrics = insight_agent._derive_governed_metrics(
        [
            {
                "report_month": "2026-08",
                "channel": "Unattributed",
                "pipeline_value": 9_000.0,
                "total_spend": 0.0,
                "collected_revenue": 8_000.0,
                "refunded_revenue": 1_000.0,
            },
            {
                "report_month": "2026-08",
                "channel": "Paid Search",
                "pipeline_value": 1_000.0,
                "total_spend": 200.0,
                "collected_revenue": 800.0,
                "refunded_revenue": 100.0,
            },
        ]
    )

    assert metrics.top_channel == "Paid Search"
    assert metrics.total_pipeline == 1_000.0
    assert metrics.total_spend == 200.0
    assert metrics.overall_roi == 5.0
    assert metrics.collected_revenue == 800.0
    assert metrics.refund_rate == 0.1111
    assert metrics.true_roi == 4.0


def test_fully_unattributed_revenue_produces_no_deliverable_metrics():
    metrics = insight_agent._derive_governed_metrics(
        [
            {
                "report_month": "2026-08",
                "channel": "Unattributed",
                "pipeline_value": 9_000.0,
                "total_spend": 0.0,
                "collected_revenue": 8_000.0,
                "refunded_revenue": 1_000.0,
            }
        ]
    )

    assert metrics.top_channel == "Unknown"
    assert metrics.total_pipeline == 0.0
    assert metrics.collected_revenue == 0.0
    assert metrics.refund_rate == 0.0
    assert metrics.true_roi == 0.0


def test_narrative_prompts_do_not_present_unattributed_revenue_as_attributed(
    monkeypatch,
):
    config = ClientConfig(
        client_id="acme",
        client_name="Acme",
        databricks_schema="workspace.attribution_acme",
        attribution_model="last_touch",
    )
    rows = [
        {
            "report_month": "2026-08",
            "channel": "Unattributed",
            "deals_count": 9,
            "pipeline_value": 9_000.0,
            "total_spend": 0.0,
            "collected_revenue": 8_000.0,
        },
        {
            "report_month": "2026-08",
            "channel": "Paid Search",
            "deals_count": 1,
            "pipeline_value": 1_000.0,
            "total_spend": 200.0,
            "collected_revenue": 800.0,
        },
    ]

    prompt = insight_agent._build_prompt(config, rows, "last_touch")
    fallback = insight_agent._build_fallback_report(config, rows, "last_touch")

    assert "Attributed pipeline value: $1,000" in prompt
    assert (
        "Unattributed CRM pipeline (excluded from attributed totals): $9,000" in prompt
    )
    assert "Attributed pipeline: $1,000 across 1 deals" in fallback["narrative"]

    calls = []

    def fake_call(agent_name, message, **_kwargs):
        calls.append((agent_name, message))
        return "{}" if agent_name == "executive-reporting" else "analysis"

    monkeypatch.setattr(n8iv_agents, "_call_agent", fake_call)
    analyst_output = n8iv_agents.run_revenue_analyst_agent(
        "acme", "Acme", rows, "last_touch"
    )
    n8iv_agents.run_executive_reporting_agent(
        "acme", "Acme", analyst_output, rows, "last_touch"
    )

    assert "$1,000 attributed pipeline" in calls[0][1]
    assert "$9,000 unattributed CRM pipeline" in calls[0][1]
    assert "Attribution method: CRM Source Match" in calls[0][1]
    assert "A supplied campaign must match exactly" in calls[0][1]
    assert "Attribution model: last_touch" not in calls[0][1]
    assert "- total_pipeline: 1000.0" in calls[1][1]
    assert "- unattributed_pipeline_excluded_from_roi: 9000.0" in calls[1][1]
    assert "Attribution method: CRM Source Match" in calls[1][1]
    assert "A supplied campaign must match exactly" in calls[1][1]
    assert "Attribution model: last_touch" not in calls[1][1]


def test_report_data_fingerprint_ignores_refresh_time_and_row_order():
    rows = [
        {
            "report_month": "2026-08",
            "channel": "Paid Search",
            "pipeline_value": 1000.0,
            "total_spend": 200,
            "ingested_at": "2026-09-01T12:00:00Z",
        },
        {
            "report_month": "2026-08",
            "channel": "Unattributed",
            "pipeline_value": 50,
            "total_spend": 0,
            "ingested_at": "2026-09-01T12:00:00Z",
        },
    ]
    refreshed = [
        {**row, "ingested_at": "2026-09-03T12:00:00Z"} for row in reversed(rows)
    ]

    baseline = insight_agent.report_data_fingerprint(rows)

    assert baseline == insight_agent.report_data_fingerprint(refreshed)
    assert baseline != insight_agent.report_data_fingerprint(
        [{**rows[0], "pipeline_value": 999.0}, rows[1]]
    )
