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
    def __init__(self, acquired=True, state=None):
        self.acquired = acquired
        self.state = state or ("claimed" if acquired else None)
        self.released = False

    def complete(self):
        pass

    def release(self):
        self.released = True


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


def _configure_pipeline(
    monkeypatch,
    checkpointer: _Checkpointer,
    *,
    send_report,
):
    state = {
        "client": ClientConfig(
            client_id="client-a",
            client_name="Client A",
            client_report_email="client@example.com",
            databricks_schema="workspace.attribution_client_a",
        ),
        "agency": AgencyConfig(
            agency_id="agency-a",
            agency_name="Agency A",
            client_ids=["client-a"],
            brand_color="123456",
            sender_name="Agency Analytics",
            sender_email="reports@example.com",
            reply_to="reply@example.com",
            powerbi_workspace_url="https://example.com/dashboard",
        ),
    }
    approvals: dict[tuple[str, str], str] = {}
    approval_requests: list[dict] = []
    report_writes: list[dict] = []
    status_updates: list[tuple[str, str]] = []

    monkeypatch.setattr("utils.checkpoint.Checkpointer", lambda: checkpointer)
    monkeypatch.setattr(agency_flow, "get_agency", lambda agency_id: state["agency"])
    monkeypatch.setattr(agency_flow, "get_client", lambda client_id: state["client"])
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
    monkeypatch.setattr(agency_flow, "send_agency_report", send_report)
    monkeypatch.setattr(
        agency_flow, "claim_report_delivery", lambda key: _DeliveryClaim()
    )
    monkeypatch.setattr(agency_flow, "dispatch_alerts", lambda alerts: None)
    monkeypatch.setattr(
        agency_flow, "fetch_latest_approved_report", lambda **kwargs: None
    )
    monkeypatch.setattr(
        agency_flow, "fetch_report_data_fingerprint", lambda *args, **kwargs: "data-v1"
    )

    def approval_status(report_id, *, delivery_config_fingerprint):
        return approvals.get((report_id, delivery_config_fingerprint))

    def ensure_approval(**kwargs):
        approval_requests.append(kwargs)
        key = (kwargs["report_id"], kwargs["delivery_config_fingerprint"])
        approvals.setdefault(key, "pending")
        return "approval-id"

    def persist(record):
        report_writes.append(record)
        return agency_flow.insight_report_id(record)

    monkeypatch.setattr(agency_flow, "report_delivery_approval_status", approval_status)
    monkeypatch.setattr(agency_flow, "ensure_report_delivery_approval", ensure_approval)
    monkeypatch.setattr(agency_flow, "write_insight_report", persist)
    monkeypatch.setattr(
        agency_flow,
        "update_insight_report_status",
        lambda report_id, status: status_updates.append((report_id, status)),
    )
    monkeypatch.setattr(agency_flow, "write_pipeline_run", lambda record: None)
    monkeypatch.setattr(agency_flow, "_notify", lambda text: None)
    monkeypatch.setattr(
        "utils.client_lock.client_lease", lambda *args, **kwargs: _Lease()
    )
    return {
        "state": state,
        "approvals": approvals,
        "approval_requests": approval_requests,
        "report_writes": report_writes,
        "status_updates": status_updates,
    }


@pytest.mark.parametrize(
    ("delivery_proven", "delivery_policy_clear", "expected"),
    [
        (True, True, "delivered"),
        (True, False, "delivered"),
        (False, False, "suppressed"),
        (False, True, "generated"),
    ],
)
def test_report_outcome_statuses(delivery_proven, delivery_policy_clear, expected):
    assert (
        agency_flow._report_outcome_status(
            delivery_policy_clear=delivery_policy_clear,
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
    status_updates = []
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
    monkeypatch.setattr(
        agency_flow, "fetch_latest_approved_report", lambda **kwargs: None
    )
    monkeypatch.setattr(
        agency_flow, "fetch_report_data_fingerprint", lambda *args, **kwargs: "data-v1"
    )
    monkeypatch.setattr(
        agency_flow,
        "ensure_report_delivery_approval",
        lambda **kwargs: "approval-id",
    )
    monkeypatch.setattr(
        agency_flow,
        "report_delivery_approval_status",
        lambda report_id, **kwargs: "approved",
    )
    monkeypatch.setattr(
        agency_flow,
        "report_delivery_config_fingerprint",
        lambda **kwargs: "config-fingerprint",
    )
    monkeypatch.setattr(agency_flow, "insight_report_id", lambda record: "report-id")

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
        checkpointer.timeline.append("report:generated")
        return "report-id"

    def update_status(report_id, status):
        status_updates.append((report_id, status))
        checkpointer.timeline.append(f"report:{status}")
        if len(status_updates) == 1:
            raise RuntimeError("report persistence unavailable")

    monkeypatch.setattr(agency_flow, "write_insight_report", persist, raising=False)
    monkeypatch.setattr(
        agency_flow, "update_insight_report_status", update_status, raising=False
    )

    first = agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        execution_run_id="run-1",
        run_benchmarks=False,
    )

    assert first["clients_failed"] == 1
    assert "pipeline_complete" not in checkpointer.completed
    assert [call["status"] for call in persistence_calls] == ["generated"]
    assert status_updates == [("report-id", "delivered")]
    assert checkpointer.results["email_sent"]["delivered"] is True
    assert len(send_calls) == 1

    second = agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        resume_run_id="run-1",
        run_benchmarks=False,
        report_month="2026-08",
    )

    assert second["clients_failed"] == 0
    assert [call["status"] for call in persistence_calls] == [
        "generated",
        "generated",
    ]
    assert status_updates == [
        ("report-id", "delivered"),
        ("report-id", "delivered"),
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
        agency_flow,
        "claim_report_delivery",
        lambda key: _DeliveryClaim(False, "sent"),
    )
    monkeypatch.setattr(
        agency_flow,
        "write_insight_report",
        lambda record: duplicate_persistence_calls.append(record) or "report-id",
    )
    monkeypatch.setattr(
        agency_flow,
        "update_insight_report_status",
        lambda report_id, status: None,
    )

    duplicate = agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        execution_run_id="run-2",
        run_benchmarks=False,
    )

    assert [call["status"] for call in duplicate_persistence_calls] == ["generated"]
    assert duplicate_checkpointer.results["email_sent"] == {
        "delivered": True,
        "reason": "existing_sent_claim",
        "delivery_key": (
            "report:client-a:2026-08:last_touch:report-id:config-fingerprint"
        ),
    }
    duplicate_events = duplicate_checkpointer.events
    assert duplicate_events.index(("started", "email_sent")) < duplicate_events.index(
        ("completed", "email_sent")
    )
    assert ("started", "pipeline_complete") in duplicate_events
    assert duplicate["results"][0]["email_sent"] is True
    assert len(send_calls) == 1

    unresolved_checkpointer = _Checkpointer()
    monkeypatch.setattr(
        "utils.checkpoint.Checkpointer", lambda: unresolved_checkpointer
    )
    monkeypatch.setattr(
        agency_flow,
        "claim_report_delivery",
        lambda key: _DeliveryClaim(False, "claimed"),
    )

    unresolved = agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        execution_run_id="run-unresolved",
        run_benchmarks=False,
    )

    assert unresolved["clients_failed"] == 1
    assert "prior delivery attempt is unresolved" in unresolved["errors"][0]["error"]
    assert "email_sent" not in unresolved_checkpointer.completed
    assert "pipeline_complete" not in unresolved_checkpointer.completed
    assert len(send_calls) == 1

    pending_checkpointer = _Checkpointer()
    pending_persistence_calls = []
    monkeypatch.setattr("utils.checkpoint.Checkpointer", lambda: pending_checkpointer)
    monkeypatch.setattr(
        agency_flow,
        "report_delivery_approval_status",
        lambda report_id, **kwargs: "pending",
    )
    monkeypatch.setattr(
        agency_flow,
        "claim_report_delivery",
        lambda key: pytest.fail("unapproved report must not claim delivery"),
    )
    monkeypatch.setattr(
        agency_flow,
        "write_insight_report",
        lambda record: pending_persistence_calls.append(record) or "report-id",
    )

    pending = agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        execution_run_id="run-3",
        run_benchmarks=False,
    )

    assert [call["status"] for call in pending_persistence_calls] == ["generated"]
    assert pending["results"][0]["approval_status"] == "pending"
    assert pending["results"][0]["status"] == "awaiting_approval"
    assert pending["results"][0]["email_sent"] is False
    assert "email_sent" not in pending_checkpointer.completed
    assert "pipeline_complete" not in pending_checkpointer.completed
    assert len(send_calls) == 1


def test_pending_approval_resumes_same_run_and_sends_the_exact_artifact(monkeypatch):
    checkpointer = _Checkpointer()
    sent = []
    harness = _configure_pipeline(
        monkeypatch,
        checkpointer,
        send_report=lambda **kwargs: sent.append(kwargs["report"]) or True,
    )

    pending = agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        execution_run_id="run-approval",
        run_benchmarks=False,
        report_month="2026-08",
    )

    assert pending["results"][0]["status"] == "awaiting_approval"
    assert "pipeline_complete" not in checkpointer.completed
    approval = harness["approval_requests"][0]
    key = (approval["report_id"], approval["delivery_config_fingerprint"])
    harness["approvals"][key] = "approved"

    delivered = agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        resume_run_id="run-approval",
        run_benchmarks=False,
        report_month="2026-08",
    )

    assert delivered["results"][0]["status"] == "complete"
    assert delivered["results"][0]["email_sent"] is True
    assert len(sent) == 1
    assert sent[0].to_dict() == _report().to_dict()
    assert "pipeline_complete" in checkpointer.completed
    assert harness["status_updates"][-1] == (approval["report_id"], "delivered")
    assert len(harness["approval_requests"]) == 1
    assert {
        agency_flow.insight_report_id(record) for record in harness["report_writes"]
    } == {approval["report_id"]}


def test_failure_after_approval_retains_authorization_on_retry(monkeypatch):
    checkpointer = _Checkpointer()
    attempts = []

    def send(**kwargs):
        attempts.append(kwargs["report"].to_dict())
        if len(attempts) == 1:
            raise agency_flow.DeliveryNotAcceptedError("provider rejected request")
        return True

    harness = _configure_pipeline(monkeypatch, checkpointer, send_report=send)
    agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        execution_run_id="run-retry",
        run_benchmarks=False,
        report_month="2026-08",
    )
    approval = harness["approval_requests"][0]
    approval_key = (
        approval["report_id"],
        approval["delivery_config_fingerprint"],
    )
    harness["approvals"][approval_key] = "approved"

    failed = agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        resume_run_id="run-retry",
        run_benchmarks=False,
        report_month="2026-08",
    )
    assert failed["clients_failed"] == 1

    delivered = agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        resume_run_id="run-retry",
        run_benchmarks=False,
        report_month="2026-08",
    )

    assert delivered["clients_failed"] == 0
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert len(harness["approval_requests"]) == 1


def test_ambiguous_provider_failure_retains_claim_and_blocks_retry(monkeypatch):
    checkpointer = _Checkpointer()
    attempts = []
    first_claim = _DeliveryClaim()

    def send(**kwargs):
        attempts.append(kwargs["report"].to_dict())
        raise RuntimeError("connection dropped after provider call")

    harness = _configure_pipeline(monkeypatch, checkpointer, send_report=send)
    agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        execution_run_id="run-ambiguous",
        run_benchmarks=False,
        report_month="2026-08",
    )
    approval = harness["approval_requests"][0]
    harness["approvals"][
        (
            approval["report_id"],
            approval["delivery_config_fingerprint"],
        )
    ] = "approved"
    claims = iter([first_claim, _DeliveryClaim(False, "claimed")])
    monkeypatch.setattr(agency_flow, "claim_report_delivery", lambda key: next(claims))

    failed = agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        resume_run_id="run-ambiguous",
        run_benchmarks=False,
        report_month="2026-08",
    )
    retry = agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        resume_run_id="run-ambiguous",
        run_benchmarks=False,
        report_month="2026-08",
    )

    assert failed["clients_failed"] == 1
    assert retry["clients_failed"] == 1
    assert "prior delivery attempt is unresolved" in retry["errors"][0]["error"]
    assert first_claim.released is False
    assert "email_sent" not in checkpointer.completed
    assert len(attempts) == 1


def test_recipient_change_invalidates_existing_approval(monkeypatch):
    checkpointer = _Checkpointer()
    sent = []
    harness = _configure_pipeline(
        monkeypatch,
        checkpointer,
        send_report=lambda **kwargs: sent.append(kwargs) or True,
    )
    agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        execution_run_id="run-config",
        run_benchmarks=False,
        report_month="2026-08",
    )
    first = harness["approval_requests"][0]
    first_key = (first["report_id"], first["delivery_config_fingerprint"])
    harness["approvals"][first_key] = "approved"
    harness["state"]["client"].client_report_email = "new@example.com"

    resumed = agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        resume_run_id="run-config",
        run_benchmarks=False,
        report_month="2026-08",
    )

    assert resumed["results"][0]["status"] == "awaiting_approval"
    assert sent == []
    assert len(harness["approval_requests"]) == 2
    second = harness["approval_requests"][1]
    assert second["recipient_email"] == "new@example.com"
    assert second["delivery_config_fingerprint"] != first["delivery_config_fingerprint"]


@pytest.mark.parametrize("approval_status", [None, "expired"])
def test_indeterminate_approval_state_fails_closed(monkeypatch, approval_status):
    checkpointer = _Checkpointer()
    sent = []
    harness = _configure_pipeline(
        monkeypatch,
        checkpointer,
        send_report=lambda **kwargs: sent.append(kwargs) or True,
    )
    monkeypatch.setattr(
        agency_flow,
        "report_delivery_approval_status",
        lambda report_id, **kwargs: approval_status,
    )

    result = agency_flow.run_agency_pipeline(
        "agency-a",
        client_filter=["client-a"],
        execution_run_id="run-indeterminate",
        run_benchmarks=False,
        report_month="2026-08",
    )

    assert result["clients_failed"] == 1
    assert "durable report approval state" in result["errors"][0]["error"]
    assert sent == []
    assert "email_sent" not in checkpointer.completed
    assert "pipeline_complete" not in checkpointer.completed
    if approval_status is None:
        assert len(harness["approval_requests"]) == 1
