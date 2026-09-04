import json

from agents.intelligence import n8iv_agents
from flows.agency_flow import _governance_blocks_live_delivery


def test_governance_review_preserves_critical_issues(monkeypatch):
    monkeypatch.setattr(
        n8iv_agents,
        "_call_agent",
        lambda *args, **kwargs: json.dumps(
            {
                "decision": "REVISE BEFORE HUMAN REVIEW",
                "warnings": ["Clarify the attribution model."],
                "critical_issues": ["Unsupported causal claim."],
            }
        ),
    )

    result = n8iv_agents.run_governance_review(
        client_id="client-a",
        client_name="Client A",
        report_narrative="Paid search caused every sale.",
        report_json={"total_pipeline": 1000},
    )

    assert result == {
        "decision": "REVISE BEFORE HUMAN REVIEW",
        "warnings": ["Clarify the attribution model."],
        "critical_issues": ["Unsupported causal claim."],
        "review_failed": False,
    }


def test_governance_review_fails_closed_when_agent_is_unavailable(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(n8iv_agents, "_call_agent", fail)

    result = n8iv_agents.run_governance_review(
        client_id="client-a",
        client_name="Client A",
        report_narrative="Draft",
        report_json={},
    )

    assert result["review_failed"] is True
    assert result["critical_issues"]


def test_governance_contract_supports_escalation_decision():
    decisions = n8iv_agents.GOVERNANCE_SCHEMA["properties"]["decision"]["enum"]
    assert "BLOCKED — ESCALATION REQUIRED" in decisions


def test_governance_review_fails_closed_on_unknown_decision(monkeypatch):
    monkeypatch.setattr(
        n8iv_agents,
        "_call_agent",
        lambda *args, **kwargs: json.dumps(
            {"decision": "SEND IT", "warnings": [], "critical_issues": []}
        ),
    )

    result = n8iv_agents.run_governance_review(
        client_id="client-a",
        client_name="Client A",
        report_narrative="Draft",
        report_json={},
    )

    assert result["decision"] == "BLOCKED — ESCALATION REQUIRED"
    assert result["review_failed"] is True
    assert result["critical_issues"]


def test_only_ready_decision_can_pass_live_delivery_gate():
    ready_with_warning = {
        "decision": "READY FOR HUMAN REVIEW",
        "warnings": ["Keep the stated limitation."],
        "critical_issues": [],
        "review_failed": False,
    }
    revise_without_critical = {
        "decision": "REVISE BEFORE HUMAN REVIEW",
        "warnings": ["Clarify the attribution model."],
        "critical_issues": [],
        "review_failed": False,
    }

    assert not _governance_blocks_live_delivery(ready_with_warning, dry_run=False)
    assert _governance_blocks_live_delivery(revise_without_critical, dry_run=False)
    assert not _governance_blocks_live_delivery(revise_without_critical, dry_run=True)
