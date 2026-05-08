"""
app.py
------
Streamlit UI for the attribution pipeline.

Local dev:
    cd attribution_agent/attribution_agent
    streamlit run app.py

Databricks App:
    Deployed via `databricks bundle deploy`. Triggers the
    "[Attribution] Monthly Pipeline" Job via the Databricks Jobs API.
"""
import logging
import os
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from config.agency_config import get_agency, list_agencies
from config.client_config import get_client

_DATABRICKS_MODE = bool(os.environ.get("ATTRIBUTION_JOB_NAME"))
_JOB_NAME = os.environ.get("ATTRIBUTION_JOB_NAME", "")

# ── Page config ───────────────────────────────────────────────
st.set_page_config(
    page_title="Attribution Pipeline",
    page_icon="◆",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ── Global styles ─────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&display=swap');

/* ── Layout ── */
[data-testid="stSidebar"],
[data-testid="collapsedControl"] { display: none !important; }

.main .block-container {
    max-width: 680px;
    padding: 3.5rem 2rem 5rem;
}

/* ── Headings — Instrument Serif ── */
h1 {
    font-family: 'Instrument Serif', Georgia, serif !important;
    font-size: 2.8rem !important;
    font-weight: 400 !important;
    letter-spacing: -0.02em !important;
    line-height: 1.1 !important;
    color: #111 !important;
    margin-bottom: 0 !important;
}
h2 {
    font-family: 'Instrument Serif', Georgia, serif !important;
    font-size: 1.35rem !important;
    font-weight: 400 !important;
    color: #111 !important;
    margin-bottom: 0.4rem !important;
}
h3 {
    font-family: 'Instrument Serif', Georgia, serif !important;
    font-size: 1.1rem !important;
    font-weight: 400 !important;
    color: #111 !important;
}

/* ── Divider ── */
hr { border: none; border-top: 1px solid #e2e2e2; margin: 1.75rem 0; }

/* ── Metric cards ── */
[data-testid="metric-container"] {
    background: #fff !important;
    border: 1px solid #e8e8e8 !important;
    border-radius: 10px !important;
    padding: 1.1rem 1rem !important;
}
[data-testid="stMetricValue"] {
    font-family: 'Instrument Serif', serif !important;
    font-size: 1.9rem !important;
    font-weight: 400 !important;
    color: #111 !important;
}
[data-testid="stMetricLabel"] {
    font-size: 0.75rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.1em !important;
    text-transform: uppercase !important;
    color: #555 !important;
}

/* ── Caption ── */
[data-testid="stCaptionContainer"] p {
    color: #666 !important;
    font-size: 0.82rem !important;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-thumb { background: #d4ccff; border-radius: 4px; }
</style>
""", unsafe_allow_html=True)


# ── Helper functions ──────────────────────────────────────────

def _trigger_databricks_job(agency_id: str, client_filter: list, dry_run: bool) -> None:
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.jobs import RunLifeCycleState
    except ImportError:
        st.error("databricks-sdk not installed.")
        return

    w = WorkspaceClient()
    job = next((j for j in w.jobs.list() if j.settings and j.settings.name == _JOB_NAME), None)
    if not job:
        st.error(f"Job '{_JOB_NAME}' not found in this workspace.")
        return

    with st.spinner("Submitting run…"):
        run = w.jobs.run_now(job_id=job.job_id)
        run_id = run.run_id

    host = os.environ.get("DATABRICKS_HOST", "").lstrip("https://")
    st.markdown(
        f'<a href="https://{host}/#job/{job.job_id}/run/{run_id}" target="_blank" '
        f'style="font-size:0.75rem;color:#7a63ff;text-decoration:none;letter-spacing:0.06em;">'
        f'↗ View run {run_id} in Databricks</a>',
        unsafe_allow_html=True,
    )

    status_area = st.empty()
    terminal = {RunLifeCycleState.TERMINATED, RunLifeCycleState.SKIPPED, RunLifeCycleState.INTERNAL_ERROR}

    import time
    while True:
        info = w.jobs.get_run(run_id=run_id)
        state = info.state.life_cycle_state
        status_area.caption(f"Status — {state.value}")
        if state in terminal:
            break
        time.sleep(10)

    result_state = info.state.result_state
    if result_state and result_state.value == "SUCCESS":
        st.success("Run complete.")
    else:
        st.error(f"Run ended: {result_state.value if result_state else 'unknown'}")


def _show_recent_runs() -> None:
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError:
        return

    w = WorkspaceClient()
    job = next((j for j in w.jobs.list() if j.settings and j.settings.name == _JOB_NAME), None)
    if not job:
        return

    runs = list(w.jobs.list_runs(job_id=job.job_id, limit=5))
    if not runs:
        st.caption("No runs yet.")
        return

    import datetime
    for r in runs:
        state = r.state.result_state.value if r.state and r.state.result_state else "RUNNING"
        color = "#22c55e" if state == "SUCCESS" else ("#ef4444" if state == "FAILED" else "#7a63ff")
        dot = "●"
        start = r.start_time // 1000 if r.start_time else 0
        ts = datetime.datetime.fromtimestamp(start).strftime("%b %d, %H:%M") if start else "—"
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:0.6rem 0;border-bottom:1px solid #f0f0f0;">'
            f'<span style="font-size:0.8rem;color:#444;">{ts}</span>'
            f'<span style="font-size:0.7rem;color:{color};letter-spacing:0.08em;">'
            f'{dot} {state}</span></div>',
            unsafe_allow_html=True,
        )


def _run_local(agency_id: str, client_filter: list, dry_run: bool) -> None:
    from flows.agency_flow import run_agency_pipeline

    log_lines: list[str] = []
    log_area = st.empty()

    class _UIHandler(logging.Handler):
        ICONS = {"INFO": "·", "WARNING": "⚠", "ERROR": "✗"}
        def emit(self, record: logging.LogRecord) -> None:
            if record.name.startswith("databricks.sql"):
                return
            icon = self.ICONS.get(record.levelname, "·")
            log_lines.append(f"{icon}  {record.getMessage()}")
            log_area.code("\n".join(log_lines[-40:]), language=None)

    handler = _UIHandler()
    handler.setLevel(logging.INFO)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    with st.spinner("Running…"):
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
    _render_results(result, dry_run)


def _render_results(result: dict, dry_run: bool) -> None:
    st.markdown("<hr>", unsafe_allow_html=True)

    processed, failed = result["clients_processed"], result["clients_failed"]
    if failed == 0:
        st.success(f"Complete — {processed} client{'s' if processed != 1 else ''} processed.")
    else:
        st.warning(f"{processed} succeeded · {failed} failed.")

    for r in result["results"]:
        cfg = get_client(r["client_id"])
        st.markdown(f"### {cfg.client_name}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("HubSpot", r["hubspot_rows"])
        c2.metric("Meta", r["meta_rows"])
        c3.metric("Stripe", r["stripe_rows"])
        c4.metric("Pipeline", f"${r['total_pipeline']:,.0f}")
        st.caption(
            f"Top channel · {r['top_channel']}   ·   "
            + ("Email skipped (dry run)" if dry_run
               else f"Email sent · {cfg.client_report_email}" if r["email_sent"]
               else "Email not sent")
        )
        st.markdown("")

    for e in result["errors"]:
        st.error(f"**{e['client_id']}** — {e['error']}")


# ── Header ────────────────────────────────────────────────────
st.markdown(
    '<p style="font-size:0.65rem;letter-spacing:0.18em;text-transform:uppercase;'
    'color:#bbb;margin-bottom:0.4rem;">Attribution Agent</p>',
    unsafe_allow_html=True,
)
st.markdown("# Monthly\nPipeline")
st.markdown("<hr>", unsafe_allow_html=True)

# ── Controls ──────────────────────────────────────────────────
agencies = list_agencies()
agency_id = st.selectbox("Agency", agencies, label_visibility="visible")
agency = get_agency(agency_id)

client_options = agency.client_ids
client_filter = st.multiselect(
    "Clients",
    client_options,
    default=client_options,
    help="Leave all selected to run every client.",
)

dry_run = st.checkbox("Dry run — generate report without sending email")

st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)

col_run, col_recent = (st.columns([3, 1]) if _DATABRICKS_MODE else (st.columns([1, 3])[:1] + [None]))
with col_run:
    run_btn = st.button("Run Pipeline", type="primary", use_container_width=True)

if _DATABRICKS_MODE and col_recent:
    with col_recent:
        if st.button("Recent runs", type="secondary", use_container_width=True):
            st.session_state["show_runs"] = not st.session_state.get("show_runs", False)

# ── Client preview ────────────────────────────────────────────
if not run_btn:
    st.markdown("<hr>", unsafe_allow_html=True)
    for cid in (client_filter or client_options):
        cfg = get_client(cid)
        sources = " · ".join(filter(None, [
            "Meta" if cfg.meta_enabled else "",
            "HubSpot" if cfg.hubspot_enabled else "",
            "Stripe" if cfg.stripe_enabled else "",
        ])) or "No sources enabled"
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
            f'padding:0.75rem 0;border-bottom:1px solid #f5f5f5;">'
            f'<span style="font-family:\'Instrument Serif\',serif;font-size:1rem;color:#0d0d0d;">'
            f'{cfg.client_name}</span>'
            f'<span style="font-size:0.7rem;color:#bbb;letter-spacing:0.05em;">{sources}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    if _DATABRICKS_MODE and st.session_state.get("show_runs"):
        st.markdown("<hr>", unsafe_allow_html=True)
        st.markdown("## Recent runs")
        _show_recent_runs()

# ── Execute ───────────────────────────────────────────────────
else:
    st.markdown("<hr>", unsafe_allow_html=True)
    if _DATABRICKS_MODE:
        _trigger_databricks_job(agency_id, client_filter, dry_run)
    else:
        _run_local(agency_id, client_filter, dry_run)
