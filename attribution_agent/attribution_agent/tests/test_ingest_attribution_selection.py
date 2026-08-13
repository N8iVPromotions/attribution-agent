from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from config.client_config import ClientConfig
from flows import ingest_flow as ingest_module


def test_step_build_attribution_uses_run_selected_model(monkeypatch):
    captured = {}

    def fake_run_attribution(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            channel_performance=pd.DataFrame([{"source_platform": "meta"}]),
            total_revenue=0.0,
            attributed_revenue=0.0,
            unattributed_revenue=0.0,
        )

    monkeypatch.setattr(ingest_module, "run_attribution", fake_run_attribution)
    monkeypatch.setattr(ingest_module, "write_attribution_results", lambda *_, **__: 1)
    monkeypatch.setattr(ingest_module, "_with_retry", lambda fn, **_: fn())

    config = ClientConfig(
        client_id="acme",
        client_name="Acme",
        databricks_schema="workspace.attribution_acme",
        attribution_model="last_touch",
        lookback_days=30,
    )

    summary = ingest_module.step_build_attribution(
        None,
        None,
        None,
        config,
        attribution_model="linear",
    )

    assert captured["model"] == "linear"
    assert captured["lookback_days"] == 30
    assert summary["attribution_model"] == "linear"
