from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    git_sha: str
    deploy_target: str


# ── Pipeline ──────────────────────────────────────────────────────────────────


class PipelineRunRequest(BaseModel):
    agency_id: str
    client_filter: str | None = None
    dry_run: bool = True
    attribution_model: str = "last_touch"


class PipelineRunResponse(BaseModel):
    run_id: str
    databricks_run_id: int | None = None
    status: str
    message: str


class PipelineRunRecord(BaseModel):
    run_id: str
    agency_id: str
    client_id: str
    run_mode: str
    attribution_model: str
    status: str
    dry_run: bool
    meta_rows: int
    google_rows: int
    linkedin_rows: int
    hubspot_rows: int
    stripe_rows: int
    normalized_ad_rows: int
    total_pipeline: float
    top_channel: str
    email_sent: bool
    warnings: str
    error: str
    started_at: datetime | None
    finished_at: datetime | None
    output_schema: str


# ── Clients ───────────────────────────────────────────────────────────────────


class ClientConfigRequest(BaseModel):
    client_name: str
    attribution_model: str = "last_touch"
    meta_enabled: bool = False
    meta_ad_account_id: str = ""
    google_ads_enabled: bool = False
    google_ads_customer_id: str = ""
    linkedin_ads_enabled: bool = False
    linkedin_ads_account_id: str = ""
    hubspot_enabled: bool = False
    hubspot_pipeline_id: str = ""
    stripe_enabled: bool = False
    stripe_account_id: str = ""
    lookback_days: int = 30
    client_report_email: str = ""
    client_display_name: str = ""
    agency_id: str = ""


class ClientConfigResponse(BaseModel):
    client_id: str
    client_name: str
    attribution_model: str
    meta_enabled: bool
    google_ads_enabled: bool
    linkedin_ads_enabled: bool
    hubspot_enabled: bool
    stripe_enabled: bool
    lookback_days: int
    client_report_email: str
    agency_id: str
    databricks_schema: str


# ── Reports ───────────────────────────────────────────────────────────────────


class InsightReportResponse(BaseModel):
    report_id: str
    client_id: str
    agency_id: str
    report_month: str
    narrative: str
    key_findings: list[str]
    top_channel: str
    total_pipeline: float
    total_spend: float
    overall_roi: float
    collected_revenue: float
    refund_rate: float
    true_roi: float
    attribution_model: str
    generated_at: datetime | None
    run_id: str
    prompt_version: str
    model_id: str


# ── Approvals ─────────────────────────────────────────────────────────────────


class ApprovalItem(BaseModel):
    action_id: str
    created_at: datetime | None
    actor: str
    description: str
    action_type: str
    status: str
    channel: str


class ApprovalResolution(BaseModel):
    resolution: str = Field(..., pattern="^(approved|rejected)$")
    resolution_note: str = ""
