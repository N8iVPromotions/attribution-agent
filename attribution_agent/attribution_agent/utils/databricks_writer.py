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
_OPS_SCHEMA = os.environ.get("ATTRIBUTION_OPS_SCHEMA", "main.attribution_ops")


def _is_databricks() -> bool:
    try:
        from pyspark.sql import SparkSession
        spark = SparkSession.getActiveSession()
        return spark is not None
    except Exception:
        return False


def _get_spark():
    from pyspark.sql import SparkSession
    return SparkSession.getActiveSession()


def _get_connection():
    from databricks import sql
    # DATABRICKS_HOST is auto-injected by Databricks Apps; strip the scheme if present
    hostname = os.environ.get("DATABRICKS_SERVER_HOSTNAME") or \
        os.environ.get("DATABRICKS_HOST", "").lstrip("https://").rstrip("/")
    if not hostname:
        raise EnvironmentError("DATABRICKS_SERVER_HOSTNAME (or DATABRICKS_HOST) is not set")
    return sql.connect(
        server_hostname=hostname,
        http_path=os.environ["DATABRICKS_HTTP_PATH"],
        access_token=os.environ["DATABRICKS_TOKEN"],
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


def ensure_tables(schema: str) -> None:
    _run_sql(META_TABLE_DDL.format(schema=schema))
    _run_sql(HUBSPOT_TABLE_DDL.format(schema=schema))
    _run_sql(STRIPE_TABLE_DDL.format(schema=schema))
    _run_sql(NORMALIZED_AD_TABLE_DDL.format(schema=schema))
    # contact_email was added in v2 — backfill the column on existing tables
    try:
        _run_sql(
            f"ALTER TABLE {schema}.hubspot_deals_raw "
            f"ADD COLUMNS (contact_email STRING)"
        )
    except Exception:
        pass  # column already present
    logger.info(f"[Databricks] Tables ready: {schema}")


def ensure_ops_tables() -> None:
    _run_sql(f"CREATE SCHEMA IF NOT EXISTS {_OPS_SCHEMA}")
    _run_sql(RUN_HISTORY_TABLE_DDL.format(ops_schema=_OPS_SCHEMA))
    logger.info(f"[Databricks] Ops tables ready: {_OPS_SCHEMA}")


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
                rows[i:i+1000]
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
    rows = _upsert_dataframe(df, schema, "meta_ads_raw",
                             ["ad_account_id", "campaign_id", "adset_id", "date"])
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
