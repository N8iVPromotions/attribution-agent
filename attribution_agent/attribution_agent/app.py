"""
app.py
------
Streamlit UI for running the attribution pipeline.

Launch:
    cd attribution_agent/attribution_agent
    streamlit run app.py
"""
import logging
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from config.agency_config import get_agency, list_agencies
from config.client_config import get_client
from flows.agency_flow import run_agency_pipeline

# ── Page config ───────────────────────────────────────────────
st.set_page_config(
    page_title="Attribution Pipeline",
    page_icon="📊",
    layout="centered",
)

st.title("📊 Attribution Pipeline")
st.caption("Ingest → Insights → Email")

# ── Sidebar controls ──────────────────────────────────────────
with st.sidebar:
    st.header("Run Settings")

    agencies = list_agencies()
    agency_id = st.selectbox("Agency", agencies)
    agency = get_agency(agency_id)

    client_options = agency.client_ids
    client_filter = st.multiselect(
        "Clients",
        client_options,
        default=client_options,
        help="Leave all selected to run every client in the agency.",
    )

    dry_run = st.checkbox(
        "Dry run",
        help="Generates the report but skips sending the email.",
    )

    st.divider()
    run_btn = st.button("▶ Run Pipeline", type="primary", use_container_width=True)

# ── Main area ─────────────────────────────────────────────────
if not run_btn:
    # Preview what will run
    st.subheader("Ready to run")
    col1, col2 = st.columns(2)
    col1.markdown(f"**Agency:** `{agency_id}`")
    col2.markdown(f"**Clients:** `{', '.join(client_filter or client_options)}`")

    if dry_run:
        st.info("Dry run mode — no emails will be sent.")

    for cid in (client_filter or client_options):
        cfg = get_client(cid)
        with st.expander(f"{cfg.client_name} (`{cid}`)"):
            cols = st.columns(3)
            cols[0].markdown(f"**Meta** {'✓' if cfg.meta_enabled else '—'}")
            cols[1].markdown(f"**HubSpot** {'✓' if cfg.hubspot_enabled else '—'}")
            cols[2].markdown(f"**Stripe** {'✓' if cfg.stripe_enabled else '—'}")
            if cfg.client_report_email:
                st.markdown(f"**Report to:** {cfg.client_report_email}")

else:
    # ── Run the pipeline ──────────────────────────────────────
    log_lines: list[str] = []
    log_area = st.empty()

    class _UIHandler(logging.Handler):
        """Captures log records and streams them into the Streamlit UI."""
        ICONS = {"INFO": "·", "WARNING": "⚠", "ERROR": "✗", "DEBUG": " "}

        def emit(self, record: logging.LogRecord) -> None:
            # Skip noisy low-level databricks transport logs
            if record.name.startswith("databricks.sql"):
                return
            icon = self.ICONS.get(record.levelname, "·")
            log_lines.append(f"{icon}  {record.getMessage()}")
            log_area.code("\n".join(log_lines[-40:]), language=None)

    handler = _UIHandler()
    handler.setLevel(logging.INFO)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    with st.spinner("Pipeline running…"):
        try:
            result = run_agency_pipeline(
                agency_id=agency_id,
                dry_run=dry_run,
                client_filter=client_filter or None,
            )
        except Exception as exc:
            st.error(f"Pipeline crashed: {exc}")
            root_logger.removeHandler(handler)
            st.stop()

    root_logger.removeHandler(handler)

    # ── Results ───────────────────────────────────────────────
    st.divider()

    processed = result["clients_processed"]
    failed = result["clients_failed"]

    if failed == 0:
        st.success(f"Pipeline complete — {processed} client(s) processed successfully.")
    else:
        st.warning(f"{processed} succeeded, {failed} failed.")

    # Successful clients
    for r in result["results"]:
        cfg = get_client(r["client_id"])
        with st.expander(f"✓  {cfg.client_name}", expanded=True):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("HubSpot Rows", r["hubspot_rows"])
            c2.metric("Meta Rows", r["meta_rows"])
            c3.metric("Stripe Rows", r["stripe_rows"])
            c4.metric("Pipeline Value", f"${r['total_pipeline']:,.0f}")

            st.markdown(f"**Top Channel:** {r['top_channel']}")
            if dry_run:
                st.markdown("**Email:** skipped (dry run)")
            elif r["email_sent"]:
                st.markdown(f"**Email:** sent to `{cfg.client_report_email}`")
            else:
                st.markdown("**Email:** not sent")

    # Failed clients
    for e in result["errors"]:
        with st.expander(f"✗  {e['client_id']}", expanded=True):
            st.error(e["error"])
