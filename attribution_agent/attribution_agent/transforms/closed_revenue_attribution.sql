-- Builds single-source closed-revenue attribution for one explicit reporting period.
--
-- Template parameters:
--   {schema}             Client schema, e.g. workspace.attribution_demo_client
--   {attribution_model}  Runtime-enforced single-source model name
--   {report_month}       Report label in YYYY-MM format
--   {period_start}       Inclusive UTC reporting-period boundary
--   {period_end}         Exclusive UTC reporting-period boundary
--   {lookback_days}      Ad-evidence lookback before each deal close date
--   {run_started_at}     UTC pipeline-start timestamp for current-run source rows
--   {hubspot_closed_won_stage_keys} Safely quoted exact normalized stage IDs
--   {reporting_currency} Runtime-enforced ISO currency code (currently USD)
--   {report_timezone}    IANA business timezone for close-date evidence windows

CREATE TABLE IF NOT EXISTS {schema}.attributed_revenue (
    report_month        STRING,
    attribution_model   STRING,
    deal_id             STRING,
    channel             STRING,
    campaign            STRING,
    source_platform     STRING,
    touchpoint_role     STRING,
    credit              DOUBLE,
    attributed_pipeline DOUBLE,
    attributed_revenue  DOUBLE,
    attributed_refunds  DOUBLE,
    total_spend         DOUBLE,
    ingested_at         TIMESTAMP
)
USING DELTA;

INSERT INTO {schema}.attributed_revenue
REPLACE WHERE report_month = '{report_month}'
  AND attribution_model = '{attribution_model}'

WITH prepared_deals AS (
    SELECT
        TRIM(CAST(deal_id AS STRING)) AS deal_id,
        REGEXP_REPLACE(
            LOWER(TRIM(COALESCE(deal_stage, ''))),
            '[^a-z0-9]+',
            ''
        ) AS deal_stage_key,
        COALESCE(TRY_CAST(amount AS DOUBLE), 0.0) AS pipeline_value,
        TRY_CAST(close_datetime AS TIMESTAMP) AS close_date,
        hs_source,
        hs_source_label,
        hs_source_detail_1,
        hs_source_detail_2,
        ROW_NUMBER() OVER (
            PARTITION BY TRIM(CAST(deal_id AS STRING))
            ORDER BY TRY_CAST(ingested_at AS TIMESTAMP) DESC
        ) AS deal_rank
    FROM {schema}.hubspot_deals_raw
    WHERE ingested_at >= CAST('{run_started_at}' AS TIMESTAMP)
      AND UPPER(TRIM(COALESCE(deal_currency_code, ''))) = '{reporting_currency}'
      AND NULLIF(TRIM(CAST(deal_id AS STRING)), '') IS NOT NULL
),

closed_deals AS (
    SELECT
        deal_id,
        pipeline_value,
        close_date,
        hs_source,
        hs_source_label,
        hs_source_detail_1,
        hs_source_detail_2
    FROM prepared_deals
    WHERE deal_rank = 1
      AND deal_stage_key IN ({hubspot_closed_won_stage_keys})
      AND pipeline_value > 0
      AND close_date >= CAST('{period_start}' AS TIMESTAMP)
      AND close_date < CAST('{period_end}' AS TIMESTAMP)
),

deal_identity_inputs AS (
    SELECT
        d.*,
        LOWER(TRIM(COALESCE(hs_source, ''))) AS hs_source_key,
        LOWER(TRIM(COALESCE(hs_source_label, ''))) AS hs_source_label_key,
        REGEXP_REPLACE(
            LOWER(TRIM(COALESCE(hs_source_detail_1, ''))),
            '[^a-z0-9]+',
            ''
        ) AS source_detail_key,
        CASE
            -- HubSpot's drill-down meanings vary by source category:
            -- Paid Search detail 1 is campaign. Paid Social detail 2 is campaign.
            WHEN LOWER(TRIM(COALESCE(hs_source, ''))) = 'paid_search'
             AND LOWER(TRIM(COALESCE(hs_source_detail_1, '')))
                    NOT IN ('', 'nan', 'none', 'null')
                THEN LOWER(TRIM(hs_source_detail_1))
            WHEN LOWER(TRIM(COALESCE(hs_source, ''))) = 'paid_social'
             AND LOWER(TRIM(COALESCE(hs_source_detail_2, '')))
                    NOT IN ('', 'nan', 'none', 'null')
                THEN LOWER(TRIM(hs_source_detail_2))
            ELSE ''
        END AS crm_campaign_key
    FROM closed_deals d
),

deals_with_identity AS (
    SELECT
        i.*,
        CASE
            -- The only supported paid-search connector is Google Ads. Exact
            -- campaign evidence is still required before any revenue is credited.
            WHEN hs_source_key = 'paid_search'
              OR hs_source_label_key = 'paid search'
                THEN 'google'
            WHEN hs_source_key = 'paid_social'
             AND source_detail_key IN ('tiktok', 'tiktokads')
                THEN 'tiktok'
            WHEN hs_source_key = 'paid_social'
             AND source_detail_key IN ('linkedin', 'linkedinads')
                THEN 'linkedin'
            WHEN hs_source_key = 'paid_social'
             AND source_detail_key IN (
                    'facebook', 'facebookads',
                    'instagram', 'instagramads',
                    'meta', 'metaads'
             )
                THEN 'meta'
            ELSE ''
        END AS source_platform
    FROM deal_identity_inputs i
),

ad_evidence_inputs AS (
    SELECT
        TRY_CAST(date AS DATE) AS spend_date,
        CASE
            WHEN LOWER(TRIM(COALESCE(source_platform, '')))
                    IN ('meta', 'facebook', 'fb', 'instagram', 'ig')
                THEN 'meta'
            WHEN LOWER(TRIM(COALESCE(source_platform, '')))
                    IN ('google', 'adwords', 'google_ads', 'googleads')
                THEN 'google'
            WHEN LOWER(TRIM(COALESCE(source_platform, '')))
                    IN ('tiktok', 'tik_tok', 'tt')
                THEN 'tiktok'
            WHEN LOWER(TRIM(COALESCE(source_platform, ''))) IN ('linkedin', 'li')
                THEN 'linkedin'
            ELSE LOWER(TRIM(COALESCE(source_platform, '')))
        END AS source_platform,
        CASE
            WHEN LOWER(TRIM(COALESCE(utm_campaign, '')))
                    NOT IN ('', 'nan', 'none', 'null')
                THEN LOWER(TRIM(utm_campaign))
            WHEN LOWER(TRIM(COALESCE(campaign_name, '')))
                    NOT IN ('', 'nan', 'none', 'null')
                THEN LOWER(TRIM(campaign_name))
            ELSE ''
        END AS campaign_key,
        GREATEST(COALESCE(TRY_CAST(spend AS DOUBLE), 0.0), 0.0) AS spend
    FROM {schema}.ad_spend_normalized
    WHERE ingested_at >= CAST('{run_started_at}' AS TIMESTAMP)
      AND TRY_CAST(date AS DATE) >= DATE_SUB(
          CAST('{period_start}' AS DATE),
          {lookback_days}
      )
      AND TRY_CAST(date AS DATE) < CAST('{period_end}' AS DATE)
),

ad_evidence_daily AS (
    SELECT
        spend_date,
        source_platform,
        campaign_key,
        SUM(spend) AS spend
    FROM ad_evidence_inputs
    WHERE spend_date IS NOT NULL
      AND source_platform <> ''
    GROUP BY spend_date, source_platform, campaign_key
),

matched_deals AS (
    SELECT
        d.deal_id,
        d.pipeline_value,
        d.close_date,
        d.crm_campaign_key,
        d.source_platform,
        CASE WHEN COUNT(a.source_platform) > 0 THEN 1 ELSE 0 END AS has_ad_match,
        COALESCE(SUM(a.spend), 0.0) AS evidence_spend
    FROM deals_with_identity d
    LEFT JOIN ad_evidence_daily a
        ON d.source_platform <> ''
        AND a.source_platform = d.source_platform
        AND a.spend_date >= DATE_SUB(
            TO_DATE(FROM_UTC_TIMESTAMP(d.close_date, '{report_timezone}')),
            {lookback_days}
        )
        AND a.spend_date <= TO_DATE(
            FROM_UTC_TIMESTAMP(d.close_date, '{report_timezone}')
        )
        AND (
            (d.crm_campaign_key <> '' AND a.campaign_key = d.crm_campaign_key)
            OR d.crm_campaign_key = ''
        )
    GROUP BY
        d.deal_id,
        d.pipeline_value,
        d.close_date,
        d.crm_campaign_key,
        d.source_platform
),

stripe_payment_history AS (
    SELECT
        TRIM(CAST(payment_id AS STRING)) AS payment_id,
        CASE
            WHEN LOWER(TRIM(COALESCE(hubspot_deal_id, '')))
                    IN ('', 'nan', 'none', 'null')
                THEN ''
            ELSE TRIM(hubspot_deal_id)
        END AS hubspot_deal_id_key,
        COALESCE(TRY_CAST(amount_paid AS DOUBLE), 0.0) AS amount_paid,
        COALESCE(TRY_CAST(refund_amount AS DOUBLE), 0.0) AS refund_amount,
        LOWER(TRIM(COALESCE(status, ''))) AS payment_status,
        ROW_NUMBER() OVER (
            PARTITION BY TRIM(CAST(payment_id AS STRING))
            ORDER BY
                TRY_CAST(ingested_at AS TIMESTAMP) DESC,
                TRY_CAST(created_at AS TIMESTAMP) DESC
        ) AS payment_rank
    FROM {schema}.stripe_payments_raw
    WHERE ingested_at >= CAST('{run_started_at}' AS TIMESTAMP)
      AND UPPER(TRIM(COALESCE(currency, ''))) = '{reporting_currency}'
      AND NULLIF(TRIM(CAST(payment_id AS STRING)), '') IS NOT NULL
),

payments_by_deal AS (
    SELECT
        hubspot_deal_id_key AS deal_id,
        SUM(GREATEST(amount_paid - refund_amount, 0.0)) AS collected_revenue,
        SUM(GREATEST(refund_amount, 0.0)) AS refunded_revenue
    FROM stripe_payment_history
    WHERE payment_rank = 1
      AND hubspot_deal_id_key <> ''
      AND payment_status = 'succeeded'
    GROUP BY hubspot_deal_id_key
)

SELECT
    '{report_month}' AS report_month,
    '{attribution_model}' AS attribution_model,
    m.deal_id,
    CASE
        WHEN m.has_ad_match = 0 THEN 'Unattributed'
        WHEN m.source_platform = 'google' THEN 'Paid Search'
        WHEN m.source_platform IN ('meta', 'linkedin', 'tiktok') THEN 'Paid Social'
        ELSE m.source_platform
    END AS channel,
    CASE
        WHEN m.has_ad_match = 0 THEN ''
        WHEN m.crm_campaign_key = '' THEN '(platform only)'
        ELSE m.crm_campaign_key
    END AS campaign,
    CASE
        WHEN m.has_ad_match = 1 THEN m.source_platform
        ELSE 'unattributed'
    END AS source_platform,
    'source_match' AS touchpoint_role,
    1.0 AS credit,
    m.pipeline_value AS attributed_pipeline,
    COALESCE(p.collected_revenue, 0.0) AS attributed_revenue,
    COALESCE(p.refunded_revenue, 0.0) AS attributed_refunds,
    CASE WHEN m.has_ad_match = 1 THEN m.evidence_spend ELSE 0.0 END AS total_spend,
    CURRENT_TIMESTAMP() AS ingested_at
FROM matched_deals m
LEFT JOIN payments_by_deal p ON m.deal_id = p.deal_id;


CREATE TABLE IF NOT EXISTS {schema}.channel_performance (
    report_month      STRING,
    channel           STRING,
    utm_campaign      STRING,
    utm_source        STRING,
    utm_medium        STRING,
    deals_count       BIGINT,
    pipeline_value    DOUBLE,
    avg_deal_value    DOUBLE,
    avg_days_to_close DOUBLE,
    total_spend       DOUBLE,
    roi               DOUBLE,
    cost_per_deal     DOUBLE,
    ingested_at       TIMESTAMP
)
USING DELTA;

INSERT INTO {schema}.channel_performance
REPLACE WHERE report_month = '{report_month}'
WITH attributed_by_platform AS (
    SELECT
        report_month,
        source_platform,
        CASE
            WHEN source_platform = 'unattributed' THEN 'Unattributed'
            WHEN source_platform = 'google' THEN 'Paid Search'
            WHEN source_platform IN ('meta', 'linkedin', 'tiktok') THEN 'Paid Social'
            ELSE source_platform
        END AS channel,
        COUNT(DISTINCT deal_id) AS deals_count,
        SUM(attributed_pipeline) AS pipeline_value
    FROM {schema}.attributed_revenue
    WHERE report_month = '{report_month}'
      AND attribution_model = '{attribution_model}'
    GROUP BY report_month, source_platform
),

period_ad_spend_inputs AS (
    SELECT
        CASE
            WHEN LOWER(TRIM(COALESCE(source_platform, '')))
                    IN ('meta', 'facebook', 'fb', 'instagram', 'ig')
                THEN 'meta'
            WHEN LOWER(TRIM(COALESCE(source_platform, '')))
                    IN ('google', 'adwords', 'google_ads', 'googleads')
                THEN 'google'
            WHEN LOWER(TRIM(COALESCE(source_platform, '')))
                    IN ('tiktok', 'tik_tok', 'tt')
                THEN 'tiktok'
            WHEN LOWER(TRIM(COALESCE(source_platform, ''))) IN ('linkedin', 'li')
                THEN 'linkedin'
            ELSE LOWER(TRIM(COALESCE(source_platform, '')))
        END AS source_platform,
        GREATEST(COALESCE(TRY_CAST(spend AS DOUBLE), 0.0), 0.0) AS spend
    FROM {schema}.ad_spend_normalized
    WHERE ingested_at >= CAST('{run_started_at}' AS TIMESTAMP)
      AND TRY_CAST(date AS DATE) >= CAST('{period_start}' AS DATE)
      AND TRY_CAST(date AS DATE) < CAST('{period_end}' AS DATE)
),

period_ad_spend_by_platform AS (
    SELECT
        '{report_month}' AS report_month,
        source_platform,
        CASE
            WHEN source_platform = 'google' THEN 'Paid Search'
            WHEN source_platform IN ('meta', 'linkedin', 'tiktok') THEN 'Paid Social'
            ELSE source_platform
        END AS channel,
        SUM(spend) AS total_spend
    FROM period_ad_spend_inputs
    WHERE source_platform <> ''
    GROUP BY source_platform
),

platform_performance AS (
    SELECT
        COALESCE(a.report_month, s.report_month) AS report_month,
        COALESCE(a.source_platform, s.source_platform) AS source_platform,
        COALESCE(a.channel, s.channel) AS channel,
        COALESCE(a.deals_count, 0) AS deals_count,
        COALESCE(a.pipeline_value, 0.0) AS pipeline_value,
        COALESCE(s.total_spend, 0.0) AS total_spend
    FROM attributed_by_platform a
    FULL OUTER JOIN period_ad_spend_by_platform s
        ON a.report_month = s.report_month
        AND a.source_platform = s.source_platform
)

SELECT
    report_month,
    channel,
    '' AS utm_campaign,
    source_platform AS utm_source,
    '' AS utm_medium,
    deals_count,
    pipeline_value,
    CASE WHEN deals_count > 0
         THEN pipeline_value / deals_count
         ELSE 0.0
    END AS avg_deal_value,
    CAST(NULL AS DOUBLE) AS avg_days_to_close,
    total_spend,
    CASE WHEN total_spend > 0
         THEN pipeline_value / total_spend
         ELSE 0.0
    END AS roi,
    CASE WHEN deals_count > 0
         THEN total_spend / deals_count
         ELSE NULL
    END AS cost_per_deal,
    CURRENT_TIMESTAMP() AS ingested_at
FROM platform_performance;


CREATE TABLE IF NOT EXISTS {schema}.channel_performance_v2 (
    report_month                 STRING,
    channel                      STRING,
    utm_campaign                 STRING,
    utm_source                   STRING,
    utm_medium                   STRING,
    deals_count                  BIGINT,
    pipeline_value               DOUBLE,
    avg_deal_value               DOUBLE,
    avg_days_to_close            DOUBLE,
    total_spend                  DOUBLE,
    roi                          DOUBLE,
    cost_per_deal                DOUBLE,
    collected_revenue            DOUBLE,
    refunded_revenue             DOUBLE,
    refund_rate                  DOUBLE,
    ltv_90day                    DOUBLE,
    true_roi                     DOUBLE,
    cost_per_collected_dollar    DOUBLE,
    ingested_at                  TIMESTAMP
)
USING DELTA;

INSERT INTO {schema}.channel_performance_v2 BY NAME
REPLACE WHERE report_month = '{report_month}'
WITH attributed_cash_by_platform AS (
    SELECT
        report_month,
        source_platform,
        SUM(attributed_revenue) AS net_collected_revenue,
        SUM(attributed_refunds) AS refunded_revenue
    FROM {schema}.attributed_revenue
    WHERE report_month = '{report_month}'
      AND attribution_model = '{attribution_model}'
    GROUP BY report_month, source_platform
)

SELECT
    cp.report_month,
    cp.channel,
    cp.utm_campaign,
    cp.utm_source,
    cp.utm_medium,
    cp.deals_count,
    cp.pipeline_value,
    cp.avg_deal_value,
    cp.avg_days_to_close,
    cp.total_spend,
    cp.roi,
    cp.cost_per_deal,
    COALESCE(cash.net_collected_revenue, 0.0) AS collected_revenue,
    COALESCE(cash.refunded_revenue, 0.0) AS refunded_revenue,
    CASE WHEN COALESCE(cash.net_collected_revenue, 0.0)
                   + COALESCE(cash.refunded_revenue, 0.0) > 0
         THEN COALESCE(cash.refunded_revenue, 0.0)
              / (
                  COALESCE(cash.net_collected_revenue, 0.0)
                  + COALESCE(cash.refunded_revenue, 0.0)
              )
         ELSE 0.0
    END AS refund_rate,
    COALESCE(cash.net_collected_revenue, 0.0) AS ltv_90day,
    CASE WHEN cp.total_spend > 0
         THEN COALESCE(cash.net_collected_revenue, 0.0) / cp.total_spend
         ELSE 0.0
    END AS true_roi,
    CASE WHEN COALESCE(cash.net_collected_revenue, 0.0) > 0
         THEN cp.total_spend / cash.net_collected_revenue
         ELSE NULL
    END AS cost_per_collected_dollar,
    CURRENT_TIMESTAMP() AS ingested_at
FROM {schema}.channel_performance cp
LEFT JOIN attributed_cash_by_platform cash
    ON cp.report_month = cash.report_month
    AND cp.utm_source = cash.source_platform
WHERE cp.report_month = '{report_month}'
