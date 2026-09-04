"""
utils/databricks_writer.py
Writes validated pandas DataFrames to Databricks Delta tables.
Runs via SQL Connector when local, Spark when inside Databricks.
"""

from __future__ import annotations
import logging
import os
import re
import time
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from itertools import islice
import numpy as np
import pandas as pd
import pyarrow as pa

logger = logging.getLogger(__name__)

# Ops schema for pipeline run history — overridable via env var.
# Default: main.attribution_ops (main catalog is writable in all workspaces).
# Override: ATTRIBUTION_OPS_SCHEMA=hive_metastore.attribution_ops
_OPS_SCHEMA = os.environ.get("ATTRIBUTION_OPS_SCHEMA", "workspace.attribution_ops")


def _clean_env(name: str, default: str = "") -> str:
    """Return env values without accidental Secret Manager/PowerShell newlines."""
    return os.environ.get(name, default).strip()


def _is_databricks() -> bool:
    """True only when a live Spark session is active (notebooks / jobs on a cluster).

    Databricks Apps run as plain Python web servers with no Spark session and no
    pyspark installed, so they return False here and take the SQL-connector path.
    """
    try:
        from pyspark.sql import SparkSession

        return SparkSession.getActiveSession() is not None
    except Exception:
        return False


def _get_spark():
    from pyspark.sql import SparkSession

    spark = SparkSession.getActiveSession()
    if spark is not None:
        return spark
    # Databricks App — use serverless Databricks Connect
    from databricks.connect import DatabricksSession

    return DatabricksSession.builder.serverless().getOrCreate()


def _get_connection():
    from databricks import sql

    # DATABRICKS_HOST is auto-injected by Databricks Apps; strip the scheme if present
    host = _clean_env("DATABRICKS_SERVER_HOSTNAME") or _clean_env("DATABRICKS_HOST")
    hostname = host.replace("https://", "").replace("http://", "").rstrip("/")
    if not hostname:
        raise EnvironmentError(
            "DATABRICKS_SERVER_HOSTNAME (or DATABRICKS_HOST) is not set"
        )
    http_path = _clean_env("DATABRICKS_HTTP_PATH")
    if not http_path:
        raise EnvironmentError("DATABRICKS_HTTP_PATH is not set")

    # Local dev authenticates with a PAT; the Databricks App has no token and
    # instead uses the OAuth (M2M) service-principal credentials it auto-injects
    # as DATABRICKS_CLIENT_ID / DATABRICKS_CLIENT_SECRET (picked up by Config()).
    access_token = _clean_env("DATABRICKS_TOKEN")
    if access_token:
        return sql.connect(
            server_hostname=hostname,
            http_path=http_path,
            access_token=access_token,
        )

    from databricks.sdk.core import Config, oauth_service_principal

    cfg = Config(host=f"https://{hostname}")
    return sql.connect(
        server_hostname=hostname,
        http_path=http_path,
        credentials_provider=lambda: oauth_service_principal(cfg),
    )


def _run_sql(statement: str) -> None:
    if _is_databricks():
        _get_spark().sql(statement)
    else:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(statement)
        cursor.close()
        conn.close()


def ensure_schema(schema: str) -> None:
    _run_sql(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    logger.info(f"[Databricks] Schema ready: {schema}")


META_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.meta_ads_raw (
    ad_account_id       STRING,
    campaign_id         STRING,
    campaign_name       STRING,
    adset_id            STRING,
    adset_name          STRING,
    date                DATE,
    spend               DOUBLE,
    impressions         BIGINT,
    clicks              BIGINT,
    reach               BIGINT,
    cpm                 DOUBLE,
    cpc                 DOUBLE,
    ctr                 DOUBLE,
    source              STRING,
    ingested_at         TIMESTAMP,
    conversions_lead    BIGINT,
    conversions_offsite_conversion_fb_pixel_lead            BIGINT,
    conversions_offsite_conversion_fb_pixel_purchase        BIGINT,
    conversions_offsite_conversion_fb_pixel_complete_registration BIGINT
)
USING DELTA
CLUSTER BY (date, campaign_id)
TBLPROPERTIES ('delta.enableDeletionVectors' = 'true')
"""

HUBSPOT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.hubspot_deals_raw (
    deal_id             STRING,
    deal_name           STRING,
    deal_stage          STRING,
    pipeline            STRING,
    amount              DOUBLE,
    close_date          DATE,
    create_date         TIMESTAMP,
    stage_probability   DOUBLE,
    contact_id          STRING,
    contact_email       STRING,
    hs_source           STRING,
    hs_source_label     STRING,
    hs_source_detail_1  STRING,
    hs_source_detail_2  STRING,
    utm_campaign        STRING,
    utm_source          STRING,
    utm_medium          STRING,
    utm_content         STRING,
    first_page_url      STRING,
    lifecycle_stage     STRING,
    lead_create_date    TIMESTAMP,
    days_to_deal        BIGINT,
    source              STRING,
    ingested_at         TIMESTAMP
)
USING DELTA
"""

STRIPE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.stripe_payments_raw (
    payment_id          STRING,
    customer_id         STRING,
    customer_email      STRING,
    amount_paid         DOUBLE,
    currency            STRING,
    status              STRING,
    refunded            BOOLEAN,
    refund_amount       DOUBLE,
    created_at          TIMESTAMP,
    description         STRING,
    hubspot_deal_id     STRING,
    source              STRING,
    ingested_at         TIMESTAMP
)
USING DELTA
"""

NORMALIZED_AD_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.ad_spend_normalized (
    client_id           STRING,
    source_platform     STRING,
    account_id          STRING,
    campaign_id         STRING,
    campaign_name       STRING,
    ad_group_id         STRING,
    ad_group_name       STRING,
    ad_id               STRING,
    ad_name             STRING,
    date                DATE,
    spend               DOUBLE,
    impressions         BIGINT,
    clicks              BIGINT,
    conversions         DOUBLE,
    utm_source          STRING,
    utm_medium          STRING,
    utm_campaign        STRING,
    landing_url         STRING,
    ingested_at         TIMESTAMP
)
USING DELTA
CLUSTER BY (date, campaign_id)
TBLPROPERTIES ('delta.enableDeletionVectors' = 'true')
"""

ATTRIBUTION_RESULTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.attribution_results (
    client_id               STRING,
    source_platform         STRING,
    attribution_model       STRING,
    spend                   DOUBLE,
    attributed_revenue      DOUBLE,
    attributed_conversions  DOUBLE,
    roas                    DOUBLE,
    cac                     DOUBLE,
    cpa                     DOUBLE,
    window_start            DATE,
    window_end              DATE,
    computed_at             TIMESTAMP
)
USING DELTA
"""

RUN_HISTORY_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.pipeline_runs (
    run_id              STRING,
    agency_id           STRING,
    client_id           STRING,
    run_mode            STRING,
    attribution_model   STRING,
    status              STRING,
    dry_run             BOOLEAN,
    meta_rows           BIGINT,
    google_rows         BIGINT,
    linkedin_rows       BIGINT,
    tiktok_rows         BIGINT,
    hubspot_rows        BIGINT,
    stripe_rows         BIGINT,
    normalized_ad_rows  BIGINT,
    total_pipeline      DOUBLE,
    top_channel         STRING,
    email_sent          BOOLEAN,
    warnings            STRING,
    error               STRING,
    started_at          TIMESTAMP,
    finished_at         TIMESTAMP,
    output_schema       STRING
)
USING DELTA
"""

AUDIT_LOG_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.audit_log (
    event_id        STRING,
    event_time      TIMESTAMP,
    event_type      STRING,
    actor           STRING,
    client_id       STRING,
    agency_id       STRING,
    resource        STRING,
    action          STRING,
    outcome         STRING,
    detail_json     STRING,
    run_id          STRING,
    ip_address      STRING,
    session_id      STRING
)
USING DELTA
PARTITIONED BY (event_type)
TBLPROPERTIES ('delta.logRetentionDuration' = 'interval 365 days')
"""

INSIGHT_REPORTS_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.insight_reports (
    report_id           STRING,
    client_id           STRING,
    agency_id           STRING,
    report_month        STRING,
    narrative           STRING,
    key_findings        STRING,
    top_channel         STRING,
    total_pipeline      DOUBLE,
    total_spend         DOUBLE,
    overall_roi         DOUBLE,
    collected_revenue   DOUBLE,
    refund_rate         DOUBLE,
    true_roi            DOUBLE,
    attribution_model   STRING,
    generated_at        TIMESTAMP,
    run_id              STRING,
    prompt_version      STRING,
    model_id            STRING,
    input_tokens        BIGINT,
    output_tokens       BIGINT,
    cache_read_tokens   BIGINT,
    status              STRING
)
USING DELTA
PARTITIONED BY (client_id)
"""

APPROVAL_QUEUE_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.approval_queue (
    action_id           STRING,
    created_at          TIMESTAMP,
    actor               STRING,
    description         STRING,
    action_type         STRING,
    payload_json        STRING,
    status              STRING,
    resolved_at         TIMESTAMP,
    resolved_by         STRING,
    resolution_note     STRING,
    channel             STRING
)
USING DELTA
"""

TELEGRAM_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.telegram_events (
    event_id            STRING,
    event_time          TIMESTAMP,
    update_id           BIGINT,
    message_id          BIGINT,
    chat_id             STRING,
    user_id             STRING,
    username            STRING,
    event_type          STRING,
    raw_text            STRING,
    voice_file_id       STRING,
    parsed_intent       STRING,
    params_json         STRING,
    action_id           STRING,
    response_summary    STRING,
    status              STRING,
    error               STRING
)
USING DELTA
PARTITIONED BY (event_type)
"""

OPERATOR_ALERTS_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.operator_alerts (
    alert_id            STRING,
    event_time          TIMESTAMP,
    severity            STRING,
    category            STRING,
    title               STRING,
    message             STRING,
    client_id           STRING,
    agency_id           STRING,
    source              STRING,
    run_id              STRING,
    action_required     STRING,
    metadata_json       STRING,
    status              STRING
)
USING DELTA
PARTITIONED BY (severity)
"""

AUTH_USERS_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.auth_users (
    user_id             STRING,
    email               STRING,
    display_name        STRING,
    role                STRING,
    agency_id           STRING,
    client_ids          STRING,
    password_hash       STRING,
    is_active           BOOLEAN,
    failed_login_count  BIGINT,
    locked_until        TIMESTAMP,
    last_login_at       TIMESTAMP,
    created_at          TIMESTAMP,
    updated_at          TIMESTAMP
)
USING DELTA
PARTITIONED BY (role)
"""

AUTH_SESSIONS_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.auth_sessions (
    session_id      STRING,
    user_id         STRING,
    email           STRING,
    role            STRING,
    agency_id       STRING,
    created_at      TIMESTAMP,
    expires_at      TIMESTAMP,
    revoked_at      TIMESTAMP,
    user_agent      STRING
)
USING DELTA
PARTITIONED BY (role)
"""

AUTH_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.auth_events (
    event_id        STRING,
    event_time      TIMESTAMP,
    event_type      STRING,
    email           STRING,
    user_id         STRING,
    role            STRING,
    agency_id       STRING,
    outcome         STRING,
    detail_json     STRING,
    session_id      STRING
)
USING DELTA
PARTITIONED BY (event_type)
"""

IDEMPOTENCY_STORE_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.idempotency_store (
    key             STRING,
    created_at      TIMESTAMP,
    expires_at      TIMESTAMP,
    result_json     STRING,
    step_name       STRING,
    run_id          STRING,
    client_id       STRING
)
USING DELTA
"""

PIPELINE_CHECKPOINTS_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.pipeline_checkpoints (
    checkpoint_id   STRING,
    run_id          STRING,
    agency_id       STRING,
    client_id       STRING,
    step_name       STRING,
    status          STRING,
    started_at      TIMESTAMP,
    completed_at    TIMESTAMP,
    result_json     STRING,
    error_detail    STRING
)
USING DELTA
PARTITIONED BY (run_id)
"""

COST_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.cost_ledger (
    ledger_id           STRING,
    event_time          TIMESTAMP,
    run_id              STRING,
    client_id           STRING,
    agency_id           STRING,
    agent_name          STRING,
    model_id            STRING,
    input_tokens        BIGINT,
    output_tokens       BIGINT,
    cache_read_tokens   BIGINT,
    cache_write_tokens  BIGINT,
    cost_usd_estimate   DOUBLE,
    prompt_version      STRING,
    task_type           STRING
)
USING DELTA
PARTITIONED BY (agency_id)
"""

AGENT_MEMORY_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.agent_memory (
    memory_id       STRING,
    client_id       STRING,
    memory_type     STRING,
    content         STRING,
    source_run_id   STRING,
    created_at      TIMESTAMP,
    valid_until     TIMESTAMP,
    importance      STRING,
    tags            STRING
)
USING DELTA
PARTITIONED BY (client_id)
"""

SEMANTIC_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.semantic_cache (
    cache_key       STRING,
    created_at      TIMESTAMP,
    expires_at      TIMESTAMP,
    input_hash      STRING,
    agent_name      STRING,
    model_id        STRING,
    response_text   STRING,
    input_tokens    BIGINT,
    output_tokens   BIGINT,
    hit_count       INT
)
USING DELTA
"""

EVAL_GOLDEN_DATASET_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.eval_golden_dataset (
    sample_id       STRING,
    created_at      TIMESTAMP,
    agent_name      STRING,
    input_hash      STRING,
    input_summary   STRING,
    expected_output STRING,
    expected_fields STRING,
    tolerance_json  STRING,
    source          STRING,
    run_id          STRING,
    is_active       BOOLEAN
)
USING DELTA
"""

EVAL_RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.eval_results (
    eval_id         STRING,
    run_at          TIMESTAMP,
    agent_name      STRING,
    prompt_version  STRING,
    model_id        STRING,
    sample_id       STRING,
    passed          BOOLEAN,
    field_results   STRING,
    score           DOUBLE,
    regression      BOOLEAN,
    notes           STRING
)
USING DELTA
"""

AB_EXPERIMENTS_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.ab_experiments (
    experiment_id       STRING,
    name                STRING,
    description         STRING,
    created_at          TIMESTAMP,
    started_at          TIMESTAMP,
    ended_at            TIMESTAMP,
    status              STRING,
    variant_a_json      STRING,
    variant_b_json      STRING,
    traffic_split       DOUBLE,
    success_metric      STRING,
    winner              STRING
)
USING DELTA
"""

AB_ASSIGNMENTS_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.ab_assignments (
    assignment_id       STRING,
    experiment_id       STRING,
    run_id              STRING,
    client_id           STRING,
    variant             STRING,
    assigned_at         TIMESTAMP,
    outcome_json        STRING
)
USING DELTA
"""

CLIENT_REGISTRY_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.client_registry (
    client_id       STRING,
    config_json     STRING,
    is_active       BOOLEAN,
    updated_at      TIMESTAMP
)
USING DELTA
"""

AGENCY_REGISTRY_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.agency_registry (
    agency_id       STRING,
    agency_name     STRING,
    config_json     STRING,
    is_active       BOOLEAN,
    created_at      TIMESTAMP,
    updated_at      TIMESTAMP
)
USING DELTA
"""

TENANT_LIFECYCLE_OPERATIONS_DDL = """
CREATE TABLE IF NOT EXISTS {ops_schema}.tenant_lifecycle_operations (
    request_id          STRING,
    command             STRING,
    entity_type         STRING,
    entity_id           STRING,
    agency_id           STRING,
    schema_name         STRING,
    requested_by        STRING,
    requested_at        TIMESTAMP,
    started_at          TIMESTAMP,
    completed_at        TIMESTAMP,
    status              STRING,
    databricks_run_id   STRING,
    ddl_statement       STRING,
    error_message       STRING,
    result_json         STRING
)
USING DELTA
"""

_RAW_TABLES = [
    "meta_ads_raw",
    "hubspot_deals_raw",
    "stripe_payments_raw",
    "ad_spend_normalized",
    "attribution_results",
]
_HIGH_VOLUME_TABLES = ("ad_spend_normalized", "meta_ads_raw")
_OPS_TABLES = [
    "pipeline_runs",
    "audit_log",
    "insight_reports",
    "approval_queue",
    "telegram_events",
    "operator_alerts",
    "auth_users",
    "auth_sessions",
    "auth_events",
    "idempotency_store",
    "pipeline_checkpoints",
    "cost_ledger",
    "agent_memory",
    "semantic_cache",
    "eval_golden_dataset",
    "eval_results",
    "ab_experiments",
    "ab_assignments",
    "client_registry",
    "agency_registry",
    "tenant_lifecycle_operations",
]


def ensure_tables(schema: str) -> None:
    _run_sql(META_TABLE_DDL.format(schema=schema))
    _run_sql(HUBSPOT_TABLE_DDL.format(schema=schema))
    _run_sql(STRIPE_TABLE_DDL.format(schema=schema))
    _run_sql(NORMALIZED_AD_TABLE_DDL.format(schema=schema))
    _run_sql(ATTRIBUTION_RESULTS_TABLE_DDL.format(schema=schema))
    # contact_email was added in v2 — backfill the column on existing tables
    try:
        _run_sql(
            f"ALTER TABLE {schema}.hubspot_deals_raw ADD COLUMNS (contact_email STRING)"
        )
    except Exception:
        pass  # column already present
    set_table_retention_policies(schema)
    logger.info(f"[Databricks] Tables ready: {schema}")


def ensure_audit_log_table() -> None:
    _run_sql(AUDIT_LOG_TABLE_DDL.format(ops_schema=_OPS_SCHEMA))
    logger.debug(f"[Databricks] Audit log table ready: {_OPS_SCHEMA}.audit_log")


def ensure_insight_reports_table() -> None:
    _run_sql(INSIGHT_REPORTS_DDL.format(ops_schema=_OPS_SCHEMA))
    logger.debug(f"[Databricks] insight_reports ready: {_OPS_SCHEMA}.insight_reports")


def ensure_approval_queue_table() -> None:
    _run_sql(APPROVAL_QUEUE_DDL.format(ops_schema=_OPS_SCHEMA))
    logger.debug(f"[Databricks] approval_queue ready: {_OPS_SCHEMA}.approval_queue")


def ensure_telegram_events_table() -> None:
    _run_sql(f"CREATE SCHEMA IF NOT EXISTS {_OPS_SCHEMA}")
    _run_sql(TELEGRAM_EVENTS_DDL.format(ops_schema=_OPS_SCHEMA))
    logger.debug(f"[Databricks] telegram_events ready: {_OPS_SCHEMA}.telegram_events")


def ensure_operator_alerts_table() -> None:
    _run_sql(f"CREATE SCHEMA IF NOT EXISTS {_OPS_SCHEMA}")
    _run_sql(OPERATOR_ALERTS_DDL.format(ops_schema=_OPS_SCHEMA))
    logger.debug(f"[Databricks] operator_alerts ready: {_OPS_SCHEMA}.operator_alerts")


_auth_tables_ready = False


def ensure_auth_tables() -> None:
    global _auth_tables_ready
    if _auth_tables_ready:
        return
    _run_sql(f"CREATE SCHEMA IF NOT EXISTS {_OPS_SCHEMA}")
    _run_sql(AUTH_USERS_DDL.format(ops_schema=_OPS_SCHEMA))
    _run_sql(AUTH_SESSIONS_DDL.format(ops_schema=_OPS_SCHEMA))
    _run_sql(AUTH_EVENTS_DDL.format(ops_schema=_OPS_SCHEMA))
    _auth_tables_ready = True
    logger.debug(f"[Databricks] auth tables ready: {_OPS_SCHEMA}")


def ensure_phase3_tables() -> None:
    _run_sql(IDEMPOTENCY_STORE_DDL.format(ops_schema=_OPS_SCHEMA))
    _run_sql(PIPELINE_CHECKPOINTS_DDL.format(ops_schema=_OPS_SCHEMA))
    _run_sql(COST_LEDGER_DDL.format(ops_schema=_OPS_SCHEMA))
    logger.debug(f"[Databricks] Phase 3 tables ready: {_OPS_SCHEMA}")


def ensure_phase4_tables() -> None:
    _run_sql(AGENT_MEMORY_DDL.format(ops_schema=_OPS_SCHEMA))
    _run_sql(SEMANTIC_CACHE_DDL.format(ops_schema=_OPS_SCHEMA))
    logger.debug(f"[Databricks] Phase 4 tables ready: {_OPS_SCHEMA}")


def ensure_phase5_tables() -> None:
    _run_sql(EVAL_GOLDEN_DATASET_DDL.format(ops_schema=_OPS_SCHEMA))
    _run_sql(EVAL_RESULTS_DDL.format(ops_schema=_OPS_SCHEMA))
    logger.debug(f"[Databricks] Phase 5 tables ready: {_OPS_SCHEMA}")


def ensure_phase6_tables() -> None:
    _run_sql(AB_EXPERIMENTS_DDL.format(ops_schema=_OPS_SCHEMA))
    _run_sql(AB_ASSIGNMENTS_DDL.format(ops_schema=_OPS_SCHEMA))
    logger.debug(f"[Databricks] Phase 6 tables ready: {_OPS_SCHEMA}")


def set_table_retention_policies(schema: str) -> None:
    """Apply Delta log-retention TBLPROPERTIES to raw and ops tables."""
    for table in _RAW_TABLES:
        try:
            _run_sql(
                f"ALTER TABLE {schema}.{table} "
                f"SET TBLPROPERTIES ('delta.logRetentionDuration' = 'interval 90 days')"
            )
        except Exception as exc:
            logger.debug(
                f"[Databricks] Could not set retention on {schema}.{table}: {exc}"
            )
    for table in _OPS_TABLES:
        try:
            _run_sql(
                f"ALTER TABLE {_OPS_SCHEMA}.{table} "
                f"SET TBLPROPERTIES ('delta.logRetentionDuration' = 'interval 365 days')"
            )
        except Exception as exc:
            logger.debug(
                f"[Databricks] Could not set retention on {_OPS_SCHEMA}.{table}: {exc}"
            )


def _validated_schema(schema: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", schema):
        raise ValueError(f"Unsafe Databricks schema identifier: {schema!r}")
    return schema


def apply_liquid_clustering_v2(schema: str) -> list[str]:
    """Migrate existing date-partitioned high-volume tables to liquid clustering.

    ``REPLACE PARTITIONED BY WITH CLUSTER BY`` requires Databricks Runtime 18.1+
    and is intentionally separate from normal table bootstrap.
    """
    safe_schema = _validated_schema(schema)
    executed = []
    for table in _HIGH_VOLUME_TABLES:
        statements = (
            f"ALTER TABLE {safe_schema}.{table} SET TBLPROPERTIES "
            "('delta.enableDeletionVectors' = 'true')",
            f"ALTER TABLE {safe_schema}.{table} REPLACE PARTITIONED BY "
            "WITH CLUSTER BY (date, campaign_id)",
        )
        for statement in statements:
            _run_sql(statement)
            executed.append(statement)
    return executed


def run_delta_maintenance(
    schema: str,
    *,
    tables: tuple[str, ...] = _HIGH_VOLUME_TABLES,
    vacuum_hours: int = 168,
) -> list[str]:
    """Compact and vacuum allow-listed high-volume Delta tables."""
    safe_schema = _validated_schema(schema)
    if vacuum_hours < 168:
        raise ValueError("VACUUM retention must be at least 168 hours")
    unknown_tables = set(tables) - set(_HIGH_VOLUME_TABLES)
    if unknown_tables:
        raise ValueError(f"Unsupported maintenance tables: {sorted(unknown_tables)}")

    executed = []
    for table in tables:
        for statement in (
            f"OPTIMIZE {safe_schema}.{table}",
            f"VACUUM {safe_schema}.{table} RETAIN {vacuum_hours} HOURS",
        ):
            _run_sql(statement)
            executed.append(statement)
    return executed


def ensure_ops_tables() -> None:
    _run_sql(f"CREATE SCHEMA IF NOT EXISTS {_OPS_SCHEMA}")
    _run_sql(RUN_HISTORY_TABLE_DDL.format(ops_schema=_OPS_SCHEMA))
    try:
        _run_sql(
            f"ALTER TABLE {_OPS_SCHEMA}.pipeline_runs ADD COLUMNS (tiktok_rows BIGINT)"
        )
    except Exception:
        pass  # column already present
    _run_sql(TELEGRAM_EVENTS_DDL.format(ops_schema=_OPS_SCHEMA))
    _run_sql(OPERATOR_ALERTS_DDL.format(ops_schema=_OPS_SCHEMA))
    _run_sql(AUTH_USERS_DDL.format(ops_schema=_OPS_SCHEMA))
    _run_sql(AUTH_SESSIONS_DDL.format(ops_schema=_OPS_SCHEMA))
    _run_sql(AUTH_EVENTS_DDL.format(ops_schema=_OPS_SCHEMA))
    _run_sql(CLIENT_REGISTRY_TABLE_DDL.format(ops_schema=_OPS_SCHEMA))
    _run_sql(AGENCY_REGISTRY_TABLE_DDL.format(ops_schema=_OPS_SCHEMA))
    _run_sql(TENANT_LIFECYCLE_OPERATIONS_DDL.format(ops_schema=_OPS_SCHEMA))
    logger.info(f"[Databricks] Ops tables ready: {_OPS_SCHEMA}")


_client_registry_table_ready = False


def ensure_client_registry_table() -> None:
    global _client_registry_table_ready
    if _client_registry_table_ready:
        return
    _run_sql(f"CREATE SCHEMA IF NOT EXISTS {_OPS_SCHEMA}")
    _run_sql(CLIENT_REGISTRY_TABLE_DDL.format(ops_schema=_OPS_SCHEMA))
    _client_registry_table_ready = True
    logger.debug(f"[Databricks] client_registry ready: {_OPS_SCHEMA}.client_registry")


def fetch_client_registry_rows() -> list[dict]:
    """Return active client registry rows as [{client_id, config_json}, ...]."""
    ensure_client_registry_table()
    query = (
        f"SELECT client_id, config_json FROM {_OPS_SCHEMA}.client_registry "
        "WHERE is_active = TRUE"
    )
    if _is_databricks():
        return [row.asDict() for row in _get_spark().sql(query).collect()]
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(query)
    columns = [desc[0] for desc in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return rows


def upsert_client_registry_entry(
    client_id: str, config_json: str, is_active: bool = True
) -> None:
    """Insert or update one client registry row (is_active=False soft-deletes)."""
    ensure_client_registry_table()
    df = pd.DataFrame(
        [
            {
                "client_id": client_id,
                "config_json": config_json,
                "is_active": bool(is_active),
                "updated_at": pd.Timestamp.utcnow(),
            }
        ]
    )
    _upsert_dataframe(df, _OPS_SCHEMA, "client_registry", ["client_id"])
    logger.info(
        f"[Databricks] client_registry upsert: {client_id} (active={is_active})"
    )


_agency_registry_table_ready = False


def ensure_agency_registry_table() -> None:
    global _agency_registry_table_ready
    if _agency_registry_table_ready:
        return
    _run_sql(f"CREATE SCHEMA IF NOT EXISTS {_OPS_SCHEMA}")
    _run_sql(AGENCY_REGISTRY_TABLE_DDL.format(ops_schema=_OPS_SCHEMA))
    _agency_registry_table_ready = True
    logger.debug(f"[Databricks] agency_registry ready: {_OPS_SCHEMA}.agency_registry")


def fetch_agency_registry_rows() -> list[dict]:
    """Return active agency registry rows."""
    ensure_agency_registry_table()
    return _fetch_rows(
        "SELECT agency_id, agency_name, config_json "
        f"FROM {_OPS_SCHEMA}.agency_registry WHERE is_active = TRUE"
    )


def upsert_agency_registry_entry(
    agency_id: str,
    agency_name: str,
    config_json: str,
    is_active: bool = True,
) -> None:
    """Insert or update one agency registry row (is_active=False soft-deletes)."""
    ensure_agency_registry_table()
    now = pd.Timestamp.utcnow()
    df = pd.DataFrame(
        [
            {
                "agency_id": agency_id,
                "agency_name": agency_name,
                "config_json": config_json,
                "is_active": bool(is_active),
                "created_at": now,
                "updated_at": now,
            }
        ]
    )
    _upsert_dataframe(df, _OPS_SCHEMA, "agency_registry", ["agency_id"])
    logger.info(
        f"[Databricks] agency_registry upsert: {agency_id} (active={is_active})"
    )


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _fetch_rows(query: str) -> list[dict]:
    if _is_databricks():
        return [row.asDict() for row in _get_spark().sql(query).collect()]
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(query)
    columns = [desc[0] for desc in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return rows


def count_auth_users() -> int:
    ensure_auth_tables()
    rows = _fetch_rows(f"SELECT COUNT(*) AS n FROM {_OPS_SCHEMA}.auth_users")
    return int(rows[0].get("n") or 0) if rows else 0


def fetch_auth_user_by_email(email: str) -> dict | None:
    ensure_auth_tables()
    normalized = str(email or "").strip().lower()
    if not normalized:
        return None
    rows = _fetch_rows(
        "SELECT user_id, email, display_name, role, agency_id, client_ids, "
        "password_hash, is_active, failed_login_count, locked_until, "
        "last_login_at, created_at, updated_at "
        f"FROM {_OPS_SCHEMA}.auth_users "
        f"WHERE lower(email) = {_sql_literal(normalized)} "
        "ORDER BY updated_at DESC LIMIT 1"
    )
    return rows[0] if rows else None


def upsert_auth_user(record: dict) -> None:
    ensure_auth_tables()
    now = datetime.now(timezone.utc)
    row = {
        "user_id": str(record.get("user_id", "") or ""),
        "email": str(record.get("email", "") or "").strip().lower(),
        "display_name": str(record.get("display_name", "") or ""),
        "role": str(record.get("role", "") or ""),
        "agency_id": str(record.get("agency_id", "") or ""),
        "client_ids": str(record.get("client_ids", "") or ""),
        "password_hash": str(record.get("password_hash", "") or ""),
        "is_active": bool(record.get("is_active", True)),
        "failed_login_count": int(record.get("failed_login_count", 0) or 0),
        "locked_until": record.get("locked_until"),
        "last_login_at": record.get("last_login_at"),
        "created_at": record.get("created_at") or now,
        "updated_at": record.get("updated_at") or now,
    }
    df = pd.DataFrame([row])
    for col in ("locked_until", "last_login_at", "created_at", "updated_at"):
        df[col] = df[col].astype(object).where(df[col].notna(), None)
    _upsert_dataframe(df, _OPS_SCHEMA, "auth_users", ["user_id"])


def write_auth_session(record: dict) -> None:
    ensure_auth_tables()
    now = datetime.now(timezone.utc)
    row = {
        "session_id": str(record.get("session_id", "") or ""),
        "user_id": str(record.get("user_id", "") or ""),
        "email": str(record.get("email", "") or "").strip().lower(),
        "role": str(record.get("role", "") or ""),
        "agency_id": str(record.get("agency_id", "") or ""),
        "created_at": record.get("created_at") or now,
        "expires_at": record.get("expires_at"),
        "revoked_at": record.get("revoked_at"),
        "user_agent": str(record.get("user_agent", "") or ""),
    }
    df = pd.DataFrame([row])
    for col in ("created_at", "expires_at", "revoked_at"):
        df[col] = df[col].astype(object).where(df[col].notna(), None)
    _upsert_dataframe(df, _OPS_SCHEMA, "auth_sessions", ["session_id"])


def fetch_auth_session(session_id: str) -> dict | None:
    ensure_auth_tables()
    if not session_id:
        return None
    rows = _fetch_rows(
        "SELECT session_id, user_id, email, role, agency_id, created_at, "
        "expires_at, revoked_at, user_agent "
        f"FROM {_OPS_SCHEMA}.auth_sessions "
        f"WHERE session_id = {_sql_literal(session_id)} "
        "AND revoked_at IS NULL "
        "AND expires_at > current_timestamp() "
        "LIMIT 1"
    )
    return rows[0] if rows else None


def revoke_auth_session(session_id: str) -> None:
    if not session_id:
        return
    session = fetch_auth_session(session_id)
    if not session:
        return
    session["revoked_at"] = datetime.now(timezone.utc)
    write_auth_session(session)


def write_auth_event(record: dict) -> None:
    ensure_auth_tables()
    row = {
        "event_id": str(record.get("event_id", "") or ""),
        "event_time": record.get("event_time") or datetime.now(timezone.utc),
        "event_type": str(record.get("event_type", "") or ""),
        "email": str(record.get("email", "") or "").strip().lower(),
        "user_id": str(record.get("user_id", "") or ""),
        "role": str(record.get("role", "") or ""),
        "agency_id": str(record.get("agency_id", "") or ""),
        "outcome": str(record.get("outcome", "") or ""),
        "detail_json": str(record.get("detail_json", "") or "{}"),
        "session_id": str(record.get("session_id", "") or ""),
    }
    if not row["event_id"]:
        import uuid

        row["event_id"] = uuid.uuid4().hex
    df = pd.DataFrame([row])
    df["event_time"] = (
        df["event_time"].astype(object).where(df["event_time"].notna(), None)
    )
    _upsert_dataframe(df, _OPS_SCHEMA, "auth_events", ["event_id"])


def _sql_param(v):
    """Coerce a DataFrame scalar to a type the SQL connector can bind."""
    if v is None or (pd.api.types.is_scalar(v) and pd.isna(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if isinstance(v, np.generic):
        return v.item()
    return v


def _is_delta_concurrency_error(exc: Exception) -> bool:
    message = str(exc)
    return any(
        marker in message
        for marker in (
            "DELTA_CONCURRENT_APPEND",
            "DELTA_CONCURRENT_DELETE",
            "DELTA_CONCURRENT_UPDATE",
            "ConcurrentAppendException",
            "ConcurrentDeleteReadException",
            "ConcurrentTransactionException",
            "Transaction conflict detected",
        )
    )


def _upsert_dataframe_via_sql_connector(
    df: pd.DataFrame,
    full_table: str,
    schema: str,
    table: str,
    merge_condition: str,
    update_set: str,
    insert_cols: str,
    insert_vals: str,
) -> None:
    staging_table = f"{schema}.{table}_staging_{uuid.uuid4().hex}"
    conn = _get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"CREATE TABLE {staging_table} USING DELTA AS "
            f"SELECT * FROM {full_table} WHERE 1=0"
        )
        columns = list(df.columns)
        col_str = ", ".join(columns)
        ph = ", ".join(["?" for _ in columns])
        # The SQL connector can't infer pandas/numpy scalar types as parameters
        # (e.g. pd.Timestamp -> "Could not infer parameter type"); bind natives.
        row_iter = (
            tuple(_sql_param(value) for value in row)
            for row in df.itertuples(index=False, name=None)
        )
        while batch := list(islice(row_iter, 1000)):
            cursor.executemany(
                f"INSERT INTO {staging_table} ({col_str}) VALUES ({ph})",
                batch,
            )
        merge_sql = f"""
            MERGE INTO {full_table} AS t
            USING {staging_table} AS s
            ON {merge_condition}
            WHEN MATCHED THEN UPDATE SET {update_set}
            WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})
        """
        cursor.execute(merge_sql)
    finally:
        try:
            cursor.execute(f"DROP TABLE IF EXISTS {staging_table}")
        finally:
            cursor.close()
            conn.close()


def _upsert_dataframe(
    df: pd.DataFrame,
    schema: str,
    table: str,
    merge_keys: list[str],
    target_predicate: str | None = None,
) -> int:
    full_table = f"{schema}.{table}"
    non_key_cols = [c for c in df.columns if c not in merge_keys]
    merge_condition = " AND ".join([f"t.{k} = s.{k}" for k in merge_keys])
    if target_predicate:
        merge_condition = f"({merge_condition}) AND ({target_predicate})"
    update_set = ", ".join([f"t.{c} = s.{c}" for c in non_key_cols])
    insert_cols = ", ".join(df.columns)
    insert_vals = ", ".join([f"s.{c}" for c in df.columns])
    staging_view = f"_staging_{table}"

    if _is_databricks():
        spark = _get_spark()
        spark_df = spark.createDataFrame(df)
        spark_df.createOrReplaceTempView(staging_view)
        merge_sql = f"""
            MERGE INTO {full_table} AS t
            USING {staging_view} AS s
            ON {merge_condition}
            WHEN MATCHED THEN UPDATE SET {update_set}
            WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})
        """
        spark.sql(merge_sql)
        spark.catalog.dropTempView(staging_view)
    else:
        max_attempts = int(
            os.environ.get("ATTRIBUTION_DATABRICKS_UPSERT_RETRIES", "3") or "3"
        )
        for attempt in range(1, max_attempts + 1):
            try:
                _upsert_dataframe_via_sql_connector(
                    df,
                    full_table,
                    schema,
                    table,
                    merge_condition,
                    update_set,
                    insert_cols,
                    insert_vals,
                )
                break
            except Exception as exc:
                if not _is_delta_concurrency_error(exc) or attempt >= max_attempts:
                    raise
                sleep_seconds = min(0.4 * (2 ** (attempt - 1)), 3.0)
                logger.warning(
                    "[Databricks] Delta concurrency conflict while upserting %s "
                    "(attempt %s/%s); retrying in %.1fs",
                    full_table,
                    attempt,
                    max_attempts,
                    sleep_seconds,
                )
                time.sleep(sleep_seconds)

    return len(df)


def _date_target_predicate(df: pd.DataFrame, column: str) -> str | None:
    """Build a target-side date bound for partition-pruned Delta MERGEs."""
    values = pd.to_datetime(df[column], errors="coerce").dropna()
    if values.empty:
        return None
    start = values.min().date().isoformat()
    end = values.max().date().isoformat()
    return f"t.{column} BETWEEN DATE '{start}' AND DATE '{end}'"


def write_meta_data(df: pd.DataFrame, schema: str) -> int:
    if df.empty:
        logger.warning("[Databricks] Meta DataFrame empty — skipping")
        return 0
    df = df.copy()
    df["ingested_at"] = pd.Timestamp.utcnow()
    df["date"] = df["date"].dt.date
    for col in [
        "conversions_lead",
        "conversions_offsite_conversion_fb_pixel_lead",
        "conversions_offsite_conversion_fb_pixel_purchase",
        "conversions_offsite_conversion_fb_pixel_complete_registration",
    ]:
        if col not in df.columns:
            df[col] = 0
    rows = _upsert_dataframe(
        df,
        schema,
        "meta_ads_raw",
        ["ad_account_id", "campaign_id", "adset_id", "date"],
        target_predicate=_date_target_predicate(df, "date"),
    )
    logger.info(f"[Databricks] Wrote {rows} Meta rows")
    return rows


def write_hubspot_data(df: pd.DataFrame, schema: str) -> int:
    if df.empty:
        logger.warning("[Databricks] HubSpot DataFrame empty — skipping")
        return 0
    df = df.copy()
    df["ingested_at"] = pd.Timestamp.utcnow()

    # Convert NaT to None so Databricks SQL connector handles nulls correctly
    timestamp_cols = ["close_date", "create_date", "lead_create_date", "ingested_at"]
    for col in timestamp_cols:
        if col in df.columns:
            df[col] = df[col].astype(object).where(df[col].notna(), None)

    # Convert days_to_deal float to int, replacing NaN with None
    if "days_to_deal" in df.columns:
        df["days_to_deal"] = df["days_to_deal"].apply(
            lambda x: int(x) if x is not None and str(x) != "nan" else None
        )

    rows = _upsert_dataframe(df, schema, "hubspot_deals_raw", ["deal_id"])
    logger.info(f"[Databricks] Wrote {rows} HubSpot rows")
    return rows


def write_stripe_data(df: pd.DataFrame, schema: str) -> int:
    if df.empty:
        logger.warning("[Databricks] Stripe DataFrame empty — skipping")
        return 0
    df = df.copy()
    df["ingested_at"] = pd.Timestamp.utcnow()

    # Convert NaT to None so Databricks SQL connector handles nulls correctly
    timestamp_cols = ["created_at", "ingested_at"]
    for col in timestamp_cols:
        if col in df.columns:
            df[col] = df[col].astype(object).where(df[col].notna(), None)

    # Ensure refunded is a proper bool, not NaN
    if "refunded" in df.columns:
        df["refunded"] = df["refunded"].fillna(False).astype(bool)

    rows = _upsert_dataframe(df, schema, "stripe_payments_raw", ["payment_id"])
    logger.info(f"[Databricks] Wrote {rows} Stripe rows")
    return rows


def write_normalized_ad_data(df: pd.DataFrame, schema: str) -> int:
    if df.empty:
        logger.warning("[Databricks] Normalized ad DataFrame empty — skipping")
        return 0
    df = df.copy()
    df["ingested_at"] = pd.Timestamp.utcnow()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    rows = _upsert_dataframe(
        df,
        schema,
        "ad_spend_normalized",
        ["client_id", "source_platform", "campaign_id", "ad_group_id", "ad_id", "date"],
        target_predicate=_date_target_predicate(df, "date"),
    )
    logger.info(f"[Databricks] Wrote {rows} normalized ad rows")
    return rows


def write_attribution_results(df: pd.DataFrame, schema: str) -> int:
    """Overwrite the per-client closed-loop attribution scorecard for a model.

    Results are a full recompute over the lookback window, so the existing rows
    for the same (client_id, source_platform, attribution_model) are replaced.
    """
    if df is None or df.empty:
        logger.warning("[Databricks] Attribution results empty — skipping")
        return 0
    df = df.copy()
    df["computed_at"] = pd.Timestamp.utcnow()
    for col in ("window_start", "window_end"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
            df[col] = df[col].astype(object).where(df[col].notna(), None)
    rows = _upsert_dataframe(
        df,
        schema,
        "attribution_results",
        ["client_id", "source_platform", "attribution_model"],
    )
    logger.info(f"[Databricks] Wrote {rows} attribution result rows")
    return rows


def _write_record_batches(
    batches: Iterable[pa.RecordBatch],
    schema: str,
    writer: Callable[[pd.DataFrame, str], int],
) -> int:
    total_rows = 0
    for batch in batches:
        if batch.num_rows:
            total_rows += writer(batch.to_pandas(), schema)
    return total_rows


def write_meta_batches(batches: Iterable[pa.RecordBatch], schema: str) -> int:
    return _write_record_batches(batches, schema, write_meta_data)


def write_hubspot_batches(batches: Iterable[pa.RecordBatch], schema: str) -> int:
    return _write_record_batches(batches, schema, write_hubspot_data)


def write_stripe_batches(batches: Iterable[pa.RecordBatch], schema: str) -> int:
    return _write_record_batches(batches, schema, write_stripe_data)


def write_normalized_ad_batches(batches: Iterable[pa.RecordBatch], schema: str) -> int:
    return _write_record_batches(batches, schema, write_normalized_ad_data)


def write_telegram_event(record: dict) -> None:
    """Persist one inbound Telegram message/callback event for operator audit."""
    ensure_telegram_events_table()
    now = datetime.now(timezone.utc)

    def _int_or_none(value):
        try:
            return int(value) if value not in ("", None) else None
        except (TypeError, ValueError):
            return None

    row = {
        "event_id": record.get("event_id", ""),
        "event_time": record.get("event_time") or now,
        "update_id": _int_or_none(record.get("update_id")),
        "message_id": _int_or_none(record.get("message_id")),
        "chat_id": str(record.get("chat_id", "") or ""),
        "user_id": str(record.get("user_id", "") or ""),
        "username": str(record.get("username", "") or ""),
        "event_type": str(record.get("event_type", "") or ""),
        "raw_text": str(record.get("raw_text", "") or ""),
        "voice_file_id": str(record.get("voice_file_id", "") or ""),
        "parsed_intent": str(record.get("parsed_intent", "") or ""),
        "params_json": str(record.get("params_json", "") or "{}"),
        "action_id": str(record.get("action_id", "") or ""),
        "response_summary": str(record.get("response_summary", "") or ""),
        "status": str(record.get("status", "") or ""),
        "error": str(record.get("error", "") or ""),
    }
    if not row["event_id"]:
        import uuid

        row["event_id"] = uuid.uuid4().hex
    df = pd.DataFrame([row])
    df["event_time"] = (
        df["event_time"].astype(object).where(df["event_time"].notna(), None)
    )
    _upsert_dataframe(df, _OPS_SCHEMA, "telegram_events", ["event_id"])


def fetch_recent_telegram_events(limit: int = 25) -> list[dict]:
    try:
        ensure_telegram_events_table()
        query = (
            "SELECT event_id, event_time, update_id, message_id, chat_id, user_id, "
            "username, event_type, raw_text, voice_file_id, parsed_intent, "
            "params_json, action_id, response_summary, status, error "
            f"FROM {_OPS_SCHEMA}.telegram_events "
            f"ORDER BY event_time DESC LIMIT {int(limit)}"
        )
        if _is_databricks():
            return [row.asDict() for row in _get_spark().sql(query).collect()]
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(query)
        columns = [desc[0] for desc in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return rows
    except Exception as exc:
        logger.warning(f"[Databricks] Could not fetch Telegram events: {exc}")
        return []


def write_operator_alert(record: dict) -> None:
    """Persist one operator alert for Command Center history."""
    ensure_operator_alerts_table()
    now = datetime.now(timezone.utc)
    row = {
        "alert_id": str(record.get("alert_id", "") or ""),
        "event_time": record.get("event_time") or now,
        "severity": str(record.get("severity", "") or ""),
        "category": str(record.get("category", "") or ""),
        "title": str(record.get("title", "") or ""),
        "message": str(record.get("message", "") or ""),
        "client_id": str(record.get("client_id", "") or ""),
        "agency_id": str(record.get("agency_id", "") or ""),
        "source": str(record.get("source", "") or ""),
        "run_id": str(record.get("run_id", "") or ""),
        "action_required": str(record.get("action_required", "") or ""),
        "metadata_json": str(record.get("metadata_json", "") or "{}"),
        "status": str(record.get("status", "open") or "open"),
    }
    if not row["alert_id"]:
        import uuid

        row["alert_id"] = uuid.uuid4().hex
    df = pd.DataFrame([row])
    df["event_time"] = (
        df["event_time"].astype(object).where(df["event_time"].notna(), None)
    )
    _upsert_dataframe(df, _OPS_SCHEMA, "operator_alerts", ["alert_id"])


def fetch_recent_operator_alerts(
    limit: int = 25,
    open_only: bool = False,
) -> list[dict]:
    try:
        ensure_operator_alerts_table()
        where = "WHERE status = 'open'" if open_only else ""
        query = (
            "SELECT alert_id, event_time, severity, category, title, message, "
            "client_id, agency_id, source, run_id, action_required, "
            f"metadata_json, status FROM {_OPS_SCHEMA}.operator_alerts "
            f"{where} ORDER BY event_time DESC LIMIT {int(limit)}"
        )
        if _is_databricks():
            return [row.asDict() for row in _get_spark().sql(query).collect()]
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(query)
        columns = [desc[0] for desc in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return rows
    except Exception as exc:
        logger.warning(f"[Databricks] Could not fetch operator alerts: {exc}")
        return []


def write_pipeline_run(record: dict) -> None:
    ensure_ops_tables()
    now = datetime.now(timezone.utc)
    row = {
        "run_id": record.get("run_id", ""),
        "agency_id": record.get("agency_id", ""),
        "client_id": record.get("client_id", ""),
        "run_mode": record.get("run_mode", ""),
        "attribution_model": record.get("attribution_model", ""),
        "status": record.get("status", ""),
        "dry_run": bool(record.get("dry_run", False)),
        "meta_rows": int(record.get("meta_rows", 0) or 0),
        "google_rows": int(record.get("google_rows", 0) or 0),
        "linkedin_rows": int(record.get("linkedin_rows", 0) or 0),
        "tiktok_rows": int(record.get("tiktok_rows", 0) or 0),
        "hubspot_rows": int(record.get("hubspot_rows", 0) or 0),
        "stripe_rows": int(record.get("stripe_rows", 0) or 0),
        "normalized_ad_rows": int(record.get("normalized_ad_rows", 0) or 0),
        "total_pipeline": float(record.get("total_pipeline", 0.0) or 0.0),
        "top_channel": record.get("top_channel", ""),
        "email_sent": bool(record.get("email_sent", False)),
        "warnings": record.get("warnings", ""),
        "error": record.get("error", ""),
        "started_at": record.get("started_at") or now,
        "finished_at": record.get("finished_at") or now,
        "output_schema": record.get("output_schema", ""),
    }
    df = pd.DataFrame([row])
    for col in ("started_at", "finished_at"):
        df[col] = df[col].astype(object).where(df[col].notna(), None)
    _upsert_dataframe(df, _OPS_SCHEMA, "pipeline_runs", ["run_id", "client_id"])


def fetch_recent_pipeline_runs(limit: int = 20) -> list[dict]:
    try:
        ensure_ops_tables()
        query = (
            "SELECT run_id, agency_id, client_id, run_mode, attribution_model, status, "
            "dry_run, meta_rows, google_rows, linkedin_rows, tiktok_rows, hubspot_rows, stripe_rows, "
            "normalized_ad_rows, total_pipeline, top_channel, email_sent, warnings, error, "
            f"started_at, finished_at, output_schema "
            f"FROM {_OPS_SCHEMA}.pipeline_runs "
            f"ORDER BY started_at DESC LIMIT {int(limit)}"
        )
        if _is_databricks():
            spark_df = _get_spark().sql(query)
            return [row.asDict() for row in spark_df.collect()]
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(query)
        columns = [desc[0] for desc in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        cursor.close()
        conn.close()
        return rows
    except Exception as exc:
        logger.warning(f"[Databricks] Could not fetch recent pipeline runs: {exc}")
        return []
