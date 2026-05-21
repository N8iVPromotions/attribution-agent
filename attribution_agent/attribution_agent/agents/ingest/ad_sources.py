"""
Shared normalized ad source schema.

Meta, Google Ads, and LinkedIn Ads each expose different field names. The
pipeline lands source-specific raw tables and also writes this normalized shape
so attribution SQL can reason about spend and touchpoints consistently.
"""
from __future__ import annotations

import pandas as pd

NORMALIZED_AD_COLUMNS = [
    "client_id",
    "source_platform",
    "account_id",
    "campaign_id",
    "campaign_name",
    "ad_group_id",
    "ad_group_name",
    "ad_id",
    "ad_name",
    "date",
    "spend",
    "impressions",
    "clicks",
    "conversions",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "landing_url",
]


def empty_normalized_ads() -> pd.DataFrame:
    return pd.DataFrame(columns=NORMALIZED_AD_COLUMNS)


def normalize_meta_ads(df: pd.DataFrame, client_id: str) -> pd.DataFrame:
    if df is None or df.empty:
        return empty_normalized_ads()
    out = pd.DataFrame({
        "client_id": client_id,
        "source_platform": "meta",
        "account_id": df.get("ad_account_id", ""),
        "campaign_id": df.get("campaign_id", ""),
        "campaign_name": df.get("campaign_name", ""),
        "ad_group_id": df.get("adset_id", ""),
        "ad_group_name": df.get("adset_name", ""),
        "ad_id": "",
        "ad_name": "",
        "date": pd.to_datetime(df["date"], errors="coerce"),
        "spend": pd.to_numeric(df.get("spend", 0), errors="coerce").fillna(0),
        "impressions": pd.to_numeric(df.get("impressions", 0), errors="coerce").fillna(0).astype("int64"),
        "clicks": pd.to_numeric(df.get("clicks", 0), errors="coerce").fillna(0).astype("int64"),
        "conversions": _sum_conversion_columns(df),
        "utm_source": "facebook",
        "utm_medium": "paid_social",
        "utm_campaign": df.get("campaign_name", ""),
        "landing_url": "",
    })
    return out[NORMALIZED_AD_COLUMNS]


def normalize_google_ads(df: pd.DataFrame, client_id: str) -> pd.DataFrame:
    if df is None or df.empty:
        return empty_normalized_ads()
    out = pd.DataFrame({
        "client_id": client_id,
        "source_platform": "google",
        "account_id": df.get("customer_id", ""),
        "campaign_id": df.get("campaign_id", ""),
        "campaign_name": df.get("campaign_name", ""),
        "ad_group_id": df.get("ad_group_id", ""),
        "ad_group_name": df.get("ad_group_name", ""),
        "ad_id": df.get("ad_id", ""),
        "ad_name": df.get("ad_name", ""),
        "date": pd.to_datetime(df["date"], errors="coerce"),
        "spend": pd.to_numeric(df.get("spend", 0), errors="coerce").fillna(0),
        "impressions": pd.to_numeric(df.get("impressions", 0), errors="coerce").fillna(0).astype("int64"),
        "clicks": pd.to_numeric(df.get("clicks", 0), errors="coerce").fillna(0).astype("int64"),
        "conversions": pd.to_numeric(df.get("conversions", 0), errors="coerce").fillna(0),
        "utm_source": "google",
        "utm_medium": "paid_search",
        "utm_campaign": df.get("campaign_name", ""),
        "landing_url": df.get("landing_url", ""),
    })
    return out[NORMALIZED_AD_COLUMNS]


def normalize_linkedin_ads(df: pd.DataFrame, client_id: str) -> pd.DataFrame:
    if df is None or df.empty:
        return empty_normalized_ads()
    out = pd.DataFrame({
        "client_id": client_id,
        "source_platform": "linkedin",
        "account_id": df.get("account_id", ""),
        "campaign_id": df.get("campaign_id", ""),
        "campaign_name": df.get("campaign_name", ""),
        "ad_group_id": df.get("creative_id", ""),
        "ad_group_name": df.get("creative_name", ""),
        "ad_id": df.get("creative_id", ""),
        "ad_name": df.get("creative_name", ""),
        "date": pd.to_datetime(df["date"], errors="coerce"),
        "spend": pd.to_numeric(df.get("spend", 0), errors="coerce").fillna(0),
        "impressions": pd.to_numeric(df.get("impressions", 0), errors="coerce").fillna(0).astype("int64"),
        "clicks": pd.to_numeric(df.get("clicks", 0), errors="coerce").fillna(0).astype("int64"),
        "conversions": pd.to_numeric(df.get("conversions", 0), errors="coerce").fillna(0),
        "utm_source": "linkedin",
        "utm_medium": "paid_social",
        "utm_campaign": df.get("campaign_name", ""),
        "landing_url": "",
    })
    return out[NORMALIZED_AD_COLUMNS]


def combine_normalized_ads(frames: list[pd.DataFrame]) -> pd.DataFrame:
    valid_frames = [frame for frame in frames if frame is not None and not frame.empty]
    if not valid_frames:
        return empty_normalized_ads()
    return pd.concat(valid_frames, ignore_index=True)[NORMALIZED_AD_COLUMNS]


def _sum_conversion_columns(df: pd.DataFrame) -> pd.Series:
    conversion_cols = [col for col in df.columns if col.startswith("conversions")]
    if not conversion_cols:
        return pd.Series([0] * len(df), index=df.index, dtype="float64")
    return df[conversion_cols].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)

