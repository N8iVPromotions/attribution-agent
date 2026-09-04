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
    client_ids: list[str] | None = None
    dry_run: bool = True
    attribution_model: str = "last_touch"
    run_mode: str = "agency"


class PipelineRunResponse(BaseModel):
    run_id: str
    databricks_run_id: int | None = None
    cloud_run_operation: str | None = None
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
    meta_access_token: str = Field(default="", repr=False)
    meta_token_expires_at: str = ""
    google_ads_enabled: bool = False
    google_ads_customer_id: str = ""
    google_ads_refresh_token: str = Field(default="", repr=False)
    google_ads_token_expires_at: str = ""
    linkedin_ads_enabled: bool = False
    linkedin_ads_account_id: str = ""
    linkedin_access_token: str = Field(default="", repr=False)
    linkedin_token_expires_at: str = ""
    tiktok_ads_enabled: bool = False
    tiktok_ads_advertiser_id: str = ""
    tiktok_access_token: str = Field(default="", repr=False)
    tiktok_token_expires_at: str = ""
    hubspot_enabled: bool = False
    hubspot_pipeline_id: str = ""
    hubspot_access_token: str = Field(default="", repr=False)
    hubspot_token_expires_at: str = ""
    stripe_enabled: bool = False
    stripe_account_id: str = ""
    stripe_secret_key: str = Field(default="", repr=False)
    stripe_token_expires_at: str = ""
    lookback_days: int = 30
    client_report_email: str = ""
    client_display_name: str = ""
    agency_id: str = ""
    databricks_schema: str = ""


class ClientConfigResponse(BaseModel):
    client_id: str
    client_name: str
    attribution_model: str
    meta_enabled: bool
    google_ads_enabled: bool
    linkedin_ads_enabled: bool
    tiktok_ads_enabled: bool
    hubspot_enabled: bool
    stripe_enabled: bool
    meta_ad_account_id: str = ""
    meta_token_expires_at: str = ""
    google_ads_customer_id: str = ""
    google_ads_token_expires_at: str = ""
    linkedin_ads_account_id: str = ""
    linkedin_token_expires_at: str = ""
    tiktok_ads_advertiser_id: str = ""
    tiktok_token_expires_at: str = ""
    hubspot_pipeline_id: str = ""
    hubspot_token_expires_at: str = ""
    stripe_account_id: str = ""
    stripe_token_expires_at: str = ""
    meta_secret_configured: bool = False
    google_ads_secret_configured: bool = False
    linkedin_ads_secret_configured: bool = False
    tiktok_secret_configured: bool = False
    hubspot_secret_configured: bool = False
    stripe_secret_configured: bool = False
    lookback_days: int
    client_report_email: str
    client_display_name: str = ""
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
    status: str = "generated"


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
