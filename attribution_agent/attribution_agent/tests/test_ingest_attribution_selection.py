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


def test_non_stream_ingest_uses_validated_frames_downstream(monkeypatch):
    config = SimpleNamespace(
        client_id="acme",
        meta_access_token="meta-token",
        google_ads_refresh_token="google-token",
        linkedin_access_token="linkedin-token",
        tiktok_access_token="tiktok-token",
        hubspot_access_token="hubspot-token",
        stripe_secret_key="stripe-token",
        databricks_schema="workspace.attribution_acme",
        attribution_model="last_touch",
        lookback_days=30,
    )
    raw_meta = pd.DataFrame([{"row": "raw-meta"}])
    raw_google = pd.DataFrame([{"row": "raw-google"}])
    raw_linkedin = pd.DataFrame([{"row": "raw-linkedin"}])
    raw_tiktok = pd.DataFrame([{"row": "raw-tiktok"}])
    raw_hubspot = pd.DataFrame([{"row": "raw-hubspot"}])
    raw_stripe = pd.DataFrame([{"row": "raw-stripe"}])
    validated_meta = pd.DataFrame([{"row": "validated-meta"}])
    validated_hubspot = pd.DataFrame([{"row": "validated-hubspot"}])
    validated_stripe = pd.DataFrame([{"row": "validated-stripe"}])
    normalized_ads = pd.DataFrame([{"row": "normalized-ads"}])
    normalized_inputs = {}
    attribution_inputs = {}

    monkeypatch.setattr(ingest_module, "get_client", lambda _: config)
    monkeypatch.setattr(ingest_module, "_streaming_enabled", lambda: False)
    monkeypatch.setattr(ingest_module, "step_setup", lambda *_: None)
    monkeypatch.setattr(ingest_module, "dispatch_alerts", lambda *_: None)
    monkeypatch.setattr(ingest_module, "build_credential_alerts", lambda *_a, **_k: [])
    monkeypatch.setattr(
        ingest_module, "build_attribution_coverage_alerts", lambda *_a, **_k: []
    )

    pulled_frames = {
        "step_pull_meta": raw_meta,
        "step_pull_google_ads": raw_google,
        "step_pull_linkedin_ads": raw_linkedin,
        "step_pull_tiktok_ads": raw_tiktok,
        "step_pull_hubspot": raw_hubspot,
        "step_pull_stripe": raw_stripe,
    }
    for step_name, frame in pulled_frames.items():
        monkeypatch.setattr(
            ingest_module,
            step_name,
            lambda *_a, _frame=frame, **_k: _frame,
        )

    monkeypatch.setattr(
        ingest_module, "step_validate_meta", lambda *_: (validated_meta, None)
    )
    monkeypatch.setattr(
        ingest_module, "step_validate_hubspot", lambda *_: (validated_hubspot, None)
    )
    monkeypatch.setattr(
        ingest_module, "step_validate_stripe", lambda *_: (validated_stripe, None)
    )
    monkeypatch.setattr(ingest_module, "step_write_meta", lambda *_: 1)
    monkeypatch.setattr(ingest_module, "step_write_hubspot", lambda *_: 1)
    monkeypatch.setattr(ingest_module, "step_write_stripe", lambda *_: 1)

    def fake_build_normalized_ads(meta, google, linkedin, tiktok, _config):
        normalized_inputs.update(
            meta=meta,
            google=google,
            linkedin=linkedin,
            tiktok=tiktok,
        )
        return normalized_ads

    def fake_run_attribution(**kwargs):
        attribution_inputs.update(kwargs)
        return SimpleNamespace(
            channel_performance=pd.DataFrame([{"source_platform": "meta"}]),
            total_revenue=1.0,
            attributed_revenue=1.0,
            unattributed_revenue=0.0,
        )

    monkeypatch.setattr(
        ingest_module, "build_normalized_ads", fake_build_normalized_ads
    )
    monkeypatch.setattr(ingest_module, "step_write_normalized_ads", lambda *_: 1)
    monkeypatch.setattr(ingest_module, "run_attribution", fake_run_attribution)
    monkeypatch.setattr(ingest_module, "write_attribution_results", lambda *_a, **_k: 1)
    monkeypatch.setattr(ingest_module, "step_alert", lambda *_: None)
    monkeypatch.setattr(
        ingest_module, "step_data_quality_agent", lambda *_a, **_k: None
    )

    ingest_module.ingest_flow("acme")

    assert normalized_inputs["meta"] is validated_meta
    assert normalized_inputs["google"] is raw_google
    assert normalized_inputs["linkedin"] is raw_linkedin
    assert normalized_inputs["tiktok"] is raw_tiktok
    assert attribution_inputs["ads"] is normalized_ads
    assert attribution_inputs["hubspot_df"] is validated_hubspot
    assert attribution_inputs["stripe_df"] is validated_stripe
