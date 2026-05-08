"""
app.py
------
Streamlit UI for the attribution pipeline.

Local dev:
    cd attribution_agent/attribution_agent
    streamlit run app.py

Databricks App:
    Deployed via `databricks bundle deploy`. The app triggers the
    "[Attribution] Monthly Pipeline" Job via the Databricks Jobs API
    instead of running the pipeline in-process.
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

# ── Runtime detection ─────────────────────────────────────────
# ATTRIBUTION_JOB_NAME is injected by databricks.yml when deployed as an App.
_DATABRICKS_MODE = bool(os.environ.get("ATTRIBUTION_JOB_NAME"))
_JOB_NAME = os.environ.get("ATTRIBUTION_JOB_NAME", "")

# ── Page config ───────────────────────────────────────────────
st.set_page_config(
    page_title="Attribution Pipeline",
    page_icon="📊",
    layout="centered",
)

st.title("📊 Attribution Pipeline")
if _DATABRICKS_MODE:
    st.caption("Triggers the Databricks Job · Ingest → Insights → Email")
else:
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

    if _DATABRICKS_MODE:
        st.divider()
        st.caption("Running as Databricks App")
        if st.button("↻ Recent runs", use_container_width=True):
            st.session_state["show_runs"] = True

# ── Main area ─────────────────────────────────────────────────
if not run_btn:
    # Preview panel
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

    # Recent runs panel (Databricks mode only)
    if _DATABRICKS_MODE and st.session_state.get("show_runs"):
        st.divider()
        st.subheader("Recent job runs")
        _show_recent_runs()

else:
    # ── Execute ───────────────────────────────────────────────
    if _DATABRICKS_MODE:
        _trigger_databricks_job(agency_id, client_filter, dry_run)
    else:
        _run_local(agency_id, client_filter, dry_run)


# ── Databricks Job trigger ────────────────────────────────────
def _trigger_databricks_job(
    agency_id: str,
    client_filter: list[str],
    dry_run: bool,
) -> None:
    """Trigger the Databricks Job and poll until complete."""
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.jobs import RunLifeCycleState
    except ImportError:
        st.error("databricks-sdk is not installed. Run: pip install databricks-sdk")
        return

    w = WorkspaceClient()

    # Look up the job by name
    job = next(
        (j for j in w.jobs.list() if j.settings and j.settings.name == _JOB_NAME),
        None,
    )
    if not job:
        st.error(f"Job '{_JOB_NAME}' not found in this workspace.")
        return

    # Build overrides for the notebook_params (agency_flow reads sys.argv,
    # so we pass them as python_params on the task override)
    params = ["--agency", agency_id]
    if dry_run:
        params.append("--dry-run")
    if client_filter:
        params += ["--client-filter"] + client_filter

    with st.spinner("Submitting job run…"):
        run = w.jobs.run_now(
            job_id=job.job_id,
            python_named_params={},  # spark_python_task uses python_params
        )
        run_id = run.run_id

    st.success(f"Job run submitted (run ID: `{run_id}`)")
    st.markdown(
        f"[View run in Databricks]"
        f"(https://{os.environ.get('DATABRICKS_HOST', '').lstrip('https://')}"
        f"/#job/{job.job_id}/run/{run_id})"
    )

    # Poll for completion
    status_area = st.empty()
    terminal = {
        RunLifeCycleState.TERMINATED,
        RunLifeCycleState.SKIPPED,
        RunLifeCycleState.INTERNAL_ERROR,
    }

    import time
    while True:
        info = w.jobs.get_run(run_id=run_id)
        state = info.state.life_cycle_state
        status_area.info(f"Status: **{state.value}**")
        if state in terminal:
            break
        time.sleep(10)

    result_state = info.state.result_state
    if result_state and result_state.value == "SUCCESS":
        st.success("Pipeline completed successfully.")
    else:
        st.error(f"Pipeline finished with state: {result_state.value if result_state else 'unknown'}")


def _show_recent_runs() -> None:
    """Display the last 5 runs of the attribution job."""
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError:
        st.warning("databricks-sdk not installed.")
        return

    w = WorkspaceClient()
    job = next(
        (j for j in w.jobs.list() if j.settings and j.settings.name == _JOB_NAME),
        None,
    )
    if not job:
        st.warning(f"Job '{_JOB_NAME}' not found.")
        return

    runs = list(w.jobs.list_runs(job_id=job.job_id, limit=5))
    if not runs:
        st.info("No runs yet.")
        return

    for r in runs:
        state = r.state.result_state.value if r.state and r.state.result_state else "RUNNING"
        icon = "✓" if state == "SUCCESS" else ("✗" if state == "FAILED" else "·")
        start = r.start_time // 1000 if r.start_time else 0
        import datetime
        ts = datetime.datetime.fromtimestamp(start).strftime("%Y-%m-%d %H:%M") if start else "—"
        st.markdown(f"{icon} `{ts}` — {state} (run `{r.run_id}`)")


# ── Local in-process run ──────────────────────────────────────
def _run_local(
    agency_id: str,
    client_filter: list[str],
    dry_run: bool,
) -> None:
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

    # Results
    st.divider()
    processed = result["clients_processed"]
    failed = result["clients_failed"]

    if failed == 0:
        st.success(f"Pipeline complete — {processed} client(s) processed successfully.")
    else:
        st.warning(f"{processed} succeeded, {failed} failed.")

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

    for e in result["errors"]:
        with st.expander(f"✗  {e['client_id']}", expanded=True):
            st.error(e["error"])
