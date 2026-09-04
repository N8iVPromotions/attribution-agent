from __future__ import annotations

from types import SimpleNamespace

import pytest

from config.budget_config import BudgetConfig
from flows import agency_flow
from utils.client_lock import ClientLease
from utils.raw_archive import archive_raw_page


def test_build_client_work_items_is_deterministic(monkeypatch):
    clients = {"agency_b": ["z", "a"], "agency_a": ["c"]}
    monkeypatch.setattr(agency_flow, "list_agencies", lambda: ["agency_b", "agency_a"])
    monkeypatch.setattr(
        agency_flow,
        "get_agency",
        lambda agency_id: SimpleNamespace(agency_id=agency_id),
    )
    monkeypatch.setattr(
        agency_flow,
        "_agency_client_ids",
        lambda agency: clients[agency.agency_id],
    )

    assert agency_flow.build_client_work_items(None) == [
        ("agency_a", "c"),
        ("agency_b", "a"),
        ("agency_b", "z"),
    ]


def test_cloud_task_runs_only_assigned_client(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", "1")
    monkeypatch.setenv("CLOUD_RUN_TASK_COUNT", "2")
    monkeypatch.setenv("CLOUD_RUN_EXECUTION", "execution-123")
    monkeypatch.setattr(
        agency_flow,
        "build_client_work_items",
        lambda *_args: [("agency", "client-a"), ("agency", "client-b")],
    )
    calls = []
    monkeypatch.setattr(
        agency_flow,
        "run_agency_pipeline",
        lambda **kwargs: calls.append(kwargs) or {"status": "complete"},
    )

    result = agency_flow.run_cloud_task("agency", True, None, "linear")

    assert result == {"status": "complete"}
    assert calls[0]["client_filter"] == ["client-b"]
    assert calls[0]["execution_run_id"] == "execution-123"
    assert calls[0]["run_benchmarks"] is False


@pytest.mark.parametrize(
    "summary",
    [
        {"clients_failed": 1, "results": []},
        {
            "clients_failed": 0,
            "results": [{"client_id": "client-b", "status": "partial"}],
        },
    ],
)
def test_cloud_task_exits_unsuccessfully_for_failed_or_partial_client(
    monkeypatch, summary
):
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", "0")
    monkeypatch.setenv("CLOUD_RUN_TASK_COUNT", "1")
    monkeypatch.delenv("ARIE_WORK_MANIFEST_URI", raising=False)
    monkeypatch.setattr(
        agency_flow,
        "build_client_work_items",
        lambda *_args: [("agency", "client-a")],
    )
    monkeypatch.setattr(
        agency_flow,
        "run_agency_pipeline",
        lambda **kwargs: summary,
    )

    with pytest.raises(RuntimeError, match="client contract"):
        agency_flow.run_cloud_task("agency", True, None, "last_touch")


def test_cloud_task_rejects_undersized_array(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", "0")
    monkeypatch.setenv("CLOUD_RUN_TASK_COUNT", "1")
    monkeypatch.setattr(
        agency_flow,
        "build_client_work_items",
        lambda *_args: [("agency", "a"), ("agency", "b")],
    )

    with pytest.raises(RuntimeError, match="1 tasks for 2 clients"):
        agency_flow.run_cloud_task("agency", True, None, "linear")


def test_partial_suppression_alert_has_stable_event_code():
    alert = agency_flow._partial_suppression_alert(
        client_id="client-a",
        agency_id="agency-a",
        run_id="run-a",
        source_failures={"pull-meta": "simulated failure"},
    )

    assert alert.title == "PARTIAL_INGESTION_REPORT_SUPPRESSED"
    assert alert.metadata["event_code"] == "PARTIAL_INGESTION_REPORT_SUPPRESSED"
    assert alert.metadata["source_failures"] == {"pull-meta": "simulated failure"}


def test_client_lease_is_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("ARIE_CLIENT_LOCKS_ENABLED", "false")

    with ClientLease("client-a", "run-a") as lease:
        assert not lease.enabled


def test_raw_archive_is_noop_without_bucket(monkeypatch):
    monkeypatch.delenv("ARIE_RAW_ARCHIVE_BUCKET", raising=False)

    assert (
        archive_raw_page(
            source="meta",
            client_id="client-a",
            run_id="run-a",
            page_number=1,
            body=b"{}",
        )
        == ""
    )


def test_model_pricing_matches_versioned_model_ids():
    budget = BudgetConfig()

    assert budget.tokens_to_usd(1_000_000, 0, "claude-haiku-4-5-20251001") == 1.0
    assert budget.tokens_to_usd(1_000_000, 0, "claude-sonnet-4-6") == 3.0
    assert budget.tokens_to_usd(
        0,
        0,
        "claude-haiku-4-5-20251001",
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    ) == pytest.approx(1.35)
