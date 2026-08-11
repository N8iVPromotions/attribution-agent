from __future__ import annotations

import pytest

from flows import ingest_flow, staging_validation


def test_staging_task_mapping_requires_unique_expected_clients():
    result = staging_validation.verify_task_mapping(
        [("agency-a", "client-a"), ("agency-a", "client-b")], 2
    )
    assert [item["task_index"] for item in result["assignments"]] == [0, 1]

    with pytest.raises(AssertionError, match="duplicate"):
        staging_validation.verify_task_mapping(
            [("agency-a", "client-a"), ("agency-b", "client-a")], 2
        )


def test_staging_vendor_failure_is_explicitly_gated(monkeypatch):
    monkeypatch.setenv("ARIE_STAGING_VALIDATION", "true")
    monkeypatch.setenv("ARIE_STAGING_FAIL_SOURCE", "meta")
    monkeypatch.setenv("ARIE_STAGING_FAIL_CLIENT_ID", "client-a")

    with pytest.raises(RuntimeError, match="STAGING_SIMULATED_VENDOR_ERROR"):
        ingest_flow._raise_staging_vendor_error("meta", "client-a")

    ingest_flow._raise_staging_vendor_error("meta", "client-b")


def test_partial_contract_uses_exact_telemetry_code():
    result = staging_validation.verify_partial_contract()
    assert result == {
        "expected_status": "partial",
        "event_code": "PARTIAL_INGESTION_REPORT_SUPPRESSED",
        "automated_email_allowed": False,
    }
