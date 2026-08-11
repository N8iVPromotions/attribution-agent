from __future__ import annotations

import gzip
import json
from pathlib import Path

from demo.enterprise_demo import run_demo


def test_enterprise_demo_generates_reconciled_offline_evidence(tmp_path: Path):
    summary = run_demo(tmp_path)

    assignments = summary["manifest"]["work_items"]
    assert len(assignments) == 20
    assert [item["task_index"] for item in assignments] == list(range(20))
    assert len({item["client_id"] for item in assignments}) == 20
    assert summary["external_calls_executed"] == 0

    assert summary["pipeline_result"]["status"] == "partial"
    assert summary["pipeline_result"]["email_sent"] is False
    assert summary["raw_archive"]["round_trip_verified"] is True

    archive = Path(summary["raw_archive"]["local_artifact"])
    assert (
        json.loads(gzip.decompress(archive.read_bytes()))["error"]["http_status"] == 429
    )
    assert all(
        model["attributed_total"] == 24_000.0
        for model in summary["attribution"]["models"].values()
    )
    assert summary["ai_gateway"]["cache_invalidated_by_data_version"] is True
    assert "analyst@example.com" not in summary["ai_gateway"]["masked_prompt"]
    assert "602-555-0199" not in summary["ai_gateway"]["masked_prompt"]
    assert summary["sandbox"]["external_calls"] == 0
    assert summary["sandbox"]["attributed_total"] == 15_000.0

    assert (tmp_path / "BOARD_DEMO_WALKTHROUGH.md").exists()
    assert "Northstar Revenue Intelligence" in (
        tmp_path / "executive_report.html"
    ).read_text(encoding="utf-8")
    assert "PARTIAL_INGESTION_REPORT_SUPPRESSED" in (
        tmp_path / "telegram_alert.txt"
    ).read_text(encoding="utf-8")


def test_enterprise_demo_marks_unproven_cost_claims_conditional(tmp_path: Path):
    summary = run_demo(tmp_path)

    budget = summary["ai_gateway"]["budget_enforcement"]
    assert budget["current_scope"] == "agency"
    assert budget["requested_20_tenant_30_day_ceiling_usd"] == 1500.0
    assert budget["requested_under_1150_monthly_target_proven"] is False
    assert summary["tco"]["finance_validation_required"] is True
    assert summary["signoff"][0]["result"] == "Conditional"
