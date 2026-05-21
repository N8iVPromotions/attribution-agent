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
@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Inter:wght@300;400;500;600&display=swap');

/* ── Reset sidebar ── */
[data-testid="stSidebar"],
[data-testid="collapsedControl"] { display: none !important; }

/* ── Root font ── */
html, body, [class*="css"] {
    font-family: 'Inter', system-ui, sans-serif !important;
}

/* ── Layout ── */
.main .block-container {
    max-width: 1240px;
    padding: 0 2.5rem 6rem;
}

/* ── Headings ── */
h1 {
    font-family: 'Instrument Serif', Georgia, serif !important;
    font-size: 2.6rem !important;
    font-weight: 400 !important;
    letter-spacing: -0.025em !important;
    line-height: 1.1 !important;
    color: #f0f0ff !important;
    margin-bottom: 0 !important;
}
h2 {
    font-family: 'Instrument Serif', Georgia, serif !important;
    font-size: 1.15rem !important;
    font-weight: 400 !important;
    color: #d0d0e8 !important;
    margin-bottom: 0.3rem !important;
}
h3 {
    font-family: 'Instrument Serif', Georgia, serif !important;
    font-size: 1rem !important;
    font-weight: 400 !important;
    color: #c8c8e0 !important;
}

/* ── Top nav bar ── */
.top-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 1.25rem 0 1rem;
    border-bottom: 1px solid rgba(255,255,255,0.06);
    margin-bottom: 2rem;
}
.top-bar-brand {
    display: flex;
    align-items: center;
    gap: 0.75rem;
}
.top-bar-logo {
    width: 28px; height: 28px;
    background: linear-gradient(135deg, #7a63ff, #a78bfa);
    border-radius: 7px;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.8rem; color: #fff; font-weight: 700;
    box-shadow: 0 0 16px rgba(122,99,255,0.5);
}
.top-bar-title {
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: #7070a0;
}
.env-badge {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 0.3rem 0.8rem;
    border-radius: 20px;
    border: 1px solid rgba(255,255,255,0.1);
    background: rgba(255,255,255,0.04);
}
.env-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}

/* ── Section label ── */
.sec-label {
    font-size: 0.6rem;
    font-weight: 700;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: #4a4a6a;
    margin-bottom: 0.75rem;
    display: block;
}

/* ── Glass card ── */
.glass {
    background: rgba(255,255,255,0.025);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 16px;
    padding: 1.5rem 1.6rem;
}

/* ── Divider ── */
.divider {
    border: none;
    border-top: 1px solid rgba(255,255,255,0.06);
    margin: 1.8rem 0;
}

/* ── Client rows ── */
.client-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.6rem 0;
    border-bottom: 1px solid rgba(255,255,255,0.04);
}
.client-row:last-child { border-bottom: none; }
.client-name {
    font-family: 'Instrument Serif', serif;
    font-size: 0.95rem;
    color: #d4d4ee;
}
.client-sources {
    display: flex;
    gap: 0.3rem;
}
.src-badge {
    font-size: 0.6rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    padding: 0.18rem 0.55rem;
    border-radius: 20px;
    text-transform: uppercase;
}
.src-meta    { background: rgba(122,99,255,0.15); color: #9b8aff; border: 1px solid rgba(122,99,255,0.25); }
.src-hubspot { background: rgba(255,120,80,0.12); color: #ff9070; border: 1px solid rgba(255,120,80,0.25); }
.src-stripe  { background: rgba(80,200,150,0.12); color: #5de0a0; border: 1px solid rgba(80,200,150,0.25); }

/* ── Credit chips ── */
.chip {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    background: rgba(122,99,255,0.12);
    color: #a090ff;
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    padding: 0.25rem 0.7rem;
    border-radius: 20px;
    border: 1px solid rgba(122,99,255,0.22);
    margin: 0 0.25rem 0.35rem 0;
}
.chip-pct {
    color: #7a63ff;
    font-weight: 700;
}

/* ── Model description ── */
.model-desc {
    font-size: 0.82rem;
    color: #6a6a90;
    line-height: 1.55;
    margin: 0.3rem 0 1.2rem;
    font-weight: 400;
}

/* ── Streamlit widgets override ── */
/* Selectbox */
[data-testid="stSelectbox"] > div > div {
    background: rgba(255,255,255,0.04) !important;
    border: 1px solid rgba(255,255,255,0.1) !important;
    border-radius: 10px !important;
}
/* Radio */
[data-testid="stRadio"] label {
    font-size: 0.82rem !important;
    font-weight: 500 !important;
}
/* Toggle */
[data-testid="stToggle"] label {
    font-size: 0.82rem !important;
}
/* Multiselect */
[data-testid="stMultiSelect"] > div > div {
    background: rgba(255,255,255,0.04) !important;
    border: 1px solid rgba(255,255,255,0.1) !important;
    border-radius: 10px !important;
}

/* ── Buttons ── */
[data-testid="baseButton-primary"] {
    background: linear-gradient(135deg, #7a63ff 0%, #5a43df 100%) !important;
    border: none !important;
    border-radius: 10px !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 600 !important;
    font-size: 0.82rem !important;
    letter-spacing: 0.04em !important;
    box-shadow: 0 0 24px rgba(122,99,255,0.35), 0 2px 8px rgba(0,0,0,0.3) !important;
    transition: box-shadow 0.2s ease !important;
}
[data-testid="baseButton-primary"]:hover {
    box-shadow: 0 0 32px rgba(122,99,255,0.55), 0 4px 12px rgba(0,0,0,0.3) !important;
}
[data-testid="baseButton-secondary"] {
    background: rgba(255,255,255,0.05) !important;
    border: 1px solid rgba(255,255,255,0.1) !important;
    border-radius: 10px !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 500 !important;
    font-size: 0.82rem !important;
}

/* ── Metrics ── */
[data-testid="metric-container"] {
    background: rgba(122,99,255,0.06) !important;
    border: 1px solid rgba(122,99,255,0.18) !important;
    border-radius: 14px !important;
    padding: 1.2rem 1.1rem !important;
}
[data-testid="stMetricValue"] {
    font-family: 'Instrument Serif', serif !important;
    font-size: 1.9rem !important;
    font-weight: 400 !important;
    color: #e8e8ff !important;
}
[data-testid="stMetricLabel"] {
    font-family: 'Inter', sans-serif !important;
    font-size: 0.65rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.14em !important;
    text-transform: uppercase !important;
    color: #5a5a80 !important;
}

/* ── Captions ── */
[data-testid="stCaptionContainer"] p {
    color: #4e4e70 !important;
    font-size: 0.78rem !important;
}

/* ── Alerts ── */
[data-testid="stAlert"] {
    border-radius: 12px !important;
    border-left-width: 3px !important;
}

/* ── Code block (log viewer) ── */
[data-testid="stCode"] {
    background: rgba(0,0,0,0.4) !important;
    border: 1px solid rgba(255,255,255,0.06) !important;
    border-radius: 10px !important;
    font-size: 0.75rem !important;
}

/* ── Run summary pill ── */
.run-summary {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.72rem;
    color: #5a5a80;
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 20px;
    padding: 0.35rem 1rem;
    margin-bottom: 1rem;
}
.run-summary strong { color: #7a63ff; }

/* ── Checkbox ── */
[data-testid="stCheckbox"] label {
    font-size: 0.82rem !important;
    font-weight: 400 !important;
    color: #7070a0 !important;
}

/* ── Recent runs ── */
.run-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.6rem 0;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    font-size: 0.78rem;
}
.run-row:last-child { border-bottom: none; }
.run-ts { color: #5a5a80; }
.run-state { font-weight: 600; letter-spacing: 0.08em; font-size: 0.68rem; }

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-thumb { background: rgba(122,99,255,0.35); border-radius: 4px; }
</style>
""", unsafe_allow_html=True)


# ── Attribution model definitions ─────────────────────────────
ATTRIBUTION_MODELS: dict[str, dict] = {
    "last_touch": {
        "label": "Last Touch",
        "description": "All conversion credit goes to the final touchpoint. Simple and easy to action — best when your closing channel is clear.",
        "credits": {"Paid Social": 0, "Paid Search": 0, "Email": 0, "Organic": 0, "Direct": 100},
    },
    "first_touch": {
        "label": "First Touch",
        "description": "Full credit to the channel that first introduced the lead. Useful for evaluating top-of-funnel awareness spend.",
        "credits": {"Paid Social": 100, "Paid Search": 0, "Email": 0, "Organic": 0, "Direct": 0},
    },
    "linear": {
        "label": "Linear",
        "description": "Credit split evenly across every touchpoint in the journey. No channel is weighted over another.",
        "credits": {"Paid Social": 40, "Paid Search": 20, "Email": 20, "Organic": 0, "Direct": 20},
    },
    "time_decay": {
        "label": "Time Decay",
        "description": "Channels closer to conversion earn exponentially more credit. Emphasizes what drove the final decision.",
        "credits": {"Paid Social": 29, "Paid Search": 6, "Email": 13, "Organic": 0, "Direct": 52},
    },
    "u_shape": {
        "label": "U-Shape",
        "description": "40% to first touch, 40% to last touch, 20% shared across the middle. Balances acquisition and close.",
        "credits": {"Paid Social": 47, "Paid Search": 7, "Email": 6, "Organic": 0, "Direct": 40},
    },
    "w_shape": {
        "label": "W-Shape",
        "description": "30% each to first touch, lead creation, and close — 10% across middle. Best for longer B2B sales cycles.",
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
                "is_selected": key == selected_model,
            })

    df = pd.DataFrame(rows)

    channel_colors = {
        "Paid Social": "#7a63ff",
        "Paid Search": "#38bdf8",
        "Email":       "#34d399",
        "Organic":     "#fbbf24",
        "Direct":      "#334155",
    }

    model_order = [v["label"] for v in ATTRIBUTION_MODELS.values()]
    channel_order = list(channel_colors.keys())

    selected_label = ATTRIBUTION_MODELS[selected_model]["label"]

    chart = (
        alt.Chart(df)
        .mark_bar(width={"band": 0.72})
        .encode(
            x=alt.X(
                "Model:N",
                sort=model_order,
                axis=alt.Axis(
                    labelAngle=0,
                    title=None,
                    labelFontSize=10.5,
                    labelFont="Inter, sans-serif",
                    labelColor="#555580",
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
                    labelColor="#444460",
                    grid=True,
                    gridColor="rgba(255,255,255,0.04)",
                    domainColor="transparent",
                    tickColor="transparent",
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
                    labelFontSize=10,
                    labelFont="Inter, sans-serif",
                    labelColor="#555580",
                    symbolSize=80,
                    symbolType="square",
                    padding=12,
                ),
            ),
            opacity=alt.condition(
                alt.datum["Model"] == selected_label,
                alt.value(1.0),
                alt.value(0.28),
            ),
            tooltip=[
                alt.Tooltip("Model:N", title="Model"),
                alt.Tooltip("Channel:N", title="Channel"),
                alt.Tooltip("Credit:Q", title="Credit %", format=".0f"),
            ],
        )
        .properties(height=230, background="transparent")
        .configure_view(strokeWidth=0, fill="transparent")
    )

    st.altair_chart(chart, use_container_width=True)
    st.markdown(
        '<p style="font-size:0.68rem;color:#3a3a58;margin-top:0.1rem;">'
        'Sample journey: Paid Social → Paid Search → Email → Paid Social → Direct'
        '</p>',
        unsafe_allow_html=True,
    )


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

    w = WorkspaceClient()
    job = next((j for j in w.jobs.list() if j.settings and j.settings.name == _JOB_NAME), None)
    if not job:
        st.error(f"Job '{_JOB_NAME}' not found in this workspace.")
        return

    with st.spinner("Submitting run…"):
        python_params = []
        if agency_id:
            python_params.extend(["--agency", agency_id])
        if dry_run:
            python_params.append("--dry-run")
        if client_filter:
            python_params.append("--client-filter")
            python_params.extend(client_filter)
        python_params.extend(["--attribution-model", attribution_model])
        python_params.extend(["--run-mode", run_mode.lower()])
        run = w.jobs.run_now(job_id=job.job_id, python_params=python_params)
        run_id = run.run_id

    host = os.environ.get("DATABRICKS_HOST", "").lstrip("https://")
    st.markdown(
        f'<a href="https://{host}/#job/{job.job_id}/run/{run_id}" target="_blank" '
        f'style="font-size:0.75rem;color:#7a63ff;text-decoration:none;letter-spacing:0.04em;'
        f'font-family:Inter,sans-serif;">'
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
        from utils.databricks_writer import fetch_recent_pipeline_runs
        runs = fetch_recent_pipeline_runs(limit=8)
    except Exception:
        runs = []

    if not runs:
        st.caption("No runs yet.")
        return

    import datetime
    for r in runs:
        state = str(r.get("status", "unknown")).upper()
        color = "#34d399" if state == "SUCCESS" else ("#f87171" if state == "FAILED" else "#7a63ff")
        start = r.get("started_at")
        if hasattr(start, "strftime"):
            ts = start.strftime("%b %d, %H:%M")
        else:
            ts = str(start or "—")[:16]
        label = (
            f"{r.get('client_id', '')} · {r.get('attribution_model', '')} · "
            f"${float(r.get('total_pipeline') or 0):,.0f}"
        )
        st.markdown(
            f'<div class="run-row">'
            f'<span class="run-ts">{ts} · {label}</span>'
            f'<span class="run-state" style="color:{color};">● {state}</span>'
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
                attribution_model=attribution_model,
                run_mode=run_mode.lower(),
            )
        except Exception as exc:
            st.error(f"Pipeline crashed: {exc}")
            root_logger.removeHandler(handler)
            st.stop()

    root_logger.removeHandler(handler)
    _render_results(result, dry_run)


def _render_results(result: dict, dry_run: bool) -> None:
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
                + ("Dry run — email skipped" if dry_run
                   else f"Sent · {cfg.client_report_email}" if r["email_sent"]
                   else "Email not sent")
            )

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


def _render_client_manager() -> None:
    st.markdown("## Client Setup")

    existing_clients = list_clients()
    action = st.radio(
        "Mode",
        ["Add client", "Edit client"],
        horizontal=True,
        label_visibility="collapsed",
    )

    selected_client_id = ""
    base = ClientConfig(
        client_id="",
        client_name="",
        attribution_model="last_touch",
        databricks_schema="",
        lookback_days=30,
    )
    if action == "Edit client" and existing_clients:
        selected_client_id = st.selectbox(
            "Client",
            existing_clients,
            format_func=_client_label,
        )
        base = get_client(selected_client_id)

    with st.form("client_config_form"):
        c1, c2 = st.columns(2)
        with c1:
            client_name = st.text_input("Business name", value=base.client_name)
            default_id = base.client_id or slugify_client_id(client_name or "new_client")
            client_id = st.text_input(
                "Client ID",
                value=default_id,
                disabled=bool(base.client_id),
            )
            display_name = st.text_input(
                "Dashboard name",
                value=base.client_display_name or base.client_name,
            )
            report_email = st.text_input(
                "Report email",
                value=base.client_report_email,
            )

        with c2:
            agency_options = [""] + list_agencies()
            agency_index = (
                agency_options.index(base.agency_id)
                if base.agency_id in agency_options else 0
            )
            agency_id = st.selectbox(
                "Agency",
                agency_options,
                index=agency_index,
                format_func=lambda a: "Direct account" if not a else AGENCY_REGISTRY[a].agency_name,
            )
            model_index = _MODEL_KEYS.index(base.attribution_model) if base.attribution_model in _MODEL_KEYS else 0
            attribution_model = st.selectbox(
                "Default model",
                _MODEL_KEYS,
                index=model_index,
                format_func=lambda m: ATTRIBUTION_MODELS[m]["label"],
            )
            lookback_days = st.number_input(
                "Lookback days",
                min_value=1,
                max_value=365,
                value=int(base.lookback_days or 30),
                step=1,
            )
            schema_default = base.databricks_schema or default_client_schema(client_id or default_id)
            databricks_schema = st.text_input("Databricks schema", value=schema_default)

        st.markdown('<hr class="divider">', unsafe_allow_html=True)
        s1, s2, s3, s4 = st.columns(4)
        with s1:
            meta_enabled = st.checkbox("Meta Ads", value=base.meta_enabled)
            meta_ad_account_id = st.text_input(
                "Meta account ID",
                value=base.meta_ad_account_id,
                disabled=not meta_enabled,
            )
        with s2:
            google_ads_enabled = st.checkbox("Google Ads", value=base.google_ads_enabled)
            google_ads_customer_id = st.text_input(
                "Google customer ID",
                value=base.google_ads_customer_id,
                disabled=not google_ads_enabled,
            )
        with s3:
            linkedin_ads_enabled = st.checkbox("LinkedIn Ads", value=base.linkedin_ads_enabled)
            linkedin_ads_account_id = st.text_input(
                "LinkedIn account ID",
                value=base.linkedin_ads_account_id,
                disabled=not linkedin_ads_enabled,
            )
        with s4:
            hubspot_enabled = st.checkbox("HubSpot", value=base.hubspot_enabled)
            hubspot_pipeline_id = st.text_input(
                "HubSpot pipeline ID",
                value=base.hubspot_pipeline_id,
                disabled=not hubspot_enabled,
            )
            stripe_enabled = st.checkbox("Stripe", value=base.stripe_enabled)
            stripe_account_id = st.text_input(
                "Stripe account ID",
                value=base.stripe_account_id,
                disabled=not stripe_enabled,
            )

        a1, a2 = st.columns(2)
        with a1:
            spend_drop_pct_alert = st.slider(
                "Spend drop alert",
                min_value=0.05,
                max_value=0.90,
                value=float(base.spend_drop_pct_alert or 0.30),
                step=0.05,
            )
        with a2:
            zero_spend_days_allowed = st.number_input(
                "Zero-spend days",
                min_value=0,
                max_value=30,
                value=int(base.zero_spend_days_allowed or 1),
                step=1,
            )

        save_btn = st.form_submit_button("Save client", type="primary", use_container_width=True)

    if save_btn:
        clean_id = base.client_id or slugify_client_id(client_id or client_name)
        if not client_name.strip():
            st.error("Business name is required.")
            return
        if not clean_id:
            st.error("Client ID is required.")
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
            spend_drop_pct_alert=float(spend_drop_pct_alert),
            zero_spend_days_allowed=int(zero_spend_days_allowed),
            stripe_enabled=stripe_enabled,
            stripe_account_id=stripe_account_id.strip(),
            agency_id=agency_id,
            client_report_email=report_email.strip(),
            client_display_name=display_name.strip(),
        )
        save_client_config(config)
        st.success(f"Saved {config.client_name}.")
        st.caption(f"Registry: {CLIENT_REGISTRY_PATH}")
        st.rerun()

    if action == "Edit client" and selected_client_id and is_custom_client(selected_client_id):
        if st.button("Delete client", type="secondary"):
            delete_client_config(selected_client_id)
            st.success("Client deleted.")
            st.rerun()


# ═══════════════════════════════════════════════
# UI
# ═══════════════════════════════════════════════

# ── Top nav bar ───────────────────────────────
env_label = "Databricks" if _DATABRICKS_MODE else "Local"
env_dot_color = "#7a63ff" if _DATABRICKS_MODE else "#34d399"

st.markdown(
    f'<div class="top-bar">'
    f'  <div class="top-bar-brand">'
    f'    <div class="top-bar-logo">◆</div>'
    f'    <span class="top-bar-title">Attribution Command Center</span>'
    f'  </div>'
    f'  <div class="env-badge">'
    f'    <span class="env-dot" style="background:{env_dot_color};'
    f'          box-shadow:0 0 6px {env_dot_color};"></span>'
    f'    <span style="color:#5a5a80;">{env_label}</span>'
    f'  </div>'
    f'</div>',
    unsafe_allow_html=True,
)

# ── Page heading ──────────────────────────────
st.markdown("# Run Attribution Pipeline")
st.markdown(
    '<p style="font-size:0.88rem;color:#3e3e5e;margin:-0.2rem 0 2rem;font-weight:400;">'
    'Select a target, choose your attribution model, and execute the pipeline.</p>',
    unsafe_allow_html=True,
)

workspace_view = st.radio(
    "Workspace",
    ["Run pipeline", "Clients"],
    horizontal=True,
    label_visibility="collapsed",
)

if workspace_view == "Clients":
    st.markdown('<hr class="divider">', unsafe_allow_html=True)
    _render_client_manager()
    st.stop()

# ── Two-column layout: Target | Model ─────────
left_col, spacer, right_col = st.columns([5, 1, 6])

# ── LEFT — Target selection ───────────────────
with left_col:
    st.markdown('<span class="sec-label">Target</span>', unsafe_allow_html=True)

    run_mode = st.radio(
        "Run mode",
        ["Agency", "Business"],
        horizontal=True,
        label_visibility="collapsed",
    )

    st.markdown("<div style='height:0.6rem'></div>", unsafe_allow_html=True)

    agency_id: str = ""
    client_filter: list[str] = []

    if run_mode == "Agency":
        agencies = list_agencies()
        agency_id = st.selectbox(
            "Agency",
            agencies,
            format_func=lambda a: AGENCY_REGISTRY[a].agency_name,
        )
        agency = get_agency(agency_id)
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
        )
        cfg = get_client(selected_client)
        agency_id = cfg.agency_id or (list_agencies()[0] if list_agencies() else "")
        client_filter = [selected_client]

    # Client preview
    if client_filter:
        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
        for cid in client_filter:
            if cid not in CLIENT_REGISTRY:
                continue
            cfg = get_client(cid)
            src_badges = ""
            if cfg.meta_enabled:
                src_badges += '<span class="src-badge src-meta">Meta</span>'
            if getattr(cfg, "google_ads_enabled", False):
                src_badges += '<span class="src-badge src-hubspot">Google</span>'
            if getattr(cfg, "linkedin_ads_enabled", False):
                src_badges += '<span class="src-badge src-meta">LinkedIn</span>'
            if cfg.hubspot_enabled:
                src_badges += '<span class="src-badge src-hubspot">HubSpot</span>'
            if cfg.stripe_enabled:
                src_badges += '<span class="src-badge src-stripe">Stripe</span>'
            if not src_badges:
                src_badges = '<span style="font-size:0.68rem;color:#3a3a58;">No sources</span>'

            st.markdown(
                f'<div class="client-row">'
                f'  <span class="client-name">{cfg.client_name}</span>'
                f'  <div class="client-sources">{src_badges}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ── RIGHT — Attribution model ─────────────────
with right_col:
    st.markdown('<span class="sec-label">Attribution Model</span>', unsafe_allow_html=True)

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
    st.markdown('<span class="sec-label">Credit distribution</span>', unsafe_allow_html=True)
    chips = ""
    for channel, pct in model_meta["credits"].items():
        if pct > 0:
            chips += (
                f'<span class="chip">{channel}'
                f' <span class="chip-pct">{pct}%</span></span>'
            )
    st.markdown(f'<div style="margin-bottom:1.4rem;">{chips}</div>', unsafe_allow_html=True)

    # Comparison chart
    st.markdown('<span class="sec-label">Model comparison</span>', unsafe_allow_html=True)
    _render_comparison_chart(selected_model)


# ── Run controls ──────────────────────────────
st.markdown('<hr class="divider">', unsafe_allow_html=True)

ctrl_l, ctrl_m, ctrl_r = st.columns([3, 1, 1])

with ctrl_l:
    dry_run = st.checkbox("Dry run — generate report, skip email delivery")

n_clients = len(client_filter)
btn_label = f"Run All ({n_clients})" if (
    run_mode == "Agency" and agency_id and
    n_clients == len(_client_ids_for_agency(agency_id))
) else f"Run Selected ({n_clients})"

with ctrl_m:
    run_btn = st.button(
        btn_label,
        type="primary",
        use_container_width=True,
        disabled=not client_filter,
    )

with ctrl_r:
    if _DATABRICKS_MODE:
        if st.button("Recent runs", type="secondary", use_container_width=True):
            st.session_state["show_runs"] = not st.session_state.get("show_runs", False)

if _DATABRICKS_MODE and st.session_state.get("show_runs"):
    st.markdown('<hr class="divider">', unsafe_allow_html=True)
    st.markdown("## Recent runs")
    _show_recent_runs()

# ── Execute ───────────────────────────────────
if run_btn and client_filter and agency_id:
    run_type = "Dry run" if dry_run else "Live"
    st.markdown(
        f'<div class="run-summary">'
        f'<strong>{model_meta["label"]}</strong>'
        f'<span style="color:#2a2a40;">·</span>'
        f'{n_clients} client{"s" if n_clients != 1 else ""}'
        f'<span style="color:#2a2a40;">·</span>'
        f'{run_type}'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<hr class="divider">', unsafe_allow_html=True)
    if _DATABRICKS_MODE:
        _trigger_databricks_job(
            agency_id,
            client_filter,
            dry_run,
            selected_model,
            run_mode,
        )
    else:
        _run_local(
            agency_id,
            client_filter,
            dry_run,
            selected_model,
            run_mode,
        )
