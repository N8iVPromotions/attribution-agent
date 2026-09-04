from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from agents.ingest.batches import records_to_batches
from agents.ingest.validator import MetaValidator, validate_record_batch
from config.client_config import ClientConfig
from flows import ingest_flow


def _records(count: int):
    for index in range(count):
        yield {"id": index, "value": f"row-{index}"}


def test_records_to_batches_bounds_memory():
    batches = list(records_to_batches(_records(10_001), batch_rows=5_000))

    assert [batch.num_rows for batch in batches] == [5_000, 5_000, 1]


def test_validate_record_batch_only_materializes_one_batch():
    frame = pd.DataFrame(
        {
            "ad_account_id": ["act-1"],
            "campaign_id": ["campaign-1"],
            "date": ["2026-08-01"],
            "spend": [10.0],
            "impressions": [100],
            "clicks": [5],
        }
    )
    batch = next(records_to_batches(frame.to_dict("records"), batch_rows=5_000))

    validated, report = validate_record_batch(batch, MetaValidator("client-1").validate)

    assert report.passed
    assert validated.num_rows == 1


def test_process_stream_source_writes_each_bounded_batch(monkeypatch):
    batch_sizes = []

    def writer(batches, _schema):
        current = list(batches)
        batch_sizes.extend(batch.num_rows for batch in current)
        return sum(batch.num_rows for batch in current)

    monkeypatch.setattr(ingest_flow, "_with_retry", lambda fn, **_kwargs: fn())
    plan = ingest_flow.StreamSourcePlan(
        name="test",
        batches=records_to_batches(_records(10_001), batch_rows=5_000),
        raw_writer=writer,
    )
    config = SimpleNamespace(databricks_schema="workspace.test", client_id="c1")

    result = ingest_flow._process_stream_source(plan, config)

    assert batch_sizes == [5_000, 5_000, 1]
    assert result.raw_rows == 10_001


def test_streaming_defaults_on_only_for_cloud_run(monkeypatch):
    monkeypatch.delenv("ARIE_STREAMING_INGEST", raising=False)
    monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
    assert ingest_flow._streaming_enabled() is False

    monkeypatch.setenv("CLOUD_RUN_JOB", "attribution-pipeline")
    assert ingest_flow._streaming_enabled() is True


def test_empty_enabled_stripe_stream_marks_run_partial(monkeypatch):
    config = ClientConfig(
        client_id="acme",
        client_name="Acme",
        databricks_schema="workspace.attribution_acme",
        stripe_enabled=True,
        stripe_account_id="acct_expected",
        stripe_history_start_date="2020-01-01",
    )
    monkeypatch.setattr(
        ingest_flow, "iter_stripe_batches", lambda *_args, **_kwargs: iter(())
    )
    monkeypatch.setattr(ingest_flow, "dispatch_alerts", lambda *_args: None)
    monkeypatch.setattr(
        ingest_flow, "build_source_failure_alerts", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        ingest_flow, "build_validation_alerts", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        ingest_flow, "_run_data_quality_reports", lambda *_args, **_kwargs: None
    )

    summary = ingest_flow._stream_ingest(
        config,
        {"stripe": "sk_live_redacted"},
        "run-1",
        "2026-08",
        62,
        require_live_mode=True,
    )

    assert summary["status"] == "partial"
    assert summary["stripe_rows"] == 0
    assert summary["source_failures"] == {
        "validate-stripe": "DataFrame is empty — no Stripe payments returned"
    }
