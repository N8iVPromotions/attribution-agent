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

from config.agency_config import get_agency, list_agencies, AGENCY_REGISTRY
from config.client_config import get_client, list_clients, CLIENT_REGISTRY

_DATABRICKS_MODE = bool(os.environ.get("ATTRIBUTION_JOB_NAME"))
_JOB_NAME = os.environ.get("ATTRIBUTION_JOB_NAME", "")

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
@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&display=swap');

[data-testid="stSidebar"],
[data-testid="collapsedControl"] { display: none !important; }

.main .block-container {
    max-width: 1200px;
    padding: 2.5rem 2.5rem 5rem;
}

h1 {
    font-family: 'Instrument Serif', Georgia, serif !important;
    font-size: 2.4rem !important;
    font-weight: 400 !important;
    letter-spacing: -0.02em !important;
    line-height: 1.1 !important;
    color: #111 !important;
    margin-bottom: 0 !important;
}
h2 {
    font-family: 'Instrument Serif', Georgia, serif !important;
    font-size: 1.2rem !important;
    font-weight: 400 !important;
    color: #111 !important;
    margin-bottom: 0.4rem !important;
}
h3 {
    font-family: 'Instrument Serif', Georgia, serif !important;
    font-size: 1rem !important;
    font-weight: 400 !important;
    color: #111 !important;
}

hr { border: none; border-top: 1px solid #e2e2e2; margin: 1.5rem 0; }

[data-testid="metric-container"] {
    background: #fff !important;
    border: 1px solid #e8e8e8 !important;
    border-radius: 10px !important;
    padding: 1rem !important;
}
[data-testid="stMetricValue"] {
    font-family: 'Instrument Serif', serif !important;
    font-size: 1.7rem !important;
    font-weight: 400 !important;
    color: #111 !important;
}
[data-testid="stMetricLabel"] {
    font-size: 0.72rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.1em !important;
    text-transform: uppercase !important;
    color: #555 !important;
}

[data-testid="stCaptionContainer"] p {
    color: #666 !important;
    font-size: 0.82rem !important;
}

/* Section cards */
.cc-card {
    background: #fafafa;
    border: 1px solid #ebebeb;
    border-radius: 12px;
    padding: 1.4rem 1.5rem;
    margin-bottom: 1rem;
}
.cc-label {
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: #999;
    margin-bottom: 0.6rem;
}
.cc-chip {
    display: inline-block;
    background: #f0eeff;
    color: #5a45e0;
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    padding: 0.25rem 0.65rem;
    border-radius: 20px;
    margin-right: 0.35rem;
}
.client-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 0.65rem 0;
    border-bottom: 1px solid #f5f5f5;
}
.client-row:last-child { border-bottom: none; }

::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-thumb { background: #d4ccff; border-radius: 4px; }
</style>
""", unsafe_allow_html=True)


# ── Attribution model definitions ────────────────────────────
ATTRIBUTION_MODELS: dict[str, dict] = {
    "last_touch": {
        "label": "Last Touch",
        "description": "100% credit to the final touchpoint before conversion.",
        "credits": {"Paid Social": 0, "Paid Search": 0, "Email": 0, "Organic": 0, "Direct": 100},
    },
    "first_touch": {
        "label": "First Touch",
        "description": "100% credit to the first channel that introduced the lead.",
        "credits": {"Paid Social": 100, "Paid Search": 0, "Email": 0, "Organic": 0, "Direct": 0},
    },
    "linear": {
        "label": "Linear",
        "description": "Equal credit distributed evenly across all touchpoints.",
        "credits": {"Paid Social": 40, "Paid Search": 20, "Email": 20, "Organic": 0, "Direct": 20},
    },
    "time_decay": {
        "label": "Time Decay",
        "description": "Recency-weighted — later touchpoints receive progressively more credit.",
        "credits": {"Paid Social": 29, "Paid Search": 6, "Email": 13, "Organic": 0, "Direct": 52},
    },
    "u_shape": {
        "label": "U-Shape",
        "description": "40% first touch, 40% last touch, 20% split across middle touchpoints.",
        "credits": {"Paid Social": 47, "Paid Search": 7, "Email": 6, "Organic": 0, "Direct": 40},
    },
    "w_shape": {
        "label": "W-Shape",
        "description": "30% first, 30% lead-stage conversion, 30% close — 10% across middle.",
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
                "Credit %": pct,
                "_selected": key == selected_model,
            })

    df = pd.DataFrame(rows)

    channel_colors = {
        "Paid Social": "#7a63ff",
        "Paid Search": "#4fc3f7",
        "Email":       "#81c784",
        "Organic":     "#ffb74d",
        "Direct":      "#e0e0e0",
    }

    model_order = [v["label"] for v in ATTRIBUTION_MODELS.values()]
    channel_order = ["Paid Social", "Paid Search", "Email", "Organic", "Direct"]

    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X(
                "Model:N",
                sort=model_order,
                axis=alt.Axis(labelAngle=0, title=None, labelFontSize=11),
            ),
            y=alt.Y(
                "Credit %:Q",
                stack="normalize",
                axis=alt.Axis(format="%", title=None, labelFontSize=10, grid=True, gridColor="#f0f0f0"),
            ),
            color=alt.Color(
                "Channel:N",
                sort=channel_order,
                scale=alt.Scale(
                    domain=list(channel_colors.keys()),
                    range=list(channel_colors.values()),
                ),
                legend=alt.Legend(title=None, orient="bottom", columns=5, labelFontSize=11),
            ),
            opacity=alt.condition(
                alt.datum["Model"] == ATTRIBUTION_MODELS[selected_model]["label"],
                alt.value(1.0),
                alt.value(0.55),
            ),
            tooltip=[
                alt.Tooltip("Model:N"),
                alt.Tooltip("Channel:N"),
                alt.Tooltip("Credit %:Q", format=".0f"),
            ],
        )
        .properties(height=260)
        .configure_view(strokeWidth=0)
        .configure_axis(domainWidth=0)
    )

    st.altair_chart(chart, use_container_width=True)
    st.caption(
        "Based on a sample 5-touch journey: Paid Social → Paid Search → Email → Paid Social → Direct  ·  "
        "Selected model is highlighted."
    )


# ── Pipeline helpers ──────────────────────────────────────────

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
        start = r.start_time // 1000 if r.start_time else 0
        ts = datetime.datetime.fromtimestamp(start).strftime("%b %d, %H:%M") if start else "—"
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;align-items:center;'
            f'padding:0.5rem 0;border-bottom:1px solid #f0f0f0;">'
            f'<span style="font-size:0.8rem;color:#444;">{ts}</span>'
            f'<span style="font-size:0.7rem;color:{color};letter-spacing:0.08em;">'
            f'● {state}</span></div>',
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

    with st.spinner("Running pipeline…"):
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

    cols = st.columns(min(len(result["results"]), 3))
    for i, r in enumerate(result["results"]):
        cfg = get_client(r["client_id"])
        with cols[i % len(cols)]:
            st.markdown(f"### {cfg.client_name}")
            m1, m2 = st.columns(2)
            m1.metric("Pipeline", f"${r['total_pipeline']:,.0f}")
            m2.metric("HubSpot Deals", r["hubspot_rows"])
            m3, m4 = st.columns(2)
            m3.metric("Meta Rows", r["meta_rows"])
            m4.metric("Stripe Rows", r["stripe_rows"])
            st.caption(
                f"Top channel · {r['top_channel']}   ·   "
                + ("Email skipped (dry run)" if dry_run
                   else f"Sent · {cfg.client_report_email}" if r["email_sent"]
                   else "Email not sent")
            )

    for e in result["errors"]:
        st.error(f"**{e['client_id']}** — {e['error']}")


# ── Header ────────────────────────────────────────────────────
col_hd, col_env = st.columns([5, 1])
with col_hd:
    st.markdown(
        '<p style="font-size:0.6rem;letter-spacing:0.2em;text-transform:uppercase;'
        'color:#bbb;margin-bottom:0.3rem;">N8iV Promotions · Internal</p>',
        unsafe_allow_html=True,
    )
    st.markdown("# Attribution Command Center")
with col_env:
    env_label = "Databricks" if _DATABRICKS_MODE else "Local"
    env_color = "#7a63ff" if _DATABRICKS_MODE else "#888"
    st.markdown(
        f'<div style="text-align:right;padding-top:2rem;">'
        f'<span style="font-size:0.65rem;color:{env_color};font-weight:600;'
        f'letter-spacing:0.1em;text-transform:uppercase;">● {env_label}</span></div>',
        unsafe_allow_html=True,
    )

st.markdown("<hr>", unsafe_allow_html=True)

# ── Target + Model selection ──────────────────────────────────
left_col, right_col = st.columns([1, 1], gap="large")

with left_col:
    st.markdown('<div class="cc-label">Target</div>', unsafe_allow_html=True)

    run_mode = st.radio(
        "Run mode",
        ["Agency", "Business"],
        horizontal=True,
        label_visibility="collapsed",
    )

    st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)

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
        agency = get_agency(agency_id)

        select_all = st.toggle("Select all clients", value=True)
        if select_all:
            client_filter = list(agency.client_ids)
        else:
            client_filter = st.multiselect(
                "Clients",
                agency.client_ids,
                default=agency.client_ids,
                format_func=lambda c: CLIENT_REGISTRY[c].client_name if c in CLIENT_REGISTRY else c,
            )

    else:
        all_clients = list_clients()
        selected_client = st.selectbox(
            "Business account",
            all_clients,
            format_func=lambda c: CLIENT_REGISTRY[c].client_name if c in CLIENT_REGISTRY else c,
            label_visibility="visible",
        )
        # For business mode, find the agency or run standalone
        cfg = get_client(selected_client)
        agency_id = cfg.agency_id or (list_agencies()[0] if list_agencies() else "")
        client_filter = [selected_client]

    # Client preview list
    if client_filter:
        st.markdown("<div style='height:0.4rem'></div>", unsafe_allow_html=True)
        for cid in client_filter:
            if cid not in CLIENT_REGISTRY:
                continue
            cfg = get_client(cid)
            sources = " · ".join(filter(None, [
                "Meta" if cfg.meta_enabled else "",
                "HubSpot" if cfg.hubspot_enabled else "",
                "Stripe" if cfg.stripe_enabled else "",
            ])) or "No sources"
            st.markdown(
                f'<div class="client-row">'
                f'<span style="font-family:\'Instrument Serif\',serif;font-size:0.95rem;color:#111;">'
                f'{cfg.client_name}</span>'
                f'<span style="font-size:0.68rem;color:#bbb;letter-spacing:0.04em;">{sources}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )


with right_col:
    st.markdown('<div class="cc-label">Attribution Model</div>', unsafe_allow_html=True)

    selected_label = st.selectbox(
        "Attribution model",
        _MODEL_LABELS,
        index=0,
        label_visibility="collapsed",
    )
    selected_model = _model_key_from_label(selected_label)
    model_meta = ATTRIBUTION_MODELS[selected_model]

    st.markdown(
        f'<p style="font-size:0.82rem;color:#555;margin:0.4rem 0 1rem;">'
        f'{model_meta["description"]}</p>',
        unsafe_allow_html=True,
    )

    # Credit breakdown chips for selected model
    st.markdown('<div class="cc-label">Credit distribution (sample journey)</div>', unsafe_allow_html=True)
    chips_html = ""
    for channel, pct in model_meta["credits"].items():
        if pct > 0:
            chips_html += f'<span class="cc-chip">{channel} {pct}%</span>'
    st.markdown(f'<div style="margin-bottom:1rem;">{chips_html}</div>', unsafe_allow_html=True)

    st.markdown('<div class="cc-label">Model comparison</div>', unsafe_allow_html=True)
    _render_comparison_chart(selected_model)


# ── Run controls ──────────────────────────────────────────────
st.markdown("<hr>", unsafe_allow_html=True)

ctrl_left, ctrl_mid, ctrl_right = st.columns([2, 1, 1])

with ctrl_left:
    dry_run = st.checkbox("Dry run — generate report, skip email delivery")

with ctrl_mid:
    run_selected = st.button(
        f"Run {'All' if run_mode == 'Agency' and len(client_filter) == len(get_agency(agency_id).client_ids if agency_id else client_filter) else 'Selected'} ({len(client_filter)})",
        type="primary",
        use_container_width=True,
        disabled=not client_filter,
    )

with ctrl_right:
    if _DATABRICKS_MODE:
        show_runs_btn = st.button("Recent runs", type="secondary", use_container_width=True)
        if show_runs_btn:
            st.session_state["show_runs"] = not st.session_state.get("show_runs", False)

if _DATABRICKS_MODE and st.session_state.get("show_runs"):
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("## Recent runs")
    _show_recent_runs()

# ── Execute ───────────────────────────────────────────────────
if run_selected and client_filter and agency_id:
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown(
        f'<p style="font-size:0.75rem;color:#888;letter-spacing:0.06em;">'
        f'Model · <strong style="color:#7a63ff">{model_meta["label"]}</strong> &nbsp;·&nbsp; '
        f'{len(client_filter)} client{"s" if len(client_filter) != 1 else ""} &nbsp;·&nbsp; '
        f'{"Dry run" if dry_run else "Live run"}</p>',
        unsafe_allow_html=True,
    )
    if _DATABRICKS_MODE:
        _trigger_databricks_job(agency_id, client_filter, dry_run)
    else:
        _run_local(agency_id, client_filter, dry_run)
