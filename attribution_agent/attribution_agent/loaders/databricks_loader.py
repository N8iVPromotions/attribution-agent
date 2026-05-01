"""
loaders/databricks_loader.py — Writes validated DataFrames to Databricks Delta.

Strategy:
  - MERGE (upsert) rather than overwrite — safe for incremental loads
  - Bronze layer only: raw data, minimal transformation
  - One table per source per client: {catalog}.{schema}.{client_id}_{source}
  - Idempotent: running twice won't duplicate rows

Tables created:
  {catalog}.bronze.meta_ad_insights
  {catalog}.bronze.hubspot_contacts
  {catalog}.bronze.hubspot_deals

All tables are multi-tenant (all clients in the same table, partitioned by client_id).
"""

import pandas as pd
from loguru import logger
from databricks import sql as databricks_sql
from databricks.sql.client import Connection

from config import DatabricksConfig


class DatabricksLoader:
    """
    Loads DataFrames into Databricks Delta tables via the SQL connector.
    Uses MERGE statements for idempotent, incremental loads.
    """

    def __init__(self, config: DatabricksConfig, catalog: str, schema: str):
        self.config = config
        self.catalog = catalog
        self.schema = schema
        self._conn: Connection | None = None

    def _get_connection(self) -> Connection:
        """Lazy connection — opens once, reuses across loads."""
        if self._conn is None:
            self._conn = databricks_sql.connect(
                server_hostname=self.config.host,
                http_path=self.config.http_path,
                access_token=self.config.access_token,
            )
            logger.info(f"[Databricks] Connected to {self.config.host}")
        return self._conn

    def _execute(self, sql: str, cursor=None):
        """Execute SQL using a provided or new cursor."""
        conn = self._get_connection()
        close_after = cursor is None
        if cursor is None:
            cursor = conn.cursor()
        try:
            logger.debug(f"[Databricks] Executing: {sql[:120]}...")
            cursor.execute(sql)
        finally:
            if close_after:
                cursor.close()

    def _ensure_schema(self):
        """Create catalog/schema if they don't exist yet."""
        self._execute(f"CREATE CATALOG IF NOT EXISTS {self.catalog}")
        self._execute(f"CREATE SCHEMA IF NOT EXISTS {self.catalog}.{self.schema}")

    def _df_to_values_sql(self, df: pd.DataFrame) -> str:
        """
        Convert a DataFrame to a SQL VALUES clause for staging.
        Handles None, strings, and numeric types safely.
        """
        rows = []
        for _, row in df.iterrows():
            cells = []
            for val in row:
                if pd.isna(val) or val is None:
                    cells.append("NULL")
                elif isinstance(val, (int, float)):
                    cells.append(str(val))
                else:
                    escaped = str(val).replace("'", "''")
                    cells.append(f"'{escaped}'")
            rows.append(f"({', '.join(cells)})")
        return ",\n".join(rows)

    # ─── META AD INSIGHTS ───────────────────────────────────────────────────

    def load_meta_insights(self, df: pd.DataFrame):
        """
        Upsert Meta ad insights into bronze.meta_ad_insights.
        Merge key: client_id + ad_id + date (one row per ad per day).
        """
        if df.empty:
            logger.warning("[Databricks] Skipping Meta load — empty DataFrame")
            return

        table = f"{self.catalog}.{self.schema}.meta_ad_insights"
        logger.info(f"[Databricks] Loading {len(df)} Meta rows → {table}")

        conn = self._get_connection()
        cursor = conn.cursor()

        self._ensure_schema()

        # Create table if not exists
        self._execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                client_id        STRING,
                campaign_id      STRING,
                campaign_name    STRING,
                adset_id         STRING,
                adset_name       STRING,
                ad_id            STRING,
                ad_name          STRING,
                spend            DOUBLE,
                impressions      BIGINT,
                clicks           BIGINT,
                cpm              DOUBLE,
                cpc              DOUBLE,
                ctr              DOUBLE,
                reach            BIGINT,
                frequency        DOUBLE,
                date             DATE,
                ingested_at      TIMESTAMP
            )
            USING DELTA
            PARTITIONED BY (client_id, date)
            TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
        """, cursor=cursor)

        # Batch insert via staging VALUES
        cols = [
            "client_id", "campaign_id", "campaign_name", "adset_id", "adset_name",
            "ad_id", "ad_name", "spend", "impressions", "clicks", "cpm", "cpc",
            "ctr", "reach", "frequency", "date", "ingested_at",
        ]
        load_df = df[[c for c in cols if c in df.columns]].copy()
        values_sql = self._df_to_values_sql(load_df)
        col_list = ", ".join(cols[:len(load_df.columns)])

        merge_sql = f"""
            MERGE INTO {table} AS target
            USING (
                SELECT {col_list}
                FROM (VALUES {values_sql}) AS t({col_list})
            ) AS source
            ON  target.client_id = source.client_id
            AND target.ad_id     = source.ad_id
            AND target.date      = source.date
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
        """
        self._execute(merge_sql, cursor=cursor)
        cursor.close()
        logger.info(f"[Databricks] ✅ Meta insights loaded — {len(df)} rows merged")

    # ─── HUBSPOT CONTACTS ───────────────────────────────────────────────────

    def load_hubspot_contacts(self, df: pd.DataFrame):
        """
        Upsert HubSpot contacts into bronze.hubspot_contacts.
        Merge key: client_id + contact_id.
        """
        if df.empty:
            logger.warning("[Databricks] Skipping contacts load — empty DataFrame")
            return

        table = f"{self.catalog}.{self.schema}.hubspot_contacts"
        logger.info(f"[Databricks] Loading {len(df)} contacts → {table}")

        conn = self._get_connection()
        cursor = conn.cursor()
        self._ensure_schema()

        self._execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                client_id        STRING,
                contact_id       STRING,
                email            STRING,
                firstname        STRING,
                lastname         STRING,
                lifecycle_stage  STRING,
                lead_status      STRING,
                hs_source        STRING,
                hs_source_data_1 STRING,
                hs_source_data_2 STRING,
                utm_source       STRING,
                utm_medium       STRING,
                utm_campaign     STRING,
                utm_content      STRING,
                utm_term         STRING,
                created_at       TIMESTAMP,
                ingested_at      TIMESTAMP
            )
            USING DELTA
            PARTITIONED BY (client_id)
            TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
        """, cursor=cursor)

        cols = [
            "client_id", "contact_id", "email", "firstname", "lastname",
            "lifecycle_stage", "lead_status", "hs_source", "hs_source_data_1",
            "hs_source_data_2", "utm_source", "utm_medium", "utm_campaign",
            "utm_content", "utm_term", "created_at", "ingested_at",
        ]
        load_df = df[[c for c in cols if c in df.columns]].copy()
        values_sql = self._df_to_values_sql(load_df)
        col_list = ", ".join(cols[:len(load_df.columns)])

        merge_sql = f"""
            MERGE INTO {table} AS target
            USING (
                SELECT {col_list}
                FROM (VALUES {values_sql}) AS t({col_list})
            ) AS source
            ON  target.client_id  = source.client_id
            AND target.contact_id = source.contact_id
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
        """
        self._execute(merge_sql, cursor=cursor)
        cursor.close()
        logger.info(f"[Databricks] ✅ Contacts loaded — {len(df)} rows merged")

    # ─── HUBSPOT DEALS ───────────────────────────────────────────────────────

    def load_hubspot_deals(self, df: pd.DataFrame):
        """
        Upsert HubSpot deals into bronze.hubspot_deals.
        Merge key: client_id + deal_id.
        """
        if df.empty:
            logger.warning("[Databricks] Skipping deals load — empty DataFrame")
            return

        table = f"{self.catalog}.{self.schema}.hubspot_deals"
        logger.info(f"[Databricks] Loading {len(df)} deals → {table}")

        conn = self._get_connection()
        cursor = conn.cursor()
        self._ensure_schema()

        self._execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                client_id                STRING,
                deal_id                  STRING,
                deal_name                STRING,
                deal_stage               STRING,
                pipeline                 STRING,
                amount                   DOUBLE,
                stage_probability        DOUBLE,
                close_date               TIMESTAMP,
                created_at               TIMESTAMP,
                associated_contact_ids   STRING,
                ingested_at              TIMESTAMP
            )
            USING DELTA
            PARTITIONED BY (client_id)
            TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
        """, cursor=cursor)

        cols = [
            "client_id", "deal_id", "deal_name", "deal_stage", "pipeline",
            "amount", "stage_probability", "close_date", "created_at",
            "associated_contact_ids", "ingested_at",
        ]
        load_df = df[[c for c in cols if c in df.columns]].copy()
        values_sql = self._df_to_values_sql(load_df)
        col_list = ", ".join(cols[:len(load_df.columns)])

        merge_sql = f"""
            MERGE INTO {table} AS target
            USING (
                SELECT {col_list}
                FROM (VALUES {values_sql}) AS t({col_list})
            ) AS source
            ON  target.client_id = source.client_id
            AND target.deal_id   = source.deal_id
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
        """
        self._execute(merge_sql, cursor=cursor)
        cursor.close()
        logger.info(f"[Databricks] ✅ Deals loaded — {len(df)} rows merged")

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("[Databricks] Connection closed")
