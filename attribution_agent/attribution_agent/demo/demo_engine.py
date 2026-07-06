"""
demo_engine.py — closed-loop lifecycle simulator for the demo environment.

Implements the four-step demo blueprint from the Unified Strategic Playbook
(docs/DEMO_PLAYBOOK.md, section 3):

  1. Traffic acquisition — an anonymous click is logged with UTM parameters.
  2. Nurturing — a return visit updates the first-party identity graph.
  3. Conversion event — a lead form links the anonymous history to an email.
  4. CRM closed-won — a mock Stripe/HubSpot webhook fires, the engine matches
     the deal email back to the click history, and the marketing timeline is
     backfilled with fractional revenue credit.

Credit allocation reuses attribution_models.allocate_credit so the demo shows
the same multi-touch models the production pipeline uses. Pure Python — unit
testable with an in-memory DemoStore.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from attribution_models import (
    SUPPORTED_ATTRIBUTION_MODELS,
    Touchpoint,
    allocate_credit,
)
from demo.demo_store import ClickLog, DealEvent, DemoStore

CLOSED_WON_STATUS = "CLOSED_WON"

__all__ = [
    "CLOSED_WON_STATUS",
    "SUPPORTED_ATTRIBUTION_MODELS",
    "AttributedClick",
    "ClosedLoopResult",
    "DemoEngine",
]


@dataclass(frozen=True)
class AttributedClick:
    """A historical click bound to a share of closed revenue."""

    click: ClickLog
    credit: float
    attributed_revenue: float


@dataclass(frozen=True)
class ClosedLoopResult:
    """The backfilled marketing timeline for one closed-won deal."""

    deal: DealEvent
    model: str
    timeline: list[AttributedClick]

    @property
    def matched(self) -> bool:
        return bool(self.timeline)

    @property
    def attributed_total(self) -> float:
        return sum(item.attributed_revenue for item in self.timeline)


class DemoEngine:
    """Drives the mock closed-loop lifecycle against a DemoStore."""

    def __init__(self, store: DemoStore) -> None:
        self.store = store

    # ── lifecycle events ──────────────────────────────────────

    def log_click(
        self,
        *,
        anon_id: str,
        utm_source: str,
        utm_campaign: str,
        channel: str,
        page: str = "",
        occurred_at: datetime,
    ) -> None:
        """Touchpoint logging: the ARIE click script writing to the database."""
        self.store.insert_click(
            anon_id=anon_id,
            utm_source=utm_source,
            utm_campaign=utm_campaign,
            channel=channel,
            page=page,
            occurred_at=occurred_at,
        )

    def submit_lead_form(
        self, *, anon_id: str, email: str, occurred_at: datetime
    ) -> None:
        """Identity resolution: bind the anonymous history to a known email."""
        self.store.upsert_identity(
            anon_id=anon_id, resolved_email=email, updated_at=occurred_at
        )

    def receive_deal_webhook(
        self,
        *,
        email: str,
        deal_amount: float,
        status: str = CLOSED_WON_STATUS,
        occurred_at: datetime,
        model: str = "u_shape",
    ) -> ClosedLoopResult:
        """
        Consume a mock CRM 'Deal Won' webhook and close the loop.

        The deal event is recorded, and if the status is closed-won the engine
        immediately backfills the marketing timeline for the deal's email.
        Non-won statuses record the event but attribute nothing.
        """
        self.store.insert_deal_event(
            email=email, deal_amount=deal_amount, status=status, occurred_at=occurred_at
        )
        deal = self.store.deal_events(email=email)[-1]
        if status != CLOSED_WON_STATUS:
            return ClosedLoopResult(deal=deal, model=model, timeline=[])
        return self.close_the_loop(deal, model=model)

    # ── matching + backfill ───────────────────────────────────

    def journey_for_email(self, email: str) -> list[ClickLog]:
        """All logged clicks reachable from an email via the identity map."""
        anon_ids = [link.anon_id for link in self.store.identity_links(email=email)]
        return self.store.click_logs(anon_ids=anon_ids)

    def close_the_loop(self, deal: DealEvent, *, model: str) -> ClosedLoopResult:
        """Match a closed-won deal to its click history and allocate revenue."""
        journey = self.journey_for_email(deal.email)
        touchpoints = [
            Touchpoint(
                touchpoint_id=str(click.click_id),
                occurred_at=click.occurred_at,
                channel=click.channel,
                campaign=click.utm_campaign,
                source_platform=click.utm_source,
            )
            for click in journey
        ]
        allocated = allocate_credit(touchpoints, model)
        clicks_by_id = {str(click.click_id): click for click in journey}
        timeline = [
            AttributedClick(
                click=clicks_by_id[item.touchpoint.touchpoint_id],
                credit=item.credit,
                attributed_revenue=round(deal.deal_amount * item.credit, 2),
            )
            for item in allocated
        ]
        return ClosedLoopResult(deal=deal, model=model, timeline=timeline)

    # ── scripted demo journey ─────────────────────────────────

    def run_scripted_journey(self, *, model: str = "u_shape") -> ClosedLoopResult:
        """
        Replay the playbook's fictional journey end to end:

        anon_881 clicks an organic SEO blog post, returns via an Instagram
        promo three days later, submits a consultation form as
        ceo@techcorp.com, and a $15,000 CLOSED_WON webhook closes the loop.
        """
        self.log_click(
            anon_id="anon_881",
            utm_source="google",
            utm_campaign="seo_guide",
            channel="Organic SEO",
            page="/blog/closed-loop-attribution-guide",
            occurred_at=datetime(2026, 7, 1, 9, 14),
        )
        self.log_click(
            anon_id="anon_881",
            utm_source="instagram",
            utm_campaign="july_promo",
            channel="Instagram Promo",
            page="/services",
            occurred_at=datetime(2026, 7, 4, 19, 42),
        )
        self.submit_lead_form(
            anon_id="anon_881",
            email="ceo@techcorp.com",
            occurred_at=datetime(2026, 7, 4, 19, 51),
        )
        return self.receive_deal_webhook(
            email="ceo@techcorp.com",
            deal_amount=15_000.0,
            occurred_at=datetime(2026, 7, 5, 16, 30),
            model=model,
        )

    def reset(self) -> None:
        self.store.reset()
