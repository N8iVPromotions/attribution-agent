from __future__ import annotations

import pytest

from agents.insight.insight_agent import InsightReport
from config.agency_config import AgencyConfig
from config.client_config import ClientConfig
from flows import agency_flow


class _Checkpointer:
    def __init__(self):
        self.completed: set[str] = set()
        self.results: dict[str, dict] = {}
        self.events: list[tuple[str, str]] = []
        self.timeline: list[str] = []

    def get_completed_steps(self, run_id, client_id):
        return set(self.completed)

    def get_step_result(self, run_id, client_id, step_name):
        return self.results[step_name]

    def start_step(self, run_id, agency_id, client_id, step_name):
        self.events.append(("started", step_name))

    def complete_step(
        self, run_id, agency_id, client_id, step_name, result: dict | None = None
    ):
        self.completed.add(step_name)
        self.events.append(("completed", step_name))
        self.timeline.append(f"checkpoint:{step_name}")
        if result is not None:
            self.results[step_name] = result

    def fail_step(self, run_id, agency_id, client_id, step_name, error=""):
        self.events.append(("failed", step_name))


class _DeliveryClaim:
    def __init__(self, acquired=True):
        self.acquired = acquired

    def complete(self):
        pass

    def release(self):
        pass


class _Lease:
    def acquire(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


def _report() -> InsightReport:
    return InsightReport(
        client_id="client-a",
        client_name="Client A",
        report_month="2026-08",
        narrative="Evidence-backed report.",
        top_channel="Paid Search",
        total_pipeline=1_000.0,
        total_spend=200.0,
        overall_roi=5.0,
        collected_revenue=800.0,
        true_roi=4.0,
        attribution_model="last_touch",
        generated_at="2026-09-03T12:00:00+00:00",
    )


@pytest.mark.parametrize(
    ("delivery_proven", "can_deliver", "expected"),
    [
        (True, True, "delivered"),
        (True, False, "delivered"),
        (False, False, "suppressed"),
        (False, True, "generated"),
    ],
)
def test_report_outcome_statuses(delivery_proven, can_deliver, expected):
    assert (
        agency_flow._report_outcome_status(
            can_deliver=can_deliver,
            delivery_proven=delivery_proven,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("checkpoint", "expected"),
    [
        ({"delivered": True}, True),
        ({"delivered": False, "reason": "duplicate_suppressed"}, False),
        ({}, False),
    ],
)
def test_only_explicit_delivery_checkpoint_proves_delivery(checkpoint, expected):
    assert agency_flow._delivery_was_proven(checkpoint) is expected


def test_persistence_failure_blocks_completion_and_retry_does_not_resend(
    monkeypatch,
):
    checkpointer = _Checkpointer()
    persistence_calls = []
    send_calls = []
    pipeline_records = []
    client = ClientConfig(
        client_id="client-a",
        client_name="Client A",
        client_report_email="client@example.com",
        databricks_schema="workspace.attribution_client_a",
    )
    agency = AgencyConfig(
        agency_id="agency-a",
        agency_name="Agency A",
        client_ids=["client-a"],
    )

    monkeypatch.setattr("utils.checkpoint.Checkpointer", lambda: checkpointer)
    monkeypatch.setattr(agency_flow, "get_agency", lambda agency_id: agency)
    monkeypatch.setattr(agency_flow, "get_client", lambda client_id: client)
    monkeypatch.setattr(
        agency_flow,
        "ingest_flow",
        lambda *args, **kwargs: {"status": "complete", "source_failures": {}},
    )
    monkeypatch.setattr(agency_flow, "run_client_attribution_sql", lambda *a, **k: None)
    monkeypatch.setattr(
        agency_flow, "generate_insight_report", lambda *args, **kwargs: _report()
    )
    monkeypatch.setattr(
        "agents.intelligence.n8iv_agents.run_governance_review",
        lambda **kwargs: {
            "decision": "READY FOR HUMAN REVIEW",
            "warnings": [],
            "critical_issues": [],
            "review_failed": False,
        },
    )
    monkeypatch.setattr(
        agency_flow,
        "send_agency_report",
        lambda **kwargs: send_calls.append(kwargs) or True,
    )
    monkeypatch.setattr(
        agency_flow, "claim_report_delivery", lambda key: _DeliveryClaim()
    )
    monkeypatch.setattr(agency_flow, "dispatch_alerts", lambda alerts: None)

    def write_pipeline_record(record):
        pipeline_records.append(record)
        checkpointer.timeline.append(f"run_history:{record['status']}")

    monkeypatch.setattr(agency_flow, "write_pipeline_run", write_pipeline_record)
    monkeypatch.setattr(agency_flow, "_notify", lambda text: None)
    monkeypatch.setattr(
        "utils.client_lock.client_lease", lambda *args, **kwargs: _Lease()
    )

    def persist(record):
        persistence_calls.append(record)
        checkpointer.timeline.append(f"report:{record['status']}")
        if len(persistence_calls) == 2:
            raise RuntimeError("report persistence unavailable")
        return "report-id"

    monkeypatch.setattr(agency_flow, "write_insight_report", persist, raising=False)

    first = agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        execution_run_id="run-1",
        run_benchmarks=False,
    )

    assert first["clients_failed"] == 1
    assert "pipeline_complete" not in checkpointer.completed
    assert [call["status"] for call in persistence_calls] == [
        "generated",
        "delivered",
    ]
    assert checkpointer.results["email_sent"]["delivered"] is True
    assert len(send_calls) == 1

    second = agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        resume_run_id="run-1",
        run_benchmarks=False,
    )

    assert second["clients_failed"] == 0
    assert [call["status"] for call in persistence_calls] == [
        "generated",
        "delivered",
        "generated",
        "delivered",
    ]
    assert second["results"][0]["email_sent"] is True
    assert pipeline_records[-1]["email_sent"] is True
    final_report_index = max(
        index
        for index, event in enumerate(checkpointer.timeline)
        if event == "report:delivered"
    )
    run_history_index = checkpointer.timeline.index("run_history:success")
    completion_index = checkpointer.timeline.index("checkpoint:pipeline_complete")
    assert final_report_index < run_history_index < completion_index
    assert len(send_calls) == 1

    duplicate_checkpointer = _Checkpointer()
    duplicate_persistence_calls = []
    monkeypatch.setattr("utils.checkpoint.Checkpointer", lambda: duplicate_checkpointer)
    monkeypatch.setattr(
        agency_flow, "claim_report_delivery", lambda key: _DeliveryClaim(False)
    )
    monkeypatch.setattr(
        agency_flow,
        "write_insight_report",
        lambda record: duplicate_persistence_calls.append(record) or "report-id-2",
    )

    duplicate = agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        execution_run_id="run-2",
        run_benchmarks=False,
    )

    assert [call["status"] for call in duplicate_persistence_calls] == [
        "generated",
        "generated",
    ]
    assert duplicate_checkpointer.results["email_sent"] == {
        "delivered": False,
        "reason": "duplicate_suppressed",
        "delivery_key": "report:client-a:2026-08:last_touch",
    }
    assert duplicate["results"][0]["email_sent"] is False
    assert len(send_calls) == 1
