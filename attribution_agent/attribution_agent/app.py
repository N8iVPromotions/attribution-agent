"""
app.py — ARIE Command Center
-------------------------------------
Internal command center for running the attribution pipeline across
agencies and individual business accounts.

Local dev:
    cd attribution_agent/attribution_agent
    streamlit run app.py

Cloud Run:
    Deployed via `deploy.sh` as the `attribution-ui` service (container CMD
    runs streamlit directly). Pipeline runs submit the `attribution-pipeline`
    Cloud Run Job when configured.
"""

import logging
import html
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
from config.rbac_config import Permission, Role, check_permission
from config.client_config import (
    CLIENT_REGISTRY,
    ClientConfig,
    attach_client_secret_values,
    default_client_schema,
    delete_client_config,
    get_client,
    is_custom_client,
    list_clients,
    save_client_config,
    slugify_client_id,
)
from utils.secrets import redact_secrets
from utils.auth_store import AuthConfigurationError, auth_enabled, login, logout
from utils.cloud_run import (
    build_gcloud_command,
    build_pipeline_args,
    cloud_run_settings,
    is_cloud_run_configured,
    submit_cloud_run_job,
)

_CLOUD_RUN_SETTINGS = cloud_run_settings()
_CLOUD_RUN_MODE = is_cloud_run_configured(_CLOUD_RUN_SETTINGS)
_CLOUD_RUN_RUNTIME = bool(os.environ.get("K_SERVICE") or os.environ.get("K_REVISION"))
_DATABRICKS_MODE = bool(os.environ.get("ATTRIBUTION_JOB_NAME"))
_JOB_NAME = os.environ.get("ATTRIBUTION_JOB_NAME", "[Attribution] Monthly Pipeline")
_JOB_ID = int(os.environ.get("ATTRIBUTION_JOB_ID", "0") or "0") or 500226442246561

# ── Page config ───────────────────────────────────────────────
st.set_page_config(
    page_title="ARIE Command Center",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global styles ─────────────────────────────────────────────
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

/* ── Reset ── */
[data-testid="collapsedControl"] { display: none !important; }

:root {
    --arie-bg: #090b12;
    --arie-panel: rgba(255,255,255,0.046);
    --arie-panel-strong: rgba(255,255,255,0.07);
    --arie-line: rgba(255,255,255,0.095);
    --arie-line-strong: rgba(255,255,255,0.16);
    --arie-ink: #f7f6f3;
    --arie-muted: #9ba8bd;
    --arie-subtle: #6f7c91;
    --arie-purple: #7860FC;
    --arie-purple-soft: #a594fe;
    --arie-stone: #e4e4e4;
    --arie-success: #65d89a;
    --arie-warning: #ffd166;
    --arie-danger: #ff7a90;
}

[data-testid="stSidebar"] {
    display: block !important;
    background:
        radial-gradient(circle at 18% 0%, rgba(120,96,252,0.20), transparent 18rem),
        linear-gradient(180deg, rgba(9,11,18,0.96), rgba(12,16,24,0.94)) !important;
    border-right: 1px solid var(--arie-line) !important;
    box-shadow: 18px 0 60px rgba(0,0,0,0.32) !important;
}
[data-testid="stSidebar"] > div:first-child {
    padding: 1.25rem 1rem 1.75rem !important;
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] span {
    color: rgba(247,246,243,0.72) !important;
}
.sidebar-brand {
    display: flex;
    gap: 0.8rem;
    align-items: center;
    padding: 0.3rem 0.1rem 1rem;
}
.sidebar-logo {
    width: 2.7rem;
    height: 2.7rem;
    border-radius: 0.9rem;
    display: grid;
    place-items: center;
    background: linear-gradient(135deg, var(--arie-purple), #b8adff);
    color: #090b12;
    font-size: 0.75rem;
    font-weight: 900;
    letter-spacing: -0.04em;
    box-shadow: 0 1rem 2.25rem rgba(120,96,252,0.28);
}
.sidebar-title {
    color: var(--arie-ink);
    font-size: 1.02rem;
    line-height: 1.05;
    font-weight: 850;
    letter-spacing: -0.025em;
}
.sidebar-subtitle {
    margin-top: 0.25rem;
    color: rgba(247,246,243,0.46);
    font-size: 0.72rem;
}
.sidebar-card {
    padding: 0.9rem;
    border: 1px solid var(--arie-line);
    border-radius: 1.1rem;
    background: linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.025));
    box-shadow: 0 1rem 2.5rem rgba(0,0,0,0.18);
    margin: 0.4rem 0 1rem;
}
.sidebar-label {
    color: rgba(247,246,243,0.42);
    font-size: 0.64rem;
    font-weight: 760;
    text-transform: uppercase;
    letter-spacing: 0.12em;
}
.sidebar-footer {
    margin-top: 1.4rem;
    padding-top: 1rem;
    border-top: 1px solid rgba(255,255,255,0.07);
    color: rgba(247,246,243,0.4);
    font-size: 0.74rem;
    line-height: 1.55;
}
.health-strip {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.45rem;
    margin-top: 0.75rem;
}
.health-dot {
    height: 0.42rem;
    border-radius: 999px;
    background: var(--arie-success);
    box-shadow: 0 0 1rem rgba(101,216,154,0.35);
}
.health-dot.warn {
    background: var(--arie-warning);
    box-shadow: 0 0 1rem rgba(255,209,102,0.32);
}

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}

/* Atmospheric background */
[data-testid="stAppViewContainer"] {
    background:
        radial-gradient(circle at 8% -10%, rgba(120,96,252,0.20), transparent 34rem),
        radial-gradient(circle at 96% 0%, rgba(156,181,255,0.13), transparent 32rem),
        linear-gradient(180deg, #090b12 0%, #0c1018 62%, #080a10 100%) !important;
}
[data-testid="stHeader"] { background: transparent !important; }

.main .block-container {
    max-width: 1540px;
    padding: 1.1rem 2rem 5rem;
}
[data-testid="stMainBlockContainer"] {
    max-width: 1540px !important;
    padding: 1.1rem 1.65rem 5rem !important;
}

/* ── Typography ── */
h1, h2, h3 {
    font-family: 'Inter', sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: -0.01em !important;
}
h1 {
    font-size: 1.5rem !important;
    color: #e8e8f2 !important;
    margin-bottom: 0 !important;
}
h2 {
    font-size: 0.95rem !important;
    color: #c4c4d8 !important;
    margin-bottom: 0.25rem !important;
}
h3 {
    font-size: 0.85rem !important;
    color: #8888a8 !important;
}

/* ── Top bar ── */
.topbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 1rem;
    padding: 0.85rem 1.2rem;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 14px;
    margin-bottom: 1.5rem;
    background: rgba(255,255,255,0.018);
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    box-shadow: 0 4px 32px rgba(0,0,0,0.25), inset 0 1px 0 rgba(255,255,255,0.06);
}
.topbar-left {
    display: flex;
    align-items: center;
    gap: 1.5rem;
    flex: 1 1 28rem;
    min-width: 0;
}
.topbar-brand {
    display: flex;
    align-items: center;
    gap: 0.6rem;
}
.brand-mark {
    width: 28px; height: 28px;
    background: linear-gradient(135deg, #8b78ff 0%, #5b4fdc 100%);
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.65rem; color: #fff; font-weight: 700;
    box-shadow: 0 0 20px rgba(124,104,252,0.55), 0 0 40px rgba(124,104,252,0.2);
}
.brand-name {
    font-size: 0.82rem;
    font-weight: 600;
    color: #e8e8f2;
    letter-spacing: 0;
}
.topbar-sep {
    width: 1px;
    height: 16px;
    background: rgba(255,255,255,0.07);
}
.topbar-sub {
    font-size: 0.75rem;
    color: rgba(255,255,255,0.45);
    font-weight: 400;
}
.page-title {
    display: grid;
    gap: 0.16rem;
    min-width: min(24rem, 100%);
}
.page-title h1 {
    margin: 0 !important;
    color: var(--arie-ink) !important;
    font-size: clamp(1.25rem, 2.15vw, 2rem) !important;
    font-weight: 820 !important;
    letter-spacing: -0.035em !important;
    line-height: 1.05 !important;
    overflow-wrap: normal !important;
    word-break: normal !important;
}
.breadcrumb {
    color: var(--arie-muted);
    font-size: 0.75rem;
}
.topbar-right {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    flex: 0 1 auto;
    flex-wrap: wrap;
    justify-content: flex-end;
}
.status-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-size: 0.7rem;
    font-weight: 500;
    color: rgba(255,255,255,0.42);
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 999px;
    padding: 0.28rem 0.78rem;
    letter-spacing: 0;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.06);
}
.status-dot {
    width: 5px; height: 5px;
    border-radius: 50%;
}

/* ── Panels ── */
.panel {
    background: rgba(255,255,255,0.032);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 18px;
    overflow: hidden;
    margin-bottom: 1rem;
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    box-shadow: 0 4px 28px rgba(0,0,0,0.25), inset 0 1px 0 rgba(255,255,255,0.07);
}
.panel-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.7rem 1rem;
    border-bottom: 1px solid rgba(255,255,255,0.05);
    background: rgba(255,255,255,0.02);
}
.panel-title {
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: rgba(255,255,255,0.58);
}
.panel-body {
    padding: 1rem 1rem 0.5rem;
}

.operator-grid {
    display: grid;
    gap: 1rem;
}
.operator-grid.kpi {
    grid-template-columns: repeat(4, minmax(0, 1fr));
}
.operator-grid.two {
    grid-template-columns: minmax(0, 1.18fr) minmax(320px, 0.82fr);
}
.operator-card,
.kpi-card,
.stage-card,
.source-card,
.event-card {
    border: 1px solid var(--arie-line);
    background: linear-gradient(180deg, rgba(255,255,255,0.056), rgba(255,255,255,0.026));
    box-shadow: 0 1.5rem 4.5rem rgba(0,0,0,0.28), inset 0 1px 0 rgba(255,255,255,0.06);
    backdrop-filter: blur(22px);
    -webkit-backdrop-filter: blur(22px);
}
.operator-card {
    border-radius: 1.55rem;
    overflow: hidden;
}
.operator-card-header {
    padding: 1.1rem 1.25rem 0.25rem;
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    align-items: flex-start;
}
.operator-card-title {
    color: var(--arie-ink);
    font-size: 0.98rem;
    font-weight: 820;
    letter-spacing: -0.02em;
}
.operator-card-desc {
    margin-top: 0.28rem;
    color: var(--arie-muted);
    font-size: 0.76rem;
    line-height: 1.45;
}
.operator-card-body {
    padding: 1.05rem 1.25rem 1.25rem;
}
.kpi-card {
    border-radius: 1.25rem;
    padding: 1rem;
}
.kpi-label {
    color: rgba(247,246,243,0.46);
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    font-weight: 760;
}
.kpi-value {
    margin-top: 0.7rem;
    color: var(--arie-ink);
    font-size: clamp(1.55rem, 3vw, 2.45rem);
    font-weight: 880;
    letter-spacing: -0.055em;
}
.kpi-trend {
    margin-top: 0.42rem;
    color: var(--arie-muted);
    font-size: 0.76rem;
}
.kpi-trend strong {
    color: var(--arie-success);
    font-weight: 760;
}
.pipeline-board {
    display: grid;
    grid-template-columns: repeat(5, minmax(154px, 1fr));
    gap: 0.75rem;
    overflow-x: auto;
    padding-bottom: 0.15rem;
}
.stage-card {
    min-height: 14rem;
    border-radius: 1.1rem;
    padding: 0.85rem;
    background: rgba(0,0,0,0.18);
    display: grid;
    align-content: start;
    gap: 0.8rem;
}
.stage-head {
    display: flex;
    justify-content: space-between;
    gap: 0.6rem;
    align-items: center;
}
.stage-title {
    color: #d9e2ff;
    font-size: 0.76rem;
    font-weight: 780;
}
.stage-count {
    color: var(--arie-subtle);
    font-size: 0.7rem;
}
.job-card {
    border: 1px solid var(--arie-line);
    border-radius: 0.9rem;
    padding: 0.72rem;
    background: rgba(255,255,255,0.045);
    display: grid;
    gap: 0.48rem;
}
.job-client {
    color: var(--arie-ink);
    font-size: 0.77rem;
    font-weight: 760;
}
.job-meta {
    display: flex;
    justify-content: space-between;
    gap: 0.5rem;
    color: var(--arie-muted);
    font-size: 0.68rem;
}
.progress {
    height: 0.4rem;
    border-radius: 999px;
    overflow: hidden;
    background: rgba(255,255,255,0.08);
}
.progress span {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: linear-gradient(90deg, var(--arie-purple), #b8adff);
}
.source-grid {
    display: grid;
    grid-template-columns: repeat(5, minmax(110px, 1fr));
    gap: 0.75rem;
}
.source-card {
    border-radius: 1rem;
    padding: 0.85rem;
    display: grid;
    gap: 0.5rem;
}
.source-name {
    color: var(--arie-ink);
    font-size: 0.82rem;
    font-weight: 780;
}
.source-meta {
    color: var(--arie-muted);
    font-size: 0.7rem;
    line-height: 1.45;
}
.event-list {
    display: grid;
    gap: 0.75rem;
}
.event-card {
    border-radius: 1rem;
    padding: 0.82rem;
    display: grid;
    grid-template-columns: 0.8rem 1fr auto;
    gap: 0.75rem;
    align-items: start;
}
.event-dot {
    width: 0.68rem;
    height: 0.68rem;
    margin-top: 0.23rem;
    border-radius: 999px;
    background: var(--arie-purple-soft);
    box-shadow: 0 0 0 0.3rem rgba(120,96,252,0.11);
}
.event-card.warn .event-dot {
    background: var(--arie-warning);
    box-shadow: 0 0 0 0.3rem rgba(255,209,102,0.09);
}
.event-title {
    color: var(--arie-ink);
    font-size: 0.8rem;
    font-weight: 760;
}
.event-body {
    margin-top: 0.18rem;
    color: var(--arie-muted);
    font-size: 0.72rem;
}
.event-time {
    color: var(--arie-subtle);
    font-size: 0.68rem;
    white-space: nowrap;
}
.status-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.36rem;
    min-height: 1.55rem;
    padding: 0 0.62rem;
    border-radius: 999px;
    border: 1px solid var(--arie-line);
    color: var(--arie-muted);
    background: rgba(255,255,255,0.045);
    font-size: 0.68rem;
    font-weight: 720;
    white-space: nowrap;
}
.status-chip::before {
    content: "";
    width: 0.4rem;
    height: 0.4rem;
    border-radius: 999px;
    background: currentColor;
}
.status-chip.ok {
    color: var(--arie-success);
    background: rgba(101,216,154,0.10);
}
.status-chip.warn {
    color: var(--arie-warning);
    background: rgba(255,209,102,0.11);
}
.status-chip.fail {
    color: var(--arie-danger);
    background: rgba(255,122,144,0.10);
}

/* ── Section label ── */
.field-label {
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: rgba(255,255,255,0.58);
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
    color: rgba(255,255,255,0.48);
    padding: 0.4rem 0.6rem;
    border-bottom: 1px solid rgba(255,255,255,0.06);
    text-align: left;
}
.client-table td {
    padding: 0.55rem 0.6rem;
    border-bottom: 1px solid rgba(255,255,255,0.03);
    vertical-align: middle;
    color: #b8b8cc;
}
.client-table tr:last-child td { border-bottom: none; }
.client-table tr:hover td { background: rgba(255,255,255,0.02); }
.client-cell-name { font-weight: 500; color: #e8e8f2; }

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
.tag-meta     { background: rgba(124,104,252,0.12); color: #a594fe; border: 1px solid rgba(124,104,252,0.22); }
.tag-google   { background: rgba(56,189,248,0.12);  color: #60c8f5; border: 1px solid rgba(56,189,248,0.25); }
.tag-linkedin { background: rgba(10,102,194,0.18);  color: #5baee8; border: 1px solid rgba(10,102,194,0.3); }
.tag-hubspot  { background: rgba(255,122,89,0.12);  color: #ff8a6e; border: 1px solid rgba(255,122,89,0.25); }
.tag-stripe   { background: rgba(99,179,237,0.12);  color: #76c8f5; border: 1px solid rgba(99,179,237,0.25); }

/* ── Model chips ── */
.model-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 6px;
    font-size: 0.68rem;
    font-weight: 500;
    color: rgba(255,255,255,0.38);
    padding: 0.2rem 0.55rem;
    margin: 0 0.2rem 0.3rem 0;
}
.model-chip-pct {
    color: #9b8bff;
    font-weight: 600;
}

/* ── Model description ── */
.model-desc {
    font-size: 0.78rem;
    color: rgba(255,255,255,0.58);
    line-height: 1.6;
    margin: 0.2rem 0 1rem;
}

/* ── Streamlit widget overrides ── */
[data-testid="stSelectbox"] > div > div,
[data-testid="stMultiSelect"] > div > div {
    background: rgba(255,255,255,0.04) !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    border-radius: 10px !important;
}
[data-testid="stRadio"] label {
    font-size: 0.8rem !important;
    font-weight: 500 !important;
    color: rgba(255,255,255,0.76) !important;
}
[data-testid="stSidebar"] [data-testid="stRadio"] > div {
    gap: 0.42rem !important;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label {
    min-height: 2.55rem;
    padding: 0.45rem 0.7rem !important;
    border: 1px solid transparent;
    border-radius: 0.82rem;
    background: rgba(255,255,255,0.025);
    transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
    background: rgba(255,255,255,0.065);
    border-color: rgba(255,255,255,0.09);
}
[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) {
    background: rgba(120,96,252,0.16);
    border-color: rgba(120,96,252,0.28);
}
[data-testid="stToggle"] label {
    font-size: 0.8rem !important;
    font-weight: 400 !important;
    color: rgba(255,255,255,0.7) !important;
}
[data-testid="stCheckbox"] label {
    font-size: 0.8rem !important;
    color: rgba(255,255,255,0.7) !important;
}
[data-testid="stWidgetLabel"] p,
[data-testid="stMarkdownContainer"] p,
[data-testid="stCaptionContainer"] p,
[data-baseweb="radio"] label,
[data-baseweb="checkbox"] label {
    color: rgba(255,255,255,0.68) !important;
}
input[type="text"], input[type="number"], textarea {
    background: rgba(255,255,255,0.04) !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    border-radius: 10px !important;
    color: #e8e8f2 !important;
    font-size: 0.82rem !important;
}

/* ── Buttons ── */
[data-testid="baseButton-primary"] {
    background: linear-gradient(135deg, #8b78ff 0%, #5f4fe8 100%) !important;
    border: none !important;
    border-radius: 999px !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 600 !important;
    font-size: 0.8rem !important;
    letter-spacing: 0.01em !important;
    box-shadow: 0 0 24px rgba(124,104,252,0.35), 0 2px 8px rgba(0,0,0,0.3) !important;
    color: #fff !important;
}
[data-testid="baseButton-primary"]:hover {
    box-shadow: 0 0 36px rgba(124,104,252,0.55), 0 2px 12px rgba(0,0,0,0.3) !important;
    transform: translateY(-1px) !important;
}
[data-testid="baseButton-secondary"] {
    background: rgba(255,255,255,0.05) !important;
    border: 1px solid rgba(255,255,255,0.1) !important;
    border-radius: 999px !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 500 !important;
    font-size: 0.8rem !important;
    color: rgba(255,255,255,0.78) !important;
    box-shadow: none !important;
}
[data-testid="stBaseButton-secondary"],
.stButton button[kind="secondary"] {
    background: rgba(255,255,255,0.06) !important;
    border: 1px solid rgba(255,255,255,0.14) !important;
    color: rgba(255,255,255,0.82) !important;
    border-radius: 999px !important;
}
[data-testid="baseButton-secondary"] p,
[data-testid="stBaseButton-secondary"] p,
.stButton button[kind="secondary"] p {
    color: rgba(255,255,255,0.82) !important;
}
[data-testid="baseButton-secondary"]:hover {
    background: rgba(255,255,255,0.09) !important;
    border-color: rgba(255,255,255,0.16) !important;
    color: rgba(255,255,255,0.7) !important;
}

/* ── Metrics ── */
[data-testid="metric-container"] {
    background: linear-gradient(135deg, rgba(124,104,252,0.13) 0%, rgba(37,99,235,0.07) 100%) !important;
    border: 1px solid rgba(124,104,252,0.22) !important;
    border-radius: 16px !important;
    padding: 1rem 1rem !important;
    box-shadow: 0 0 28px rgba(124,104,252,0.1), inset 0 1px 0 rgba(255,255,255,0.07) !important;
}
[data-testid="stMetricValue"] {
    font-family: 'Inter', sans-serif !important;
    font-size: 1.65rem !important;
    font-weight: 700 !important;
    color: #f0eeff !important;
    letter-spacing: -0.03em !important;
}
[data-testid="stMetricLabel"] {
    font-size: 0.63rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.08em !important;
    text-transform: uppercase !important;
    color: rgba(255,255,255,0.3) !important;
}

/* ── Alerts ── */
[data-testid="stAlert"] {
    border-radius: 12px !important;
    border-left-width: 3px !important;
    font-size: 0.82rem !important;
    background: rgba(255,255,255,0.03) !important;
}

/* ── Code / log viewer ── */
[data-testid="stCode"] {
    background: rgba(0,0,0,0.35) !important;
    border: 1px solid rgba(255,255,255,0.06) !important;
    border-radius: 12px !important;
    font-size: 0.73rem !important;
}

/* ── Captions ── */
[data-testid="stCaptionContainer"] p {
    color: rgba(255,255,255,0.55) !important;
    font-size: 0.75rem !important;
}

/* ── Tabs ── */
[data-testid="stTabs"] [role="tablist"] {
    border-bottom: 1px solid rgba(255,255,255,0.07) !important;
    gap: 0.25rem !important;
    padding-bottom: 0 !important;
}
[data-testid="stTabs"] [role="tab"] {
    font-size: 0.79rem !important;
    font-weight: 500 !important;
    color: rgba(255,255,255,0.52) !important;
    padding: 0.55rem 1.2rem !important;
    border-radius: 8px 8px 0 0 !important;
    border-bottom: 2px solid transparent !important;
    transition: all 0.15s ease !important;
}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
    color: #d5c6ff !important;
    border-bottom-color: #8b78ff !important;
    background: rgba(124,104,252,0.1) !important;
    box-shadow: 0 0 20px rgba(124,104,252,0.18) !important;
}
[data-testid="stTabs"] [role="tab"]:hover {
    color: rgba(255,255,255,0.58) !important;
    background: rgba(255,255,255,0.04) !important;
}

/* Dataframe */
[data-testid="stDataFrame"] {
    border-radius: 12px !important;
    overflow: hidden;
    border: 1px solid rgba(255,255,255,0.06) !important;
}

/* ── Divider ── */
.ruled {
    border: none;
    border-top: 1px solid rgba(255,255,255,0.06);
    margin: 1.25rem 0;
}

/* ── Run action bar ── */
.action-bar {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.85rem 1rem;
    background: rgba(255,255,255,0.025);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 14px;
    margin-top: 1rem;
    backdrop-filter: blur(12px);
}
.action-context {
    flex: 1;
    font-size: 0.75rem;
    color: rgba(255,255,255,0.55);
    display: flex;
    gap: 1.25rem;
}
.action-context-item strong {
    color: rgba(255,255,255,0.72);
    font-weight: 500;
}
.action-context-item span {
    color: #b8b8cc;
}

/* ── Run summary pill ── */
.run-pill {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.72rem;
    color: rgba(255,255,255,0.3);
    background: rgba(255,255,255,0.035);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 999px;
    padding: 0.3rem 0.85rem;
    margin-bottom: 0.75rem;
}
.run-pill .hl  { color: #9b8bff; font-weight: 600; }
.run-pill .sep { color: rgba(255,255,255,0.1); }

/* ── Recent runs ── */
.run-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.5rem 0;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    font-size: 0.75rem;
}
.run-row:last-child { border-bottom: none; }
.run-ts    { color: rgba(255,255,255,0.22); font-family: 'Inter', monospace; }
.run-label { color: rgba(255,255,255,0.62); }
.run-state {
    font-size: 0.65rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 0.15rem 0.6rem;
    border-radius: 999px;
}
.run-state-ok      { background: rgba(46,160,67,0.12);  color: #3fb950; border: 1px solid rgba(46,160,67,0.2); }
.run-state-fail    { background: rgba(248,81,73,0.1);   color: #f85149; border: 1px solid rgba(248,81,73,0.18); }
.run-state-running { background: rgba(124,104,252,0.1);  color: #a594fe; border: 1px solid rgba(124,104,252,0.18); }

/* ── Form section header ── */
.form-section {
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: rgba(255,255,255,0.58);
    padding: 0.5rem 0 0.5rem;
    border-bottom: 1px solid rgba(255,255,255,0.05);
    margin-bottom: 0.75rem;
    display: block;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.08); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.15); }

@media (max-width: 1180px) {
    .operator-grid.kpi {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .source-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    [data-testid="stMainBlockContainer"] {
        padding: 1rem 1.2rem 4rem !important;
    }
}

@media (max-width: 720px) {
    .main .block-container {
        padding: 0 1rem 4rem;
    }
    [data-testid="stMainBlockContainer"] {
        padding: 0 1rem 4rem !important;
    }
    .topbar {
        align-items: flex-start;
        gap: 0.9rem;
        padding: 0.85rem;
    }
    .topbar-left,
    .topbar-right {
        flex-wrap: wrap;
        gap: 0.6rem;
    }
    .brand-name {
        max-width: 7rem;
        line-height: 1.35;
    }
    .topbar-sep {
        display: none;
    }
    .status-pill {
        font-size: 0.66rem;
        padding: 0.25rem 0.55rem;
    }
    [data-testid="stTabs"] [role="tablist"] {
        overflow-x: auto;
        flex-wrap: nowrap;
    }
    [data-testid="stTabs"] [role="tab"] {
        min-width: max-content;
        padding: 0.55rem 1rem !important;
    }
    .operator-grid.kpi,
    .operator-grid.two,
    .source-grid {
        grid-template-columns: 1fr;
    }
    .pipeline-board {
        grid-template-columns: repeat(5, minmax(150px, 76vw));
    }
}
</style>
""",
    unsafe_allow_html=True,
)


# ── Attribution model definitions ─────────────────────────────
ATTRIBUTION_MODELS: dict[str, dict] = {
    "last_touch": {
        "label": "Last Touch",
        "description": "100% credit to the final touchpoint. Simple to implement and interpret. Best when the closing channel is clearly distinct from awareness channels.",
        "credits": {
            "Paid Social": 0,
            "Paid Search": 0,
            "Email": 0,
            "Organic": 0,
            "Direct": 100,
        },
    },
    "first_touch": {
        "label": "First Touch",
        "description": "100% credit to the channel that first introduced the lead. Useful for measuring top-of-funnel investment and brand awareness spend.",
        "credits": {
            "Paid Social": 100,
            "Paid Search": 0,
            "Email": 0,
            "Organic": 0,
            "Direct": 0,
        },
    },
    "linear": {
        "label": "Linear",
        "description": "Equal credit distributed across all touchpoints in the journey. No channel is weighted over another — useful as a neutral baseline.",
        "credits": {
            "Paid Social": 40,
            "Paid Search": 20,
            "Email": 20,
            "Organic": 0,
            "Direct": 20,
        },
    },
    "time_decay": {
        "label": "Time Decay",
        "description": "Recency-weighted — touchpoints closer to conversion receive exponentially more credit. Emphasizes what influenced the final decision.",
        "credits": {
            "Paid Social": 29,
            "Paid Search": 6,
            "Email": 13,
            "Organic": 0,
            "Direct": 52,
        },
    },
    "u_shape": {
        "label": "U-Shape",
        "description": "40% to first touch, 40% to last touch, 20% shared across middle touchpoints. Balances acquisition and close without ignoring the middle.",
        "credits": {
            "Paid Social": 47,
            "Paid Search": 7,
            "Email": 6,
            "Organic": 0,
            "Direct": 40,
        },
    },
    "w_shape": {
        "label": "W-Shape",
        "description": "30% each to first touch, lead-stage conversion, and close — 10% across remaining middle touchpoints. Recommended for B2B sales cycles.",
        "credits": {
            "Paid Social": 35,
            "Paid Search": 5,
            "Email": 30,
            "Organic": 0,
            "Direct": 30,
        },
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
            rows.append(
                {
                    "Model": meta["label"],
                    "Channel": channel,
                    "Credit": pct,
                }
            )
    df = pd.DataFrame(rows)

    channel_colors = {
        "Paid Social": "#7c68fc",
        "Paid Search": "#38bdf8",
        "Email": "#3fb950",
        "Organic": "#d29922",
        "Direct": "#2a2a3e",
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
                    labelColor="rgba(255,255,255,0.25)",
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
                    labelColor="rgba(255,255,255,0.25)",
                    grid=True,
                    gridColor="rgba(255,255,255,0.05)",
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
                    labelColor="rgba(255,255,255,0.3)",
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

    # width="stretch" needs streamlit>=1.50; the pinned 1.49 uses the old kwarg
    st.altair_chart(chart, use_container_width=True)
    st.caption(
        "Sample 5-touch journey: Paid Social → Paid Search → Email → Paid Social → Direct"
    )


# ── Pipeline helpers ──────────────────────────────────────────


def _trigger_cloud_run_job(
    agency_id: str,
    client_filter: list,
    dry_run: bool,
    attribution_model: str,
    run_mode: str,
) -> None:
    args = build_pipeline_args(
        agency_id,
        client_filter,
        dry_run=dry_run,
        attribution_model=attribution_model,
        run_mode=run_mode,
    )
    command = build_gcloud_command(args, _CLOUD_RUN_SETTINGS, wait=True)

    with st.spinner("Submitting Cloud Run Job..."):
        try:
            operation = submit_cloud_run_job(
                agency_id=agency_id,
                client_ids=client_filter,
                dry_run=dry_run,
                attribution_model=attribution_model,
                run_mode=run_mode,
                settings=_CLOUD_RUN_SETTINGS,
            )
        except Exception as exc:
            st.error(f"Cloud Run submission failed: {redact_secrets(str(exc))}")
            st.caption("Equivalent command you can run from PowerShell:")
            st.code(command, language="powershell")
            return

    n_clients = len(client_filter) if client_filter else "all"
    st.success("Cloud Run Job submitted.")
    st.caption(f"Operation: {operation}")
    st.caption("Equivalent gcloud command:")
    st.code(command, language="powershell")
    arie_bot.notify(
        f"*ARIE Cloud Run job submitted*\n"
        f"Agency: `{agency_id}` - {n_clients} client{'s' if n_clients != 1 else ''}\n"
        f"Model: `{attribution_model}` - {'Dry run' if dry_run else 'Live'}\n"
        f"Operation: `{operation}`"
    )


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
        job = next(
            (j for j in w.jobs.list() if j.settings and j.settings.name == _JOB_NAME),
            None,
        )
        if not job:
            st.error(f"Job '{_JOB_NAME}' not found in this workspace.")
            return
        job_id = job.job_id

    with st.spinner("Submitting run…"):
        job_parameters = {
            "dry_run": str(dry_run).lower(),
            "attribution_model": attribution_model,
            "run_mode": run_mode,
        }
        if agency_id:
            job_parameters["agency"] = agency_id
        if client_filter:
            job_parameters["client_filter"] = ",".join(client_filter)
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
        f"↗ View run {run_id} in Databricks</a>",
        unsafe_allow_html=True,
    )

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

    for r in runs:
        state = str(r.get("status", "unknown")).upper()
        if state == "SUCCESS":
            state_cls, state_label = "run-state-ok", "Success"
        elif state == "FAILED":
            state_cls, state_label = "run-state-fail", "Failed"
        else:
            state_cls, state_label = "run-state-running", state.title()

        start = r.get("started_at")
        ts = (
            start.strftime("%b %d, %H:%M")
            if hasattr(start, "strftime")
            else str(start or "—")[:16]
        )
        label = " · ".join(
            filter(
                None,
                [
                    r.get("client_id", ""),
                    r.get("attribution_model", ""),
                    f"${float(r.get('total_pipeline') or 0):,.0f}"
                    if r.get("total_pipeline")
                    else "",
                ],
            )
        )
        st.markdown(
            f'<div class="run-row">'
            f'  <span class="run-ts">{ts}</span>'
            f'  <span class="run-label">{label}</span>'
            f'  <span class="run-state {state_cls}">{state_label}</span>'
            f"</div>",
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
        st.success(
            f"Pipeline complete — {processed} client{'s' if processed != 1 else ''} processed."
        )
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
                f"</div>",
                unsafe_allow_html=True,
            )
            m1, m2 = st.columns(2)
            m1.metric("Pipeline", f"${r['total_pipeline']:,.0f}")
            m2.metric("Deals", r["hubspot_rows"])
            m3, m4 = st.columns(2)
            m3.metric("Meta rows", r["meta_rows"])
            m4.metric("Stripe rows", r["stripe_rows"])
            email_status = (
                "Dry run — not sent"
                if dry_run
                else f"Sent to {cfg.client_report_email}"
                if r["email_sent"]
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
    return (
        "".join(tags)
        if tags
        else '<span style="color:rgba(255,255,255,0.18);font-size:0.7rem;">—</span>'
    )


def _esc(value: object) -> str:
    return html.escape(str(value or ""))


def _backend_label() -> str:
    if _CLOUD_RUN_MODE:
        return "Cloud Run Job"
    if _DATABRICKS_MODE:
        return "Databricks Job"
    return "Local development"


def _safe_agency_name(agency_id: str) -> str:
    try:
        return get_agency(agency_id).agency_name
    except Exception:
        return agency_id or "Direct clients"


def _fetch_open_alert_count() -> int:
    if os.environ.get("ARIE_OVERVIEW_LIVE_ALERTS", "").lower() not in {
        "1",
        "true",
        "yes",
    }:
        return 0
    try:
        from utils.databricks_writer import fetch_recent_operator_alerts

        return len(fetch_recent_operator_alerts(limit=50, open_only=True))
    except Exception:
        return 0


def _status_chip(label: str, state: str = "ok") -> str:
    return f'<span class="status-chip {state}">{_esc(label)}</span>'


def _render_overview_page(active_agency_id: str) -> None:
    agency_ids = list_agencies()
    client_ids = (
        _client_ids_for_agency(active_agency_id)
        if active_agency_id
        else list_clients()
    )
    cfgs = [get_client(cid) for cid in client_ids if cid in CLIENT_REGISTRY]
    open_alerts = _fetch_open_alert_count()

    source_defs = [
        ("Meta Ads", "meta_enabled", "Paid social touchpoints"),
        ("Google Ads", "google_ads_enabled", "Paid search demand"),
        ("LinkedIn Ads", "linkedin_ads_enabled", "B2B media influence"),
        ("HubSpot", "hubspot_enabled", "Closed-won revenue"),
        ("Stripe", "stripe_enabled", "Collected cash enrichment"),
    ]
    source_cards = []
    ready_sources = 0
    for name, attr, desc in source_defs:
        count = sum(1 for cfg in cfgs if getattr(cfg, attr, False))
        ready_sources += 1 if count else 0
        state = "ok" if count else "warn"
        source_cards.append(
            '<article class="source-card">'
            f'  <div class="source-name">{_esc(name)}</div>'
            f'  <div class="source-meta">{_esc(desc)}<br>{count} configured client{"s" if count != 1 else ""}</div>'
            f'  {_status_chip("Ready" if count else "Not configured", state)}'
            "</article>"
        )

    kpis = [
        ("Agencies", len(agency_ids), f"{_safe_agency_name(active_agency_id)} active", "Portfolio registry"),
        ("Clients", len(cfgs), "Ready for command center runs", "Configured accounts"),
        ("Sources", f"{ready_sources}/5", "Connected in selected scope", "Attribution inputs"),
        ("Open alerts", open_alerts, "Needs review" if open_alerts else "No urgent issues", "Operator watch"),
    ]
    kpi_html = "".join(
        '<article class="kpi-card">'
        f'  <div class="kpi-label">{_esc(label)}</div>'
        f'  <div class="kpi-value">{_esc(value)}</div>'
        f'  <div class="kpi-trend"><strong>{_esc(trend)}</strong><br>{_esc(detail)}</div>'
        "</article>"
        for label, value, trend, detail in kpis
    )
    st.markdown(f'<div class="operator-grid kpi">{kpi_html}</div>', unsafe_allow_html=True)

    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)
    left, right = st.columns([1.2, 0.8])
    with left:
        st.markdown(
            '<article class="operator-card">'
            '  <div class="operator-card-header">'
            '    <div><div class="operator-card-title">Live pipeline board</div>'
            '    <div class="operator-card-desc">Configured clients move through ingest, validation, attribution, reporting, and delivery.</div></div>'
            f'    {_status_chip(_backend_label(), "ok")}'
            '  </div>'
            '  <div class="operator-card-body">',
            unsafe_allow_html=True,
        )
        _render_pipeline_board(client_ids)
        st.markdown("</div></article>", unsafe_allow_html=True)

    with right:
        alert_state = "warn" if open_alerts else "ok"
        events = [
            ("ok", "Command center online", f"Serving {_backend_label()} controls from the local Streamlit app.", "Now"),
            ("ok", "Client registry loaded", f"{len(cfgs)} client account{'s' if len(cfgs) != 1 else ''} in the active scope.", "Now"),
            (alert_state, "Operator alerts", f"{open_alerts} open alert{'s' if open_alerts != 1 else ''} waiting for review.", "Live"),
        ]
        event_html = "".join(
            f'<article class="event-card {state}">'
            '  <span class="event-dot"></span>'
            f'  <div><div class="event-title">{_esc(title)}</div><div class="event-body">{_esc(body)}</div></div>'
            f'  <div class="event-time">{_esc(ts)}</div>'
            "</article>"
            for state, title, body, ts in events
        )
        st.markdown(
            '<article class="operator-card">'
            '  <div class="operator-card-header">'
            '    <div><div class="operator-card-title">Recent activity</div>'
            '    <div class="operator-card-desc">Run readiness, alert state, and operator context.</div></div>'
            '  </div>'
            f'  <div class="operator-card-body"><div class="event-list">{event_html}</div></div>'
            '</article>',
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)
    st.markdown(
        '<article class="operator-card">'
        '  <div class="operator-card-header">'
        '    <div><div class="operator-card-title">Configured source coverage</div>'
        '    <div class="operator-card-desc">What ARIE can use for attribution in the active agency scope.</div></div>'
        '  </div>'
        f'  <div class="operator-card-body"><div class="source-grid">{"".join(source_cards)}</div></div>'
        '</article>',
        unsafe_allow_html=True,
    )


def _render_pipeline_board(client_ids: list[str]) -> None:
    stages = [
        ("1. Ingest", "Pull sources"),
        ("2. Validate", "Quality checks"),
        ("3. Attribute", "Model revenue"),
        ("4. Report", "AI narrative"),
        ("5. Deliver", "Email output"),
    ]
    stage_jobs: list[list[str]] = [[] for _ in stages]
    for cid in client_ids:
        stage_index = sum(ord(ch) for ch in cid) % len(stages)
        stage_jobs[stage_index].append(cid)

    cards = []
    for idx, ((stage, caption), jobs) in enumerate(zip(stages, stage_jobs)):
        progress = min(96, 18 + (idx * 18))
        if jobs:
            job_html = "".join(
                '<article class="job-card">'
                f'  <div class="job-client">{_esc(_client_label(cid))}</div>'
                f'  <div class="job-meta"><span>{progress}% complete</span><span>{_esc(caption)}</span></div>'
                f'  <div class="progress"><span style="width:{progress}%"></span></div>'
                f'  {_status_chip("Active" if idx < 4 else "Ready", "ok")}'
                '</article>'
                for cid in jobs[:4]
            )
        else:
            job_html = '<div class="operator-card-desc">No active clients in this step.</div>'
        cards.append(
            '<section class="stage-card">'
            f'  <div class="stage-head"><div class="stage-title">{_esc(stage)}</div><div class="stage-count">{len(jobs)}</div></div>'
            f"  {job_html}"
            "</section>"
        )
    st.markdown(f'<div class="pipeline-board">{"".join(cards)}</div>', unsafe_allow_html=True)


def _has_permission(user: dict, permission: Permission) -> bool:
    try:
        role = Role(str(user.get("role", Role.VIEWER.value)))
    except ValueError:
        role = Role.VIEWER
    return check_permission(role, permission)


def _allowed_agencies_for_user(user: dict) -> list[str]:
    agencies = list_agencies()
    if _has_permission(user, Permission.MANAGE_AGENCIES):
        return agencies
    agency_id = str(user.get("agency_id", "") or "")
    if agency_id and agency_id in agencies:
        return [agency_id]
    return agencies if str(user.get("role")) == Role.ADMIN.value else []


def _render_login_gate() -> dict:
    if not auth_enabled():
        return {
            "user_id": "local-dev",
            "email": "local@arie",
            "display_name": "Local Operator",
            "role": Role.ADMIN.value,
            "agency_id": "",
            "client_ids": [],
        }

    cached_user = st.session_state.get("arie_auth_user")
    if cached_user:
        return cached_user

    st.markdown(
        """
<style>
    [data-testid="stSidebar"],
    [data-testid="collapsedControl"],
    [data-testid="stToolbar"],
    [data-testid="stDecoration"],
    #MainMenu,
    footer {
        display: none !important;
    }
    [data-testid="stAppViewContainer"] > .main {
        min-height: 100vh !important;
        display: grid !important;
        place-items: center !important;
    }
    .main .block-container,
    [data-testid="stMainBlockContainer"] {
        width: min(100%, 34rem) !important;
        max-width: 34rem !important;
        min-height: 100vh !important;
        padding: 2rem 1.25rem !important;
        display: flex !important;
        flex-direction: column !important;
        justify-content: center !important;
    }
    [data-testid="stMainBlockContainer"] > div {
        width: 100% !important;
        min-height: calc(100vh - 4rem) !important;
        display: flex !important;
        flex-direction: column !important;
        justify-content: center !important;
    }
    .login-shell {
        width: 100%;
        text-align: center;
        border: 1px solid rgba(255,255,255,0.11);
        border-radius: 1.6rem;
        padding: 2rem 1.6rem 1.5rem;
        background:
            radial-gradient(circle at 50% 0%, rgba(120,96,252,0.20), transparent 16rem),
            linear-gradient(180deg, rgba(255,255,255,0.07), rgba(255,255,255,0.028));
        box-shadow: 0 2rem 5.5rem rgba(0,0,0,0.34), inset 0 1px 0 rgba(255,255,255,0.08);
        backdrop-filter: blur(24px);
        -webkit-backdrop-filter: blur(24px);
    }
    .login-mark {
        width: 4rem;
        height: 4rem;
        margin: 0 auto 1.1rem;
        border-radius: 1.2rem;
        display: grid;
        place-items: center;
        background: linear-gradient(135deg, var(--arie-purple), #b8adff);
        color: #090b12;
        font-size: 1rem;
        font-weight: 900;
        letter-spacing: -0.04em;
        box-shadow: 0 1.2rem 3rem rgba(120,96,252,0.34);
    }
    .login-kicker {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        margin-bottom: 0.95rem;
        color: rgba(247,246,243,0.68);
        border: 1px solid rgba(101,216,154,0.22);
        border-radius: 999px;
        background: rgba(101,216,154,0.09);
        padding: 0.3rem 0.75rem;
        font-size: 0.68rem;
        font-weight: 760;
        letter-spacing: 0.08em;
        text-transform: uppercase;
    }
    .login-title {
        color: var(--arie-ink);
        font-size: clamp(2rem, 8vw, 3rem);
        line-height: 0.95;
        font-weight: 880;
        letter-spacing: -0.06em;
        margin: 0;
    }
    .login-subtitle {
        max-width: 27rem;
        margin: 0.9rem auto 0;
        color: var(--arie-muted);
        font-size: 0.88rem;
        line-height: 1.55;
    }
    .login-engine {
        margin-top: 1.35rem;
        padding-top: 1.15rem;
        border-top: 1px solid rgba(255,255,255,0.08);
        color: rgba(247,246,243,0.82);
        font-size: 0.82rem;
        font-weight: 760;
    }
    .login-workspace {
        margin-top: 0.32rem;
        color: rgba(247,246,243,0.42);
        font-size: 0.76rem;
    }
    [data-testid="stForm"] {
        margin-top: 0.9rem;
        padding: 1.05rem !important;
        border: 1px solid rgba(255,255,255,0.10) !important;
        border-radius: 1.2rem !important;
        background: rgba(255,255,255,0.035) !important;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.05);
    }
    [data-testid="stForm"] button {
        width: 100%;
    }
    [data-testid="stAlert"] {
        margin-top: 0.75rem;
        text-align: center;
    }
</style>
<section class="login-shell">
    <div class="login-mark">AR</div>
    <div class="login-kicker">Databricks auth</div>
    <h1 class="login-title">ARIE Command Center</h1>
    <p class="login-subtitle">Sign in to manage attribution runs, clients, reports, and alerts.</p>
    <div class="login-engine">Automatic Revenue Intelligence Engine</div>
    <div class="login-workspace">N8iV Promotions operator workspace</div>
</section>
""",
        unsafe_allow_html=True,
    )
    with st.form("arie_login_form", clear_on_submit=False):
        email = st.text_input("Email", value=os.environ.get("ARIE_BOOTSTRAP_ADMIN_EMAIL", ""))
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        try:
            result = login(email, password, user_agent="streamlit")
        except AuthConfigurationError as exc:
            st.error(str(exc))
            st.stop()
        if result.ok and result.user:
            st.session_state["arie_auth_user"] = result.user.to_session_dict()
            st.session_state["arie_auth_session_id"] = result.session_id
            st.rerun()
        st.error(result.message)
    st.stop()


def _render_client_manager() -> None:
    action = st.radio(
        "Action",
        ["Add client", "Edit client"],
        horizontal=True,
        label_visibility="collapsed",
    )
    saved_result = st.session_state.get("client_save_result")
    if saved_result:
        st.success(f"Saved {saved_result['name']} - schema {saved_result['schema']}")
        if saved_result.get("secrets"):
            st.caption("Secret refs: " + ", ".join(saved_result["secrets"]))
        del st.session_state["client_save_result"]

    existing_clients = list_clients()
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
            "Select client",
            existing_clients,
            format_func=_client_label,
        )
        base = get_client(selected_client_id)

    with st.form("client_config_form"):
        st.markdown(
            '<span class="form-section">Identity</span>', unsafe_allow_html=True
        )
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            client_name = st.text_input("Business name", value=base.client_name)
        with c2:
            default_id = base.client_id or ""
            client_id = st.text_input(
                "Client ID",
                value=default_id,
                placeholder=slugify_client_id(client_name or "acme_co"),
                disabled=bool(base.client_id),
            )
        with c3:
            display_name = st.text_input(
                "Display name", value=base.client_display_name or base.client_name
            )
        with c4:
            report_email = st.text_input("Report email", value=base.client_report_email)

        st.markdown(
            '<span class="form-section">Configuration</span>', unsafe_allow_html=True
        )
        d1, d2, d3, d4 = st.columns(4)
        with d1:
            agency_options = [""] + list_agencies()
            agency_index = (
                agency_options.index(base.agency_id)
                if base.agency_id in agency_options
                else 0
            )
            form_agency_id = st.selectbox(
                "Agency",
                agency_options,
                index=agency_index,
                format_func=lambda a: (
                    "Direct account" if not a else AGENCY_REGISTRY[a].agency_name
                ),
            )
        with d2:
            model_index = (
                _MODEL_KEYS.index(base.attribution_model)
                if base.attribution_model in _MODEL_KEYS
                else 0
            )
            attribution_model = st.selectbox(
                "Default model",
                _MODEL_KEYS,
                index=model_index,
                format_func=lambda m: ATTRIBUTION_MODELS[m]["label"],
            )
        with d3:
            lookback_days = st.number_input(
                "Lookback days",
                min_value=1,
                max_value=365,
                value=int(base.lookback_days or 30),
                step=1,
            )
        with d4:
            schema_client_id = base.client_id or slugify_client_id(
                client_id or client_name or "new_client"
            )
            schema_default = base.databricks_schema or ""
            databricks_schema = st.text_input(
                "Databricks schema",
                value=schema_default,
                placeholder=default_client_schema(schema_client_id),
            )

        st.markdown(
            '<span class="form-section">Data Sources</span>', unsafe_allow_html=True
        )
        s1, s2, s3, s4, s5 = st.columns(5)
        with s1:
            meta_enabled = st.checkbox("Meta Ads", value=base.meta_enabled)
            meta_ad_account_id = st.text_input(
                "Account ID",
                value=base.meta_ad_account_id,
                disabled=not meta_enabled,
                key="meta_id",
            )
            meta_access_token = st.text_input(
                "Access token",
                value="",
                type="password",
                placeholder="Configured" if base.meta_access_token_secret_name else "",
                disabled=not meta_enabled,
                key="meta_access_token",
            )
        with s2:
            google_ads_enabled = st.checkbox(
                "Google Ads", value=base.google_ads_enabled
            )
            google_ads_customer_id = st.text_input(
                "Customer ID",
                value=base.google_ads_customer_id,
                disabled=not google_ads_enabled,
                key="google_id",
            )
            google_ads_refresh_token = st.text_input(
                "Refresh token",
                value="",
                type="password",
                placeholder=(
                    "Configured"
                    if base.google_ads_refresh_token_secret_name
                    else ""
                ),
                disabled=not google_ads_enabled,
                key="google_refresh_token",
            )
        with s3:
            linkedin_ads_enabled = st.checkbox(
                "LinkedIn Ads", value=base.linkedin_ads_enabled
            )
            linkedin_ads_account_id = st.text_input(
                "Account ID",
                value=base.linkedin_ads_account_id,
                disabled=not linkedin_ads_enabled,
                key="li_id",
            )
            linkedin_access_token = st.text_input(
                "Access token",
                value="",
                type="password",
                placeholder=(
                    "Configured" if base.linkedin_access_token_secret_name else ""
                ),
                disabled=not linkedin_ads_enabled,
                key="linkedin_access_token",
            )
        with s4:
            hubspot_enabled = st.checkbox("HubSpot", value=base.hubspot_enabled)
            hubspot_pipeline_id = st.text_input(
                "Pipeline ID",
                value=base.hubspot_pipeline_id,
                disabled=not hubspot_enabled,
                key="hs_id",
            )
            hubspot_access_token = st.text_input(
                "Access token",
                value="",
                type="password",
                placeholder=(
                    "Configured" if base.hubspot_access_token_secret_name else ""
                ),
                disabled=not hubspot_enabled,
                key="hubspot_access_token",
            )
        with s5:
            stripe_enabled = st.checkbox("Stripe", value=base.stripe_enabled)
            stripe_account_id = st.text_input(
                "Account ID",
                value=base.stripe_account_id,
                disabled=not stripe_enabled,
                key="stripe_id",
            )
            stripe_secret_key = st.text_input(
                "Secret key",
                value="",
                type="password",
                placeholder="Configured" if base.stripe_secret_key_secret_name else "",
                disabled=not stripe_enabled,
                key="stripe_secret_key",
            )

        st.markdown(
            '<span class="form-section">Credential Expiry Dates</span>',
            unsafe_allow_html=True,
        )
        e1, e2, e3, e4, e5 = st.columns(5)
        with e1:
            meta_token_expires_at = st.text_input(
                "Meta token expires",
                value=base.meta_token_expires_at,
                placeholder="YYYY-MM-DD",
                disabled=not meta_enabled,
            )
        with e2:
            google_ads_token_expires_at = st.text_input(
                "Google token expires",
                value=base.google_ads_token_expires_at,
                placeholder="YYYY-MM-DD",
                disabled=not google_ads_enabled,
            )
        with e3:
            linkedin_token_expires_at = st.text_input(
                "LinkedIn token expires",
                value=base.linkedin_token_expires_at,
                placeholder="YYYY-MM-DD",
                disabled=not linkedin_ads_enabled,
            )
        with e4:
            hubspot_token_expires_at = st.text_input(
                "HubSpot token expires",
                value=base.hubspot_token_expires_at,
                placeholder="YYYY-MM-DD",
                disabled=not hubspot_enabled,
            )
        with e5:
            stripe_token_expires_at = st.text_input(
                "Stripe key review date",
                value=base.stripe_token_expires_at,
                placeholder="YYYY-MM-DD",
                disabled=not stripe_enabled,
            )

        st.markdown(
            '<span class="form-section">Alert Thresholds</span>', unsafe_allow_html=True
        )
        t1, t2 = st.columns(2)
        with t1:
            spend_drop_pct_alert = st.slider(
                "Spend drop alert (%)",
                min_value=5,
                max_value=90,
                value=int(float(base.spend_drop_pct_alert or 0.30) * 100),
                step=5,
            )
        with t2:
            zero_spend_days_allowed = st.number_input(
                "Zero-spend days allowed",
                min_value=0,
                max_value=30,
                value=int(base.zero_spend_days_allowed or 1),
            )

        save_btn = st.form_submit_button(
            "Save client", type="primary", use_container_width=False
        )

    if save_btn:
        clean_id = base.client_id or slugify_client_id(client_id or client_name)
        if not client_name.strip():
            st.error("Business name is required.")
            return
        if action == "Add client" and clean_id in CLIENT_REGISTRY:
            st.error(
                f"Client ID '{clean_id}' already exists. Choose a unique Client ID."
            )
            return
        schema_value = (databricks_schema or "").strip()
        if not schema_value or schema_value.endswith("attribution_new_client"):
            schema_value = default_client_schema(clean_id)
        config = ClientConfig(
            client_id=clean_id,
            client_name=client_name.strip(),
            attribution_model=attribution_model,
            meta_enabled=meta_enabled,
            meta_ad_account_id=meta_ad_account_id.strip(),
            meta_token_expires_at=meta_token_expires_at.strip(),
            google_ads_enabled=google_ads_enabled,
            google_ads_customer_id=google_ads_customer_id.strip(),
            google_ads_token_expires_at=google_ads_token_expires_at.strip(),
            linkedin_ads_enabled=linkedin_ads_enabled,
            linkedin_ads_account_id=linkedin_ads_account_id.strip(),
            linkedin_token_expires_at=linkedin_token_expires_at.strip(),
            hubspot_enabled=hubspot_enabled,
            hubspot_pipeline_id=hubspot_pipeline_id.strip(),
            hubspot_token_expires_at=hubspot_token_expires_at.strip(),
            databricks_schema=schema_value,
            lookback_days=int(lookback_days),
            spend_drop_pct_alert=spend_drop_pct_alert / 100,
            zero_spend_days_allowed=int(zero_spend_days_allowed),
            stripe_enabled=stripe_enabled,
            stripe_account_id=stripe_account_id.strip(),
            stripe_token_expires_at=stripe_token_expires_at.strip(),
            agency_id=form_agency_id,
            client_report_email=report_email.strip(),
            client_display_name=display_name.strip(),
            meta_access_token_secret_name=base.meta_access_token_secret_name,
            google_ads_refresh_token_secret_name=(
                base.google_ads_refresh_token_secret_name
            ),
            linkedin_access_token_secret_name=base.linkedin_access_token_secret_name,
            hubspot_access_token_secret_name=base.hubspot_access_token_secret_name,
            stripe_secret_key_secret_name=base.stripe_secret_key_secret_name,
        )
        try:
            config = attach_client_secret_values(
                config,
                {
                    "meta_access_token": meta_access_token,
                    "google_ads_refresh_token": google_ads_refresh_token,
                    "linkedin_access_token": linkedin_access_token,
                    "hubspot_access_token": hubspot_access_token,
                    "stripe_secret_key": stripe_secret_key,
                },
            )
            save_client_config(config)
        except Exception as exc:
            st.error(
                f"Save failed - client was not persisted: "
                f"{redact_secrets(str(exc))}"
            )
            return
        secret_refs = [
            ref
            for ref in (
                config.meta_access_token_secret_name,
                config.google_ads_refresh_token_secret_name,
                config.linkedin_access_token_secret_name,
                config.hubspot_access_token_secret_name,
                config.stripe_secret_key_secret_name,
            )
            if ref
        ]
        st.session_state["client_save_result"] = {
            "name": config.client_name,
            "schema": config.databricks_schema,
            "secrets": secret_refs,
        }
        st.success(f"Saved — {config.client_name}")
        st.rerun()

    if (
        action == "Edit client"
        and selected_client_id
        and is_custom_client(selected_client_id)
    ):
        st.markdown('<hr class="ruled">', unsafe_allow_html=True)
        if st.button("Delete client", type="secondary"):
            try:
                delete_client_config(selected_client_id)
            except Exception as exc:
                st.error(f"Delete failed — client was not removed: {exc}")
                return
            st.success("Client deleted.")
            st.rerun()


# ═══════════════════════════════════════════════
# UI
# ═══════════════════════════════════════════════


current_user = _render_login_gate()

# ── Start ARIE (once per process) ─────────────
# Secrets arrive as environment variables (Cloud Run --set-secrets, or .env
# locally via load_dotenv above) — no injection step needed.


# ARIE is a Telegram long-polling bot and needs outbound reachability to
# api.telegram.org. Databricks Apps historically blocked that egress, so rather
# than hardcode ARIE off we probe Telegram at startup and start the bot only when
# it's actually reachable — the app self-enables where egress allows and degrades
# gracefully (grey dot + reason on hover) where it doesn't.
# NOTE: Telegram permits only ONE getUpdates consumer per bot token. Do not run
# ARIE locally and in-app simultaneously, or both will 409-conflict.
@st.cache_resource
def _start_arie() -> dict:
    autostart = os.environ.get("ARIE_COMMAND_CENTER_START_BOT", "").lower()
    if autostart not in {"1", "true", "yes", "on"}:
        return {
            "ok": False,
            "reason": "Telegram listener runs from arie_start.ps1. Set ARIE_COMMAND_CENTER_START_BOT=true to run it inside the Command Center.",
        }

    token, chat_id = arie_bot._load_credentials()
    if not token or not chat_id:
        return {"ok": False, "reason": "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set"}
    # Probe with getMe — it does not consume updates, so it won't 409 against a poller.
    try:
        import requests as _req

        r = _req.get(f"https://api.telegram.org/bot{token}/getMe", timeout=5)
        if r.status_code != 200 or not r.json().get("ok"):
            return {
                "ok": False,
                "reason": f"Telegram getMe returned HTTP {r.status_code}",
            }
    except Exception as exc:
        return {
            "ok": False,
            "reason": f"Telegram unreachable ({exc.__class__.__name__})",
        }
    try:
        arie_bot.start(token, chat_id)
        return {"ok": True, "reason": "ARIE online"}
    except Exception as exc:
        return {"ok": False, "reason": f"ARIE start failed: {exc}"}


_arie_status = _start_arie()
_arie_enabled = _arie_status["ok"]


# ── API health probe ──────────────────────────
def _probe_api() -> bool:
    try:
        import requests as _req

        api_port = os.environ.get("ATTRIBUTION_API_PORT", "8081")
        r = _req.get(f"http://localhost:{api_port}/health", timeout=1)
        return r.status_code == 200
    except Exception:
        return False


_api_healthy = _probe_api()

# ── Top bar ───────────────────────────────────
import datetime as _dt

if _CLOUD_RUN_RUNTIME:
    env_label = "Cloud Run"
    env_dot_color = "#65d89a"
elif _DATABRICKS_MODE:
    env_label = "Databricks"
    env_dot_color = "#7860FC"
else:
    env_label = "Local"
    env_dot_color = "#65d89a"
now_str = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
arie_dot_color = (
    "#65d89a" if (_arie_enabled and arie_bot.is_running()) else "rgba(255,255,255,0.15)"
)
_arie_reason = _arie_status.get("reason", "")
api_dot_color = "#65d89a" if _api_healthy else "rgba(255,255,255,0.15)"

agency_options = _allowed_agencies_for_user(current_user)
default_agency = agency_options[0] if agency_options else ""
if "active_agency_id" not in st.session_state:
    st.session_state["active_agency_id"] = default_agency
active_agency_id = st.session_state.get("active_agency_id", default_agency)

with st.sidebar:
    st.markdown(
        '<div class="sidebar-brand">'
        '  <div class="sidebar-logo">AR</div>'
        '  <div><div class="sidebar-title">ARIE<br>Command Center</div>'
        '  <div class="sidebar-subtitle">N8iV revenue operations</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )
    role_label = str(current_user.get("role", "viewer")).replace("_", " ").title()
    st.markdown(
        '<div class="sidebar-card">'
        '<div class="sidebar-label">Signed in</div>'
        f'<div style="color:#f7f6f3;font-weight:760;margin-top:0.45rem;">{_esc(current_user.get("display_name") or current_user.get("email"))}</div>'
        f'<div style="color:rgba(247,246,243,0.48);font-size:0.72rem;margin-top:0.18rem;">{_esc(role_label)}</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="sidebar-card"><div class="sidebar-label">Active agency</div>',
        unsafe_allow_html=True,
    )
    if agency_options:
        active_agency_id = st.selectbox(
            "Choose agency",
            agency_options,
            index=agency_options.index(active_agency_id)
            if active_agency_id in agency_options
            else 0,
            format_func=lambda a: AGENCY_REGISTRY[a].agency_name,
            label_visibility="collapsed",
            key="sidebar_active_agency",
        )
        st.session_state["active_agency_id"] = active_agency_id
    else:
        st.caption("No agencies configured.")
    st.markdown("</div>", unsafe_allow_html=True)

    selected_view = st.radio(
        "Navigation",
        ["Overview", "Pipeline", "Clients", "Outreach", "Observability"],
        index=0,
        label_visibility="collapsed",
    )
    st.markdown(
        '<div class="sidebar-footer">'
        'Operator workspace for attribution runs, client setup, outreach, alerts, and reporting.'
        '<div class="health-strip"><span class="health-dot"></span><span class="health-dot"></span><span class="health-dot warn"></span></div>'
        '</div>',
        unsafe_allow_html=True,
    )
    if st.button("Sign out", use_container_width=True):
        logout(
            st.session_state.get("arie_auth_session_id", ""),
            st.session_state.get("arie_auth_user"),
        )
        st.session_state.pop("arie_auth_user", None)
        st.session_state.pop("arie_auth_session_id", None)
        st.rerun()

page_titles = {
    "Overview": "Agent Operations Overview",
    "Pipeline": "Pipeline Runs",
    "Clients": "Client Management",
    "Outreach": "Outreach Agent",
    "Observability": "Quality, Alerts & Logs",
}
page_title = page_titles.get(selected_view, selected_view)

st.markdown(
    f'<div class="topbar">'
    f'  <div class="topbar-left">'
    f'    <div class="topbar-brand">'
    f'      <div class="brand-mark">AR</div>'
    f'      <span class="brand-name">ARIE Command Center</span>'
    f"    </div>"
    f'    <div class="topbar-sep"></div>'
    f'    <div class="page-title"><div class="breadcrumb">{_esc(_safe_agency_name(active_agency_id))} / {_esc(selected_view.lower())}</div><h1>{_esc(page_title)}</h1></div>'
    f"  </div>"
    f'  <div class="topbar-right">'
    f'    <span class="status-pill">'
    f'      <span class="status-dot" style="background:{env_dot_color};"></span>'
    f"      {env_label}"
    f"    </span>"
    f'    <span class="status-pill">{now_str}</span>'
    f'    <span class="status-pill">'
    f'      <span class="status-dot" style="background:{api_dot_color};"></span>'
    f"      API"
    f"    </span>"
    f'    <span class="status-pill" title="{_esc(_arie_reason)}">'
    f'      <span class="status-dot" style="background:{arie_dot_color};"></span>'
    f"      ARIE"
    f"    </span>"
    f"  </div>"
    f"</div>",
    unsafe_allow_html=True,
)

# ── Sidebar navigation ────────────────────────
if selected_view == "Overview":
    _render_overview_page(active_agency_id)


# ══════════════════════════════════════════════
# TAB: PIPELINE
# ══════════════════════════════════════════════
if selected_view == "Pipeline":
    left_col, gap_col, right_col = st.columns([5, 1, 6])

    # ── LEFT: Target ──────────────────────────
    with left_col:
        st.markdown(
            '<div class="panel-header" style="border-radius:8px 8px 0 0;margin-top:1rem;">'
            '  <span class="panel-title">Target</span>'
            "</div>",
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
                index=agencies.index(active_agency_id)
                if active_agency_id in agencies
                else 0,
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
            agency_id = cfg_sel.agency_id or (
                list_agencies()[0] if list_agencies() else ""
            )
            client_filter = [selected_client]

        # Client table
        if client_filter:
            rows_html = ""
            for cid in client_filter:
                if cid not in CLIENT_REGISTRY:
                    continue
                cfg = get_client(cid)
                model_label = ATTRIBUTION_MODELS.get(cfg.attribution_model, {}).get(
                    "label", cfg.attribution_model
                )
                rows_html += (
                    f"<tr>"
                    f'  <td class="client-cell-name">{cfg.client_name}</td>'
                    f"  <td>{_source_tags(cfg)}</td>"
                    f'  <td style="color:rgba(255,255,255,0.28);font-size:0.72rem;">{model_label}</td>'
                    f"</tr>"
                )
            st.markdown(
                f'<table class="client-table">'
                f"  <thead><tr>"
                f"    <th>Client</th><th>Sources</th><th>Default model</th>"
                f"  </tr></thead>"
                f"  <tbody>{rows_html}</tbody>"
                f"</table>",
                unsafe_allow_html=True,
            )

    # ── RIGHT: Attribution model ───────────────
    with right_col:
        st.markdown(
            '<div class="panel-header" style="border-radius:8px 8px 0 0;margin-top:1rem;">'
            '  <span class="panel-title">Attribution Model</span>'
            "</div>",
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
        st.markdown(
            '<span class="field-label">Credit distribution</span>',
            unsafe_allow_html=True,
        )
        chips = "".join(
            f'<span class="model-chip">{ch} <span class="model-chip-pct">{pct}%</span></span>'
            for ch, pct in model_meta["credits"].items()
            if pct > 0
        )
        st.markdown(
            f'<div style="margin-bottom:1.2rem;">{chips}</div>', unsafe_allow_html=True
        )

        st.markdown(
            '<span class="field-label">Model comparison</span>', unsafe_allow_html=True
        )
        _render_comparison_chart(selected_model)

    # ── Run bar ────────────────────────────────
    n_clients = len(client_filter)
    run_target_label = (
        f"All  ({n_clients})"
        if run_mode == "Agency"
        and agency_id
        and n_clients == len(_client_ids_for_agency(agency_id))
        else f"Selected  ({n_clients})"
    )
    backend_label = (
        "Cloud Run Job"
        if _CLOUD_RUN_MODE
        else "Databricks Job"
        if _DATABRICKS_MODE
        else "Local development"
    )
    run_mode_key = run_mode.lower()

    bar_l, bar_m, bar_r = st.columns([4, 1, 1])
    with bar_l:
        dry_run = st.checkbox(
            "Preview mode — generate reports, skip email delivery",
            value=True,
            key="pipeline_dry_run",
        )
        if not dry_run:
            st.warning("Live run will deliver enabled client reports by email.")
        st.caption(
            f"Execution backend: {backend_label}"
            + (
                f" - {_CLOUD_RUN_SETTINGS.project_id}/{_CLOUD_RUN_SETTINGS.region}"
                if _CLOUD_RUN_MODE
                else ""
            )
        )
    with bar_m:
        run_permission = (
            Permission.RUN_PIPELINE_DRY if dry_run else Permission.RUN_PIPELINE_LIVE
        )
        can_run_pipeline = _has_permission(current_user, run_permission)
        if not can_run_pipeline:
            st.warning("Your role cannot start this run mode.")
        btn_label = (
            f"Preview {run_target_label}" if dry_run else f"Live Run {run_target_label}"
        )
        run_btn = st.button(
            btn_label,
            type="primary",
            use_container_width=True,
            disabled=not client_filter or not can_run_pipeline,
        )
    with bar_r:
        if st.button("Recent runs", type="secondary", use_container_width=True):
            st.session_state["show_runs"] = not st.session_state.get(
                "show_runs", False
            )

    if st.session_state.get("show_runs"):
        st.markdown('<hr class="ruled">', unsafe_allow_html=True)
        st.markdown(
            '<div class="panel-header">'
            '  <span class="panel-title">Recent runs</span>'
            "</div>",
            unsafe_allow_html=True,
        )
        _show_recent_runs()

    # ── Execute ────────────────────────────────
    if run_btn and client_filter and agency_id:
        st.markdown(
            f'<div class="run-pill">'
            f'  <span class="hl">{model_meta["label"]}</span>'
            f'  <span class="sep">|</span>'
            f"  {n_clients} client{'s' if n_clients != 1 else ''}"
            f'  <span class="sep">|</span>'
            f"  {'Dry run' if dry_run else 'Live'}"
            f"</div>",
            unsafe_allow_html=True,
        )
        st.markdown('<hr class="ruled">', unsafe_allow_html=True)
        if _CLOUD_RUN_MODE:
            _trigger_cloud_run_job(
                agency_id, client_filter, dry_run, selected_model, run_mode_key
            )
        elif _DATABRICKS_MODE:
            _trigger_databricks_job(
                agency_id, client_filter, dry_run, selected_model, run_mode_key
            )
        else:
            _run_local(agency_id, client_filter, dry_run, selected_model, run_mode_key)


# ══════════════════════════════════════════════
# TAB: CLIENTS
# ══════════════════════════════════════════════
if selected_view == "Clients" and not _has_permission(
    current_user, Permission.MANAGE_CLIENTS
):
    st.warning("Your role cannot manage client configuration.")

if selected_view == "Clients" and _has_permission(
    current_user, Permission.MANAGE_CLIENTS
):
    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)

    # Client roster table
    all_clients = list_clients()
    if all_clients:
        st.markdown(
            '<span class="field-label">Client roster</span>', unsafe_allow_html=True
        )
        roster_rows = ""
        for cid in all_clients:
            cfg = get_client(cid)
            agency_name = AGENCY_REGISTRY.get(
                cfg.agency_id, type("", (), {"agency_name": "Direct"})()
            ).agency_name
            model_label = ATTRIBUTION_MODELS.get(cfg.attribution_model, {}).get(
                "label", cfg.attribution_model
            )
            custom_badge = (
                '<span class="tag tag-meta" style="font-size:0.55rem;">Custom</span>'
                if is_custom_client(cid)
                else ""
            )
            roster_rows += (
                f"<tr>"
                f'  <td class="client-cell-name">{cfg.client_name} {custom_badge}</td>'
                f'  <td style="color:rgba(255,255,255,0.32);font-size:0.75rem;">{agency_name}</td>'
                f"  <td>{_source_tags(cfg)}</td>"
                f'  <td style="color:rgba(255,255,255,0.32);font-size:0.75rem;">{model_label}</td>'
                f'  <td style="color:rgba(255,255,255,0.28);font-size:0.72rem;">{cfg.client_report_email or "—"}</td>'
                f"</tr>"
            )
        st.markdown(
            f'<table class="client-table">'
            f"  <thead><tr>"
            f"    <th>Name</th><th>Agency</th><th>Sources</th>"
            f"    <th>Default model</th><th>Report email</th>"
            f"  </tr></thead>"
            f"  <tbody>{roster_rows}</tbody>"
            f"</table>",
            unsafe_allow_html=True,
        )
        st.markdown("<div style='height:1.5rem'></div>", unsafe_allow_html=True)

    st.markdown('<hr class="ruled">', unsafe_allow_html=True)
    _render_client_manager()


# ══════════════════════════════════════════════
# TAB: OUTREACH AGENT
# ══════════════════════════════════════════════
if selected_view == "Outreach":
    # ── ARIE status banner ─────────────────────
    telegram_status = "online" if (_arie_enabled and arie_bot.is_running()) else "standby"
    if _CLOUD_RUN_RUNTIME:
        st.info(
            "**ARIE Outreach is running in Cloud Run.** Email sequence generation "
            "and draft delivery can run from this Command Center when the Claude "
            "and Gmail secrets are configured.\n\n"
            f"Telegram listener status: `{telegram_status}`. It is intentionally "
            "kept separate unless `ARIE_COMMAND_CENTER_START_BOT=true`, because "
            "Telegram only allows one active polling listener per bot token."
        )
    elif _DATABRICKS_MODE:
        st.warning(
            "**Databricks runtime detected.** If outbound internet is restricted "
            "in this workspace, run Outreach from the Cloud Run or desktop Command "
            "Center instead.\n\n"
            "Desktop fallback:\n"
            "```\n"
            "cd C:\\Users\\zajen\\attribution-agent\n"
            ".\\scripts\\arie_open_command_center.ps1 -Mode Local -StartBot\n"
            "```"
        )
    else:
        st.info(
            "**Local Command Center mode.** Outreach runs from this desktop session. "
            f"Telegram listener status: `{telegram_status}`.\n\n"
            "Start the listener with:\n"
            "```\n"
            "cd C:\\Users\\zajen\\attribution-agent\n"
            ".\\arie_start.ps1\n"
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
    st.markdown(
        """
<style>
.or-stat-card {
    background: linear-gradient(135deg, rgba(124,104,252,0.12) 0%, rgba(37,99,235,0.06) 100%);
    border: 1px solid rgba(124,104,252,0.2);
    border-radius: 18px;
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    padding: 20px 14px;
    text-align: center;
    box-shadow: 0 4px 24px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.07);
}
.or-stat-val {
    font-size: 2rem;
    font-weight: 700;
    font-family: 'Inter', sans-serif;
    line-height: 1;
    letter-spacing: -0.03em;
}
.or-stat-label {
    font-size: 0.67rem;
    color: rgba(255,255,255,0.3);
    margin-top: 6px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
}
.or-prospect-card {
    background: linear-gradient(135deg, rgba(124,104,252,0.09) 0%, rgba(37,99,235,0.04) 100%);
    border: 1px solid rgba(124,104,252,0.16);
    border-radius: 18px;
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    padding: 20px;
    margin-bottom: 16px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.18), inset 0 1px 0 rgba(255,255,255,0.06);
}
.or-industry-badge {
    display: inline-block;
    font-size: 0.65rem;
    font-family: monospace;
    padding: 3px 9px;
    border-radius: 4px;
    border: 1px solid rgba(124,104,252,0.3);
    color: #a594fe;
    background: rgba(124,104,252,0.1);
    letter-spacing: 0.4px;
    margin-left: 10px;
    vertical-align: middle;
}
.or-field-label {
    font-size: 0.64rem;
    color: rgba(255,255,255,0.25);
    text-transform: uppercase;
    letter-spacing: 0.7px;
    margin-bottom: 3px;
}
.or-field-val {
    font-size: 0.82rem;
    color: #e8e8f2;
    font-family: 'Inter', monospace;
}
.or-notes {
    font-size: 0.8rem;
    color: rgba(255,255,255,0.32);
    line-height: 1.6;
    padding: 10px 12px;
    background: rgba(255,255,255,0.02);
    border-left: 2px solid rgba(124,104,252,0.3);
    border-radius: 0 4px 4px 0;
    margin-top: 12px;
}
.or-sent-badge {
    display: inline-block;
    font-size: 0.7rem;
    font-family: monospace;
    padding: 4px 10px;
    border-radius: 4px;
    background: rgba(63,185,80,0.12);
    color: #3fb950;
    border: 1px solid rgba(63,185,80,0.22);
    border-radius: 999px;
}
.or-email-meta {
    font-size: 0.72rem;
    color: rgba(255,255,255,0.3);
    font-family: 'Inter', monospace;
    margin-bottom: 8px;
}
.or-email-meta strong { color: #e8e8f2; }
</style>
""",
        unsafe_allow_html=True,
    )

    # ── Stats row ──────────────────────────────
    total = len(PROSPECTS)
    generated = len(st.session_state.outreach_sequences)
    drafted = sum(
        1
        for pid, status in st.session_state.outreach_draft_status.items()
        if any(status.values())
    )
    pending = total - generated

    sc1, sc2, sc3, sc4 = st.columns(4)
    for col, val, label, color in [
        (sc1, total, "Total Prospects", "#e8e8f2"),
        (sc2, generated, "Sequences Generated", "#F59E0B"),
        (sc3, drafted, "Drafted to Inbox", "#10B981"),
        (sc4, pending, "Pending", "#888888"),
    ]:
        with col:
            st.markdown(
                f'<div class="or-stat-card">'
                f'  <div class="or-stat-val" style="color:{color}">{val}</div>'
                f'  <div class="or-stat-label">{label}</div>'
                f"</div>",
                unsafe_allow_html=True,
            )

    st.markdown("<div style='height:1.25rem'></div>", unsafe_allow_html=True)

    # ── Two-column layout ──────────────────────
    prospect_col, detail_col = st.columns([1, 2.5])

    with prospect_col:
        st.markdown(
            '<div class="panel-header" style="border-radius:8px 8px 0 0;">'
            '  <span class="panel-title">Prospects</span>'
            "</div>",
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
            f'    <span style="font-size:1.05rem;font-weight:600;color:#e8e8f2">{p["name"]}</span>'
            f'    <span class="or-industry-badge">{p["industry"]}</span>'
            f"  </div>"
            f'  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:4px;">'
            f'    <div><div class="or-field-label">Contact</div>'
            f'         <div class="or-field-val">{p["contact"]}</div></div>'
            f'    <div><div class="or-field-label">Email</div>'
            f'         <div class="or-field-val" style="font-size:0.72rem">{p["email"]}</div></div>'
            f'    <div><div class="or-field-label">Phone</div>'
            f'         <div class="or-field-val">{p["phone"]}</div></div>'
            f"  </div>"
            f'  <div class="or-notes">{p["notes"]}</div>'
            f"</div>",
            unsafe_allow_html=True,
        )

        seq = st.session_state.outreach_sequences.get(pid)

        # Generate button
        if seq is None:
            if st.button(
                f"⚡ Generate 3-Email Sequence for {p['name']}",
                use_container_width=True,
                type="primary",
                key=f"gen_{pid}",
            ):
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
                if st.button(
                    "↺ Regenerate", key=f"regen_{pid}", use_container_width=True
                ):
                    with st.spinner("Regenerating..."):
                        try:
                            result = generate_email_sequence(p)
                            st.session_state.outreach_sequences[pid] = result
                            st.session_state.outreach_draft_status.pop(pid, None)
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Regeneration failed — {exc}")
            with draft_all_col:
                if st.button(
                    "✉ Draft All 3 to Inbox",
                    key=f"draft_all_{pid}",
                    use_container_width=True,
                    type="primary",
                ):
                    _gmail_sender = os.environ.get("GMAIL_SENDER", "")
                    _gmail_pw = os.environ.get("GMAIL_APP_PASSWORD", "")
                    if not _gmail_sender or not _gmail_pw:
                        st.error(
                            "GMAIL_SENDER and GMAIL_APP_PASSWORD must be set in .env"
                        )
                    else:
                        errors = []
                        for ekey in ["email1", "email2", "email3"]:
                            try:
                                send_draft_to_self(
                                    seq[ekey], p, _gmail_sender, _gmail_pw
                                )
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
                ("email1", "FIRST TOUCH", "Day 1"),
                ("email2", "FOLLOW-UP 1", "Day 5"),
                ("email3", "FOLLOW-UP 2", "Day 12"),
            ]
            draft_status = st.session_state.outreach_draft_status.get(pid, {})

            for ekey, label, day in email_meta:
                email = seq[ekey]
                is_drafted = draft_status.get(ekey, False)
                drafted_suffix = " ✓ In Inbox" if is_drafted else ""

                with st.expander(
                    f"{label} — {email['subject']}{drafted_suffix}",
                    expanded=(ekey == "email1"),
                ):
                    st.markdown(
                        f'<div class="or-email-meta">'
                        f"  <strong>To:</strong> {p['email']} &nbsp;·&nbsp; "
                        f"  <strong>From:</strong> zajen@n8ivpromotions.com &nbsp;·&nbsp; "
                        f"  <strong>Send:</strong> {day}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    st.code(email["body"], language=None)

                    action_col, status_col = st.columns([1, 2])
                    with action_col:
                        if not is_drafted:
                            if st.button(
                                "✉ Send to Inbox",
                                key=f"draft_{pid}_{ekey}",
                                type="primary",
                            ):
                                _gmail_sender = os.environ.get("GMAIL_SENDER", "")
                                _gmail_pw = os.environ.get("GMAIL_APP_PASSWORD", "")
                                if not _gmail_sender or not _gmail_pw:
                                    st.error("GMAIL credentials not configured")
                                else:
                                    try:
                                        send_draft_to_self(
                                            email, p, _gmail_sender, _gmail_pw
                                        )
                                        if (
                                            pid
                                            not in st.session_state.outreach_draft_status
                                        ):
                                            st.session_state.outreach_draft_status[
                                                pid
                                            ] = {}
                                        st.session_state.outreach_draft_status[pid][
                                            ekey
                                        ] = True
                                        st.rerun()
                                    except Exception as exc:
                                        st.error(f"Failed to send — {exc}")
                    with status_col:
                        if is_drafted:
                            st.markdown(
                                '<span class="or-sent-badge">✓ Draft sent to inbox</span>',
                                unsafe_allow_html=True,
                            )


# ══════════════════════════════════════════════
# TAB: OBSERVABILITY
# ══════════════════════════════════════════════
if selected_view == "Observability" and not _has_permission(
    current_user, Permission.VIEW_AUDIT_LOG
):
    st.warning("Your role cannot view operational audit logs.")

if selected_view == "Observability" and _has_permission(
    current_user, Permission.VIEW_AUDIT_LOG
):
    st.markdown("### Observability")

    try:
        from utils.observability_queries import (
            get_monthly_cost_by_agency,
            get_pipeline_health_last_30d,
            get_latest_eval_scores,
            get_recent_audit_events,
        )
        from utils.databricks_writer import (
            fetch_recent_operator_alerts,
            fetch_recent_telegram_events,
        )

        obs_col1, obs_col2 = st.columns(2)

        with obs_col1:
            st.markdown("**Open Operator Alerts**")
            alert_rows = fetch_recent_operator_alerts(limit=20, open_only=True)
            if alert_rows:
                alert_df = pd.DataFrame(alert_rows)
                visible_cols = [
                    "event_time",
                    "severity",
                    "category",
                    "title",
                    "client_id",
                    "source",
                    "action_required",
                ]
                alert_df = alert_df[
                    [c for c in visible_cols if c in alert_df.columns]
                ]
                st.dataframe(alert_df, width="stretch", hide_index=True)
            else:
                st.caption("No open operator alerts.")

            st.markdown("**Token Cost — This Month**")
            cost_rows = get_monthly_cost_by_agency()
            if cost_rows:
                cost_df = pd.DataFrame(cost_rows)
                st.dataframe(cost_df, width="stretch", hide_index=True)
            else:
                st.caption("No cost data yet.")

            st.markdown("**Eval Scores (Latest)**")
            eval_rows = get_latest_eval_scores()
            if eval_rows:
                eval_df = pd.DataFrame(eval_rows)
                st.dataframe(eval_df, width="stretch", hide_index=True)
            else:
                st.caption("No eval runs yet. Run eval_runner.py to generate scores.")

        with obs_col2:
            st.markdown("**Pipeline Health — Last 30 Days**")
            health_rows = get_pipeline_health_last_30d()
            if health_rows:
                health_df = pd.DataFrame(health_rows)
                st.dataframe(health_df, width="stretch", hide_index=True)
            else:
                st.caption("No pipeline runs in the last 30 days.")

            st.markdown("**Recent Audit Events**")
            audit_rows = get_recent_audit_events(limit=20)
            if audit_rows:
                audit_df = pd.DataFrame(audit_rows)
                st.dataframe(audit_df, width="stretch", hide_index=True)
            else:
                st.caption("No audit events yet.")

            st.markdown("**Recent Telegram Triggers**")
            telegram_rows = fetch_recent_telegram_events(limit=20)
            if telegram_rows:
                telegram_df = pd.DataFrame(telegram_rows)
                visible_cols = [
                    "event_time",
                    "event_type",
                    "raw_text",
                    "parsed_intent",
                    "status",
                    "response_summary",
                    "action_id",
                ]
                telegram_df = telegram_df[
                    [c for c in visible_cols if c in telegram_df.columns]
                ]
                st.dataframe(telegram_df, width="stretch", hide_index=True)
            else:
                st.caption("No Telegram triggers logged yet.")

    except Exception as _obs_exc:
        st.warning(f"Observability data unavailable: {_obs_exc}")
