import type { CommandCenterData } from "@/lib/types";

const now = Date.now();
const ago = (milliseconds: number) => new Date(now - milliseconds).toISOString();

export const mockCommandCenterData: CommandCenterData = {
  source: "demo",
  generatedAt: new Date(now).toISOString(),
  summary: {
    activeClients: 4,
    runs30d: 22,
    successRate: 0.86,
    attributedPipeline: 411_500,
    openAlerts: 3,
    criticalAlerts: 1,
    activeRuns: 1,
    partialRuns: 1,
    suppressedDeliveries: 1,
    aiSpendMonth: 18.42
  },
  agencies: [
    {
      agencyId: "n8iv_agency",
      name: "N8iV Agency",
      active: true,
      createdAt: ago(31_536_000_000),
      updatedAt: ago(3_600_000)
    }
  ],
  clients: [
    {
      clientId: "n8iv_promotions",
      name: "N8iV Promotions",
      agencyId: "n8iv_agency",
      attributionModel: "w_shape",
      reportEmail: "ops@n8ivpromotions.com",
      active: true,
      lookbackDays: 60,
      platforms: { meta: true, google: true, linkedin: true, tiktok: false, hubspot: true, stripe: true },
      updatedAt: ago(3_600_000)
    },
    {
      clientId: "agency_pilot",
      name: "Agency Pilot Account",
      agencyId: "n8iv_agency",
      attributionModel: "linear",
      reportEmail: "ops@example.com",
      active: true,
      lookbackDays: 30,
      platforms: { meta: true, google: false, linkedin: true, tiktok: false, hubspot: true, stripe: true },
      updatedAt: ago(86_400_000)
    },
    {
      clientId: "b2b_saas",
      name: "B2B SaaS Demo",
      agencyId: "n8iv_agency",
      attributionModel: "time_decay",
      reportEmail: "revenue@example.com",
      active: true,
      lookbackDays: 90,
      platforms: { meta: false, google: true, linkedin: true, tiktok: false, hubspot: true, stripe: true },
      updatedAt: ago(172_800_000)
    },
    {
      clientId: "luxe_medspa",
      name: "Luxe Aesthetics MedSpa",
      agencyId: "n8iv_agency",
      attributionModel: "u_shape",
      reportEmail: "marketing@example.com",
      active: true,
      lookbackDays: 45,
      platforms: { meta: true, google: true, linkedin: false, tiktok: true, hubspot: true, stripe: true },
      updatedAt: ago(259_200_000)
    }
  ],
  runs: [
    {
      runId: "demo-running-04",
      agencyId: "n8iv_agency",
      clientId: "b2b_saas",
      runMode: "client",
      attributionModel: "time_decay",
      status: "running",
      dryRun: true,
      totalPipeline: 0,
      topChannel: "Pending",
      emailSent: false,
      deliverySuppressed: false,
      warnings: "",
      error: "",
      startedAt: ago(180_000),
      finishedAt: "",
      outputSchema: "workspace.attribution_b2b_saas",
      sourceRows: { meta: 0, google: 412, linkedin: 0, tiktok: 0, hubspot: 0, stripe: 0, normalized: 412 }
    },
    {
      runId: "demo-partial-03",
      agencyId: "n8iv_agency",
      clientId: "agency_pilot",
      runMode: "client",
      attributionModel: "linear",
      status: "partial",
      dryRun: false,
      totalPipeline: 92_500,
      topChannel: "LinkedIn Ads",
      emailSent: false,
      deliverySuppressed: true,
      warnings: "Meta returned HTTP 429; report delivery suppressed.",
      error: "",
      startedAt: ago(7_200_000),
      finishedAt: ago(6_780_000),
      outputSchema: "workspace.attribution_agency_pilot",
      sourceRows: { meta: 0, google: 0, linkedin: 190, tiktok: 0, hubspot: 46, stripe: 21, normalized: 190 }
    },
    {
      runId: "demo-success-02",
      agencyId: "n8iv_agency",
      clientId: "n8iv_promotions",
      runMode: "client",
      attributionModel: "w_shape",
      status: "success",
      dryRun: true,
      totalPipeline: 124_000,
      topChannel: "LinkedIn Ads",
      emailSent: false,
      deliverySuppressed: false,
      warnings: "",
      error: "",
      startedAt: ago(25_200_000),
      finishedAt: ago(24_840_000),
      outputSchema: "workspace.attribution_n8iv_promotions",
      sourceRows: { meta: 830, google: 410, linkedin: 520, tiktok: 0, hubspot: 74, stripe: 31, normalized: 1760 }
    },
    {
      runId: "demo-success-01",
      agencyId: "n8iv_agency",
      clientId: "luxe_medspa",
      runMode: "client",
      attributionModel: "u_shape",
      status: "success",
      dryRun: false,
      totalPipeline: 195_000,
      topChannel: "Meta Ads",
      emailSent: true,
      deliverySuppressed: false,
      warnings: "",
      error: "",
      startedAt: ago(90_000_000),
      finishedAt: ago(89_640_000),
      outputSchema: "workspace.attribution_luxe_medspa",
      sourceRows: { meta: 1240, google: 680, linkedin: 0, tiktok: 0, hubspot: 96, stripe: 54, normalized: 1920 }
    }
  ],
  checkpoints: [
    { checkpointId: "cp-1", runId: "demo-running-04", clientId: "b2b_saas", stepName: "ingest_google_ads", status: "success", startedAt: ago(180_000), completedAt: ago(120_000), errorDetail: "" },
    { checkpointId: "cp-2", runId: "demo-running-04", clientId: "b2b_saas", stepName: "validate_sources", status: "running", startedAt: ago(115_000), completedAt: "", errorDetail: "" },
    { checkpointId: "cp-3", runId: "demo-partial-03", clientId: "agency_pilot", stepName: "ingest_meta", status: "failed", startedAt: ago(7_190_000), completedAt: ago(7_170_000), errorDetail: "HTTP 429 rate limit" },
    { checkpointId: "cp-4", runId: "demo-partial-03", clientId: "agency_pilot", stepName: "delivery", status: "suppressed", startedAt: ago(6_800_000), completedAt: ago(6_790_000), errorDetail: "Partial ingestion policy" }
  ],
  alerts: [
    { alertId: "alert-1", severity: "critical", category: "credential_missing", title: "Google Ads credential missing", message: "Google Ads is enabled for a pilot client but no refresh token is available.", clientId: "agency_pilot", source: "google_ads", runId: "", actionRequired: "Reconnect Google Ads before the next live run.", status: "open", eventTime: ago(5_400_000) },
    { alertId: "alert-2", severity: "warning", category: "partial_ingestion", title: "Partial ingestion — delivery suppressed", message: "Meta rate limiting left this run without paid-social rows.", clientId: "agency_pilot", source: "meta", runId: "demo-partial-03", actionRequired: "Retry Meta ingestion, then rerun in preview mode.", status: "open", eventTime: ago(6_780_000) },
    { alertId: "alert-3", severity: "warning", category: "data_quality", title: "UTM coverage below target", message: "18% of recent touchpoints have no campaign identifier.", clientId: "luxe_medspa", source: "hubspot", runId: "demo-success-01", actionRequired: "Review form and campaign UTM templates.", status: "open", eventTime: ago(86_400_000) }
  ],
  reports: [
    { reportId: "report-1", clientId: "n8iv_promotions", agencyId: "n8iv_agency", reportMonth: "2026-07", narrative: "LinkedIn-assisted opportunities produced the strongest closed-revenue contribution while Google captured high-intent demand. The W-shape model confirms that both acquisition and opportunity creation deserve material credit.", keyFindings: ["LinkedIn influenced 41% of attributed pipeline.", "Google search improved cost per opportunity by 12%.", "Collected revenue remains inside the expected settlement window."], topChannel: "LinkedIn Ads", totalPipeline: 124_000, totalSpend: 18_600, overallRoi: 6.67, trueRoi: 6.21, collectedRevenue: 115_500, refundRate: 0.02, attributionModel: "w_shape", generatedAt: ago(24_700_000), runId: "demo-success-02", promptVersion: "insight-v4", modelId: "claude-sonnet", status: "generated" },
    { reportId: "report-2", clientId: "luxe_medspa", agencyId: "n8iv_agency", reportMonth: "2026-07", narrative: "Meta lead generation remains the primary acquisition engine, with Google converting returning prospects at higher intent. Refund-adjusted ROI remains healthy.", keyFindings: ["Meta sourced 54% of new consultations.", "True ROI held above 5x after refunds."], topChannel: "Meta Ads", totalPipeline: 195_000, totalSpend: 31_400, overallRoi: 6.21, trueRoi: 5.74, collectedRevenue: 180_200, refundRate: 0.031, attributionModel: "u_shape", generatedAt: ago(89_500_000), runId: "demo-success-01", promptVersion: "insight-v4", modelId: "claude-sonnet", status: "delivered" }
  ],
  costs: [{ agencyId: "n8iv_agency", totalTokens: 884_200, costUsd: 18.42, lastEventAt: ago(24_700_000) }],
  evals: [
    { agentName: "insight_agent", score: 0.96, passed: true, regression: false, runAt: ago(43_200_000), promptVersion: "insight-v4", modelId: "claude-sonnet" },
    { agentName: "validator", score: 0.99, passed: true, regression: false, runAt: ago(43_200_000), promptVersion: "schema-v2", modelId: "deterministic" }
  ],
  audits: [
    { eventId: "audit-1", eventTime: ago(180_000), eventType: "PIPELINE_STARTED", actor: "command-center", clientId: "b2b_saas", resource: "pipeline", action: "dry_run", outcome: "accepted", runId: "demo-running-04" },
    { eventId: "audit-2", eventTime: ago(6_780_000), eventType: "REPORT_SUPPRESSED", actor: "agency_flow", clientId: "agency_pilot", resource: "email", action: "suppress", outcome: "partial_ingestion", runId: "demo-partial-03" }
  ],
  approvals: [{ actionId: "approval-1", createdAt: ago(6_500_000), actor: "governance-reviewer", description: "Review partial July report before delivery retry", actionType: "report_release", status: "pending", channel: "email" }],
  lifecycleOperations: [],
  recommendations: [
    { severity: "critical", title: "Resolve Google Ads credentials", body: "Agency Pilot cannot produce complete paid-search attribution until the refresh token is restored." },
    { severity: "warning", title: "Keep Agency Pilot delivery suppressed", body: "Retry Meta ingestion and complete a clean preview run before releasing the report." }
  ],
  capabilities: {
    databricks: false,
    pipelineExecution: false,
    approvalActions: false,
    tenantLifecycle: false,
    clientConfiguration: false
  },
  warnings: ["DEMO ISOLATION ACTIVE — synthetic operations data only."]
};
