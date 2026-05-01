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
    _root = "/Workspace/Users/zajen@n8ivpromotions.com/attributionAgent/attribution_agent/attribution_agent"
sys.path.insert(0, _root)


def _get_secret(key: str) -> str:
    """Must be called from main thread where dbutils is available."""
    try:
        from databricks.sdk.runtime import dbutils
        return dbutils.secrets.get(scope="attribution", key=key)
    except Exception:
        return os.environ.get(key, "")


from config.client_config import ClientConfig, get_client, list_clients
from agents.ingest.meta_connector import pull_meta_data
from agents.ingest.hubspot_connector import pull_hubspot_data
from agents.ingest.stripe_connector import pull_stripe_data
from agents.ingest.validator import validate_meta, validate_hubspot, validate_stripe, ValidationReport
from utils.databricks_writer import (
    ensure_schema, ensure_tables,
    write_meta_data, write_hubspot_data, write_stripe_data,
)


def _with_retry(fn, retries: int = 3, delay: int = 15, label: str = ""):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            logger.warning(f"[{label}] Attempt {attempt}/{retries} failed: {exc}")
            if attempt < retries:
                time.sleep(delay)
    raise last_exc


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
        retries=3, delay=30, label="pull-meta",
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
        retries=3, delay=30, label="pull-hubspot",
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
        retries=2, delay=15, label="write-meta",
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
        retries=2, delay=15, label="write-hubspot",
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
        retries=3, delay=30, label="pull-stripe",
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
        retries=2, delay=15, label="write-stripe",
    )


def step_alert(meta_result, hubspot_result, stripe_result, config: ClientConfig) -> None:
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


def ingest_flow(client_id: str) -> dict:
    logger.info(f"{'='*50}")
    logger.info(f"Ingest Flow START | client={client_id}")
    logger.info(f"{'='*50}")

    config = get_client(client_id)

    meta_token    = _get_secret("META_ACCESS_TOKEN")
    hubspot_token = _get_secret("HUBSPOT_ACCESS_TOKEN")
    stripe_token  = _get_secret("STRIPE_SECRET_KEY")

    step_setup(config.databricks_schema)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        meta_future    = pool.submit(step_pull_meta,    config, meta_token)
        hubspot_future = pool.submit(step_pull_hubspot, config, hubspot_token)
        stripe_future  = pool.submit(step_pull_stripe,  config, stripe_token)
        meta_df    = meta_future.result()
        hubspot_df = hubspot_future.result()
        stripe_df  = stripe_future.result()

    meta_validated    = step_validate_meta(meta_df,    config)
    hubspot_validated = step_validate_hubspot(hubspot_df, config)
    stripe_validated  = step_validate_stripe(stripe_df,  config)

    meta_rows    = step_write_meta(meta_validated,    config)
    hubspot_rows = step_write_hubspot(hubspot_validated, config)
    stripe_rows  = step_write_stripe(stripe_validated,  config)

    step_alert(meta_validated, hubspot_validated, stripe_validated, config)

    summary = {
        "client_id":    client_id,
        "meta_rows":    meta_rows,
        "hubspot_rows": hubspot_rows,
        "stripe_rows":  stripe_rows,
        "status":       "complete",
    }
    logger.info(f"Ingest Flow COMPLETE | {summary}")
    return summary


def ingest_all_clients() -> list[dict]:
    results = []
    for client_id in list_clients():
        result = ingest_flow(client_id)
        results.append(result)
    return results


if __name__ == "__main__":
    try:
        from databricks.sdk.runtime import dbutils
        client_id = dbutils.widgets.get("client")
        ingest_flow(client_id=client_id)
    except Exception:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--client", type=str, default=None)
        args, _ = parser.parse_known_args()
        if args.client:
            ingest_flow(client_id=args.client)
        else:
            ingest_all_clients()