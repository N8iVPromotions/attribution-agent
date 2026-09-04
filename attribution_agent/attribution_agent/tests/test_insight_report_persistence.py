from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agents.insight.insight_agent import InsightReport
from api.auth import AuthPrincipal
from api.routers import reports
from config.rbac_config import Role
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
        lambda frame, schema, table, keys, **kwargs: writes.append(
            (frame.to_dict("records")[0], schema, table, keys, kwargs)
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
        "client_name",
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
        "data_version",
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
        {"update_on_match": False},
    )


def test_report_id_is_content_addressed_and_excludes_status_and_run_id():
    record = {
        **_report().to_dict(),
        "agency_id": "agency-a",
        "run_id": "run-1",
        "status": "generated",
    }

    report_id = db.insight_report_id(record)

    assert report_id == db.insight_report_id(
        {**record, "run_id": "run-2", "status": "delivered"}
    )
    assert report_id != db.insight_report_id(
        {**record, "narrative": "Changed after approval."}
    )
    assert report_id != db.insight_report_id(
        {**record, "generated_at": "2026-09-03T12:00:01+00:00"}
    )


@pytest.mark.parametrize("generated_at", [None, "", "NaT"])
def test_report_id_rejects_missing_or_invalid_generated_at(generated_at):
    record = {
        **_report().to_dict(),
        "agency_id": "agency-a",
        "generated_at": generated_at,
    }

    with pytest.raises(ValueError, match="generated_at"):
        db.insight_report_id(record)


def test_status_update_changes_only_status_and_verifies_persistence(monkeypatch):
    statements = []
    monkeypatch.setattr(db, "ensure_insight_reports_table", lambda: None)
    monkeypatch.setattr(db, "_run_sql", lambda sql: statements.append(sql))
    monkeypatch.setattr(db, "_fetch_rows", lambda query: [{"status": "delivered"}])

    db.update_insight_report_status("report-a", "delivered")

    assert "SET status = 'delivered'" in statements[0]
    assert "WHERE report_id = 'report-a'" in statements[0]


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


def test_report_generation_logs_and_reraises(monkeypatch, caplog):
    def fail(client_id, run_id):
        raise RuntimeError("warehouse unavailable")

    monkeypatch.setattr(reports, "_generate_and_store_report", fail)

    with (
        caplog.at_level("ERROR"),
        pytest.raises(RuntimeError, match="warehouse unavailable"),
    ):
        reports._run_report_generation("client-a", "api-run-3")

    assert "Report generation failed for client-a" in caplog.text


def test_generate_endpoint_completes_before_returning(monkeypatch):
    from config import client_config

    registry = {"client-a": SimpleNamespace()}
    monkeypatch.setattr(client_config, "CLIENT_REGISTRY", registry)
    monkeypatch.setattr(client_config, "reload_client_registry", lambda: registry)
    monkeypatch.setattr(
        reports,
        "_run_report_generation",
        lambda client_id, run_id: "report-id",
    )

    result = asyncio.run(
        reports.trigger_report_generation("client-a", AuthPrincipal(Role.ADMIN))
    )

    assert result["report_id"] == "report-id"
    assert result["status"] == "generated"


def test_generate_endpoint_returns_non_2xx_when_work_fails(monkeypatch):
    from config import client_config

    registry = {"client-a": SimpleNamespace()}
    monkeypatch.setattr(client_config, "CLIENT_REGISTRY", registry)
    monkeypatch.setattr(client_config, "reload_client_registry", lambda: registry)
    monkeypatch.setattr(
        reports,
        "_run_report_generation",
        lambda client_id, run_id: (_ for _ in ()).throw(
            RuntimeError("warehouse unavailable")
        ),
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            reports.trigger_report_generation("client-a", AuthPrincipal(Role.ADMIN))
        )

    assert exc.value.status_code == 502
