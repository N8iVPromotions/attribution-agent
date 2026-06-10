"""
app.py — Attribution Command Center
-------------------------------------
Internal command center for running the attribution pipeline across
agencies and individual business accounts.

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

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from agents.control import arie_bot
from agents.outreach.outreach_agent import (
    PROSPECTS,
    generate_email_sequence,
    send_draft_to_self,
)
from config.agency_config import get_agency, list_agencies, AGENCY_REGISTRY
from config.client_config import (
    CLIENT_REGISTRY,
    CLIENT_REGISTRY_PATH,
    ClientConfig,
    default_client_schema,
    delete_client_config,
    get_client,
    is_custom_client,
    list_clients,
    save_client_config,
    slugify_client_id,
)

_DATABRICKS_MODE = bool(os.environ.get("ATTRIBUTION_JOB_NAME"))
_JOB_NAME = os.environ.get("ATTRIBUTION_JOB_NAME", "[Attribution] Monthly Pipeline")
_JOB_ID = int(os.environ.get("ATTRIBUTION_JOB_ID", "0") or "0") or 500226442246561

# ── Page config ───────────────────────────────────────────────
st.set_page_config(
    page_title="Attribution Command Center",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Global styles ─────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

/* ── Reset ── */
[data-testid="stSidebar"],
[data-testid="collapsedControl"] { display: none !important; }

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}

.main .block-container {
    max-width: 1320px;
    padding: 0 2rem 5rem;
}

/* ── Typography ── */
h1, h2, h3 {
    font-family: 'Inter', sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: -0.01em !important;
}
h1 {
    font-size: 1.5rem !important;
    color: #e6edf3 !important;
    margin-bottom: 0 !important;
}
h2 {
    font-size: 0.95rem !important;
    color: #c9d1d9 !important;
    margin-bottom: 0.25rem !important;
}
h3 {
    font-size: 0.85rem !important;
    color: #8b949e !important;
}

/* ── Top bar ── */
.topbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 1rem 0;
    border-bottom: 1px solid #21262d;
    margin-bottom: 1.5rem;
}
.topbar-left {
    display: flex;
    align-items: center;
    gap: 1.5rem;
}
.topbar-brand {
    display: flex;
    align-items: center;
    gap: 0.6rem;
}
.brand-mark {
    width: 24px; height: 24px;
    background: #7c68fc;
    border-radius: 5px;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.65rem; color: #fff; font-weight: 700;
}
.brand-name {
    font-size: 0.82rem;
    font-weight: 600;
    color: #e6edf3;
    letter-spacing: 0;
}
.topbar-sep {
    width: 1px;
    height: 16px;
    background: #30363d;
}
.topbar-sub {
    font-size: 0.75rem;
    color: #484f58;
    font-weight: 400;
}
.topbar-right {
    display: flex;
    align-items: center;
    gap: 0.75rem;
}
.status-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.7rem;
    font-weight: 500;
    color: #8b949e;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 0.28rem 0.65rem;
    letter-spacing: 0;
}
.status-dot {
    width: 5px; height: 5px;
    border-radius: 50%;
}

/* ── Panels ── */
.panel {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 8px;
    overflow: hidden;
    margin-bottom: 1rem;
}
.panel-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.7rem 1rem;
    border-bottom: 1px solid #21262d;
    background: #161b22;
}
.panel-title {
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #8b949e;
}
.panel-body {
    padding: 1rem 1rem 0.5rem;
}

/* ── Section label ── */
.field-label {
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: #484f58;
    margin-bottom: 0.4rem;
    display: block;
}

/* ── Client table ── */
.client-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8rem;
    margin-top: 0.25rem;
}
.client-table th {
    font-size: 0.65rem;
    font-weight: 600;
    letter-spacing: 0.07em;
    text-transform: uppercase;
    color: #484f58;
    padding: 0.4rem 0.6rem;
    border-bottom: 1px solid #21262d;
    text-align: left;
}
.client-table td {
    padding: 0.55rem 0.6rem;
    border-bottom: 1px solid #161b22;
    vertical-align: middle;
    color: #c9d1d9;
}
.client-table tr:last-child td { border-bottom: none; }
.client-table tr:hover td { background: rgba(255,255,255,0.02); }
.client-cell-name { font-weight: 500; color: #e6edf3; }

/* ── Source tags ── */
.tag {
    display: inline-block;
    font-size: 0.58rem;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    padding: 0.15rem 0.45rem;
    border-radius: 4px;
    margin-right: 0.2rem;
}
.tag-meta     { background: rgba(124,104,252,0.15); color: #a594fe; border: 1px solid rgba(124,104,252,0.3); }
.tag-google   { background: rgba(56,189,248,0.12);  color: #60c8f5; border: 1px solid rgba(56,189,248,0.25); }
.tag-linkedin { background: rgba(10,102,194,0.18);  color: #5baee8; border: 1px solid rgba(10,102,194,0.3); }
.tag-hubspot  { background: rgba(255,122,89,0.12);  color: #ff8a6e; border: 1px solid rgba(255,122,89,0.25); }
.tag-stripe   { background: rgba(99,179,237,0.12);  color: #76c8f5; border: 1px solid rgba(99,179,237,0.25); }

/* ── Model chips ── */
.model-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    background: #1c2128;
    border: 1px solid #30363d;
    border-radius: 4px;
    font-size: 0.68rem;
    font-weight: 500;
    color: #8b949e;
    padding: 0.2rem 0.55rem;
    margin: 0 0.2rem 0.3rem 0;
}
.model-chip-pct {
    color: #7c68fc;
    font-weight: 600;
}

/* ── Model description ── */
.model-desc {
    font-size: 0.78rem;
    color: #6e7681;
    line-height: 1.6;
    margin: 0.2rem 0 1rem;
}

/* ── Streamlit widget overrides ── */
[data-testid="stSelectbox"] > div > div,
[data-testid="stMultiSelect"] > div > div {
    background: #0d1117 !important;
    border: 1px solid #30363d !important;
    border-radius: 6px !important;
}
[data-testid="stRadio"] label {
    font-size: 0.8rem !important;
    font-weight: 500 !important;
}
[data-testid="stToggle"] label {
    font-size: 0.8rem !important;
    font-weight: 400 !important;
    color: #8b949e !important;
}
[data-testid="stCheckbox"] label {
    font-size: 0.8rem !important;
    color: #8b949e !important;
}
input[type="text"], input[type="number"], textarea {
    background: #0d1117 !important;
    border: 1px solid #30363d !important;
    border-radius: 6px !important;
    color: #e6edf3 !important;
    font-size: 0.82rem !important;
}

/* ── Buttons ── */
[data-testid="baseButton-primary"] {
    background: #7c68fc !important;
    border: 1px solid #7c68fc !important;
    border-radius: 6px !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 500 !important;
    font-size: 0.8rem !important;
    letter-spacing: 0 !important;
    box-shadow: none !important;
    color: #fff !important;
}
[data-testid="baseButton-primary"]:hover {
    background: #6d5ae8 !important;
    border-color: #6d5ae8 !important;
}
[data-testid="baseButton-secondary"] {
    background: #21262d !important;
    border: 1px solid #30363d !important;
    border-radius: 6px !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 500 !important;
    font-size: 0.8rem !important;
    color: #c9d1d9 !important;
    box-shadow: none !important;
}
[data-testid="baseButton-secondary"]:hover {
    background: #30363d !important;
    border-color: #484f58 !important;
}

/* ── Metrics ── */
[data-testid="metric-container"] {
    background: #161b22 !important;
    border: 1px solid #21262d !important;
    border-radius: 6px !important;
    padding: 0.9rem 1rem !important;
}
[data-testid="stMetricValue"] {
    font-family: 'Inter', sans-serif !important;
    font-size: 1.5rem !important;
    font-weight: 600 !important;
    color: #e6edf3 !important;
    letter-spacing: -0.02em !important;
}
[data-testid="stMetricLabel"] {
    font-size: 0.65rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.08em !important;
    text-transform: uppercase !important;
    color: #484f58 !important;
}

/* ── Alerts ── */
[data-testid="stAlert"] {
    border-radius: 6px !important;
    border-left-width: 3px !important;
    font-size: 0.82rem !important;
}

/* ── Code / log viewer ── */
[data-testid="stCode"] {
    background: #0d1117 !important;
    border: 1px solid #21262d !important;
    border-radius: 6px !important;
    font-size: 0.73rem !important;
}

/* ── Captions ── */
[data-testid="stCaptionContainer"] p {
    color: #484f58 !important;
    font-size: 0.75rem !important;
}

/* ── Tabs ── */
[data-testid="stTabs"] [role="tablist"] {
    border-bottom: 1px solid #21262d !important;
    gap: 0 !important;
}
[data-testid="stTabs"] [role="tab"] {
    font-size: 0.8rem !important;
    font-weight: 500 !important;
    color: #8b949e !important;
    padding: 0.6rem 1rem !important;
    border-radius: 0 !important;
    border-bottom: 2px solid transparent !important;
}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
    color: #e6edf3 !important;
    border-bottom-color: #7c68fc !important;
    background: transparent !important;
}

/* ── Divider ── */
.ruled {
    border: none;
    border-top: 1px solid #21262d;
    margin: 1.25rem 0;
}

/* ── Run action bar ── */
.action-bar {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.85rem 1rem;
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 8px;
    margin-top: 1rem;
}
.action-context {
    flex: 1;
    font-size: 0.75rem;
    color: #484f58;
    display: flex;
    gap: 1.25rem;
}
.action-context-item strong {
    color: #8b949e;
    font-weight: 500;
}
.action-context-item span {
    color: #c9d1d9;
}

/* ── Run summary pill ── */
.run-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.72rem;
    color: #6e7681;
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 4px;
    padding: 0.3rem 0.75rem;
    margin-bottom: 0.75rem;
}
.run-pill .hl { color: #7c68fc; font-weight: 600; }
.run-pill .sep { color: #21262d; }

/* ── Recent runs ── */
.run-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.5rem 0;
    border-bottom: 1px solid #21262d;
    font-size: 0.75rem;
}
.run-row:last-child { border-bottom: none; }
.run-ts { color: #484f58; font-family: 'Inter', monospace; }
.run-label { color: #8b949e; }
.run-state {
    font-size: 0.65rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
}
.run-state-ok      { background: rgba(46,160,67,0.15);  color: #3fb950; border: 1px solid rgba(46,160,67,0.25); }
.run-state-fail    { background: rgba(248,81,73,0.12);  color: #f85149; border: 1px solid rgba(248,81,73,0.2); }
.run-state-running { background: rgba(124,104,252,0.12); color: #a594fe; border: 1px solid rgba(124,104,252,0.2); }

/* ── Form section header ── */
.form-section {
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #484f58;
    padding: 0.5rem 0 0.5rem;
    border-bottom: 1px solid #21262d;
    margin-bottom: 0.75rem;
    display: block;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: #0d1117; }
::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #484f58; }
</style>
""", unsafe_allow_html=True)


# ── Attribution model definitions ─────────────────────────────
ATTRIBUTION_MODELS: dict[str, dict] = {
    "last_touch": {
        "label": "Last Touch",
        "description": "100% credit to the final touchpoint. Simple to implement and interpret. Best when the closing channel is clearly distinct from awareness channels.",
        "credits": {"Paid Social": 0, "Paid Search": 0, "Email": 0, "Organic": 0, "Direct": 100},
    },
    "first_touch": {
        "label": "First Touch",
        "description": "100% credit to the channel that first introduced the lead. Useful for measuring top-of-funnel investment and brand awareness spend.",
        "credits": {"Paid Social": 100, "Paid Search": 0, "Email": 0, "Organic": 0, "Direct": 0},
    },
    "linear": {
        "label": "Linear",
        "description": "Equal credit distributed across all touchpoints in the journey. No channel is weighted over another — useful as a neutral baseline.",
        "credits": {"Paid Social": 40, "Paid Search": 20, "Email": 20, "Organic": 0, "Direct": 20},
    },
    "time_decay": {
        "label": "Time Decay",
        "description": "Recency-weighted — touchpoints closer to conversion receive exponentially more credit. Emphasizes what influenced the final decision.",
        "credits": {"Paid Social": 29, "Paid Search": 6, "Email": 13, "Organic": 0, "Direct": 52},
    },
    "u_shape": {
        "label": "U-Shape",
        "description": "40% to first touch, 40% to last touch, 20% shared across middle touchpoints. Balances acquisition and close without ignoring the middle.",
        "credits": {"Paid Social": 47, "Paid Search": 7, "Email": 6, "Organic": 0, "Direct": 40},
    },
    "w_shape": {
        "label": "W-Shape",
        "description": "30% each to first touch, lead-stage conversion, and close — 10% across remaining middle touchpoints. Recommended for B2B sales cycles.",
        "credits": {"Paid Social": 35, "Paid Search": 5, "Email": 30, "Organic": 0, "Direct": 30},
    },
}

_MODEL_KEYS = list(ATTRIBUTION_MODELS.keys())
_MODEL_LABELS = [v["label"] for v in ATTRIBUTION_MODELS.values()]


def _model_key_from_label(label: str) -> str:
    for k, v in ATTRIBUTION_MODELS.items():
        if v["label"] == label:
            return k
    return "last_touch"


# ── Comparison chart ─────────────────────────────────────────
def _render_comparison_chart(selected_model: str) -> None:
    try:
        import altair as alt
    except ImportError:
        st.caption("Install altair to view the comparison chart.")
        return

    rows = []
    for key, meta in ATTRIBUTION_MODELS.items():
        for channel, pct in meta["credits"].items():
            rows.append({
                "Model": meta["label"],
                "Channel": channel,
                "Credit": pct,
            })
    df = pd.DataFrame(rows)

    channel_colors = {
        "Paid Social": "#7c68fc",
        "Paid Search": "#38bdf8",
        "Email":       "#3fb950",
        "Organic":     "#d29922",
        "Direct":      "#30363d",
    }
    model_order = [v["label"] for v in ATTRIBUTION_MODELS.values()]
    channel_order = list(channel_colors.keys())
    selected_label = ATTRIBUTION_MODELS[selected_model]["label"]

    chart = (
        alt.Chart(df)
        .mark_bar(width={"band": 0.68})
        .encode(
            x=alt.X(
                "Model:N",
                sort=model_order,
                axis=alt.Axis(
                    labelAngle=0,
                    title=None,
                    labelFontSize=10,
                    labelFont="Inter, sans-serif",
                    labelColor="#484f58",
                    tickColor="transparent",
                    domainColor="transparent",
                ),
            ),
            y=alt.Y(
                "Credit:Q",
                stack="normalize",
                axis=alt.Axis(
                    format="%",
                    title=None,
                    labelFontSize=9,
                    labelFont="Inter, sans-serif",
                    labelColor="#484f58",
                    grid=True,
                    gridColor="#21262d",
                    domainColor="transparent",
                    tickColor="transparent",
                    tickCount=4,
                ),
            ),
            color=alt.Color(
                "Channel:N",
                sort=channel_order,
                scale=alt.Scale(
                    domain=list(channel_colors.keys()),
                    range=list(channel_colors.values()),
                ),
                legend=alt.Legend(
                    title=None,
                    orient="bottom",
                    columns=5,
                    labelFontSize=9.5,
                    labelFont="Inter, sans-serif",
                    labelColor="#6e7681",
                    symbolSize=70,
                    symbolType="square",
                    padding=10,
                ),
            ),
            opacity=alt.condition(
                alt.datum["Model"] == selected_label,
                alt.value(1.0),
                alt.value(0.22),
            ),
            tooltip=[
                alt.Tooltip("Model:N", title="Model"),
                alt.Tooltip("Channel:N", title="Channel"),
                alt.Tooltip("Credit:Q", title="Credit %", format=".0f"),
            ],
        )
        .properties(height=210, background="transparent")
        .configure_view(strokeWidth=0, fill="transparent")
    )

    st.altair_chart(chart, use_container_width=True)
    st.caption("Sample 5-touch journey: Paid Social → Paid Search → Email → Paid Social → Direct")


# ── Pipeline helpers ──────────────────────────────────────────

def _trigger_databricks_job(
    agency_id: str,
    client_filter: list,
    dry_run: bool,
    attribution_model: str,
    run_mode: str,
) -> None:
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.jobs import RunLifeCycleState
    except ImportError:
        st.error("databricks-sdk not installed.")
        return

    # Databricks Apps auto-inject OAuth credentials (DATABRICKS_CLIENT_ID/SECRET).
    # Using WorkspaceClient() with no args picks up OAuth automatically.
    # If DATABRICKS_TOKEN is also present it causes a conflict — remove it first.
    os.environ.pop("DATABRICKS_TOKEN", None)
    w = WorkspaceClient()


    # Resolve job ID — prefer the env var to avoid a list-all-jobs permission check
    job_id = _JOB_ID
    if not job_id:
        job = next((j for j in w.jobs.list() if j.settings and j.settings.name == _JOB_NAME), None)
        if not job:
            st.error(f"Job '{_JOB_NAME}' not found in this workspace.")
            return
        job_id = job.job_id

    with st.spinner("Submitting run…"):
        job_parameters = {"dry_run": str(dry_run).lower(), "attribution_model": attribution_model}
        if agency_id:
            job_parameters["agency"] = agency_id
        run = w.jobs.run_now(job_id=job_id, job_parameters=job_parameters)
        run_id = run.run_id

    n_clients = len(client_filter) if client_filter else "all"
    arie_bot.notify(
        f"⚡ *Pipeline started*\n"
        f"Agency: `{agency_id}` · {n_clients} client{'s' if n_clients != 1 else ''}\n"
        f"Model: `{attribution_model}` · {'Dry run' if dry_run else 'Live'}\n"
        f"Run: `{run_id}`"
    )

    host = os.environ.get("DATABRICKS_HOST", "").lstrip("https://")
    st.markdown(
        f'<a href="https://{host}/#job/{job_id}/run/{run_id}" target="_blank" '
        f'style="font-size:0.75rem;color:#7c68fc;text-decoration:none;font-family:Inter,sans-serif;">'
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
    success = result_state and result_state.value == "SUCCESS"
    if success:
        st.success("Run complete.")
        arie_bot.notify(
            f"✅ *Pipeline complete*\n"
            f"Agency: `{agency_id}` · {'Dry run' if dry_run else 'Live'}\n"
            f"Run: `{run_id}`"
        )
    else:
        state_label = result_state.value if result_state else "unknown"
        st.error(f"Run ended: {state_label}")
        arie_bot.notify(
            f"❌ *Pipeline failed* — `{state_label}`\n"
            f"Agency: `{agency_id}` · Run: `{run_id}`"
        )


def _show_recent_runs() -> None:
    try:
        from utils.databricks_writer import fetch_recent_pipeline_runs
        runs = fetch_recent_pipeline_runs(limit=8)
    except Exception:
        runs = []

    if not runs:
        st.caption("No recent runs found.")
        return

    import datetime
    for r in runs:
        state = str(r.get("status", "unknown")).upper()
        if state == "SUCCESS":
            state_cls, state_label = "run-state-ok", "Success"
        elif state == "FAILED":
            state_cls, state_label = "run-state-fail", "Failed"
        else:
            state_cls, state_label = "run-state-running", state.title()

        start = r.get("started_at")
        ts = start.strftime("%b %d, %H:%M") if hasattr(start, "strftime") else str(start or "—")[:16]
        label = " · ".join(filter(None, [
            r.get("client_id", ""),
            r.get("attribution_model", ""),
            f"${float(r.get('total_pipeline') or 0):,.0f}" if r.get("total_pipeline") else "",
        ]))
        st.markdown(
            f'<div class="run-row">'
            f'  <span class="run-ts">{ts}</span>'
            f'  <span class="run-label">{label}</span>'
            f'  <span class="run-state {state_cls}">{state_label}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )


def _run_local(
    agency_id: str,
    client_filter: list,
    dry_run: bool,
    attribution_model: str,
    run_mode: str,
) -> None:
    from flows.agency_flow import run_agency_pipeline

    log_lines: list[str] = []
    log_area = st.empty()

    class _UIHandler(logging.Handler):
        ICONS = {"INFO": "·", "WARNING": "!", "ERROR": "✗"}
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

    with st.spinner("Running pipeline…"):
        try:
            result = run_agency_pipeline(
                agency_id=agency_id,
                dry_run=dry_run,
                client_filter=client_filter or None,
                attribution_model=attribution_model,
                run_mode=run_mode.lower(),
            )
        except Exception as exc:
            st.error(f"Pipeline error: {exc}")
            root_logger.removeHandler(handler)
            st.stop()

    root_logger.removeHandler(handler)
    _render_results(result, dry_run)


def _render_results(result: dict, dry_run: bool) -> None:
    processed, failed = result["clients_processed"], result["clients_failed"]
    if failed == 0:
        st.success(f"Pipeline complete — {processed} client{'s' if processed != 1 else ''} processed.")
    else:
        st.warning(f"{processed} succeeded · {failed} failed.")

    if not result["results"]:
        return
    cols = st.columns(min(len(result["results"]), 3))
    for i, r in enumerate(result["results"]):
        cfg = get_client(r["client_id"])
        with cols[i % len(cols)]:
            st.markdown(
                f'<div class="panel-header" style="border-radius:6px 6px 0 0;">'
                f'  <span class="panel-title">{cfg.client_name}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            m1, m2 = st.columns(2)
            m1.metric("Pipeline", f"${r['total_pipeline']:,.0f}")
            m2.metric("Deals", r["hubspot_rows"])
            m3, m4 = st.columns(2)
            m3.metric("Meta rows", r["meta_rows"])
            m4.metric("Stripe rows", r["stripe_rows"])
            email_status = (
                "Dry run — not sent" if dry_run
                else f"Sent to {cfg.client_report_email}" if r["email_sent"]
                else "Email not sent"
            )
            st.caption(f"Top channel: {r['top_channel']}  ·  {email_status}")

    for e in result["errors"]:
        st.error(f"**{e['client_id']}** — {e['error']}")


def _client_ids_for_agency(agency_id: str) -> list[str]:
    agency = get_agency(agency_id)
    client_ids = list(agency.client_ids)
    for client_id in list_clients():
        cfg = get_client(client_id)
        if cfg.agency_id == agency_id and client_id not in client_ids:
            client_ids.append(client_id)
    return client_ids


def _client_label(client_id: str) -> str:
    try:
        return get_client(client_id).client_name
    except Exception:
        return client_id


def _source_tags(cfg: ClientConfig) -> str:
    tags = []
    if cfg.meta_enabled:
        tags.append('<span class="tag tag-meta">Meta</span>')
    if getattr(cfg, "google_ads_enabled", False):
        tags.append('<span class="tag tag-google">Google</span>')
    if getattr(cfg, "linkedin_ads_enabled", False):
        tags.append('<span class="tag tag-linkedin">LinkedIn</span>')
    if cfg.hubspot_enabled:
        tags.append('<span class="tag tag-hubspot">HubSpot</span>')
    if cfg.stripe_enabled:
        tags.append('<span class="tag tag-stripe">Stripe</span>')
    return "".join(tags) if tags else '<span style="color:#484f58;font-size:0.7rem;">—</span>'


def _render_client_manager() -> None:
    action = st.radio(
        "Action",
        ["Add client", "Edit client"],
        horizontal=True,
        label_visibility="collapsed",
    )

    existing_clients = list_clients()
    selected_client_id = ""
    base = ClientConfig(
        client_id="", client_name="", attribution_model="last_touch",
        databricks_schema="", lookback_days=30,
    )
    if action == "Edit client" and existing_clients:
        selected_client_id = st.selectbox(
            "Select client",
            existing_clients,
            format_func=_client_label,
        )
        base = get_client(selected_client_id)

    with st.form("client_config_form"):
        st.markdown('<span class="form-section">Identity</span>', unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            client_name = st.text_input("Business name", value=base.client_name)
        with c2:
            default_id = base.client_id or slugify_client_id(client_name or "new_client")
            client_id = st.text_input("Client ID", value=default_id, disabled=bool(base.client_id))
        with c3:
            display_name = st.text_input("Display name", value=base.client_display_name or base.client_name)
        with c4:
            report_email = st.text_input("Report email", value=base.client_report_email)

        st.markdown('<span class="form-section">Configuration</span>', unsafe_allow_html=True)
        d1, d2, d3, d4 = st.columns(4)
        with d1:
            agency_options = [""] + list_agencies()
            agency_index = agency_options.index(base.agency_id) if base.agency_id in agency_options else 0
            form_agency_id = st.selectbox(
                "Agency",
                agency_options,
                index=agency_index,
                format_func=lambda a: "Direct account" if not a else AGENCY_REGISTRY[a].agency_name,
            )
        with d2:
            model_index = _MODEL_KEYS.index(base.attribution_model) if base.attribution_model in _MODEL_KEYS else 0
            attribution_model = st.selectbox(
                "Default model", _MODEL_KEYS, index=model_index,
                format_func=lambda m: ATTRIBUTION_MODELS[m]["label"],
            )
        with d3:
            lookback_days = st.number_input("Lookback days", min_value=1, max_value=365,
                                            value=int(base.lookback_days or 30), step=1)
        with d4:
            schema_default = base.databricks_schema or default_client_schema(default_id)
            databricks_schema = st.text_input("Databricks schema", value=schema_default)

        st.markdown('<span class="form-section">Data Sources</span>', unsafe_allow_html=True)
        s1, s2, s3, s4, s5 = st.columns(5)
        with s1:
            meta_enabled = st.checkbox("Meta Ads", value=base.meta_enabled)
            meta_ad_account_id = st.text_input("Account ID", value=base.meta_ad_account_id,
                                               disabled=not meta_enabled, key="meta_id")
        with s2:
            google_ads_enabled = st.checkbox("Google Ads", value=base.google_ads_enabled)
            google_ads_customer_id = st.text_input("Customer ID", value=base.google_ads_customer_id,
                                                   disabled=not google_ads_enabled, key="google_id")
        with s3:
            linkedin_ads_enabled = st.checkbox("LinkedIn Ads", value=base.linkedin_ads_enabled)
            linkedin_ads_account_id = st.text_input("Account ID", value=base.linkedin_ads_account_id,
                                                    disabled=not linkedin_ads_enabled, key="li_id")
        with s4:
            hubspot_enabled = st.checkbox("HubSpot", value=base.hubspot_enabled)
            hubspot_pipeline_id = st.text_input("Pipeline ID", value=base.hubspot_pipeline_id,
                                                disabled=not hubspot_enabled, key="hs_id")
        with s5:
            stripe_enabled = st.checkbox("Stripe", value=base.stripe_enabled)
            stripe_account_id = st.text_input("Account ID", value=base.stripe_account_id,
                                              disabled=not stripe_enabled, key="stripe_id")

        st.markdown('<span class="form-section">Alert Thresholds</span>', unsafe_allow_html=True)
        t1, t2 = st.columns(2)
        with t1:
            spend_drop_pct_alert = st.slider("Spend drop alert (%)", min_value=5, max_value=90,
                                             value=int(float(base.spend_drop_pct_alert or 0.30) * 100), step=5)
        with t2:
            zero_spend_days_allowed = st.number_input("Zero-spend days allowed", min_value=0,
                                                      max_value=30, value=int(base.zero_spend_days_allowed or 1))

        save_btn = st.form_submit_button("Save client", type="primary", use_container_width=False)

    if save_btn:
        clean_id = base.client_id or slugify_client_id(client_id or client_name)
        if not client_name.strip():
            st.error("Business name is required.")
            return
        config = ClientConfig(
            client_id=clean_id,
            client_name=client_name.strip(),
            attribution_model=attribution_model,
            meta_enabled=meta_enabled,
            meta_ad_account_id=meta_ad_account_id.strip(),
            google_ads_enabled=google_ads_enabled,
            google_ads_customer_id=google_ads_customer_id.strip(),
            linkedin_ads_enabled=linkedin_ads_enabled,
            linkedin_ads_account_id=linkedin_ads_account_id.strip(),
            hubspot_enabled=hubspot_enabled,
            hubspot_pipeline_id=hubspot_pipeline_id.strip(),
            databricks_schema=(databricks_schema or default_client_schema(clean_id)).strip(),
            lookback_days=int(lookback_days),
            spend_drop_pct_alert=spend_drop_pct_alert / 100,
            zero_spend_days_allowed=int(zero_spend_days_allowed),
            stripe_enabled=stripe_enabled,
            stripe_account_id=stripe_account_id.strip(),
            agency_id=form_agency_id,
            client_report_email=report_email.strip(),
            client_display_name=display_name.strip(),
        )
        save_client_config(config)
        st.success(f"Saved — {config.client_name}")
        st.rerun()

    if action == "Edit client" and selected_client_id and is_custom_client(selected_client_id):
        st.markdown('<hr class="ruled">', unsafe_allow_html=True)
        if st.button("Delete client", type="secondary"):
            delete_client_config(selected_client_id)
            st.success("Client deleted.")
            st.rerun()


# ═══════════════════════════════════════════════
# UI
# ═══════════════════════════════════════════════

# ── Start ARIE (once per process) ─────────────
def _get_secret(key: str) -> str:
    """
    Read a secret — tries three methods in order:
    1. Databricks SDK WorkspaceClient (works in Databricks Apps)
    2. dbutils.secrets (works in notebooks/jobs)
    3. os.environ (works locally via .env)
    """
    # 1. WorkspaceClient — primary path for Databricks Apps
    try:
        import base64
        from databricks.sdk import WorkspaceClient
        resp = WorkspaceClient().secrets.get_secret(scope="attribution", key=key)
        val = resp.value or ""
        try:
            return base64.b64decode(val).decode("utf-8")
        except Exception:
            return val
    except Exception:
        pass
    # 2. dbutils — notebooks / jobs
    try:
        from databricks.sdk.runtime import dbutils
        return dbutils.secrets.get(scope="attribution", key=key)
    except Exception:
        pass
    # 3. Local .env
    return os.environ.get(key, "")

@st.cache_resource
def _inject_secrets() -> None:
    """Pull Databricks secrets into os.environ so downstream modules find them."""
    for key in (
        "ANTHROPIC_API_KEY",
        "GMAIL_SENDER",
        "GMAIL_APP_PASSWORD",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "OPENAI_API_KEY",
        "META_ACCESS_TOKEN",
        "HUBSPOT_ACCESS_TOKEN",
        "STRIPE_SECRET_KEY",
        "GOOGLE_ADS_DEVELOPER_TOKEN",
        "GOOGLE_ADS_CLIENT_ID",
        "GOOGLE_ADS_CLIENT_SECRET",
        "GOOGLE_ADS_REFRESH_TOKEN",
        "LINKEDIN_ACCESS_TOKEN",
        "DATABRICKS_TOKEN",
    ):
        if not os.environ.get(key):
            val = _get_secret(key)
            if val:
                os.environ[key] = val

_inject_secrets()

# Databricks App has no outbound internet (api.telegram.org is unreachable
# inside the workspace VPC). ARIE must run locally on the operator's machine.
_arie_enabled = False
_arie_status  = {"ok": False, "reason": "ARIE runs locally — see README"}

# ── Top bar ───────────────────────────────────
import datetime as _dt
env_label = "Databricks" if _DATABRICKS_MODE else "Local"
env_dot_color = "#7c68fc" if _DATABRICKS_MODE else "#3fb950"
now_str = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
arie_dot_color = "#3fb950" if (_arie_enabled and arie_bot.is_running()) else "#484f58"

st.markdown(
    f'<div class="topbar">'
    f'  <div class="topbar-left">'
    f'    <div class="topbar-brand">'
    f'      <div class="brand-mark">◆</div>'
    f'      <span class="brand-name">Attribution Command Center</span>'
    f'    </div>'
    f'    <div class="topbar-sep"></div>'
    f'    <span class="topbar-sub">N8iV Promotions</span>'
    f'  </div>'
    f'  <div class="topbar-right">'
    f'    <span class="status-pill">'
    f'      <span class="status-dot" style="background:{env_dot_color};"></span>'
    f'      {env_label}'
    f'    </span>'
    f'    <span class="status-pill">{now_str}</span>'
    f'    <span class="status-pill">'
    f'      <span class="status-dot" style="background:{arie_dot_color};"></span>'
    f'      ARIE'
    f'    </span>'
    f'  </div>'
    f'</div>',
    unsafe_allow_html=True,
)

# ── Navigation tabs ───────────────────────────
tab_pipeline, tab_clients, tab_outreach = st.tabs(["Pipeline", "Clients", "Outreach"])


# ══════════════════════════════════════════════
# TAB: PIPELINE
# ══════════════════════════════════════════════
with tab_pipeline:

    left_col, gap_col, right_col = st.columns([5, 1, 6])

    # ── LEFT: Target ──────────────────────────
    with left_col:
        st.markdown(
            '<div class="panel-header" style="border-radius:8px 8px 0 0;margin-top:1rem;">'
            '  <span class="panel-title">Target</span>'
            '</div>',
            unsafe_allow_html=True,
        )

        run_mode = st.radio(
            "Scope",
            ["Agency", "Business"],
            horizontal=True,
            label_visibility="collapsed",
        )

        agency_id: str = ""
        client_filter: list[str] = []

        if run_mode == "Agency":
            agencies = list_agencies()
            agency_id = st.selectbox(
                "Agency",
                agencies,
                format_func=lambda a: AGENCY_REGISTRY[a].agency_name,
                label_visibility="visible",
            )
            agency_client_ids = _client_ids_for_agency(agency_id)
            select_all = st.toggle("All clients", value=True)
            if select_all:
                client_filter = agency_client_ids
            else:
                client_filter = st.multiselect(
                    "Select clients",
                    agency_client_ids,
                    default=agency_client_ids,
                    format_func=_client_label,
                )
        else:
            all_clients = list_clients()
            selected_client = st.selectbox(
                "Business account",
                all_clients,
                format_func=_client_label,
                label_visibility="visible",
            )
            cfg_sel = get_client(selected_client)
            agency_id = cfg_sel.agency_id or (list_agencies()[0] if list_agencies() else "")
            client_filter = [selected_client]

        # Client table
        if client_filter:
            rows_html = ""
            for cid in client_filter:
                if cid not in CLIENT_REGISTRY:
                    continue
                cfg = get_client(cid)
                model_label = ATTRIBUTION_MODELS.get(cfg.attribution_model, {}).get("label", cfg.attribution_model)
                rows_html += (
                    f'<tr>'
                    f'  <td class="client-cell-name">{cfg.client_name}</td>'
                    f'  <td>{_source_tags(cfg)}</td>'
                    f'  <td style="color:#484f58;font-size:0.72rem;">{model_label}</td>'
                    f'</tr>'
                )
            st.markdown(
                f'<table class="client-table">'
                f'  <thead><tr>'
                f'    <th>Client</th><th>Sources</th><th>Default model</th>'
                f'  </tr></thead>'
                f'  <tbody>{rows_html}</tbody>'
                f'</table>',
                unsafe_allow_html=True,
            )

    # ── RIGHT: Attribution model ───────────────
    with right_col:
        st.markdown(
            '<div class="panel-header" style="border-radius:8px 8px 0 0;margin-top:1rem;">'
            '  <span class="panel-title">Attribution Model</span>'
            '</div>',
            unsafe_allow_html=True,
        )

        selected_label = st.selectbox(
            "Model",
            _MODEL_LABELS,
            index=0,
            label_visibility="collapsed",
        )
        selected_model = _model_key_from_label(selected_label)
        model_meta = ATTRIBUTION_MODELS[selected_model]

        st.markdown(
            f'<p class="model-desc">{model_meta["description"]}</p>',
            unsafe_allow_html=True,
        )

        # Credit chips
        st.markdown('<span class="field-label">Credit distribution</span>', unsafe_allow_html=True)
        chips = "".join(
            f'<span class="model-chip">{ch} <span class="model-chip-pct">{pct}%</span></span>'
            for ch, pct in model_meta["credits"].items() if pct > 0
        )
        st.markdown(f'<div style="margin-bottom:1.2rem;">{chips}</div>', unsafe_allow_html=True)

        st.markdown('<span class="field-label">Model comparison</span>', unsafe_allow_html=True)
        _render_comparison_chart(selected_model)

    # ── Run bar ────────────────────────────────
    n_clients = len(client_filter)
    btn_label = (
        f"Run All  ({n_clients})"
        if run_mode == "Agency" and agency_id and n_clients == len(_client_ids_for_agency(agency_id))
        else f"Run Selected  ({n_clients})"
    )

    bar_l, bar_m, bar_r = st.columns([4, 1, 1])
    with bar_l:
        dry_run = st.checkbox("Dry run — generate reports, skip email delivery")
    with bar_m:
        run_btn = st.button(btn_label, type="primary", use_container_width=True, disabled=not client_filter)
    with bar_r:
        if _DATABRICKS_MODE:
            if st.button("Recent runs", type="secondary", use_container_width=True):
                st.session_state["show_runs"] = not st.session_state.get("show_runs", False)

    if _DATABRICKS_MODE and st.session_state.get("show_runs"):
        st.markdown('<hr class="ruled">', unsafe_allow_html=True)
        st.markdown(
            '<div class="panel-header">'
            '  <span class="panel-title">Recent runs</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        _show_recent_runs()

    # ── Execute ────────────────────────────────
    if run_btn and client_filter and agency_id:
        st.markdown(
            f'<div class="run-pill">'
            f'  <span class="hl">{model_meta["label"]}</span>'
            f'  <span class="sep">|</span>'
            f'  {n_clients} client{"s" if n_clients != 1 else ""}'
            f'  <span class="sep">|</span>'
            f'  {"Dry run" if dry_run else "Live"}'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<hr class="ruled">', unsafe_allow_html=True)
        _run_local(agency_id, client_filter, dry_run, selected_model, run_mode)


# ══════════════════════════════════════════════
# TAB: CLIENTS
# ══════════════════════════════════════════════
with tab_clients:
    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)

    # Client roster table
    all_clients = list_clients()
    if all_clients:
        st.markdown('<span class="field-label">Client roster</span>', unsafe_allow_html=True)
        roster_rows = ""
        for cid in all_clients:
            cfg = get_client(cid)
            agency_name = AGENCY_REGISTRY.get(cfg.agency_id, type("", (), {"agency_name": "Direct"})()).agency_name
            model_label = ATTRIBUTION_MODELS.get(cfg.attribution_model, {}).get("label", cfg.attribution_model)
            custom_badge = (
                '<span class="tag tag-meta" style="font-size:0.55rem;">Custom</span>'
                if is_custom_client(cid) else ""
            )
            roster_rows += (
                f'<tr>'
                f'  <td class="client-cell-name">{cfg.client_name} {custom_badge}</td>'
                f'  <td style="color:#6e7681;font-size:0.75rem;">{agency_name}</td>'
                f'  <td>{_source_tags(cfg)}</td>'
                f'  <td style="color:#6e7681;font-size:0.75rem;">{model_label}</td>'
                f'  <td style="color:#484f58;font-size:0.72rem;">{cfg.client_report_email or "—"}</td>'
                f'</tr>'
            )
        st.markdown(
            f'<table class="client-table">'
            f'  <thead><tr>'
            f'    <th>Name</th><th>Agency</th><th>Sources</th>'
            f'    <th>Default model</th><th>Report email</th>'
            f'  </tr></thead>'
            f'  <tbody>{roster_rows}</tbody>'
            f'</table>',
            unsafe_allow_html=True,
        )
        st.markdown("<div style='height:1.5rem'></div>", unsafe_allow_html=True)

    st.markdown('<hr class="ruled">', unsafe_allow_html=True)
    _render_client_manager()


# ══════════════════════════════════════════════
# TAB: OUTREACH AGENT
# ══════════════════════════════════════════════
with tab_outreach:

    # ── ARIE status banner ─────────────────────
    st.info(
        "**ARIE runs on your local machine** — the Databricks workspace has no outbound internet access.\n\n"
        "Start ARIE locally:\n"
        "```\n"
        "cd attribution_agent/attribution_agent\n"
        "python agents/control/arie_bot.py\n"
        "```"
    )

    # ── Session state init ─────────────────────
    if "outreach_sequences" not in st.session_state:
        st.session_state.outreach_sequences = {}
    if "outreach_draft_status" not in st.session_state:
        st.session_state.outreach_draft_status = {}
    if "outreach_selected" not in st.session_state:
        st.session_state.outreach_selected = PROSPECTS[0]["id"]

    # ── Outreach-specific styles ───────────────
    st.markdown("""
<style>
.or-stat-card {
    background: #111111;
    border: 1px solid #1E1E1E;
    border-radius: 8px;
    padding: 18px 12px;
    text-align: center;
}
.or-stat-val {
    font-size: 1.75rem;
    font-weight: 700;
    font-family: 'DM Mono', 'Courier New', monospace;
    line-height: 1;
}
.or-stat-label {
    font-size: 0.7rem;
    color: #888888;
    margin-top: 6px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
}
.or-prospect-card {
    background: #111111;
    border: 1px solid #1E1E1E;
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 16px;
}
.or-industry-badge {
    display: inline-block;
    font-size: 0.65rem;
    font-family: monospace;
    padding: 3px 9px;
    border-radius: 4px;
    border: 1px solid #2563EB44;
    color: #2563EB;
    background: rgba(37,99,235,0.08);
    letter-spacing: 0.4px;
    margin-left: 10px;
    vertical-align: middle;
}
.or-field-label {
    font-size: 0.65rem;
    color: #888888;
    text-transform: uppercase;
    letter-spacing: 0.7px;
    margin-bottom: 3px;
}
.or-field-val {
    font-size: 0.82rem;
    color: #e6edf3;
    font-family: 'DM Mono', 'Courier New', monospace;
}
.or-notes {
    font-size: 0.8rem;
    color: #888888;
    line-height: 1.6;
    padding: 10px 12px;
    background: rgba(255,255,255,0.03);
    border-left: 2px solid #1E1E1E;
    border-radius: 0 4px 4px 0;
    margin-top: 12px;
}
.or-sent-badge {
    display: inline-block;
    font-size: 0.7rem;
    font-family: monospace;
    padding: 4px 10px;
    border-radius: 4px;
    background: rgba(16,185,129,0.12);
    color: #10B981;
    border: 1px solid rgba(16,185,129,0.25);
}
.or-email-meta {
    font-size: 0.72rem;
    color: #888888;
    font-family: 'DM Mono', 'Courier New', monospace;
    margin-bottom: 8px;
}
.or-email-meta strong { color: #e6edf3; }
</style>
""", unsafe_allow_html=True)

    # ── Stats row ──────────────────────────────
    total = len(PROSPECTS)
    generated = len(st.session_state.outreach_sequences)
    drafted = sum(
        1 for pid, status in st.session_state.outreach_draft_status.items()
        if any(status.values())
    )
    pending = total - generated

    sc1, sc2, sc3, sc4 = st.columns(4)
    for col, val, label, color in [
        (sc1, total,     "Total Prospects",     "#e6edf3"),
        (sc2, generated, "Sequences Generated", "#F59E0B"),
        (sc3, drafted,   "Drafted to Inbox",    "#10B981"),
        (sc4, pending,   "Pending",             "#888888"),
    ]:
        with col:
            st.markdown(
                f'<div class="or-stat-card">'
                f'  <div class="or-stat-val" style="color:{color}">{val}</div>'
                f'  <div class="or-stat-label">{label}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.markdown("<div style='height:1.25rem'></div>", unsafe_allow_html=True)

    # ── Two-column layout ──────────────────────
    prospect_col, detail_col = st.columns([1, 2.5])

    with prospect_col:
        st.markdown(
            '<div class="panel-header" style="border-radius:8px 8px 0 0;">'
            '  <span class="panel-title">Prospects</span>'
            '</div>',
            unsafe_allow_html=True,
        )

        def _status_dot_color(pid: int) -> str:
            if st.session_state.outreach_draft_status.get(pid) and any(
                st.session_state.outreach_draft_status[pid].values()
            ):
                return "#10B981"
            if pid in st.session_state.outreach_sequences:
                return "#F59E0B"
            return "#444444"

        prospect_names = [p["name"] for p in PROSPECTS]
        selected_name = st.radio(
            "Prospect",
            prospect_names,
            label_visibility="collapsed",
            key="outreach_prospect_radio",
        )
        selected_prospect = next(p for p in PROSPECTS if p["name"] == selected_name)
        st.session_state.outreach_selected = selected_prospect["id"]

    with detail_col:
        p = selected_prospect
        pid = p["id"]

        # Prospect card
        st.markdown(
            f'<div class="or-prospect-card">'
            f'  <div style="display:flex;align-items:center;margin-bottom:14px;">'
            f'    <span style="font-size:1.05rem;font-weight:600;color:#e6edf3">{p["name"]}</span>'
            f'    <span class="or-industry-badge">{p["industry"]}</span>'
            f'  </div>'
            f'  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:4px;">'
            f'    <div><div class="or-field-label">Contact</div>'
            f'         <div class="or-field-val">{p["contact"]}</div></div>'
            f'    <div><div class="or-field-label">Email</div>'
            f'         <div class="or-field-val" style="font-size:0.72rem">{p["email"]}</div></div>'
            f'    <div><div class="or-field-label">Phone</div>'
            f'         <div class="or-field-val">{p["phone"]}</div></div>'
            f'  </div>'
            f'  <div class="or-notes">{p["notes"]}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        seq = st.session_state.outreach_sequences.get(pid)

        # Generate button
        if seq is None:
            if st.button(f"⚡ Generate 3-Email Sequence for {p['name']}", use_container_width=True, type="primary", key=f"gen_{pid}"):
                with st.spinner("Generating personalized email sequence via Claude..."):
                    try:
                        result = generate_email_sequence(p)
                        st.session_state.outreach_sequences[pid] = result
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Generation failed — {exc}")
        else:
            # Regenerate + Draft All row
            regen_col, draft_all_col = st.columns([1, 1])
            with regen_col:
                if st.button("↺ Regenerate", key=f"regen_{pid}", use_container_width=True):
                    with st.spinner("Regenerating..."):
                        try:
                            result = generate_email_sequence(p)
                            st.session_state.outreach_sequences[pid] = result
                            st.session_state.outreach_draft_status.pop(pid, None)
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Regeneration failed — {exc}")
            with draft_all_col:
                if st.button("✉ Draft All 3 to Inbox", key=f"draft_all_{pid}", use_container_width=True, type="primary"):
                    _gmail_sender = os.environ.get("GMAIL_SENDER", "")
                    _gmail_pw = os.environ.get("GMAIL_APP_PASSWORD", "")
                    if not _gmail_sender or not _gmail_pw:
                        st.error("GMAIL_SENDER and GMAIL_APP_PASSWORD must be set in .env")
                    else:
                        errors = []
                        for ekey in ["email1", "email2", "email3"]:
                            try:
                                send_draft_to_self(seq[ekey], p, _gmail_sender, _gmail_pw)
                                if pid not in st.session_state.outreach_draft_status:
                                    st.session_state.outreach_draft_status[pid] = {}
                                st.session_state.outreach_draft_status[pid][ekey] = True
                            except Exception as exc:
                                errors.append(str(exc))
                        if errors:
                            st.error(f"Some drafts failed: {'; '.join(errors)}")
                        else:
                            st.success(f"✓ All 3 drafts sent to {_gmail_sender}")
                        st.rerun()

            # Email sequence expanders
            email_meta = [
                ("email1", "FIRST TOUCH",  "Day 1"),
                ("email2", "FOLLOW-UP 1",  "Day 5"),
                ("email3", "FOLLOW-UP 2",  "Day 12"),
            ]
            draft_status = st.session_state.outreach_draft_status.get(pid, {})

            for ekey, label, day in email_meta:
                email = seq[ekey]
                is_drafted = draft_status.get(ekey, False)
                drafted_suffix = " ✓ In Inbox" if is_drafted else ""

                with st.expander(f"{label} — {email['subject']}{drafted_suffix}", expanded=(ekey == "email1")):
                    st.markdown(
                        f'<div class="or-email-meta">'
                        f'  <strong>To:</strong> {p["email"]} &nbsp;·&nbsp; '
                        f'  <strong>From:</strong> zajen@n8ivpromotions.com &nbsp;·&nbsp; '
                        f'  <strong>Send:</strong> {day}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    st.code(email["body"], language=None)

                    action_col, status_col = st.columns([1, 2])
                    with action_col:
                        if not is_drafted:
                            if st.button(f"✉ Send to Inbox", key=f"draft_{pid}_{ekey}", type="primary"):
                                _gmail_sender = os.environ.get("GMAIL_SENDER", "")
                                _gmail_pw = os.environ.get("GMAIL_APP_PASSWORD", "")
                                if not _gmail_sender or not _gmail_pw:
                                    st.error("GMAIL credentials not configured")
                                else:
                                    try:
                                        send_draft_to_self(email, p, _gmail_sender, _gmail_pw)
                                        if pid not in st.session_state.outreach_draft_status:
                                            st.session_state.outreach_draft_status[pid] = {}
                                        st.session_state.outreach_draft_status[pid][ekey] = True
                                        st.rerun()
                                    except Exception as exc:
                                        st.error(f"Failed to send — {exc}")
                    with status_col:
                        if is_drafted:
                            st.markdown(
                                '<span class="or-sent-badge">✓ Draft sent to inbox</span>',
                                unsafe_allow_html=True,
                            )
