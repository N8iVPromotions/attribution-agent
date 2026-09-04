from __future__ import annotations

import concurrent.futures
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

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
        hubspot_closed_won_stage_ids=("Closed Won", "enterprise-won"),
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
    assert captured["closed_won_stages"] == ("closedwon", "enterprisewon")
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
        hubspot_closed_won_stage_ids=("closedwon", "won"),
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
    pull_lookbacks = {}

    monkeypatch.setattr(ingest_module, "get_client", lambda _: config)
    monkeypatch.setattr(ingest_module, "_streaming_enabled", lambda: False)
    monkeypatch.setattr(
        ingest_module, "extraction_lookback_days", lambda *_args, **_kwargs: 62
    )
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

        def fake_pull(*args, _frame=frame, _name=step_name, **_kwargs):
            pull_lookbacks[_name] = args[3]
            return _frame

        monkeypatch.setattr(
            ingest_module,
            step_name,
            fake_pull,
        )

    monkeypatch.setattr(
        ingest_module, "step_validate_meta", lambda *_: (validated_meta, None)
    )
    monkeypatch.setattr(
        ingest_module, "step_validate_hubspot", lambda *_: (validated_hubspot, None)
    )
    monkeypatch.setattr(
        ingest_module,
        "step_validate_stripe",
        lambda *_args, **_kwargs: (validated_stripe, None),
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

    summary = ingest_module.ingest_flow("acme", report_month="2026-08")

    assert normalized_inputs["meta"] is validated_meta
    assert normalized_inputs["google"] is raw_google
    assert normalized_inputs["linkedin"] is raw_linkedin
    assert normalized_inputs["tiktok"] is raw_tiktok
    assert attribution_inputs["ads"] is normalized_ads
    assert attribution_inputs["hubspot_df"] is validated_hubspot
    assert attribution_inputs["stripe_df"] is validated_stripe
    assert set(pull_lookbacks.values()) == {62}
    assert summary["report_month"] == "2026-08"
    assert summary["extraction_lookback_days"] == 62


def test_stream_source_plans_use_one_derived_extraction_window(monkeypatch):
    config = ClientConfig(
        client_id="acme",
        client_name="Acme",
        meta_enabled=True,
        meta_ad_account_id="meta",
        google_ads_enabled=True,
        google_ads_customer_id="google",
        linkedin_ads_enabled=True,
        linkedin_ads_account_id="linkedin",
        tiktok_ads_enabled=True,
        tiktok_ads_advertiser_id="tiktok",
        hubspot_enabled=True,
        stripe_enabled=True,
        stripe_account_id="acct_expected",
        stripe_history_start_date="2020-01-01",
        databricks_schema="workspace.attribution_acme",
        lookback_days=30,
    )
    captured = {}

    def fake_iterator(name, lookback_index):
        def iterator(*args, **kwargs):
            captured[name] = args[lookback_index]
            if name == "stripe":
                captured["stripe_history_start"] = kwargs["start_datetime"]
            return []

        return iterator

    monkeypatch.setattr(
        ingest_module, "iter_meta_data_batches", fake_iterator("meta", 1)
    )
    monkeypatch.setattr(
        ingest_module, "iter_google_ads_batches", fake_iterator("google", 1)
    )
    monkeypatch.setattr(
        ingest_module, "iter_linkedin_ads_batches", fake_iterator("linkedin", 1)
    )
    monkeypatch.setattr(
        ingest_module, "iter_tiktok_ads_batches", fake_iterator("tiktok", 1)
    )
    monkeypatch.setattr(
        ingest_module, "iter_hubspot_batches", fake_iterator("hubspot", 0)
    )
    monkeypatch.setattr(
        ingest_module, "iter_stripe_batches", fake_iterator("stripe", 0)
    )

    plans = ingest_module._stream_source_plans(
        config,
        {
            "meta": "token",
            "google_ads": "token",
            "linkedin_ads": "token",
            "tiktok_ads": "token",
            "hubspot": "token",
            "stripe": "token",
        },
        "run",
        62,
    )
    for plan in plans:
        list(plan.batches)

    assert captured == {
        "meta": 62,
        "google": 62,
        "linkedin": 62,
        "tiktok": 62,
        "hubspot": 62,
        "stripe": 62,
        "stripe_history_start": datetime(2020, 1, 1, tzinfo=timezone.utc),
    }


def test_non_stream_stripe_uses_absolute_history_start(monkeypatch):
    config = ClientConfig(
        client_id="acme",
        client_name="Acme",
        stripe_enabled=True,
        stripe_account_id="acct_expected",
        stripe_history_start_date="2018-04-03",
        lookback_days=30,
    )
    captured = {}

    def fake_pull(**kwargs):
        captured.update(kwargs)
        return pd.DataFrame()

    monkeypatch.setattr(ingest_module, "pull_stripe_data", fake_pull)
    monkeypatch.setattr(ingest_module, "_with_retry", lambda fn, **_: fn())

    ingest_module.step_pull_stripe(
        config,
        "stripe-token",
        run_id="run-1",
        lookback_days=62,
    )

    assert captured["lookback_days"] == 62
    assert captured["start_datetime"] == datetime(2018, 4, 3, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "history_start",
    [
        "",
        "2026-02-30",
        (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat(),
    ],
)
def test_non_stream_invalid_stripe_history_is_a_source_failure(
    monkeypatch, history_start
):
    config = ClientConfig(
        client_id="acme",
        client_name="Acme",
        stripe_enabled=True,
        stripe_account_id="acct_expected",
        stripe_history_start_date=history_start,
    )
    monkeypatch.setattr(
        ingest_module,
        "pull_stripe_data",
        lambda **_kwargs: pytest.fail("Stripe must not be called with invalid history"),
    )
    failures = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            ingest_module.step_pull_stripe,
            config,
            "stripe-token",
            "run-1",
            62,
        )
        result = ingest_module._collect(future, "pull-stripe", failures)

    assert result is None
    assert "stripe_history_start_date" in failures["pull-stripe"]


@pytest.mark.parametrize(
    "history_start",
    [
        "",
        "not-a-date",
        (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat(),
    ],
)
def test_stream_invalid_stripe_history_is_a_source_failure(monkeypatch, history_start):
    config = ClientConfig(
        client_id="acme",
        client_name="Acme",
        stripe_enabled=True,
        stripe_account_id="acct_expected",
        stripe_history_start_date=history_start,
        databricks_schema="workspace.attribution_acme",
    )
    monkeypatch.setattr(
        ingest_module,
        "iter_stripe_batches",
        lambda *_args, **_kwargs: pytest.fail(
            "Stripe must not be called with invalid history"
        ),
    )
    monkeypatch.setattr(ingest_module, "dispatch_alerts", lambda *_args: None)
    monkeypatch.setattr(
        ingest_module, "build_source_failure_alerts", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        ingest_module, "build_validation_alerts", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        ingest_module, "_run_data_quality_reports", lambda *_args, **_kwargs: None
    )

    summary = ingest_module._stream_ingest(
        config,
        {"stripe": "stripe-token"},
        "run-1",
        "2026-08",
        62,
    )

    assert summary["status"] == "partial"
    assert "stripe_history_start_date" in summary["source_failures"]["pull-stripe"]
