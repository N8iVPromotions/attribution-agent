-- One-time migration for Databricks Runtime 18.1 or newer.
-- Replace {{schema}} with each active client's catalog.schema identifier.
ALTER TABLE {{schema}}.ad_spend_normalized
SET TBLPROPERTIES ('delta.enableDeletionVectors' = 'true');
ALTER TABLE {{schema}}.ad_spend_normalized
REPLACE PARTITIONED BY WITH CLUSTER BY (date, campaign_id);

ALTER TABLE {{schema}}.meta_ads_raw
SET TBLPROPERTIES ('delta.enableDeletionVectors' = 'true');
ALTER TABLE {{schema}}.meta_ads_raw
REPLACE PARTITIONED BY WITH CLUSTER BY (date, campaign_id);

-- Rewrite files once after changing clustering keys.
OPTIMIZE {{schema}}.ad_spend_normalized FULL;
OPTIMIZE {{schema}}.meta_ads_raw FULL;
