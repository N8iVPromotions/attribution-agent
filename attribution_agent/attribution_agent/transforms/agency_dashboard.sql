-- transforms/agency_dashboard.sql
-- Produces portfolio_performance: one row per client per month.
-- This is the Power BI source table for the agency dashboard.
--
-- Python template — fill before executing:
--   {agency_id}        — agency slug, e.g. "acme_media"
--   {union_all_clause} — UNION ALL across client schemas, generated in agency_flow.py
--
-- Usage (Python in agency_flow.py):
--   union_clauses = "\nUNION ALL\n".join([
--       f"SELECT '{{cid}}' AS client_id, '{{name}}' AS client_name, * "
--       f"FROM workspace.attribution_{{cid}}.channel_performance"
--       for cid, name in client_pairs
--   ])
--   sql = open("transforms/agency_dashboard.sql").read()
--   _run_sql(sql.format(agency_id=agency_id, union_all_clause=union_clauses))

CREATE SCHEMA IF NOT EXISTS workspace.agency_{agency_id};

CREATE OR REPLACE TABLE workspace.agency_{agency_id}.portfolio_performance AS

WITH all_data AS (
    {union_all_clause}
),

client_monthly AS (
    SELECT
        client_id,
        client_name,
        report_month,
        SUM(pipeline_value)                                             AS total_pipeline,
        SUM(total_spend)                                                AS total_spend,
        SUM(deals_count)                                                AS total_deals,
        CASE WHEN SUM(total_spend) > 0
             THEN SUM(pipeline_value) / SUM(total_spend)
             ELSE 0.0
        END                                                             AS overall_roi,
        SUM(CASE WHEN channel = 'Unattributed' THEN pipeline_value ELSE 0 END)
            / NULLIF(SUM(pipeline_value), 0)                            AS unattributed_pct,
        FIRST_VALUE(channel) OVER (
            PARTITION BY client_id, report_month
            ORDER BY pipeline_value DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
        )                                                               AS top_channel
    FROM all_data
    GROUP BY client_id, client_name, report_month, channel, pipeline_value
)

SELECT DISTINCT
    client_id,
    client_name,
    report_month,
    total_pipeline,
    total_spend,
    total_deals,
    overall_roi,
    top_channel,
    COALESCE(unattributed_pct, 0.0)                                     AS unattributed_pct
FROM client_monthly
ORDER BY report_month DESC, overall_roi DESC;


-- Benchmarking view: ROI vs agency average per channel
CREATE OR REPLACE VIEW workspace.agency_{agency_id}.channel_benchmarks AS

WITH all_data AS (
    {union_all_clause}
),

with_avg AS (
    SELECT
        client_id,
        client_name,
        report_month,
        channel,
        deals_count,
        pipeline_value,
        total_spend,
        roi,
        AVG(roi) OVER (PARTITION BY report_month, channel)             AS agency_avg_roi
    FROM all_data
)

SELECT
    *,
    roi - agency_avg_roi                                                AS roi_vs_agency_avg
FROM with_avg
ORDER BY report_month DESC, channel, roi DESC
