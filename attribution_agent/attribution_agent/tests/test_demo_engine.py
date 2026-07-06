from datetime import datetime

import pytest

from demo.demo_engine import CLOSED_WON_STATUS, DemoEngine
from demo.demo_store import DemoStore


@pytest.fixture()
def engine():
    store = DemoStore()
    yield DemoEngine(store)
    store.close()


def _seed_two_touch_journey(engine):
    engine.log_click(
        anon_id="anon_881",
        utm_source="google",
        utm_campaign="seo_guide",
        channel="Organic SEO",
        occurred_at=datetime(2026, 7, 1, 9, 0),
    )
    engine.log_click(
        anon_id="anon_881",
        utm_source="instagram",
        utm_campaign="july_promo",
        channel="Instagram Promo",
        occurred_at=datetime(2026, 7, 4, 19, 0),
    )
    engine.submit_lead_form(
        anon_id="anon_881",
        email="ceo@techcorp.com",
        occurred_at=datetime(2026, 7, 4, 19, 5),
    )


def test_clicks_are_logged_in_chronological_order(engine):
    _seed_two_touch_journey(engine)
    clicks = engine.store.click_logs()
    assert [c.utm_source for c in clicks] == ["google", "instagram"]
    assert clicks[0].occurred_at < clicks[1].occurred_at


def test_lead_form_resolves_anonymous_history_to_email(engine):
    _seed_two_touch_journey(engine)
    journey = engine.journey_for_email("ceo@techcorp.com")
    assert [c.anon_id for c in journey] == ["anon_881", "anon_881"]


def test_email_matching_is_case_insensitive(engine):
    _seed_two_touch_journey(engine)
    result = engine.receive_deal_webhook(
        email="CEO@TechCorp.com",
        deal_amount=15_000.0,
        occurred_at=datetime(2026, 7, 5, 16, 0),
    )
    assert result.matched


def test_closed_won_webhook_backfills_full_timeline(engine):
    _seed_two_touch_journey(engine)
    result = engine.receive_deal_webhook(
        email="ceo@techcorp.com",
        deal_amount=15_000.0,
        occurred_at=datetime(2026, 7, 5, 16, 0),
        model="linear",
    )
    assert result.matched
    assert len(result.timeline) == 2
    assert [item.credit for item in result.timeline] == [0.5, 0.5]
    assert result.attributed_total == pytest.approx(15_000.0)


def test_u_shape_two_touch_splits_evenly(engine):
    _seed_two_touch_journey(engine)
    result = engine.receive_deal_webhook(
        email="ceo@techcorp.com",
        deal_amount=15_000.0,
        occurred_at=datetime(2026, 7, 5, 16, 0),
        model="u_shape",
    )
    assert [item.attributed_revenue for item in result.timeline] == [7_500.0, 7_500.0]


def test_non_won_deal_records_event_but_attributes_nothing(engine):
    _seed_two_touch_journey(engine)
    result = engine.receive_deal_webhook(
        email="ceo@techcorp.com",
        deal_amount=15_000.0,
        status="OPEN",
        occurred_at=datetime(2026, 7, 5, 16, 0),
    )
    assert not result.matched
    assert engine.store.deal_events()[-1].status == "OPEN"


def test_deal_with_no_click_history_yields_empty_timeline(engine):
    result = engine.receive_deal_webhook(
        email="stranger@nowhere.com",
        deal_amount=5_000.0,
        occurred_at=datetime(2026, 7, 5, 16, 0),
    )
    assert not result.matched
    assert result.attributed_total == 0.0


def test_scripted_journey_closes_the_loop_end_to_end(engine):
    result = engine.run_scripted_journey(model="u_shape")
    assert result.deal.email == "ceo@techcorp.com"
    assert result.deal.status == CLOSED_WON_STATUS
    assert len(result.timeline) == 2
    assert result.attributed_total == pytest.approx(15_000.0)
    channels = [item.click.channel for item in result.timeline]
    assert channels == ["Organic SEO", "Instagram Promo"]


def test_reset_clears_all_tables(engine):
    engine.run_scripted_journey()
    engine.reset()
    assert engine.store.click_logs() == []
    assert engine.store.identity_links() == []
    assert engine.store.deal_events() == []
