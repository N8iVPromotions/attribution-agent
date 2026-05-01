"""
utils/databricks_writer.py
Writes validated pandas DataFrames to Databricks Delta tables.
Runs via SQL Connector when local, Spark when inside Databricks.
"""
from __future__ import annotations
import logging
import os
import pandas as pd

logger = logging.getLogger(__name__)


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
    return sql.connect(
        server_hostname=os.environ["DATABRICKS_SERVER_HOSTNAME"],
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


def ensure_tables(schema: str) -> None:
    _run_sql(META_TABLE_DDL.format(schema=schema))
    _run_sql(HUBSPOT_TABLE_DDL.format(schema=schema))
    _run_sql(STRIPE_TABLE_DDL.format(schema=schema))
    # contact_email was added in v2 — backfill the column on existing tables
    try:
        _run_sql(
            f"ALTER TABLE {schema}.hubspot_deals_raw "
            f"ADD COLUMNS (contact_email STRING)"
        )
    except Exception:
        pass  # column already present
    logger.info(f"[Databricks] Tables ready: {schema}")


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