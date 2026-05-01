-- transforms/attribution_model.sql
-- Builds the channel_performance Silver table from Bronze HubSpot + Meta data.
-- Run on a monthly schedule in a Databricks Job after ingest_flow completes.
--
-- Usage (Databricks SQL notebook or Job):
--   Replace workspace.attribution_demo_client with the target schema.
--   Or run via Python: sql.format(schema="workspace.attribution_demo_client")

-- Step 1: Create table (no-op if already exists)
CREATE TABLE IF NOT EXISTS {schema}.channel_performance (
    report_month        STRING,
    channel             STRING,
    utm_campaign        STRING,
    utm_source          STRING,
    utm_medium          STRING,
    deals_count         BIGINT,
    pipeline_value      DOUBLE,
    avg_deal_value      DOUBLE,
    avg_days_to_close   DOUBLE,
    total_spend         DOUBLE,
    roi                 DOUBLE,
    cost_per_deal       DOUBLE,
    ingested_at         TIMESTAMP
)
USING DELTA;

-- Step 2: Overwrite with latest data
INSERT OVERWRITE {schema}.channel_performance

WITH deals_with_channel AS (
    SELECT
        deal_id,
        amount,
        create_date,
        days_to_deal,
        COALESCE(utm_campaign, 'unknown') AS utm_campaign,
        COALESCE(utm_source,   'unknown') AS utm_source,
        COALESCE(utm_medium,   'unknown') AS utm_medium,
        CASE
            WHEN LOWER(utm_medium) IN ('cpc','paid','ppc')
                AND LOWER(utm_source) LIKE '%facebook%'  THEN 'Paid Social - Meta'
            WHEN LOWER(utm_medium) IN ('cpc','paid','ppc')
                AND LOWER(utm_source) LIKE '%google%'    THEN 'Paid Search - Google'
            WHEN LOWER(utm_medium) = 'email'             THEN 'Email'
            WHEN LOWER(utm_medium) = 'social'            THEN 'Organic Social'
            WHEN hs_source = 'PAID_SOCIAL'               THEN 'Paid Social'
            WHEN hs_source = 'PAID_SEARCH'               THEN 'Paid Search'
            WHEN hs_source = 'ORGANIC_SEARCH'            THEN 'Organic Search'
            WHEN hs_source = 'EMAIL_MARKETING'           THEN 'Email'
            WHEN hs_source = 'DIRECT_TRAFFIC'            THEN 'Direct'
            ELSE 'Unattributed'
        END AS channel
    FROM {schema}.hubspot_deals_raw
),

deals_agg AS (
    SELECT
        DATE_FORMAT(CURRENT_DATE(), 'yyyy-MM') AS report_month,
        channel,
        utm_campaign,
        utm_source,
        utm_medium,
        COUNT(deal_id)              AS deals_count,
        SUM(COALESCE(amount, 0))    AS pipeline_value,
        AVG(COALESCE(amount, 0))    AS avg_deal_value,
        AVG(days_to_deal)           AS avg_days_to_close
    FROM deals_with_channel
    GROUP BY channel, utm_campaign, utm_source, utm_medium
),

meta_spend AS (
    SELECT
        'Paid Social - Meta'        AS channel,
        SUM(spend)                  AS total_spend
    FROM {schema}.meta_ads_raw
    WHERE date >= DATE_TRUNC('month', CURRENT_DATE())
    GROUP BY 1
)

SELECT
    d.report_month,
    d.channel,
    d.utm_campaign,
    d.utm_source,
    d.utm_medium,
    d.deals_count,
    d.pipeline_value,
    d.avg_deal_value,
    d.avg_days_to_close,
    COALESCE(m.total_spend, 0)                              AS total_spend,
    CASE
        WHEN COALESCE(m.total_spend, 0) = 0 THEN NULL
        ELSE ROUND(d.pipeline_value / m.total_spend, 2)
    END                                                     AS roi,
    CASE
        WHEN d.deals_count = 0 THEN NULL
        ELSE ROUND(COALESCE(m.total_spend, 0) / d.deals_count, 2)
    END                                                     AS cost_per_deal,
    CURRENT_TIMESTAMP()                                     AS ingested_at
FROM deals_agg d
LEFT JOIN meta_spend m ON d.channel = m.channel;
