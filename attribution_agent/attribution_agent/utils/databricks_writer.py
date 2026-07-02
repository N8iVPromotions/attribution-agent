"""
utils/databricks_writer.py
Writes validated pandas DataFrames to Databricks Delta tables.
Runs via SQL Connector when local, Spark when inside Databricks.
"""

from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
import pandas as pd

logger = logging.getLogger(__name__)

# Ops schema for pipeline run history — overridable via env var.
# Default: main.attribution_ops (main catalog is writable in all workspaces).
# Override: ATTRIBUTION_OPS_SCHEMA=hive_metastore.attribution_ops
_OPS_SCHEMA = os.environ.get("ATTRIBUTION_OPS_SCHEMA", "workspace.attribution_ops")


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
    host = os.environ.get("DATABRICKS_SERVER_HOSTNAME") or os.environ.get(
        "DATABRICKS_HOST", ""
    )
    hostname = host.replace("https://", "").replace("http://", "").rstrip("/")
    if not hostname:
        raise EnvironmentError(
            "DATABRICKS_SERVER_HOSTNAME (or DATABRICKS_HOST) is not set"
        )
    http_path = os.environ.get("DATABRICKS_HTTP_PATH")
    if not http_path:
        raise EnvironmentError("DATABRICKS_HTTP_PATH is not set")

    # Local dev authenticates with a PAT; the Databricks App has no token and
    # instead uses the OAuth (M2M) service-principal credentials it auto-injects
    # as DATABRICKS_CLIENT_ID / DATABRICKS_CLIENT_SECRET (picked up by Config()).
    access_token = os.environ.get("DATABRICKS_TOKEN")
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
PARTITIONED BY (date)
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
PARTITIONED BY (date)
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

_RAW_TABLES = [
    "meta_ads_raw",
    "hubspot_deals_raw",
    "stripe_payments_raw",
    "ad_spend_normalized",
    "attribution_results",
]
_OPS_TABLES = [
    "pipeline_runs",
    "audit_log",
    "insight_reports",
    "approval_queue",
    "idempotency_store",
    "pipeline_checkpoints",
    "cost_ledger",
    "agent_memory",
    "semantic_cache",
    "eval_golden_dataset",
    "eval_results",
    "ab_experiments",
    "ab_assignments",
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


def ensure_ops_tables() -> None:
    _run_sql(f"CREATE SCHEMA IF NOT EXISTS {_OPS_SCHEMA}")
    _run_sql(RUN_HISTORY_TABLE_DDL.format(ops_schema=_OPS_SCHEMA))
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


def _upsert_dataframe(
    df: pd.DataFrame,
    schema: str,
    table: str,
    merge_keys: list[str],
) -> int:
    full_table = f"{schema}.{table}"
    non_key_cols = [c for c in df.columns if c not in merge_keys]
    merge_condition = " AND ".join([f"t.{k} = s.{k}" for k in merge_keys])
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
        staging_table = f"{schema}.{table}_staging"
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"CREATE OR REPLACE TABLE {staging_table} USING DELTA AS "
            f"SELECT * FROM {full_table} WHERE 1=0"
        )
        columns = list(df.columns)
        col_str = ", ".join(columns)
        ph = ", ".join(["?" for _ in columns])
        rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
        for i in range(0, len(rows), 1000):
            cursor.executemany(
                f"INSERT INTO {staging_table} ({col_str}) VALUES ({ph})",
                rows[i : i + 1000],
            )
        merge_sql = f"""
            MERGE INTO {full_table} AS t
            USING {staging_table} AS s
            ON {merge_condition}
            WHEN MATCHED THEN UPDATE SET {update_set}
            WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})
        """
        cursor.execute(merge_sql)
        cursor.execute(f"DROP TABLE IF EXISTS {staging_table}")
        cursor.close()
        conn.close()

    return len(df)


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
        df, schema, "meta_ads_raw", ["ad_account_id", "campaign_id", "adset_id", "date"]
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
            "dry_run, meta_rows, google_rows, linkedin_rows, hubspot_rows, stripe_rows, "
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
