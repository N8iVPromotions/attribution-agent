from types import SimpleNamespace

from evals.eval_runner import _EVAL_RESPONSE_SCHEMAS, _call_agent_for_eval


def test_eval_calls_use_strict_structured_output(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "utils.prompt_loader.PromptLoader.load",
        lambda _self, _agent_name: SimpleNamespace(body="system prompt"),
    )

    def fake_call(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(text='{"status":"DRAFT_HUMAN_REVIEW_REQUIRED"}')

    monkeypatch.setattr("utils.model_gateway.call", fake_call)

    output = _call_agent_for_eval("executive-reporting", "evaluation input")

    assert output == '{"status":"DRAFT_HUMAN_REVIEW_REQUIRED"}'
    assert captured["response_schema"] is _EVAL_RESPONSE_SCHEMAS["executive-reporting"]
    assert captured["response_schema"]["additionalProperties"] is False
    assert captured["response_schema"]["properties"]["status"]["enum"] == [
        "DRAFT_HUMAN_REVIEW_REQUIRED",
        "REVISE_BEFORE_HUMAN_REVIEW",
    ]


def test_governance_schema_scopes_partial_disclosure_to_draft():
    field = _EVAL_RESPONSE_SCHEMAS["governance-reviewer"]["properties"][
        "partial_data_disclosed"
    ]

    assert "draft itself" in field["description"]
    assert "Reviewer-only evidence does not count" in field["description"]
