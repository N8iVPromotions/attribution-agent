import json

from agents.intelligence import n8iv_agents


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
