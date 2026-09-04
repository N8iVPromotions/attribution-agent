from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agents.insight.insight_agent import InsightReport
from flows import agency_flow
from utils.checkpoint import Checkpointer


class _Checkpointer:
    def __init__(self, payload: object):
        self.payload = payload
        self.started: list[tuple] = []
        self.completed: list[tuple] = []

    def get_step_result(self, run_id: str, client_id: str, step: str):
        return self.payload

    def start_step(self, *args):
        self.started.append(args)

    def complete_step(self, *args):
        self.completed.append(args)


def _report(report_month: str = "2026-08") -> InsightReport:
    return InsightReport(
        client_id="client-a",
        client_name="Client A",
        report_month=report_month,
        narrative="Evidence-backed draft.",
        total_pipeline=100.0 if report_month != "N/A" else 0.0,
        attribution_model="last_touch",
        generated_at="2026-09-03T12:00:00+00:00",
    )


def _load(checkpointer: _Checkpointer, completed_steps: set[str]):
    return agency_flow._load_or_generate_report(
        checkpointer=checkpointer,
        completed_steps=completed_steps,
        run_id="run-1",
        agency_id="agency-a",
        client_id="client-a",
        attribution_model="last_touch",
        report_month="2026-08",
    )


def test_resume_restores_valid_report_without_regeneration(monkeypatch):
    checkpointer = _Checkpointer(_report().to_dict())

    monkeypatch.setattr(
        agency_flow,
        "generate_insight_report",
        lambda *args, **kwargs: pytest.fail("valid checkpoint should be restored"),
    )

    restored, report_id = _load(checkpointer, {"generate_report"})

    assert restored.report_month == "2026-08"
    assert report_id == agency_flow.insight_report_id(
        {**restored.to_dict(), "agency_id": "agency-a"}
    )
    assert checkpointer.started == []
    assert checkpointer.completed == []


def test_resume_rejects_report_from_a_different_model():
    payload = _report().to_dict()
    payload["attribution_model"] = "w_shape"
    checkpointer = _Checkpointer(payload)

    with pytest.raises(RuntimeError, match="does not match"):
        _load(checkpointer, {"generate_report"})


def test_resume_regenerates_legacy_empty_report_checkpoint(monkeypatch):
    checkpointer = _Checkpointer({})
    monkeypatch.setattr(
        agency_flow,
        "generate_insight_report",
        lambda *args, **kwargs: _report(),
    )

    restored, report_id = _load(checkpointer, {"generate_report"})

    assert restored.report_month == "2026-08"
    assert report_id
    assert len(checkpointer.started) == 1
    checkpoint = checkpointer.completed[0][-1]
    assert checkpoint["report"]["report_month"] == "2026-08"
    assert checkpoint["report_id"] == report_id


def test_empty_report_is_not_saved_as_completed(monkeypatch):
    checkpointer = _Checkpointer(None)
    monkeypatch.setattr(
        agency_flow,
        "generate_insight_report",
        lambda *args, **kwargs: _report("N/A"),
    )

    with pytest.raises(RuntimeError, match="no attribution data"):
        _load(checkpointer, set())

    assert len(checkpointer.started) == 1
    assert checkpointer.completed == []


def test_spend_only_report_is_not_saved_as_attribution(monkeypatch):
    checkpointer = _Checkpointer(None)
    spend_only = _report("2026-08")
    spend_only.total_pipeline = 0.0
    spend_only.collected_revenue = 0.0
    spend_only.total_spend = 500.0
    monkeypatch.setattr(
        agency_flow,
        "generate_insight_report",
        lambda *args, **kwargs: spend_only,
    )

    with pytest.raises(RuntimeError, match="no attribution data"):
        _load(checkpointer, set())

    assert checkpointer.completed == []


def test_sql_freshness_timestamp_is_always_rendered_in_utc():
    phoenix = timezone(-timedelta(hours=7))

    assert (
        agency_flow._utc_sql_timestamp(datetime(2026, 9, 3, 5, 30, tzinfo=phoenix))
        == "2026-09-03 12:30:00.000000"
    )
    assert (
        agency_flow._utc_sql_timestamp(datetime(2026, 9, 3, 12, 30))
        == "2026-09-03 12:30:00.000000"
    )


def test_warehouse_rejects_unobserved_multitouch_models():
    with pytest.raises(ValueError, match="observed multi-touch evidence"):
        agency_flow.run_client_attribution_sql(
            "client-a",
            "w_shape",
            run_started_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
            report_period=agency_flow.resolve_report_period("2026-08"),
        )


def test_warehouse_sql_uses_only_safely_normalized_configured_won_stages(
    monkeypatch,
):
    statements: list[str] = []
    monkeypatch.setattr(
        agency_flow,
        "get_client",
        lambda _client_id: SimpleNamespace(
            databricks_schema="workspace.attribution_acme",
            lookback_days=45,
            reporting_currency="USD",
            hubspot_closed_won_stage_ids=(
                "Closed Won",
                "CUSTOM'); DROP TABLE sensitive; --",
            ),
        ),
    )
    monkeypatch.setattr(agency_flow, "_run_sql", statements.append)

    agency_flow.run_client_attribution_sql(
        "acme",
        "last_touch",
        run_started_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        report_period=agency_flow.resolve_report_period("2026-08"),
    )

    rendered = "\n".join(statements).lower()
    assert "deal_stage_key in ('closedwon', 'customdroptablesensitive')" in rendered
    assert "drop table sensitive" not in rendered


def test_warehouse_migrates_cash_scorecard_before_name_aligned_insert(monkeypatch):
    statements: list[str] = []
    monkeypatch.setattr(
        agency_flow,
        "get_client",
        lambda _client_id: SimpleNamespace(
            databricks_schema="workspace.attribution_acme",
            lookback_days=45,
            reporting_currency="USD",
            hubspot_closed_won_stage_ids=("closedwon",),
        ),
    )

    monkeypatch.setattr(agency_flow, "_run_sql", statements.append)

    agency_flow.run_client_attribution_sql(
        "acme",
        "last_touch",
        run_started_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        report_period=agency_flow.resolve_report_period("2026-08"),
    )

    migration_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("ALTER TABLE")
    )
    insert_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith(
            "INSERT INTO workspace.attribution_acme.channel_performance_v2"
        )
    )
    assert migration_index < insert_index
    assert "ADD COLUMNS (refunded_revenue DOUBLE)" in statements[migration_index]
    assert "BY NAME\nREPLACE WHERE" in statements[insert_index]


def test_warehouse_schema_migration_ignores_existing_refund_column(monkeypatch):
    def run_sql(_statement: str) -> None:
        raise RuntimeError("[FIELDS_ALREADY_EXISTS] refunded_revenue already exists")

    monkeypatch.setattr(agency_flow, "_run_sql", run_sql)

    agency_flow._ensure_channel_performance_v2_schema("workspace.attribution_acme")


def test_warehouse_schema_migration_fails_closed_on_unexpected_error(monkeypatch):
    statements: list[str] = []
    monkeypatch.setattr(
        agency_flow,
        "get_client",
        lambda _client_id: SimpleNamespace(
            databricks_schema="workspace.attribution_acme",
            lookback_days=45,
            reporting_currency="USD",
            hubspot_closed_won_stage_ids=("closedwon",),
        ),
    )

    def run_sql(statement: str) -> None:
        statements.append(statement)
        if statement.startswith("ALTER TABLE"):
            raise RuntimeError("permission denied")

    monkeypatch.setattr(agency_flow, "_run_sql", run_sql)

    with pytest.raises(RuntimeError, match="permission denied"):
        agency_flow.run_client_attribution_sql(
            "acme",
            "last_touch",
            run_started_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
            report_period=agency_flow.resolve_report_period("2026-08"),
        )

    assert not any(
        statement.startswith(
            "INSERT INTO workspace.attribution_acme.channel_performance_v2"
        )
        for statement in statements
    )


def test_ingest_checkpoint_binds_model_period_client_and_delivery_mode():
    payload = {
        "client_id": "client-a",
        "attribution_model": "last_touch",
        "report_month": "2026-08",
        "period_start": "2026-08-01T04:00:00+00:00",
        "period_end": "2026-09-01T04:00:00+00:00",
        "dry_run": True,
    }

    period = agency_flow._period_from_ingest_checkpoint(
        payload,
        client_id="client-a",
        attribution_model="last_touch",
        dry_run=True,
        requested_month="2026-08",
    )
    assert period.month == "2026-08"

    for changed in (
        {"client_id": "client-b"},
        {"attribution_model": "w_shape"},
        {
            "report_month": "2026-07",
            "period_start": "2026-07-01T04:00:00+00:00",
        },
        {"dry_run": False},
    ):
        with pytest.raises(RuntimeError, match="does not match"):
            agency_flow._period_from_ingest_checkpoint(
                {**payload, **changed},
                client_id="client-a",
                attribution_model="last_touch",
                dry_run=True,
                requested_month="2026-08",
            )


def test_checkpoint_read_outage_fails_closed(monkeypatch):
    monkeypatch.setattr("utils.databricks_writer._is_databricks", lambda: False)
    monkeypatch.setattr(
        "utils.databricks_writer._get_connection",
        lambda: (_ for _ in ()).throw(RuntimeError("warehouse unavailable")),
    )

    with pytest.raises(RuntimeError, match="refusing to rerun steps"):
        Checkpointer().get_completed_steps("run-1", "client-a")


def test_missing_completed_checkpoint_result_fails_closed(monkeypatch):
    class _EmptyQuery:
        def collect(self):
            return []

    class _Spark:
        def sql(self, query):
            return _EmptyQuery()

    monkeypatch.setattr("utils.databricks_writer._is_databricks", lambda: True)
    monkeypatch.setattr("utils.databricks_writer._get_spark", lambda: _Spark())

    with pytest.raises(RuntimeError, match="Unable to restore checkpoint result"):
        Checkpointer().get_step_result("run-1", "client-a", "generate_report")


def test_checkpoint_start_and_completion_fail_closed(monkeypatch):
    monkeypatch.setattr(
        "utils.databricks_writer._upsert_dataframe",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("checkpoint insert failed")
        ),
    )
    with pytest.raises(RuntimeError, match="persist checkpoint start"):
        Checkpointer().start_step("run-1", "agency-a", "client-a", "ingest")

    monkeypatch.setattr(
        "utils.databricks_writer._run_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("checkpoint update failed")
        ),
    )
    with pytest.raises(RuntimeError, match="persist checkpoint state"):
        Checkpointer().complete_step("run-1", "agency-a", "client-a", "ingest")

    Checkpointer().fail_step("run-1", "agency-a", "client-a", "ingest", "boom")


def test_checkpoint_preserves_apostrophes_in_content_addressed_report(monkeypatch):
    report = _report()
    report.narrative = "Client's attributed revenue isn't estimated."
    report_id = agency_flow.insight_report_id(
        {**report.to_dict(), "agency_id": "agency-a"}
    )
    payload = agency_flow._report_checkpoint_payload(report, report_id)
    stored = {}

    def capture_update(statement):
        match = re.search(r"result_json = ('(?:''|[^'])*')", statement)
        assert match is not None
        stored["result_json"] = match.group(1)[1:-1].replace("''", "'")

    monkeypatch.setattr("utils.databricks_writer._run_sql", capture_update)
    monkeypatch.setattr(
        "utils.databricks_writer._fetch_rows", lambda query: [{"status": "completed"}]
    )

    Checkpointer().complete_step(
        "run-1", "agency-a", "client-a", "generate_report", payload
    )
    restored_payload = json.loads(stored["result_json"])
    restored = agency_flow._report_from_checkpoint(
        restored_payload,
        agency_id="agency-a",
        expected_client_id="client-a",
        expected_model="last_touch",
        expected_month="2026-08",
    )

    assert restored is not None
    restored_report, restored_id = restored
    assert restored_report.narrative == report.narrative
    assert restored_id == report_id


def test_checkpoint_completion_fails_when_no_started_step_was_updated(monkeypatch):
    monkeypatch.setattr("utils.databricks_writer._run_sql", lambda statement: None)
    monkeypatch.setattr("utils.databricks_writer._fetch_rows", lambda query: [])

    with pytest.raises(RuntimeError, match="persist checkpoint state"):
        Checkpointer().complete_step(
            "run-1", "agency-a", "client-a", "generate_report", {"ok": True}
        )


def test_resume_requires_explicit_report_month(monkeypatch):
    monkeypatch.setattr(
        agency_flow,
        "get_agency",
        lambda agency_id: type("Agency", (), {"client_ids": []})(),
    )

    with pytest.raises(ValueError, match="report_month is required"):
        agency_flow.run_agency_pipeline(
            "agency-a",
            client_filter=["client-a"],
            resume_run_id="run-1",
        )
