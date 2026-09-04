from __future__ import annotations

import json

from config.agency_config import AgencyConfig
from utils import report_approval


def _agency(**changes) -> AgencyConfig:
    values = {
        "agency_id": "agency-a",
        "agency_name": "Agency A",
        "brand_color": "123456",
        "brand_logo_url": "https://example.com/logo.png",
        "sender_name": "Agency Analytics",
        "sender_email": "reports@example.com",
        "reply_to": "reply@example.com",
        "powerbi_workspace_url": "https://example.com/dashboard",
    }
    values.update(changes)
    return AgencyConfig(**values)


def test_approval_id_is_stable_and_report_specific():
    first = report_approval.report_delivery_approval_id("report-a", "config-a")
    assert first == report_approval.report_delivery_approval_id("report-a", "config-a")
    assert first != report_approval.report_delivery_approval_id("report-b", "config-a")
    assert first != report_approval.report_delivery_approval_id("report-a", "config-b")


def test_delivery_config_fingerprint_changes_with_recipient_or_comms_config():
    baseline = report_approval.report_delivery_config_fingerprint(
        recipient_email="client@example.com",
        agency_config=_agency(),
    )
    assert baseline != report_approval.report_delivery_config_fingerprint(
        recipient_email="other@example.com",
        agency_config=_agency(),
    )
    assert baseline != report_approval.report_delivery_config_fingerprint(
        recipient_email="client@example.com",
        agency_config=_agency(powerbi_workspace_url="https://example.com/new"),
    )


def test_approval_insert_never_resets_existing_status(monkeypatch):
    statements = []
    monkeypatch.setattr(
        "utils.databricks_writer.ensure_approval_queue_table", lambda: None
    )
    monkeypatch.setattr(
        "utils.databricks_writer._run_sql", lambda sql: statements.append(sql)
    )

    action_id = report_approval.ensure_report_delivery_approval(
        report_id="report-a",
        client_id="client-a",
        agency_id="agency-a",
        report_month="2026-08",
        attribution_model="last_touch",
        recipient_email="client@example.com",
        delivery_config_fingerprint="config-a",
        description="Approve report O'Reilly",
    )

    assert action_id == report_approval.report_delivery_approval_id(
        "report-a", "config-a"
    )
    statement = statements[0]
    assert "WHEN NOT MATCHED THEN INSERT" in statement
    assert "WHEN MATCHED" not in statement
    assert "O''Reilly" in statement
    payload_literal = statement.split("AS payload_json", 1)[0]
    assert json.dumps("report-a")[1:-1] in payload_literal
    assert "client@example.com" in payload_literal
    assert "config-a" in payload_literal


def test_fetch_approved_report_is_bound_to_period_model_and_undelivered(monkeypatch):
    queries = []
    monkeypatch.setattr(
        "utils.databricks_writer.ensure_insight_reports_table", lambda: None
    )
    monkeypatch.setattr(
        "utils.databricks_writer.ensure_approval_queue_table", lambda: None
    )
    monkeypatch.setattr(
        report_approval,
        "_fetch_one",
        lambda query: queries.append(query) or {"report_id": "report-a"},
    )

    row = report_approval.fetch_latest_approved_report(
        client_id="client-a",
        agency_id="agency-a",
        report_month="2026-08",
        attribution_model="last_touch",
        data_version="sha256:data-a",
        delivery_config_fingerprint="config-a",
    )

    assert row == {"report_id": "report-a"}
    query = queries[0]
    assert "report.client_id = 'client-a'" in query
    assert "report.agency_id = 'agency-a'" in query
    assert "report.report_month = '2026-08'" in query
    assert "report.attribution_model = 'last_touch'" in query
    assert "report.data_version = 'sha256:data-a'" in query
    assert "report.status = 'generated'" in query
    assert "approval.status = 'approved'" in query
    assert "delivery_config_fingerprint" in query
    assert "config-a" in query


def test_approval_status_requires_the_delivery_fingerprint(monkeypatch):
    queries = []
    monkeypatch.setattr(
        "utils.databricks_writer.ensure_approval_queue_table", lambda: None
    )
    monkeypatch.setattr(
        report_approval,
        "_fetch_one",
        lambda query: queries.append(query) or {"status": "approved"},
    )

    status = report_approval.report_delivery_approval_status(
        "report-a", delivery_config_fingerprint="config-a"
    )

    assert status == "approved"
    assert "config-a" in queries[0]
