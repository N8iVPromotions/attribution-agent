import pandas as pd

from agents.ingest.ad_sources import (
    combine_normalized_ads,
    normalize_google_ads,
    normalize_linkedin_ads,
    normalize_meta_ads,
)


def test_normalize_meta_ads_maps_to_shared_schema():
    df = pd.DataFrame(
        [
            {
                "ad_account_id": "act_1",
                "campaign_id": "c1",
                "campaign_name": "Meta Campaign",
                "adset_id": "a1",
                "adset_name": "Ad Set",
                "date": "2026-01-01",
                "spend": "10.5",
                "impressions": "100",
                "clicks": "5",
                "conversions_lead": 2,
            }
        ]
    )
    out = normalize_meta_ads(df, "client-a")
    assert out.loc[0, "client_id"] == "client-a"
    assert out.loc[0, "source_platform"] == "meta"
    assert out.loc[0, "utm_source"] == "facebook"
    assert out.loc[0, "conversions"] == 2


def test_normalize_google_ads_maps_to_shared_schema():
    df = pd.DataFrame(
        [
            {
                "customer_id": "123",
                "campaign_id": "g1",
                "campaign_name": "Search",
                "ad_group_id": "ag1",
                "ad_group_name": "Group",
                "date": "2026-01-01",
                "spend": 20,
                "impressions": 200,
                "clicks": 10,
                "conversions": 1,
            }
        ]
    )
    out = normalize_google_ads(df, "client-a")
    assert out.loc[0, "source_platform"] == "google"
    assert out.loc[0, "utm_medium"] == "paid_search"


def test_normalize_linkedin_ads_maps_to_shared_schema():
    df = pd.DataFrame(
        [
            {
                "account_id": "999",
                "campaign_id": "l1",
                "campaign_name": "LinkedIn Campaign",
                "date": "2026-01-01",
                "spend": 30,
                "impressions": 300,
                "clicks": 15,
                "conversions": 3,
            }
        ]
    )
    out = normalize_linkedin_ads(df, "client-a")
    assert out.loc[0, "source_platform"] == "linkedin"
    assert out.loc[0, "utm_source"] == "linkedin"


def test_combine_normalized_ads_ignores_empty_frames():
    meta = normalize_meta_ads(pd.DataFrame(), "client-a")
    google = normalize_google_ads(
        pd.DataFrame(
            [
                {
                    "customer_id": "123",
                    "campaign_id": "g1",
                    "campaign_name": "Search",
                    "ad_group_id": "ag1",
                    "ad_group_name": "Group",
                    "date": "2026-01-01",
                    "spend": 20,
                    "impressions": 200,
                    "clicks": 10,
                    "conversions": 1,
                }
            ]
        ),
        "client-a",
    )
    out = combine_normalized_ads([meta, google])
    assert len(out) == 1
    assert out.loc[0, "source_platform"] == "google"
