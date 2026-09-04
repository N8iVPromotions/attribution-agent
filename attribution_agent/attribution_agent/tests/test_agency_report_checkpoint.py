from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agents.insight.insight_agent import InsightReport
from flows import agency_flow


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
    )


def _load(checkpointer: _Checkpointer, completed_steps: set[str]):
    return agency_flow._load_or_generate_report(
        checkpointer=checkpointer,
        completed_steps=completed_steps,
        run_id="run-1",
        agency_id="agency-a",
        client_id="client-a",
        attribution_model="w_shape",
    )


def test_resume_restores_valid_report_without_regeneration(monkeypatch):
    checkpointer = _Checkpointer(_report().to_dict())

    monkeypatch.setattr(
        agency_flow,
        "generate_insight_report",
        lambda *args, **kwargs: pytest.fail("valid checkpoint should be restored"),
    )

    restored = _load(checkpointer, {"generate_report"})

    assert restored.report_month == "2026-08"
    assert checkpointer.started == []
    assert checkpointer.completed == []


def test_resume_regenerates_legacy_empty_report_checkpoint(monkeypatch):
    checkpointer = _Checkpointer({})
    monkeypatch.setattr(
        agency_flow,
        "generate_insight_report",
        lambda *args, **kwargs: _report(),
    )

    restored = _load(checkpointer, {"generate_report"})

    assert restored.report_month == "2026-08"
    assert len(checkpointer.started) == 1
    assert checkpointer.completed[0][-1]["report_month"] == "2026-08"


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
