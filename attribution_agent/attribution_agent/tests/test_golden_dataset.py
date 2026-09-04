from __future__ import annotations

import json

from evals.golden_dataset import GoldenDatasetManager


def test_promote_sample_is_untruncated_and_pii_masked(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "utils.databricks_writer._upsert_dataframe",
        lambda frame, *_args: captured.update(frame.iloc[0].to_dict()),
    )
    input_text = "contact=analyst@example.com " + ("multi-channel-row " * 80)

    GoldenDatasetManager().promote_sample(
        "revenue-analyst",
        input_text,
        '{"owner":"analyst@example.com"}',
        {"owner": "analyst@example.com"},
        raise_on_error=True,
    )

    assert len(captured["input_summary"]) > 500
    assert "analyst@example.com" not in captured["input_summary"]
    assert "analyst@example.com" not in captured["expected_output"]
    assert "analyst@example.com" not in captured["expected_fields"]


def test_evaluate_accepts_dict_columns_and_applies_numeric_tolerance():
    sample = {
        "expected_fields": {"total_spend": 100.0, "top_channel": "Google Ads"},
        "tolerance_json": {"total_spend": 0.02},
    }

    result = GoldenDatasetManager().evaluate(
        "revenue-analyst",
        '{"total_spend":101.5,"top_channel":"Google Ads"}',
        sample,
    )

    assert result["passed"] is True


def test_evaluate_fails_changed_nonnumeric_field():
    sample = {
        "expected_fields": json.dumps({"decision": "REVISE"}),
        "tolerance_json": "{}",
    }
    result = GoldenDatasetManager().evaluate(
        "governance-reviewer", '{"decision":"READY"}', sample
    )
    assert result["passed"] is False


def test_load_seed_samples_is_local_untruncated_and_pii_masked(tmp_path):
    seed_path = tmp_path / "seed.json"
    long_input = "contact=analyst@example.com " + ("multi-channel-row " * 80)
    seed_path.write_text(
        json.dumps(
            [
                {
                    "agent_name": "revenue-analyst",
                    "input_text": long_input,
                    "expected_output": '{"owner":"analyst@example.com"}',
                    "expected_fields": {"owner": "analyst@example.com"},
                    "tolerance": {},
                    "source": "test-seed",
                }
            ]
        ),
        encoding="utf-8",
    )

    samples = GoldenDatasetManager().load_seed_samples(seed_path)

    assert len(samples) == 1
    sample = samples[0]
    assert sample["agent_name"] == "revenue-analyst"
    assert len(sample["input_summary"]) > 500
    assert "analyst@example.com" not in sample["input_summary"]
    assert "analyst@example.com" not in sample["expected_output"]
    assert "analyst@example.com" not in sample["expected_fields"]
    assert sample["source"] == "test-seed"
