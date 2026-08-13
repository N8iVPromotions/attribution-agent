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
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import pyarrow as pa
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
from agents.ingest.meta_connector import iter_meta_data_batches, pull_meta_data
from agents.ingest.google_ads_connector import (
    iter_google_ads_batches,
    pull_google_ads_data,
)
from agents.ingest.hubspot_connector import iter_hubspot_batches, pull_hubspot_data
from agents.ingest.linkedin_connector import (
    iter_linkedin_ads_batches,
    pull_linkedin_ads_data,
)
from agents.ingest.tiktok_connector import (
    iter_tiktok_ads_batches,
    pull_tiktok_ads_data,
)
from agents.ingest.stripe_connector import iter_stripe_batches, pull_stripe_data
from agents.ingest.ad_sources import (
    combine_normalized_ads,
    normalize_google_ads,
    normalize_linkedin_ads,
    normalize_meta_ads,
    normalize_tiktok_ads,
)
from agents.ingest.validator import (
    ValidationReport,
    validate_hubspot,
    validate_meta,
    validate_stripe,
)
from attribution_engine import run_attribution
from attribution_models import normalize_model
from utils.secrets import redact_secrets
from utils.databricks_writer import (
    ensure_schema,
    ensure_tables,
    write_meta_data,
    write_hubspot_data,
    write_stripe_data,
    write_normalized_ad_data,
    write_attribution_results,
    write_hubspot_batches,
    write_meta_batches,
    write_normalized_ad_batches,
    write_stripe_batches,
)
from utils.operator_alerts import (
    build_attribution_coverage_alerts,
    build_credential_alerts,
    build_source_failure_alerts,
    build_validation_alerts,
    dispatch_alerts,
)


@dataclass
class StreamSourcePlan:
    name: str
    batches: Iterable[pa.RecordBatch]
    raw_writer: Callable[[Iterable[pa.RecordBatch], str], int] | None = None
    validator: (
        Callable[[pd.DataFrame], tuple[pd.DataFrame, ValidationReport]] | None
    ) = None
    normalizer: Callable[[pd.DataFrame, str], pd.DataFrame] | None = None


@dataclass
class StreamSourceResult:
    source_rows: int = 0
    raw_rows: int = 0
    normalized_rows: int = 0
    reports: list[ValidationReport] = field(default_factory=list)


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


def _streaming_enabled() -> bool:
    configured = os.environ.get("ARIE_STREAMING_INGEST")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes"}
    return bool(os.environ.get("CLOUD_RUN_JOB"))


def _process_stream_source(
    plan: StreamSourcePlan, config: ClientConfig
) -> StreamSourceResult:
    result = StreamSourceResult()
    for batch in plan.batches:
        result.source_rows += batch.num_rows
        frame = batch.to_pandas()
        if plan.validator:
            frame, report = plan.validator(frame)
            result.reports.append(report)
            if not report.passed:
                continue
            batch = pa.RecordBatch.from_pandas(frame, preserve_index=False)
        if plan.raw_writer:
            result.raw_rows += _with_retry(
                lambda current=batch: plan.raw_writer(
                    [current], config.databricks_schema
                ),
                retries=2,
                delay=15,
                label=f"write-{plan.name}",
            )
        if plan.normalizer:
            normalized = plan.normalizer(frame, config.client_id)
            if not normalized.empty:
                normalized_batch = pa.RecordBatch.from_pandas(
                    normalized, preserve_index=False
                )
                result.normalized_rows += _with_retry(
                    lambda current=normalized_batch: write_normalized_ad_batches(
                        [current], config.databricks_schema
                    ),
                    retries=2,
                    delay=15,
                    label=f"write-{plan.name}-normalized",
                )
    return result


def step_setup(schema: str) -> None:
    logger.info(f"[Setup] Ensuring schema + tables: {schema}")
    _with_retry(lambda: ensure_schema(schema), label="setup-schema")
    _with_retry(lambda: ensure_tables(schema), label="setup-tables")


def step_pull_meta(config: ClientConfig, meta_token: str, run_id: str = ""):
    if not config.meta_enabled:
        logger.info("[Meta] Not enabled — skipping")
        return None
    return _with_retry(
        lambda: pull_meta_data(
            ad_account_id=config.meta_ad_account_id,
            lookback_days=config.lookback_days,
            access_token=meta_token,
            client_id=config.client_id,
            run_id=run_id,
        ),
        retries=3,
        delay=30,
        label="pull-meta",
    )


def step_pull_hubspot(config: ClientConfig, hubspot_token: str, run_id: str = ""):
    if not config.hubspot_enabled:
        logger.info("[HubSpot] Not enabled — skipping")
        return None
    return _with_retry(
        lambda: pull_hubspot_data(
            lookback_days=config.lookback_days,
            pipeline_id=config.hubspot_pipeline_id,
            access_token=hubspot_token,
            client_id=config.client_id,
            run_id=run_id,
        ),
        retries=3,
        delay=30,
        label="pull-hubspot",
    )


def step_pull_google_ads(config: ClientConfig, google_token: str, run_id: str = ""):
    if not getattr(config, "google_ads_enabled", False):
        logger.info("[Google Ads] Not enabled — skipping")
        return None
    return _with_retry(
        lambda: pull_google_ads_data(
            customer_id=config.google_ads_customer_id,
            lookback_days=config.lookback_days,
            access_token=google_token,
            client_id=config.client_id,
            run_id=run_id,
        ),
        retries=3,
        delay=30,
        label="pull-google-ads",
    )


def step_pull_linkedin_ads(config: ClientConfig, linkedin_token: str, run_id: str = ""):
    if not getattr(config, "linkedin_ads_enabled", False):
        logger.info("[LinkedIn Ads] Not enabled — skipping")
        return None
    return _with_retry(
        lambda: pull_linkedin_ads_data(
            account_id=config.linkedin_ads_account_id,
            lookback_days=config.lookback_days,
            access_token=linkedin_token,
            client_id=config.client_id,
            run_id=run_id,
        ),
        retries=3,
        delay=30,
        label="pull-linkedin-ads",
    )


def step_pull_tiktok_ads(config: ClientConfig, tiktok_token: str, run_id: str = ""):
    if not getattr(config, "tiktok_ads_enabled", False):
        logger.info("[TikTok Ads] Not enabled — skipping")
        return None
    return _with_retry(
        lambda: pull_tiktok_ads_data(
            advertiser_id=config.tiktok_ads_advertiser_id,
            lookback_days=config.lookback_days,
            access_token=tiktok_token,
            client_id=config.client_id,
            run_id=run_id,
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


def step_pull_stripe(config: ClientConfig, stripe_token: str, run_id: str = ""):
    if not config.stripe_enabled:
        logger.info("[Stripe] Not enabled — skipping")
        return None
    return _with_retry(
        lambda: pull_stripe_data(
            lookback_days=config.lookback_days,
            access_token=stripe_token,
            client_id=config.client_id,
            run_id=run_id,
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
    normalized_ads,
    hubspot_df,
    stripe_df,
    config: ClientConfig,
    attribution_model: str | None = None,
) -> dict:
    """Closed-loop join: connect ad touchpoints to closed/won revenue.

    Non-fatal — attribution is a downstream read model, so a failure here must
    not fail the ingest. Returns a small summary dict for the run record.
    """
    try:
        selected_model = normalize_model(attribution_model or config.attribution_model)
        result = run_attribution(
            client_id=config.client_id,
            ads=normalized_ads if normalized_ads is not None else None,
            model=selected_model,
            hubspot_df=hubspot_df,
            stripe_df=stripe_df,
            lookback_days=config.lookback_days,
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
            f"[Attribution] {config.client_id} | model={selected_model} | "
            f"total=${result.total_revenue:,.0f} attributed=${result.attributed_revenue:,.0f} "
            f"unattributed=${result.unattributed_revenue:,.0f} | rows={rows_written}"
        )
        return {
            "attribution_model": selected_model,
            "total_revenue": result.total_revenue,
            "attributed_revenue": result.attributed_revenue,
            "unattributed_revenue": result.unattributed_revenue,
            "attribution_rows": rows_written,
        }
    except Exception as exc:
        logger.error(f"[Attribution] build failed for {config.client_id}: {exc!r}")
        return {"attribution_error": repr(exc)}


def step_alert(
    meta_result,
    hubspot_result,
    stripe_result,
    config: ClientConfig,
    run_id: str = "",
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
    dispatch_alerts(build_validation_alerts(config, issues, run_id=run_id))


def step_data_quality_agent(
    meta_result,
    hubspot_result,
    stripe_result,
    ingest_summary: dict,
    config: ClientConfig,
) -> None:
    reports = [
        result[1]
        for result in (meta_result, hubspot_result, stripe_result)
        if result is not None and result[1] is not None
    ]
    _run_data_quality_reports(reports, ingest_summary, config)


def _run_data_quality_reports(
    reports: list[ValidationReport], ingest_summary: dict, config: ClientConfig
) -> None:
    """Run the advisory agent without allowing it to fail ingestion."""
    try:
        from agents.intelligence.n8iv_agents import run_data_quality_agent

        findings = run_data_quality_agent(
            client_id=config.client_id,
            validation_reports=reports,
            ingest_summary=ingest_summary,
            agency_id=config.agency_id,
            run_id=ingest_summary.get("run_id", ""),
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


def _stream_source_plans(
    config: ClientConfig, tokens: dict[str, str], run_id: str
) -> list[StreamSourcePlan]:
    plans: list[StreamSourcePlan] = []
    if config.meta_enabled:
        plans.append(
            StreamSourcePlan(
                name="meta",
                batches=iter_meta_data_batches(
                    config.meta_ad_account_id,
                    config.lookback_days,
                    tokens["meta"],
                    config.client_id,
                    run_id,
                ),
                raw_writer=write_meta_batches,
                validator=lambda frame: validate_meta(
                    frame,
                    config.client_id,
                    config.spend_drop_pct_alert,
                    config.zero_spend_days_allowed,
                ),
                normalizer=normalize_meta_ads,
            )
        )
    if getattr(config, "google_ads_enabled", False):
        plans.append(
            StreamSourcePlan(
                name="google-ads",
                batches=iter_google_ads_batches(
                    config.google_ads_customer_id,
                    config.lookback_days,
                    tokens["google_ads"],
                    config.client_id,
                    run_id,
                ),
                normalizer=normalize_google_ads,
            )
        )
    if getattr(config, "linkedin_ads_enabled", False):
        plans.append(
            StreamSourcePlan(
                name="linkedin-ads",
                batches=iter_linkedin_ads_batches(
                    config.linkedin_ads_account_id,
                    config.lookback_days,
                    tokens["linkedin_ads"],
                    config.client_id,
                    run_id,
                ),
                normalizer=normalize_linkedin_ads,
            )
        )
    if getattr(config, "tiktok_ads_enabled", False):
        plans.append(
            StreamSourcePlan(
                name="tiktok-ads",
                batches=iter_tiktok_ads_batches(
                    config.tiktok_ads_advertiser_id,
                    config.lookback_days,
                    tokens["tiktok_ads"],
                    config.client_id,
                    run_id,
                ),
                normalizer=normalize_tiktok_ads,
            )
        )
    if config.hubspot_enabled:
        plans.append(
            StreamSourcePlan(
                name="hubspot",
                batches=iter_hubspot_batches(
                    config.lookback_days,
                    config.hubspot_pipeline_id,
                    tokens["hubspot"],
                    config.client_id,
                    run_id,
                ),
                raw_writer=write_hubspot_batches,
                validator=lambda frame: validate_hubspot(frame, config.client_id),
            )
        )
    if config.stripe_enabled:
        plans.append(
            StreamSourcePlan(
                name="stripe",
                batches=iter_stripe_batches(
                    config.lookback_days,
                    tokens["stripe"],
                    config.client_id,
                    run_id,
                ),
                raw_writer=write_stripe_batches,
                validator=lambda frame: validate_stripe(frame, config.client_id),
            )
        )
    return plans


def _stream_ingest(config: ClientConfig, tokens: dict[str, str], run_id: str) -> dict:
    source_results: dict[str, StreamSourceResult] = {}
    source_failures: dict[str, str] = {}
    reports: list[ValidationReport] = []

    for plan in _stream_source_plans(config, tokens, run_id):
        try:
            _raise_staging_vendor_error(plan.name, config.client_id)
            result = _process_stream_source(plan, config)
            source_results[plan.name] = result
            reports.extend(result.reports)
            failed_reports = [report for report in result.reports if not report.passed]
            if failed_reports:
                source_failures[f"validate-{plan.name}"] = "; ".join(
                    error for report in failed_reports for error in report.errors
                )
        except Exception as exc:
            error = redact_secrets(repr(exc))
            logger.error("[pull-%s] streaming source failed: %s", plan.name, error)
            source_failures[f"pull-{plan.name}"] = error

    def source_rows(name: str, attribute: str = "source_rows") -> int:
        result = source_results.get(name)
        return int(getattr(result, attribute, 0)) if result else 0

    attribution = {"mode": "warehouse_sql", "attribution_rows": 0}
    summary = {
        "client_id": config.client_id,
        "run_id": run_id,
        "meta_rows": source_rows("meta", "raw_rows"),
        "google_rows": source_rows("google-ads"),
        "linkedin_rows": source_rows("linkedin-ads"),
        "tiktok_rows": source_rows("tiktok-ads"),
        "hubspot_rows": source_rows("hubspot", "raw_rows"),
        "stripe_rows": source_rows("stripe", "raw_rows"),
        "normalized_ad_rows": sum(
            source_rows(name, "normalized_rows")
            for name in ("meta", "google-ads", "linkedin-ads", "tiktok-ads")
        ),
        "attribution": attribution,
        "source_failures": source_failures,
        "status": "partial" if source_failures else "complete",
        "ingest_mode": "arrow_streaming",
    }
    if source_failures:
        dispatch_alerts(
            build_source_failure_alerts(config, source_failures, run_id=run_id)
        )
    dispatch_alerts(build_validation_alerts(config, reports, run_id=run_id))
    _run_data_quality_reports(reports, summary, config)
    logger.info("Ingest Flow COMPLETE | %s", summary)
    return summary


def _raise_staging_vendor_error(source: str, client_id: str) -> None:
    """Inject one explicit dry-run fault for staging telemetry validation."""
    if os.environ.get("ARIE_STAGING_VALIDATION", "false").lower() != "true":
        return
    configured_source = os.environ.get("ARIE_STAGING_FAIL_SOURCE", "").strip()
    configured_client = os.environ.get("ARIE_STAGING_FAIL_CLIENT_ID", "").strip()
    if configured_source == source and (
        not configured_client or configured_client == client_id
    ):
        raise RuntimeError(f"STAGING_SIMULATED_VENDOR_ERROR:{source}")


def ingest_flow(
    client_id: str,
    run_id: str = "",
    attribution_model: str | None = None,
) -> dict:
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
    tokens = {
        "meta": meta_token,
        "google_ads": google_token,
        "linkedin_ads": linkedin_token,
        "tiktok_ads": tiktok_token,
        "hubspot": hubspot_token,
        "stripe": stripe_token,
    }

    dispatch_alerts(
        build_credential_alerts(
            config,
            tokens=tokens,
            run_id=run_id,
        )
    )

    step_setup(config.databricks_schema)
    if _streaming_enabled():
        return _stream_ingest(config, tokens, run_id)

    source_failures: dict = {}
    try:
        source_workers = max(
            1, int(os.environ.get("ARIE_INGEST_SOURCE_WORKERS", "3") or "3")
        )
    except ValueError:
        logger.warning("[Ingest] Invalid ARIE_INGEST_SOURCE_WORKERS; using 3")
        source_workers = 3
    with concurrent.futures.ThreadPoolExecutor(max_workers=source_workers) as pool:
        meta_future = pool.submit(step_pull_meta, config, meta_token, run_id)
        google_future = pool.submit(step_pull_google_ads, config, google_token, run_id)
        linkedin_future = pool.submit(
            step_pull_linkedin_ads, config, linkedin_token, run_id
        )
        tiktok_future = pool.submit(step_pull_tiktok_ads, config, tiktok_token, run_id)
        hubspot_future = pool.submit(step_pull_hubspot, config, hubspot_token, run_id)
        stripe_future = pool.submit(step_pull_stripe, config, stripe_token, run_id)
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
    attribution = step_build_attribution(
        normalized_ads,
        hubspot_df,
        stripe_df,
        config,
        attribution_model=attribution_model,
    )

    step_alert(meta_validated, hubspot_validated, stripe_validated, config, run_id)

    summary = {
        "client_id": client_id,
        "run_id": run_id,
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
        dispatch_alerts(
            build_source_failure_alerts(config, source_failures, run_id=run_id)
        )

    dispatch_alerts(
        build_attribution_coverage_alerts(config, attribution, run_id=run_id)
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
