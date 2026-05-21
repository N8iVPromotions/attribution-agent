-- Builds attributed closed-revenue tables for a client schema.
--
-- Template parameters:
--   {schema}             Client schema, e.g. workspace.attribution_demo_client
--   {attribution_model}  last_touch | first_touch | linear | time_decay | u_shape | w_shape

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
        deal_id,
        COALESCE(deal_name, deal_id) AS deal_name,
        COALESCE(amount, 0.0) AS pipeline_value,
        COALESCE(close_date, create_date) AS close_date,
        create_date,
        contact_email,
        hs_source,
        hs_source_label,
        hs_source_detail_1,
        hs_source_detail_2,
        utm_campaign,
        utm_source,
        utm_medium,
        lead_create_date,
        days_to_deal
    FROM {schema}.hubspot_deals_raw
    WHERE COALESCE(amount, 0.0) > 0
),

payments_by_deal AS (
    SELECT
        d.deal_id,
        SUM(CASE WHEN s.status = 'succeeded' THEN COALESCE(s.amount_paid, 0.0) ELSE 0.0 END) AS collected_revenue,
        SUM(COALESCE(s.refund_amount, 0.0)) AS refunded_revenue
    FROM closed_deals d
    LEFT JOIN {schema}.stripe_payments_raw s
        ON LOWER(s.customer_email) = LOWER(d.contact_email)
        AND s.customer_email <> ''
        AND d.contact_email IS NOT NULL
    GROUP BY d.deal_id
),

deal_touchpoints AS (
    SELECT
        deal_id,
        close_date,
        pipeline_value,
        COALESCE(lead_create_date, create_date, close_date) AS touchpoint_at,
        'first_touch' AS touchpoint_role,
        CASE
            WHEN hs_source = 'PAID_SOCIAL' THEN 'Paid Social'
            WHEN hs_source = 'PAID_SEARCH' THEN 'Paid Search'
            WHEN hs_source = 'ORGANIC_SEARCH' THEN 'Organic Search'
            WHEN hs_source = 'EMAIL_MARKETING' THEN 'Email'
            WHEN hs_source = 'DIRECT_TRAFFIC' THEN 'Direct'
            WHEN hs_source_label IS NOT NULL AND hs_source_label <> '' THEN hs_source_label
            ELSE 'Unattributed'
        END AS channel,
        COALESCE(hs_source_detail_2, hs_source_detail_1, 'unknown') AS campaign,
        CASE
            WHEN LOWER(COALESCE(hs_source_detail_1, '')) LIKE '%linkedin%' THEN 'linkedin'
            WHEN LOWER(COALESCE(hs_source_detail_1, '')) LIKE '%google%' THEN 'google'
            WHEN LOWER(COALESCE(hs_source_detail_1, '')) LIKE '%facebook%'
              OR LOWER(COALESCE(hs_source_detail_1, '')) LIKE '%instagram%' THEN 'meta'
            ELSE 'crm'
        END AS source_platform
    FROM closed_deals

    UNION ALL

    SELECT
        deal_id,
        close_date,
        pipeline_value,
        COALESCE(create_date, close_date) AS touchpoint_at,
        'lead_creation' AS touchpoint_role,
        CASE
            WHEN LOWER(COALESCE(utm_medium, '')) IN ('cpc', 'paid', 'ppc', 'paid_search') THEN 'Paid Search'
            WHEN LOWER(COALESCE(utm_medium, '')) IN ('paid_social', 'social_paid') THEN 'Paid Social'
            WHEN LOWER(COALESCE(utm_medium, '')) = 'email' THEN 'Email'
            WHEN LOWER(COALESCE(utm_medium, '')) = 'social' THEN 'Organic Social'
            WHEN LOWER(COALESCE(utm_source, '')) LIKE '%linkedin%' THEN 'Paid Social'
            WHEN LOWER(COALESCE(utm_source, '')) LIKE '%facebook%'
              OR LOWER(COALESCE(utm_source, '')) LIKE '%instagram%' THEN 'Paid Social'
            WHEN LOWER(COALESCE(utm_source, '')) LIKE '%google%' THEN 'Paid Search'
            ELSE 'Unattributed'
        END AS channel,
        COALESCE(utm_campaign, hs_source_detail_2, 'unknown') AS campaign,
        CASE
            WHEN LOWER(COALESCE(utm_source, '')) LIKE '%linkedin%' THEN 'linkedin'
            WHEN LOWER(COALESCE(utm_source, '')) LIKE '%google%' THEN 'google'
            WHEN LOWER(COALESCE(utm_source, '')) LIKE '%facebook%'
              OR LOWER(COALESCE(utm_source, '')) LIKE '%instagram%' THEN 'meta'
            ELSE 'crm'
        END AS source_platform
    FROM closed_deals

    UNION ALL

    SELECT
        deal_id,
        close_date,
        pipeline_value,
        COALESCE(close_date, create_date) AS touchpoint_at,
        'close_touch' AS touchpoint_role,
        CASE
            WHEN LOWER(COALESCE(utm_medium, '')) IN ('cpc', 'paid', 'ppc', 'paid_search') THEN 'Paid Search'
            WHEN LOWER(COALESCE(utm_medium, '')) IN ('paid_social', 'social_paid') THEN 'Paid Social'
            WHEN LOWER(COALESCE(utm_medium, '')) = 'email' THEN 'Email'
            WHEN hs_source = 'DIRECT_TRAFFIC' THEN 'Direct'
            ELSE 'Unattributed'
        END AS channel,
        COALESCE(utm_campaign, hs_source_detail_2, 'unknown') AS campaign,
        CASE
            WHEN LOWER(COALESCE(utm_source, '')) LIKE '%linkedin%' THEN 'linkedin'
            WHEN LOWER(COALESCE(utm_source, '')) LIKE '%google%' THEN 'google'
            WHEN LOWER(COALESCE(utm_source, '')) LIKE '%facebook%'
              OR LOWER(COALESCE(utm_source, '')) LIKE '%instagram%' THEN 'meta'
            ELSE 'crm'
        END AS source_platform
    FROM closed_deals
),

deduped_touchpoints AS (
    SELECT DISTINCT
        deal_id,
        close_date,
        pipeline_value,
        touchpoint_at,
        touchpoint_role,
        channel,
        campaign,
        source_platform
    FROM deal_touchpoints
    WHERE channel <> 'Unattributed' OR touchpoint_role = 'close_touch'
),

scored AS (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY deal_id ORDER BY touchpoint_at, touchpoint_role) AS touch_index,
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
        *,
        raw_credit / NULLIF(SUM(raw_credit) OVER (PARTITION BY deal_id), 0) AS credit
    FROM weighted
),

ad_spend AS (
    SELECT
        DATE_FORMAT(date, 'yyyy-MM') AS report_month,
        CASE
            WHEN source_platform = 'google' THEN 'Paid Search'
            WHEN source_platform IN ('meta', 'linkedin') THEN 'Paid Social'
            ELSE source_platform
        END AS channel,
        COALESCE(utm_campaign, campaign_name, 'unknown') AS campaign,
        source_platform,
        SUM(COALESCE(spend, 0.0)) AS total_spend
    FROM {schema}.ad_spend_normalized
    GROUP BY DATE_FORMAT(date, 'yyyy-MM'), channel, COALESCE(utm_campaign, campaign_name, 'unknown'), source_platform
)

SELECT
    DATE_FORMAT(COALESCE(n.close_date, CURRENT_DATE()), 'yyyy-MM') AS report_month,
    '{attribution_model}' AS attribution_model,
    n.deal_id,
    n.channel,
    n.campaign,
    n.source_platform,
    n.touchpoint_role,
    COALESCE(n.credit, 0.0) AS credit,
    n.pipeline_value * COALESCE(n.credit, 0.0) AS attributed_pipeline,
    COALESCE(p.collected_revenue, 0.0) * COALESCE(n.credit, 0.0) AS attributed_revenue,
    COALESCE(p.refunded_revenue, 0.0) * COALESCE(n.credit, 0.0) AS attributed_refunds,
    COALESCE(a.total_spend, 0.0) AS total_spend,
    CURRENT_TIMESTAMP() AS ingested_at
FROM normalized n
LEFT JOIN payments_by_deal p ON n.deal_id = p.deal_id
LEFT JOIN ad_spend a
    ON DATE_FORMAT(COALESCE(n.close_date, CURRENT_DATE()), 'yyyy-MM') = a.report_month
    AND n.channel = a.channel
    AND n.source_platform = a.source_platform;


CREATE OR REPLACE TABLE {schema}.channel_performance AS
SELECT
    report_month,
    channel,
    campaign AS utm_campaign,
    source_platform AS utm_source,
    '' AS utm_medium,
    COUNT(DISTINCT deal_id) AS deals_count,
    SUM(attributed_pipeline) AS pipeline_value,
    CASE WHEN COUNT(DISTINCT deal_id) > 0
         THEN SUM(attributed_pipeline) / COUNT(DISTINCT deal_id)
         ELSE 0.0
    END AS avg_deal_value,
    CAST(NULL AS DOUBLE) AS avg_days_to_close,
    MAX(total_spend) AS total_spend,
    CASE WHEN MAX(total_spend) > 0
         THEN SUM(attributed_pipeline) / MAX(total_spend)
         ELSE 0.0
    END AS roi,
    CASE WHEN COUNT(DISTINCT deal_id) > 0
         THEN MAX(total_spend) / COUNT(DISTINCT deal_id)
         ELSE NULL
    END AS cost_per_deal,
    CURRENT_TIMESTAMP() AS ingested_at
FROM {schema}.attributed_revenue
GROUP BY report_month, channel, campaign, source_platform;


CREATE OR REPLACE TABLE {schema}.channel_performance_v2 AS
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
    SUM(attributed_revenue) AS collected_revenue,
    CASE WHEN SUM(attributed_revenue) > 0
         THEN SUM(attributed_refunds) / SUM(attributed_revenue)
         ELSE 0.0
    END AS refund_rate,
    SUM(attributed_revenue) AS ltv_90day,
    CASE WHEN cp.total_spend > 0
         THEN SUM(attributed_revenue) / cp.total_spend
         ELSE 0.0
    END AS true_roi,
    CASE WHEN SUM(attributed_revenue) > 0
         THEN cp.total_spend / SUM(attributed_revenue)
         ELSE NULL
    END AS cost_per_collected_dollar,
    CURRENT_TIMESTAMP() AS ingested_at
FROM {schema}.channel_performance cp
LEFT JOIN {schema}.attributed_revenue ar
    ON cp.report_month = ar.report_month
    AND cp.channel = ar.channel
    AND cp.utm_campaign = ar.campaign
    AND cp.utm_source = ar.source_platform
GROUP BY
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
    cp.cost_per_deal
