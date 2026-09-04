-- Builds attributed closed-revenue tables for a client schema.
--
-- Template parameters:
--   {schema}             Client schema, e.g. workspace.attribution_demo_client
--   {attribution_model}  last_touch | first_touch | linear | time_decay | u_shape | w_shape
--   {lookback_days}      Inclusive ad-spend lookback window ending on the deal date
--   {run_started_at}     UTC pipeline-start timestamp used to exclude stale raw rows

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

INSERT OVERWRITE {schema}.attributed_revenue

WITH closed_deals AS (
    SELECT
        TRIM(CAST(deal_id AS STRING)) AS deal_id,
        COALESCE(TRY_CAST(amount AS DOUBLE), 0.0) AS pipeline_value,
        COALESCE(
            TRY_CAST(close_date AS TIMESTAMP),
            TRY_CAST(create_date AS TIMESTAMP)
        ) AS close_date,
        TRY_CAST(create_date AS TIMESTAMP) AS create_date,
        CASE
            WHEN LOWER(TRIM(COALESCE(contact_email, ''))) IN ('', 'nan', 'none', 'null') THEN ''
            ELSE LOWER(TRIM(contact_email))
        END AS contact_email_key,
        hs_source,
        hs_source_label,
        hs_source_detail_1,
        hs_source_detail_2,
        utm_campaign,
        utm_source,
        utm_medium,
        TRY_CAST(lead_create_date AS TIMESTAMP) AS lead_create_date
    FROM {schema}.hubspot_deals_raw
    WHERE ingested_at >= CAST('{run_started_at}' AS TIMESTAMP)
      AND REGEXP_REPLACE(
              LOWER(TRIM(COALESCE(deal_stage, ''))),
              '[^a-z0-9]+',
              ''
          ) IN ('closedwon', 'won')
      AND COALESCE(TRY_CAST(amount AS DOUBLE), 0.0) > 0
      AND COALESCE(
              TRY_CAST(close_date AS TIMESTAMP),
              TRY_CAST(create_date AS TIMESTAMP)
          ) IS NOT NULL
      AND NULLIF(TRIM(CAST(deal_id AS STRING)), '') IS NOT NULL
),

unique_closed_deal_emails AS (
    SELECT
        contact_email_key,
        MIN(deal_id) AS deal_id
    FROM closed_deals
    WHERE contact_email_key <> ''
    GROUP BY contact_email_key
    HAVING COUNT(DISTINCT deal_id) = 1
),

prepared_payments AS (
    SELECT
        CASE
            WHEN LOWER(TRIM(COALESCE(hubspot_deal_id, ''))) IN ('', 'nan', 'none', 'null') THEN ''
            ELSE TRIM(hubspot_deal_id)
        END AS hubspot_deal_id_key,
        CASE
            WHEN LOWER(TRIM(COALESCE(customer_email, ''))) IN ('', 'nan', 'none', 'null') THEN ''
            ELSE LOWER(TRIM(customer_email))
        END AS customer_email_key,
        COALESCE(TRY_CAST(amount_paid AS DOUBLE), 0.0) AS amount_paid,
        COALESCE(TRY_CAST(refund_amount AS DOUBLE), 0.0) AS refund_amount
    FROM {schema}.stripe_payments_raw
    WHERE ingested_at >= CAST('{run_started_at}' AS TIMESTAMP)
      AND LOWER(TRIM(COALESCE(status, ''))) = 'succeeded'
),

resolved_payments AS (
    SELECT
        COALESCE(id_match.deal_id, email_match.deal_id) AS deal_id,
        p.amount_paid,
        p.refund_amount
    FROM prepared_payments p
    LEFT JOIN closed_deals id_match
        ON p.hubspot_deal_id_key <> ''
        AND p.hubspot_deal_id_key = id_match.deal_id
    LEFT JOIN unique_closed_deal_emails email_match
        ON p.hubspot_deal_id_key = ''
        AND p.customer_email_key <> ''
        AND p.customer_email_key = email_match.contact_email_key
),

payments_by_deal AS (
    SELECT
        deal_id,
        SUM(GREATEST(amount_paid - refund_amount, 0.0)) AS collected_revenue,
        SUM(GREATEST(refund_amount, 0.0)) AS refunded_revenue
    FROM resolved_payments
    WHERE deal_id IS NOT NULL
    GROUP BY deal_id
),

deal_identity_inputs AS (
    SELECT
        d.*,
        LOWER(TRIM(COALESCE(utm_source, ''))) AS utm_source_key,
        LOWER(TRIM(COALESCE(hs_source_detail_1, ''))) AS source_detail_key,
        CASE
            WHEN LOWER(TRIM(COALESCE(utm_campaign, ''))) NOT IN ('', 'nan', 'none', 'null')
                THEN LOWER(TRIM(utm_campaign))
            WHEN LOWER(TRIM(COALESCE(hs_source_detail_2, ''))) NOT IN ('', 'nan', 'none', 'null')
                THEN LOWER(TRIM(hs_source_detail_2))
            ELSE ''
        END AS crm_campaign_key
    FROM closed_deals d
),

deals_with_identity AS (
    SELECT
        i.*,
        CASE
            WHEN utm_source_key IN ('meta', 'facebook', 'fb', 'instagram', 'ig') THEN 'meta'
            WHEN utm_source_key IN ('google', 'adwords', 'google_ads', 'googleads') THEN 'google'
            WHEN utm_source_key IN ('tiktok', 'tik_tok', 'tt') THEN 'tiktok'
            WHEN utm_source_key IN ('linkedin', 'li') THEN 'linkedin'
            WHEN source_detail_key LIKE '%tiktok%' THEN 'tiktok'
            WHEN source_detail_key LIKE '%linkedin%' THEN 'linkedin'
            WHEN source_detail_key LIKE '%google%' OR source_detail_key LIKE '%adwords%' THEN 'google'
            WHEN source_detail_key LIKE '%facebook%'
              OR source_detail_key LIKE '%instagram%'
              OR source_detail_key = 'meta' THEN 'meta'
            ELSE ''
        END AS source_platform
    FROM deal_identity_inputs i
),

deal_touchpoints AS (
    SELECT
        deal_id,
        close_date,
        pipeline_value,
        COALESCE(create_date, close_date) AS touchpoint_at,
        'first_touch' AS touchpoint_role,
        crm_campaign_key,
        source_platform
    FROM deals_with_identity

    UNION ALL

    SELECT
        deal_id,
        close_date,
        pipeline_value,
        lead_create_date AS touchpoint_at,
        'lead_creation' AS touchpoint_role,
        crm_campaign_key,
        source_platform
    FROM deals_with_identity
    WHERE lead_create_date IS NOT NULL

    UNION ALL

    SELECT
        deal_id,
        close_date,
        pipeline_value,
        close_date AS touchpoint_at,
        'close_touch' AS touchpoint_role,
        crm_campaign_key,
        source_platform
    FROM deals_with_identity
),

deduped_touchpoints AS (
    SELECT DISTINCT
        deal_id,
        close_date,
        pipeline_value,
        touchpoint_at,
        touchpoint_role,
        crm_campaign_key,
        source_platform
    FROM deal_touchpoints
),

scored AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY deal_id
            ORDER BY
                touchpoint_at,
                CASE touchpoint_role
                    WHEN 'first_touch' THEN 1
                    WHEN 'lead_creation' THEN 2
                    ELSE 3
                END
        ) AS touch_index,
        COUNT(*) OVER (PARTITION BY deal_id) AS touch_count,
        MAX(touchpoint_at) OVER (PARTITION BY deal_id) AS last_touch_at,
        MAX(CASE WHEN touchpoint_role = 'lead_creation' THEN 1 ELSE 0 END)
            OVER (PARTITION BY deal_id) AS has_lead_touch
    FROM deduped_touchpoints
),

weighted AS (
    SELECT
        s.*,
        CASE
            WHEN touch_count = 1 THEN 1.0
            WHEN '{attribution_model}' = 'first_touch'
                THEN CASE WHEN touch_index = 1 THEN 1.0 ELSE 0.0 END
            WHEN '{attribution_model}' = 'last_touch'
                THEN CASE WHEN touch_index = touch_count THEN 1.0 ELSE 0.0 END
            WHEN '{attribution_model}' = 'linear'
                THEN 1.0 / touch_count
            WHEN '{attribution_model}' = 'time_decay'
                THEN EXP(-DATEDIFF(last_touch_at, touchpoint_at) / 7.0)
            WHEN '{attribution_model}' = 'u_shape' AND touch_count = 2
                THEN 0.5
            WHEN '{attribution_model}' = 'u_shape'
                THEN CASE
                    WHEN touch_index = 1 THEN 0.4
                    WHEN touch_index = touch_count THEN 0.4
                    ELSE 0.2 / NULLIF(touch_count - 2, 0)
                END
            WHEN '{attribution_model}' = 'w_shape'
              AND has_lead_touch = 0
              AND touch_count = 2
                THEN 0.5
            WHEN '{attribution_model}' = 'w_shape'
              AND has_lead_touch = 0
                THEN CASE
                    WHEN touch_index = 1 THEN 0.4
                    WHEN touch_index = touch_count THEN 0.4
                    ELSE 0.2 / NULLIF(touch_count - 2, 0)
                END
            WHEN '{attribution_model}' = 'w_shape' AND touch_count <= 3
                THEN 1.0 / touch_count
            WHEN '{attribution_model}' = 'w_shape'
                THEN CASE
                    WHEN touch_index = 1 THEN 0.3
                    WHEN touch_index = touch_count THEN 0.3
                    WHEN touchpoint_role = 'lead_creation' THEN 0.3
                    ELSE 0.1 / NULLIF(touch_count - 3, 0)
                END
            ELSE CASE WHEN touch_index = touch_count THEN 1.0 ELSE 0.0 END
        END AS raw_credit
    FROM scored s
),

normalized AS (
    SELECT
        deal_id,
        close_date,
        pipeline_value,
        touchpoint_at,
        touchpoint_role,
        crm_campaign_key,
        source_platform,
        touch_index,
        touch_count,
        has_lead_touch,
        raw_credit / NULLIF(SUM(raw_credit) OVER (PARTITION BY deal_id), 0) AS credit
    FROM weighted
),

ad_spend_inputs AS (
    SELECT
        TRY_CAST(date AS DATE) AS spend_date,
        CASE
            WHEN LOWER(TRIM(COALESCE(source_platform, ''))) IN ('meta', 'facebook', 'fb', 'instagram', 'ig') THEN 'meta'
            WHEN LOWER(TRIM(COALESCE(source_platform, ''))) IN ('google', 'adwords', 'google_ads', 'googleads') THEN 'google'
            WHEN LOWER(TRIM(COALESCE(source_platform, ''))) IN ('tiktok', 'tik_tok', 'tt') THEN 'tiktok'
            WHEN LOWER(TRIM(COALESCE(source_platform, ''))) IN ('linkedin', 'li') THEN 'linkedin'
            ELSE LOWER(TRIM(COALESCE(source_platform, '')))
        END AS source_platform,
        CASE
            WHEN LOWER(TRIM(COALESCE(utm_campaign, ''))) NOT IN ('', 'nan', 'none', 'null')
                THEN LOWER(TRIM(utm_campaign))
            WHEN LOWER(TRIM(COALESCE(campaign_name, ''))) NOT IN ('', 'nan', 'none', 'null')
                THEN LOWER(TRIM(campaign_name))
            ELSE ''
        END AS campaign_key,
        COALESCE(TRY_CAST(spend AS DOUBLE), 0.0) AS spend
    FROM {schema}.ad_spend_normalized
    WHERE ingested_at >= CAST('{run_started_at}' AS TIMESTAMP)
),

ad_spend_daily AS (
    SELECT
        spend_date,
        source_platform,
        campaign_key,
        SUM(spend) AS total_spend
    FROM ad_spend_inputs
    WHERE spend_date IS NOT NULL
      AND source_platform <> ''
    GROUP BY spend_date, source_platform, campaign_key
),

matched_touchpoints AS (
    SELECT
        n.deal_id,
        n.close_date,
        n.pipeline_value,
        n.touchpoint_at,
        n.touchpoint_role,
        n.crm_campaign_key,
        n.source_platform,
        n.touch_index,
        n.touch_count,
        n.has_lead_touch,
        n.credit,
        CASE WHEN COUNT(a.source_platform) > 0 THEN 1 ELSE 0 END AS has_ad_match,
        COALESCE(SUM(a.total_spend), 0.0) AS matched_spend
    FROM normalized n
    LEFT JOIN ad_spend_daily a
        ON n.source_platform <> ''
        AND a.source_platform = n.source_platform
        AND a.spend_date BETWEEN DATE_SUB(CAST(n.close_date AS DATE), {lookback_days})
                             AND CAST(n.close_date AS DATE)
        AND (
            (n.crm_campaign_key <> '' AND a.campaign_key = n.crm_campaign_key)
            OR n.crm_campaign_key = ''
        )
    GROUP BY
        n.deal_id,
        n.close_date,
        n.pipeline_value,
        n.touchpoint_at,
        n.touchpoint_role,
        n.crm_campaign_key,
        n.source_platform,
        n.touch_index,
        n.touch_count,
        n.has_lead_touch,
        n.credit
)

SELECT
    DATE_FORMAT(m.close_date, 'yyyy-MM') AS report_month,
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
    CASE WHEN m.has_ad_match = 1 THEN m.source_platform ELSE 'unattributed' END AS source_platform,
    m.touchpoint_role,
    COALESCE(m.credit, 0.0) AS credit,
    m.pipeline_value * COALESCE(m.credit, 0.0) AS attributed_pipeline,
    COALESCE(p.collected_revenue, 0.0) * COALESCE(m.credit, 0.0) AS attributed_revenue,
    COALESCE(p.refunded_revenue, 0.0) * COALESCE(m.credit, 0.0) AS attributed_refunds,
    CASE WHEN m.has_ad_match = 1 THEN m.matched_spend ELSE 0.0 END AS total_spend,
    CURRENT_TIMESTAMP() AS ingested_at
FROM matched_touchpoints m
LEFT JOIN payments_by_deal p ON m.deal_id = p.deal_id;


CREATE OR REPLACE TABLE {schema}.channel_performance AS
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
    GROUP BY report_month, source_platform
),

current_run_ad_spend_inputs AS (
    SELECT
        DATE_FORMAT(TRY_CAST(date AS DATE), 'yyyy-MM') AS report_month,
        CASE
            WHEN LOWER(TRIM(COALESCE(source_platform, ''))) IN ('meta', 'facebook', 'fb', 'instagram', 'ig') THEN 'meta'
            WHEN LOWER(TRIM(COALESCE(source_platform, ''))) IN ('google', 'adwords', 'google_ads', 'googleads') THEN 'google'
            WHEN LOWER(TRIM(COALESCE(source_platform, ''))) IN ('tiktok', 'tik_tok', 'tt') THEN 'tiktok'
            WHEN LOWER(TRIM(COALESCE(source_platform, ''))) IN ('linkedin', 'li') THEN 'linkedin'
            ELSE LOWER(TRIM(COALESCE(source_platform, '')))
        END AS source_platform,
        COALESCE(TRY_CAST(spend AS DOUBLE), 0.0) AS spend
    FROM {schema}.ad_spend_normalized
    WHERE ingested_at >= CAST('{run_started_at}' AS TIMESTAMP)
      AND TRY_CAST(date AS DATE) IS NOT NULL
),

current_run_ad_spend_by_platform AS (
    SELECT
        report_month,
        source_platform,
        CASE
            WHEN source_platform = 'google' THEN 'Paid Search'
            WHEN source_platform IN ('meta', 'linkedin', 'tiktok') THEN 'Paid Social'
            ELSE source_platform
        END AS channel,
        SUM(spend) AS total_spend
    FROM current_run_ad_spend_inputs
    WHERE source_platform <> ''
    GROUP BY report_month, source_platform
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
    FULL OUTER JOIN current_run_ad_spend_by_platform s
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


CREATE OR REPLACE TABLE {schema}.channel_performance_v2 AS
WITH attributed_cash_by_platform AS (
    SELECT
        report_month,
        source_platform,
        SUM(attributed_revenue) AS net_collected_revenue,
        SUM(attributed_refunds) AS refunded_revenue
    FROM {schema}.attributed_revenue
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
