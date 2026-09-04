from __future__ import annotations

from agents.ingest.hubspot_connector import CONTACT_PROPERTIES, HubSpotConnector


def _deal(source: str, detail_1: str, detail_2: str) -> dict:
    return {
        "id": "deal-1",
        "properties": {
            "dealname": "Deal One",
            "dealstage": "closedwon",
            "pipeline": "default",
            "amount": "1000",
            "deal_currency_code": "usd",
            "closedate": "2026-08-15T12:00:00Z",
            "createdate": "2026-08-01T12:00:00Z",
            "hs_analytics_source": source,
            "hs_analytics_source_data_1": detail_1,
            "hs_analytics_source_data_2": detail_2,
        },
        "associations": {
            "contacts": {"results": [{"id": "contact-z"}, {"id": "contact-a"}]}
        },
    }


def test_contact_request_uses_only_portable_diagnostic_properties():
    assert not {
        "utm_campaign",
        "utm_source",
        "utm_medium",
        "utm_content",
    }.intersection(CONTACT_PROPERTIES)


def test_paid_social_identity_comes_from_deal_not_associated_contact():
    contacts = {
        "contact-a": {
            "email": "first@example.test",
            "utm_source": "linkedin",
            "utm_campaign": "wrong-contact-campaign",
        },
        "contact-z": {"email": "other@example.test"},
    }

    row = (
        HubSpotConnector()
        ._normalize([_deal("PAID_SOCIAL", "facebook", "launch")], contacts)
        .iloc[0]
    )

    assert row["contact_id"] == "contact-a"
    assert row["contact_email"] == "first@example.test"
    assert row["hs_source"] == "PAID_SOCIAL"
    assert row["utm_source"] == "facebook"
    assert row["utm_campaign"] == "launch"
    assert row["utm_medium"] == "paid_social"


def test_paid_search_uses_hubspot_campaign_detail_and_google_platform():
    row = (
        HubSpotConnector()
        ._normalize([_deal("PAID_SEARCH", "brand-campaign", "keyword")], {})
        .iloc[0]
    )

    assert row["utm_source"] == "google"
    assert row["utm_campaign"] == "brand-campaign"
    assert row["utm_medium"] == "paid_search"
    assert row["deal_currency_code"] == "USD"
