-- transforms/attribution_model_with_payments.sql
-- Creates channel_performance_v2 by joining Stripe payments to HubSpot deals.
-- Adds true revenue metrics on top of all existing channel_performance columns.
--
-- Prerequisites: channel_performance, hubspot_deals_raw, stripe_payments_raw
-- Run after: attribution_model.sql and ingest_flow both complete for the period
--
-- Usage (Python):
--   sql = open("transforms/attribution_model_with_payments.sql").read()
--   _run_sql(sql.format(schema="workspace.attribution_demo_client"))

CREATE OR REPLACE TABLE {schema}.channel_performance_v2 AS

WITH channel_base AS (
    SELECT * FROM {schema}.channel_performance
),

-- Replicate the same channel CASE logic from attribution_model.sql so
-- channel names match channel_base exactly when we JOIN below.
deals_with_channel AS (
    SELECT
        deal_id,
        contact_email,
        lead_create_date,
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

deal_payments AS (
    SELECT
        d.deal_id,
        d.channel,
        d.lead_create_date,
        SUM(CASE WHEN s.status = 'succeeded'
                 THEN COALESCE(s.amount_paid, 0) ELSE 0
            END)                                         AS collected_revenue,
        SUM(COALESCE(s.refund_amount, 0))               AS total_refunded,
        SUM(CASE
            WHEN s.status = 'succeeded'
             AND DATEDIFF(s.created_at, d.lead_create_date) <= 90
            THEN s.amount_paid ELSE 0
        END)                                             AS ltv_90day
    FROM deals_with_channel d
    LEFT JOIN {schema}.stripe_payments_raw s
        ON  LOWER(s.customer_email) = LOWER(d.contact_email)
        AND s.customer_email <> ''
        AND d.contact_email IS NOT NULL
    GROUP BY d.deal_id, d.channel, d.lead_create_date
),

channel_payments AS (
    SELECT
        channel,
        SUM(collected_revenue)                          AS collected_revenue,
        SUM(total_refunded)                             AS total_refunded,
        SUM(ltv_90day)                                  AS ltv_90day,
        CASE WHEN SUM(collected_revenue) > 0
             THEN SUM(total_refunded) / SUM(collected_revenue)
             ELSE 0.0
        END                                             AS refund_rate
    FROM deal_payments
    GROUP BY channel
)

SELECT
    b.report_month,
    b.channel,
    b.utm_campaign,
    b.utm_source,
    b.utm_medium,
    b.deals_count,
    b.pipeline_value,
    b.avg_deal_value,
    b.avg_days_to_close,
    b.total_spend,
    b.roi,
    b.cost_per_deal,
    COALESCE(p.collected_revenue, 0.0)                  AS collected_revenue,
    COALESCE(p.refund_rate,       0.0)                  AS refund_rate,
    COALESCE(p.ltv_90day,         0.0)                  AS ltv_90day,
    CASE WHEN b.total_spend > 0
         THEN COALESCE(p.collected_revenue, 0.0) / b.total_spend
         ELSE 0.0
    END                                                 AS true_roi,
    CASE WHEN COALESCE(p.collected_revenue, 0.0) > 0
         THEN b.total_spend / p.collected_revenue
         ELSE NULL
    END                                                 AS cost_per_collected_dollar,
    CURRENT_TIMESTAMP()                                 AS ingested_at
FROM channel_base b
LEFT JOIN channel_payments p
    ON b.channel = p.channel
