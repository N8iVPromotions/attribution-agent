-- transforms/agency_benchmark.sql
-- Aggregates channel_performance across all clients in an agency.
-- Creates a benchmarking table showing each client's ROI vs agency average.
--
-- This is a Python f-string template. The UNION ALL clause is generated
-- dynamically in agency_flow.py from the agency's client_ids list.
--
-- Usage (Python in agency_flow.py):
--   union_clauses = "\nUNION ALL\n".join([
--       f"SELECT '{cid}' AS client_id, '{name}' AS client_name, * "
--       f"FROM workspace.attribution_{cid}.channel_performance"
--       for cid, name in client_pairs
--   ])
--   sql = open("transforms/agency_benchmark.sql").read()
--   _run_sql(sql.format(agency_id=agency_id, union_all_clause=union_clauses))

CREATE OR REPLACE TABLE workspace.agency_{agency_id}.client_benchmarks AS

WITH all_client_data AS (
    {union_all_clause}
),

with_agency_avg AS (
    SELECT
        *,
        AVG(roi) OVER (
            PARTITION BY report_month, channel
        )                                       AS agency_avg_roi
    FROM all_client_data
)

SELECT
    client_id,
    client_name,
    report_month,
    channel,
    deals_count,
    pipeline_value,
    total_spend,
    roi,
    cost_per_deal,
    agency_avg_roi,
    roi - agency_avg_roi                        AS roi_vs_agency_avg
FROM with_agency_avg
ORDER BY report_month DESC, roi DESC;


-- Agency summary: one row per client, ranked by ROI
CREATE OR REPLACE TABLE workspace.agency_{agency_id}.agency_summary AS

WITH all_client_data AS (
    {union_all_clause}
)

SELECT
    client_id,
    client_name,
    report_month,
    SUM(pipeline_value)                         AS total_pipeline,
    SUM(total_spend)                            AS total_spend,
    SUM(deals_count)                            AS total_deals,
    CASE WHEN SUM(total_spend) > 0
         THEN SUM(pipeline_value) / SUM(total_spend)
         ELSE 0.0
    END                                         AS overall_roi,
    FIRST_VALUE(channel) OVER (
        PARTITION BY client_id, report_month
        ORDER BY pipeline_value DESC
    )                                           AS top_channel,
    RANK() OVER (
        PARTITION BY report_month
        ORDER BY CASE WHEN SUM(total_spend) > 0
                      THEN SUM(pipeline_value) / SUM(total_spend)
                      ELSE 0 END DESC
    )                                           AS roi_rank
FROM all_client_data
GROUP BY client_id, client_name, report_month, channel, pipeline_value
ORDER BY report_month DESC, overall_roi DESC
