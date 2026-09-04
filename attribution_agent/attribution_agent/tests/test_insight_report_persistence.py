from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agents.insight.insight_agent import InsightReport
from api.routers import reports
from utils import databricks_writer as db


def _report(*, report_month: str = "2026-08", total_pipeline: float = 1_000.0):
    return InsightReport(
        client_id="client-a",
        client_name="Client A",
        report_month=report_month,
        narrative="Evidence-backed report.",
        key_findings=["Paid Search led attributed pipeline."],
        top_channel="Paid Search",
        total_pipeline=total_pipeline,
        total_spend=200.0,
        overall_roi=5.0,
        collected_revenue=800.0,
        refund_rate=0.05,
        true_roi=4.0,
        attribution_model="last_touch",
        generated_at="2026-09-03T12:00:00+00:00",
    )


def test_write_insight_report_upserts_every_ddl_field_with_stable_id(monkeypatch):
    writes = []
    monkeypatch.setattr(db, "ensure_insight_reports_table", lambda: None)
    monkeypatch.setattr(
        db,
        "_upsert_dataframe",
        lambda frame, schema, table, keys: writes.append(
            (frame.to_dict("records")[0], schema, table, keys)
        ),
    )
    record = {
        **_report().to_dict(),
        "agency_id": "agency-a",
        "run_id": "run-123",
        "prompt_version": "executive-reporting:v2",
        "model_id": "claude-sonnet",
        "input_tokens": 120,
        "output_tokens": 80,
        "cache_read_tokens": 40,
        "status": "generated",
    }

    first_id = db.write_insight_report(record)
    second_id = db.write_insight_report({**record, "status": "delivered"})

    expected_columns = {
        "report_id",
        "client_id",
        "agency_id",
        "report_month",
        "narrative",
        "key_findings",
        "top_channel",
        "total_pipeline",
        "total_spend",
        "overall_roi",
        "collected_revenue",
        "refund_rate",
        "true_roi",
        "attribution_model",
        "generated_at",
        "run_id",
        "prompt_version",
        "model_id",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "status",
    }
    assert first_id == second_id
    assert set(writes[0][0]) == expected_columns
    assert json.loads(writes[0][0]["key_findings"]) == record["key_findings"]
    assert writes[0][0]["status"] == "generated"
    assert writes[1][0]["status"] == "delivered"
    assert writes[0][1:] == (
        db._OPS_SCHEMA,
        "insight_reports",
        ["report_id"],
    )


def test_api_generation_persists_only_reports_with_attributed_revenue(monkeypatch):
    persisted = []
    monkeypatch.setattr(
        reports,
        "generate_insight_report",
        lambda client_id, run_id: _report(),
        raising=False,
    )
    monkeypatch.setattr(
        reports,
        "get_client",
        lambda client_id: SimpleNamespace(agency_id="agency-a"),
        raising=False,
    )
    monkeypatch.setattr(
        reports,
        "write_insight_report",
        lambda record: persisted.append(record) or "report-id",
        raising=False,
    )

    report_id = reports._generate_and_store_report("client-a", "api-run-1")

    assert report_id == "report-id"
    assert persisted[0]["agency_id"] == "agency-a"
    assert persisted[0]["run_id"] == "api-run-1"
    assert persisted[0]["status"] == "generated"


def test_api_generation_rejects_report_without_attributed_revenue(monkeypatch):
    monkeypatch.setattr(
        reports,
        "generate_insight_report",
        lambda client_id, run_id: _report(report_month="N/A", total_pipeline=0.0),
        raising=False,
    )
    monkeypatch.setattr(
        reports,
        "write_insight_report",
        lambda record: pytest.fail("no-data report must not be persisted"),
        raising=False,
    )

    with pytest.raises(RuntimeError, match="no attribution data"):
        reports._generate_and_store_report("client-a", "api-run-2")


def test_report_response_exposes_status_with_legacy_default():
    assert reports._row_to_response({"status": "delivered"}).status == "delivered"
    assert reports._row_to_response({}).status == "generated"


def test_background_generation_logs_and_reraises(monkeypatch, caplog):
    def fail(client_id, run_id):
        raise RuntimeError("warehouse unavailable")

    monkeypatch.setattr(reports, "_generate_and_store_report", fail)

    with (
        caplog.at_level("ERROR"),
        pytest.raises(RuntimeError, match="warehouse unavailable"),
    ):
        reports._run_report_generation("client-a", "api-run-3")

    assert "Background generation failed for client-a" in caplog.text
