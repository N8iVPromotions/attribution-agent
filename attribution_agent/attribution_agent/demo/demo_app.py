"""
demo_app.py — customer-facing closed-loop demo control panel.

The lightweight UI control panel from the Unified Strategic Playbook
(docs/DEMO_PLAYBOOK.md, section 4): trigger each lifecycle step live in front
of a prospect, watch the mock database tables update, and show the deal-won
webhook backfilling the marketing timeline with fractional revenue credit.

Run it standalone (separate from the internal Vercel Command Center):

    cd attribution_agent/attribution_agent
    streamlit run demo/demo_app.py

Everything is in-memory and mock — no Databricks, no live APIs, no secrets.
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from attribution_models import ATTRIBUTION_MODEL_LABELS
from demo.demo_engine import CLOSED_WON_STATUS, DemoEngine
from demo.demo_store import DemoStore

st.set_page_config(
    page_title="ARIE — Closed-Loop Attribution Demo",
    page_icon="◆",
    layout="wide",
)

DEMO_EMAIL = "ceo@techcorp.com"
DEMO_ANON_ID = "anon_881"
DEMO_DEAL_AMOUNT = 15_000.0


def _engine() -> DemoEngine:
    if "demo_engine" not in st.session_state:
        st.session_state.demo_engine = DemoEngine(DemoStore())
    return st.session_state.demo_engine


engine = _engine()

st.title("◆ ARIE — Automatic Revenue Intelligence Engine")
st.caption(
    "Live closed-loop demo: anonymous click → identity resolution → "
    "CRM deal-won webhook → backfilled revenue attribution. "
    "All data below is mock and self-contained."
)

# ── Control panel ─────────────────────────────────────────────
st.subheader("Control panel")
model_key = st.selectbox(
    "Attribution model",
    options=list(ATTRIBUTION_MODEL_LABELS),
    format_func=ATTRIBUTION_MODEL_LABELS.get,
    index=list(ATTRIBUTION_MODEL_LABELS).index("u_shape"),
)

col1, col2, col3, col4 = st.columns(4)
if col1.button("1 · SEO blog click", use_container_width=True):
    engine.log_click(
        anon_id=DEMO_ANON_ID,
        utm_source="google",
        utm_campaign="seo_guide",
        channel="Organic SEO",
        page="/blog/closed-loop-attribution-guide",
        occurred_at=datetime(2026, 7, 1, 9, 14),
    )
    st.toast("Touchpoint 1 logged: organic SEO click (anon_881)")
if col2.button("2 · Instagram return visit", use_container_width=True):
    engine.log_click(
        anon_id=DEMO_ANON_ID,
        utm_source="instagram",
        utm_campaign="july_promo",
        channel="Instagram Promo",
        page="/services",
        occurred_at=datetime(2026, 7, 4, 19, 42),
    )
    st.toast("Touchpoint 2 logged: Instagram promo return visit")
if col3.button("3 · Submit consultation form", use_container_width=True):
    engine.submit_lead_form(
        anon_id=DEMO_ANON_ID,
        email=DEMO_EMAIL,
        occurred_at=datetime(2026, 7, 4, 19, 51),
    )
    st.toast(f"Identity resolved: {DEMO_ANON_ID} → {DEMO_EMAIL}")
if col4.button(
    "4 · Fire Deal-Won webhook ($15,000)", type="primary", use_container_width=True
):
    engine.receive_deal_webhook(
        email=DEMO_EMAIL,
        deal_amount=DEMO_DEAL_AMOUNT,
        occurred_at=datetime(2026, 7, 5, 16, 30),
        model=model_key,
    )
    st.toast("CLOSED_WON webhook received — loop closed")

col_run, col_reset = st.columns([1, 1])
if col_run.button("▶ Run full scripted journey", use_container_width=True):
    engine.run_scripted_journey(model=model_key)
    st.toast("Full journey replayed: 2 touchpoints, form fill, $15,000 close")
if col_reset.button("↺ Reset demo data", use_container_width=True):
    engine.reset()
    st.toast("Demo tables cleared")

# ── Live database view ────────────────────────────────────────
st.subheader("Live mock database")
clicks = engine.store.click_logs()
identities = engine.store.identity_links()
deals = engine.store.deal_events()

tab_clicks, tab_identity, tab_deals = st.tabs(
    ["arie_click_logs", "arie_identity_map", "crm_deal_events"]
)
with tab_clicks:
    if clicks:
        st.dataframe(
            pd.DataFrame(
                {
                    "anon_id": c.anon_id,
                    "utm_source": c.utm_source,
                    "utm_campaign": c.utm_campaign,
                    "channel": c.channel,
                    "page": c.page,
                    "occurred_at": c.occurred_at,
                }
                for c in clicks
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No clicks logged yet — start with step 1.")
with tab_identity:
    if identities:
        st.dataframe(
            pd.DataFrame(
                {
                    "anon_id": i.anon_id,
                    "resolved_email": i.resolved_email,
                    "updated_at": i.updated_at,
                }
                for i in identities
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No identities resolved yet — the visitor is still anonymous.")
with tab_deals:
    if deals:
        st.dataframe(
            pd.DataFrame(
                {
                    "email": d.email,
                    "deal_amount": d.deal_amount,
                    "status": d.status,
                    "occurred_at": d.occurred_at,
                }
                for d in deals
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No CRM deal events yet — fire the webhook in step 4.")

# ── Closed-loop attribution timeline ──────────────────────────
st.subheader("Closed-loop attribution timeline")
won_deals = [d for d in deals if d.status == CLOSED_WON_STATUS]
if not won_deals:
    st.info(
        "Waiting for a CLOSED_WON deal. When the webhook fires, ARIE matches the "
        "deal email back through the identity graph and backfills every logged "
        "touchpoint with its share of the revenue."
    )
else:
    for deal in won_deals:
        result = engine.close_the_loop(deal, model=model_key)
        st.markdown(
            f"**${deal.deal_amount:,.0f} · {deal.email} · "
            f"{ATTRIBUTION_MODEL_LABELS[result.model]} model**"
        )
        if not result.matched:
            st.warning(
                "No click history matched this deal — the revenue is real, but "
                "the journey is invisible. This is the blind spot ARIE removes."
            )
            continue
        st.dataframe(
            pd.DataFrame(
                {
                    "occurred_at": item.click.occurred_at,
                    "channel": item.click.channel,
                    "utm_source": item.click.utm_source,
                    "utm_campaign": item.click.utm_campaign,
                    "credit": f"{item.credit:.0%}",
                    "attributed_revenue": f"${item.attributed_revenue:,.2f}",
                }
                for item in result.timeline
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            f"Attributed total: \\${result.attributed_total:,.2f} of "
            f"\\${deal.deal_amount:,.2f} — every marketing dollar mapped to a "
            "closed deal."
        )
