"""
flows/ingest_flow.py
Pure Python orchestrator — runs as a Databricks Job (Python script task).
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

try:
    _root = str(Path(__file__).parent.parent)
except NameError:
    import inspect as _inspect

    _root = str(Path(_inspect.getfile(_inspect.currentframe())).parent.parent)
sys.path.insert(0, _root)


from config.client_config import ClientConfig, get_client, list_clients
from agents.ingest.meta_connector import pull_meta_data
from agents.ingest.google_ads_connector import pull_google_ads_data
from agents.ingest.hubspot_connector import pull_hubspot_data
from agents.ingest.linkedin_connector import pull_linkedin_ads_data
from agents.ingest.tiktok_connector import pull_tiktok_ads_data
from agents.ingest.stripe_connector import pull_stripe_data
from agents.ingest.ad_sources import (
    combine_normalized_ads,
    normalize_google_ads,
    normalize_linkedin_ads,
    normalize_meta_ads,
    normalize_tiktok_ads,
)
from agents.ingest.validator import validate_meta, validate_hubspot, validate_stripe
from attribution_engine import run_attribution
from utils.secrets import redact_secrets
from utils.databricks_writer import (
    ensure_schema,
    ensure_tables,
    write_meta_data,
    write_hubspot_data,
    write_stripe_data,
    write_normalized_ad_data,
    write_attribution_results,
)


def _with_retry(fn, retries: int = 3, delay: int = 15, label: str = ""):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            logger.warning(
                f"[{label}] Attempt {attempt}/{retries} failed: "
                + redact_secrets(str(exc))
            )
            if attempt < retries:
                time.sleep(delay)
    raise last_exc


def _collect(future, label: str, failures: dict):
    """Resolve a source-pull future without letting one bad source abort the run.

    On failure the source degrades to "no data" (returns None) and the error is
    recorded in `failures` so the run can finish with the sources that did
    succeed and report partial status. Retries already happened inside the
    future via `_with_retry`; reaching here means they were exhausted.
    """
    try:
        return future.result()
    except Exception as exc:
        err = redact_secrets(repr(exc))
        logger.error(f"[{label}] source failed after retries — skipping: {err}")
        failures[label] = err
        return None


def step_setup(schema: str) -> None:
    logger.info(f"[Setup] Ensuring schema + tables: {schema}")
    _with_retry(lambda: ensure_schema(schema), label="setup-schema")
    _with_retry(lambda: ensure_tables(schema), label="setup-tables")


def step_pull_meta(config: ClientConfig, meta_token: str):
    if not config.meta_enabled:
        logger.info("[Meta] Not enabled — skipping")
        return None
    return _with_retry(
        lambda: pull_meta_data(
            ad_account_id=config.meta_ad_account_id,
            lookback_days=config.lookback_days,
            access_token=meta_token,
        ),
        retries=3,
        delay=30,
        label="pull-meta",
    )


def step_pull_hubspot(config: ClientConfig, hubspot_token: str):
    if not config.hubspot_enabled:
        logger.info("[HubSpot] Not enabled — skipping")
        return None
    return _with_retry(
        lambda: pull_hubspot_data(
            lookback_days=config.lookback_days,
            pipeline_id=config.hubspot_pipeline_id,
            access_token=hubspot_token,
        ),
        retries=3,
        delay=30,
        label="pull-hubspot",
    )


def step_pull_google_ads(config: ClientConfig, google_token: str):
    if not getattr(config, "google_ads_enabled", False):
        logger.info("[Google Ads] Not enabled — skipping")
        return None
    return _with_retry(
        lambda: pull_google_ads_data(
            customer_id=config.google_ads_customer_id,
            lookback_days=config.lookback_days,
            access_token=google_token,
        ),
        retries=3,
        delay=30,
        label="pull-google-ads",
    )


def step_pull_linkedin_ads(config: ClientConfig, linkedin_token: str):
    if not getattr(config, "linkedin_ads_enabled", False):
        logger.info("[LinkedIn Ads] Not enabled — skipping")
        return None
    return _with_retry(
        lambda: pull_linkedin_ads_data(
            account_id=config.linkedin_ads_account_id,
            lookback_days=config.lookback_days,
            access_token=linkedin_token,
        ),
        retries=3,
        delay=30,
        label="pull-linkedin-ads",
    )


def step_pull_tiktok_ads(config: ClientConfig, tiktok_token: str):
    if not getattr(config, "tiktok_ads_enabled", False):
        logger.info("[TikTok Ads] Not enabled — skipping")
        return None
    return _with_retry(
        lambda: pull_tiktok_ads_data(
            advertiser_id=config.tiktok_ads_advertiser_id,
            lookback_days=config.lookback_days,
            access_token=tiktok_token,
        ),
        retries=3,
        delay=30,
        label="pull-tiktok-ads",
    )


def step_validate_meta(df, config: ClientConfig):
    if df is None:
        return None, None
    return validate_meta(
        df=df,
        client_id=config.client_id,
        spend_drop_pct_alert=config.spend_drop_pct_alert,
        zero_spend_days_allowed=config.zero_spend_days_allowed,
    )


def step_validate_hubspot(df, config: ClientConfig):
    if df is None:
        return None, None
    return validate_hubspot(df=df, client_id=config.client_id)


def step_write_meta(validated_result, config: ClientConfig) -> int:
    if not validated_result or validated_result[0] is None:
        return 0
    df, report = validated_result
    if report and not report.passed:
        logger.warning(f"[Meta] Validation failed — skipping write: {report.errors}")
        return 0
    return _with_retry(
        lambda: write_meta_data(df, schema=config.databricks_schema),
        retries=2,
        delay=15,
        label="write-meta",
    )


def step_write_hubspot(validated_result, config: ClientConfig) -> int:
    if not validated_result or validated_result[0] is None:
        return 0
    df, report = validated_result
    if report and not report.passed:
        logger.warning(f"[HubSpot] Validation failed — skipping write: {report.errors}")
        return 0
    return _with_retry(
        lambda: write_hubspot_data(df, schema=config.databricks_schema),
        retries=2,
        delay=15,
        label="write-hubspot",
    )


def step_pull_stripe(config: ClientConfig, stripe_token: str):
    if not config.stripe_enabled:
        logger.info("[Stripe] Not enabled — skipping")
        return None
    return _with_retry(
        lambda: pull_stripe_data(
            lookback_days=config.lookback_days,
            access_token=stripe_token,
        ),
        retries=3,
        delay=30,
        label="pull-stripe",
    )


def step_validate_stripe(df, config: ClientConfig):
    if df is None:
        return None, None
    return validate_stripe(df=df, client_id=config.client_id)


def step_write_stripe(validated_result, config: ClientConfig) -> int:
    if not validated_result or validated_result[0] is None:
        return 0
    df, report = validated_result
    if report and not report.passed:
        logger.warning(f"[Stripe] Validation failed — skipping write: {report.errors}")
        return 0
    return _with_retry(
        lambda: write_stripe_data(df, schema=config.databricks_schema),
        retries=2,
        delay=15,
        label="write-stripe",
    )


def build_normalized_ads(
    meta_df, google_df, linkedin_df, tiktok_df, config: ClientConfig
):
    return combine_normalized_ads(
        [
            normalize_meta_ads(meta_df, config.client_id),
            normalize_google_ads(google_df, config.client_id),
            normalize_linkedin_ads(linkedin_df, config.client_id),
            normalize_tiktok_ads(tiktok_df, config.client_id),
        ]
    )


def step_write_normalized_ads(normalized, config: ClientConfig) -> int:
    if normalized is None or normalized.empty:
        logger.warning("[Ads] No normalized ad rows to write")
        return 0
    return _with_retry(
        lambda: write_normalized_ad_data(normalized, schema=config.databricks_schema),
        retries=2,
        delay=15,
        label="write-normalized-ads",
    )


def step_build_attribution(
    normalized_ads, hubspot_df, stripe_df, config: ClientConfig
) -> dict:
    """Closed-loop join: connect ad touchpoints to closed/won revenue.

    Non-fatal — attribution is a downstream read model, so a failure here must
    not fail the ingest. Returns a small summary dict for the run record.
    """
    try:
        result = run_attribution(
            client_id=config.client_id,
            ads=normalized_ads if normalized_ads is not None else None,
            model=config.attribution_model,
            hubspot_df=hubspot_df,
            stripe_df=stripe_df,
            lookback_days=max(config.lookback_days, 90),
        )
        scorecard = result.channel_performance
        rows_written = _with_retry(
            lambda: write_attribution_results(
                scorecard, schema=config.databricks_schema
            ),
            retries=2,
            delay=15,
            label="write-attribution-results",
        )
        logger.info(
            f"[Attribution] {config.client_id} | model={config.attribution_model} | "
            f"total=${result.total_revenue:,.0f} attributed=${result.attributed_revenue:,.0f} "
            f"unattributed=${result.unattributed_revenue:,.0f} | rows={rows_written}"
        )
        return {
            "attribution_model": config.attribution_model,
            "total_revenue": result.total_revenue,
            "attributed_revenue": result.attributed_revenue,
            "unattributed_revenue": result.unattributed_revenue,
            "attribution_rows": rows_written,
        }
    except Exception as exc:
        logger.error(f"[Attribution] build failed for {config.client_id}: {exc!r}")
        return {"attribution_error": repr(exc)}


def step_alert(
    meta_result, hubspot_result, stripe_result, config: ClientConfig
) -> None:
    all_reports = []
    if meta_result and meta_result[1]:
        all_reports.append(meta_result[1])
    if hubspot_result and hubspot_result[1]:
        all_reports.append(hubspot_result[1])
    if stripe_result and stripe_result[1]:
        all_reports.append(stripe_result[1])
    issues = [r for r in all_reports if r.warnings or not r.passed]
    if not issues:
        logger.info("[Alert] No issues")
        return
    for r in issues:
        logger.warning(r.summary())


def step_data_quality_agent(
    meta_result,
    hubspot_result,
    stripe_result,
    ingest_summary: dict,
    config: ClientConfig,
) -> None:
    """Non-blocking: run the N8iV data-quality agent and log findings."""
    try:
        from agents.intelligence.n8iv_agents import run_data_quality_agent

        reports = [
            r[1]
            for r in (meta_result, hubspot_result, stripe_result)
            if r is not None and r[1] is not None
        ]
        findings = run_data_quality_agent(
            client_id=config.client_id,
            validation_reports=reports,
            ingest_summary=ingest_summary,
        )
        if findings.get("escalations"):
            logger.error(
                f"[DataQuality] Escalations for {config.client_id}: "
                + "; ".join(findings["escalations"])
            )
        elif findings.get("issues"):
            logger.warning(
                f"[DataQuality] Issues for {config.client_id}: "
                + "; ".join(findings["issues"][:3])
            )
    except Exception as exc:
        logger.warning(f"[DataQuality] agent step failed (non-fatal): {exc!r}")


def ingest_flow(client_id: str) -> dict:
    logger.info(f"{'=' * 50}")
    logger.info(f"Ingest Flow START | client={client_id}")
    logger.info(f"{'=' * 50}")

    config = get_client(client_id)

    meta_token = config.meta_access_token
    hubspot_token = config.hubspot_access_token
    stripe_token = config.stripe_secret_key
    google_token = config.google_ads_refresh_token
    linkedin_token = config.linkedin_access_token
    tiktok_token = config.tiktok_access_token

    step_setup(config.databricks_schema)

    source_failures: dict = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        meta_future = pool.submit(step_pull_meta, config, meta_token)
        google_future = pool.submit(step_pull_google_ads, config, google_token)
        linkedin_future = pool.submit(step_pull_linkedin_ads, config, linkedin_token)
        tiktok_future = pool.submit(step_pull_tiktok_ads, config, tiktok_token)
        hubspot_future = pool.submit(step_pull_hubspot, config, hubspot_token)
        stripe_future = pool.submit(step_pull_stripe, config, stripe_token)
        meta_df = _collect(meta_future, "pull-meta", source_failures)
        google_df = _collect(google_future, "pull-google-ads", source_failures)
        linkedin_df = _collect(linkedin_future, "pull-linkedin-ads", source_failures)
        tiktok_df = _collect(tiktok_future, "pull-tiktok-ads", source_failures)
        hubspot_df = _collect(hubspot_future, "pull-hubspot", source_failures)
        stripe_df = _collect(stripe_future, "pull-stripe", source_failures)

    meta_validated = step_validate_meta(meta_df, config)
    hubspot_validated = step_validate_hubspot(hubspot_df, config)
    stripe_validated = step_validate_stripe(stripe_df, config)

    meta_rows = step_write_meta(meta_validated, config)
    hubspot_rows = step_write_hubspot(hubspot_validated, config)
    stripe_rows = step_write_stripe(stripe_validated, config)
    normalized_ads = build_normalized_ads(
        meta_df, google_df, linkedin_df, tiktok_df, config
    )
    normalized_ad_rows = step_write_normalized_ads(normalized_ads, config)

    # Closed-loop join: attribute closed/won revenue back to ad touchpoints.
    attribution = step_build_attribution(normalized_ads, hubspot_df, stripe_df, config)

    step_alert(meta_validated, hubspot_validated, stripe_validated, config)

    summary = {
        "client_id": client_id,
        "meta_rows": meta_rows,
        "google_rows": 0 if google_df is None else len(google_df),
        "linkedin_rows": 0 if linkedin_df is None else len(linkedin_df),
        "tiktok_rows": 0 if tiktok_df is None else len(tiktok_df),
        "hubspot_rows": hubspot_rows,
        "stripe_rows": stripe_rows,
        "normalized_ad_rows": normalized_ad_rows,
        "attribution": attribution,
        "source_failures": source_failures,
        "status": "partial" if source_failures else "complete",
    }
    if source_failures:
        logger.error(
            f"[Ingest] {len(source_failures)} source(s) failed for {client_id}: "
            + ", ".join(source_failures.keys())
        )

    step_data_quality_agent(
        meta_validated,
        hubspot_validated,
        stripe_validated,
        ingest_summary=summary,
        config=config,
    )

    logger.info(f"Ingest Flow COMPLETE | {summary}")
    return summary


def ingest_all_clients() -> list[dict]:
    results = []
    for client_id in list_clients():
        result = ingest_flow(client_id)
        results.append(result)
    return results


if __name__ == "__main__":
    # Widgets only on real Databricks compute — see agency_flow.py __main__.
    if os.environ.get("DATABRICKS_RUNTIME_VERSION"):
        from databricks.sdk.runtime import dbutils

        client_id = dbutils.widgets.get("client")
        ingest_flow(client_id=client_id)
    else:
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--client", type=str, default=None)
        args, _ = parser.parse_known_args()
        if args.client:
            ingest_flow(client_id=args.client)
        else:
            ingest_all_clients()
