from types import SimpleNamespace

import json
import sys

import pytest

from evals.eval_runner import (
    _EVAL_RESPONSE_SCHEMAS,
    _call_agent_for_eval,
    main,
    run_eval_for_agent,
)


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


def test_local_seed_eval_never_reads_or_writes_databricks(monkeypatch):
    sample = {
        "sample_id": "sample-1",
        "input_summary": "Return the governed result.",
        "expected_fields": {"status": "complete"},
        "tolerance_json": {},
    }
    monkeypatch.setattr(
        "evals.eval_runner._get_last_score",
        lambda _agent: pytest.fail("local seed eval must not query prior scores"),
    )
    monkeypatch.setattr(
        "evals.eval_runner._write_result",
        lambda **_kwargs: pytest.fail("local seed eval must not persist results"),
    )
    monkeypatch.setattr(
        "evals.eval_runner._call_agent_for_eval",
        lambda *_args: json.dumps({"status": "complete"}),
    )

    passed, score = run_eval_for_agent(
        "data-quality",
        ci_mode=True,
        require_samples=True,
        samples=[sample],
        persist_results=False,
    )

    assert passed is True
    assert score == 1.0


def test_main_routes_local_seed_file_without_persisting_it(monkeypatch, tmp_path):
    seed_path = tmp_path / "seed.json"
    seed_path.write_text("[]", encoding="utf-8")
    local_samples = [
        {
            "agent_name": "data-quality",
            "sample_id": "sample-1",
            "input_summary": "input",
            "expected_fields": {},
            "tolerance_json": {},
        }
    ]
    calls = []

    monkeypatch.setattr(
        "evals.golden_dataset.GoldenDatasetManager.load_seed_samples",
        lambda _self, path: local_samples,
    )
    monkeypatch.setattr(
        "evals.golden_dataset.GoldenDatasetManager.seed_file",
        lambda *_args: pytest.fail("local seed must not be persisted"),
    )

    def fake_run(agent_name, **kwargs):
        calls.append((agent_name, kwargs))
        return True, 1.0

    monkeypatch.setattr("evals.eval_runner.run_eval_for_agent", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "eval_runner.py",
            "--agent",
            "data-quality",
            "--ci-mode",
            "--require-samples",
            "--local-seed-file",
            str(seed_path),
        ],
    )

    main()

    assert calls == [
        (
            "data-quality",
            {
                "ci_mode": True,
                "require_samples": True,
                "samples": local_samples,
                "persist_results": False,
            },
        )
    ]
