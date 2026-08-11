import { hasPipelineExecutionConfig } from "@/lib/cloud-run";
import { hasControlApiConfig } from "@/lib/control-api";
import { runSql, hasDatabricksConfig, opsSchema } from "@/lib/databricks";
import { mockCommandCenterData } from "@/lib/mock-data";
import { hasTenantLifecycleConfig } from "@/lib/tenant-lifecycle";
import type {
  AgencyAccount,
  ApprovalItem,
  AuditEvent,
  ClientAccount,
  CommandCenterData,
  CostSummary,
  EvalScore,
  InsightReport,
  OperatorAlert,
  PipelineRun,
  Recommendation,
  RunCheckpoint,
  RunStatus,
  TenantLifecycleOperation
} from "@/lib/types";

function text(value: unknown, fallback = "") {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function number(value: unknown) {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function bool(value: unknown) {
  return value === true || value === "true" || value === "1" || value === 1;
}

function safeStatus(value: unknown): RunStatus {
  const status = text(value, "queued").toLowerCase();
  if (
    status === "success" ||
    status === "partial" ||
    status === "failed" ||
    status === "running" ||
    status === "queued" ||
    status === "warning"
  ) {
    return status;
  }
  return "warning";
}

function stringList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map(String);
  }
  try {
    const parsed = JSON.parse(text(value, "[]"));
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return value ? [String(value)] : [];
  }
}

function parseClient(row: Record<string, unknown>): ClientAccount {
  let config: Record<string, unknown> = {};
  try {
    config = JSON.parse(text(row.config_json, "{}")) as Record<string, unknown>;
  } catch {
    config = {};
  }

  return {
    clientId: text(row.client_id),
    name: text(config.client_display_name, text(config.client_name, text(row.client_id, "Unnamed client"))),
    agencyId: text(config.agency_id, "unassigned"),
    attributionModel: text(config.attribution_model, "last_touch"),
    reportEmail: text(config.client_report_email),
    active: bool(row.is_active ?? true),
    lookbackDays: number(config.lookback_days || 30),
    platforms: {
      meta: bool(config.meta_enabled),
      google: bool(config.google_ads_enabled),
      linkedin: bool(config.linkedin_ads_enabled),
      tiktok: bool(config.tiktok_ads_enabled),
      hubspot: bool(config.hubspot_enabled),
      stripe: bool(config.stripe_enabled)
    },
    updatedAt: text(row.updated_at, new Date().toISOString())
  };
}

function parseAgency(row: Record<string, unknown>): AgencyAccount {
  return {
    agencyId: text(row.agency_id),
    name: text(row.agency_name, text(row.agency_id, "Unnamed agency")),
    active: bool(row.is_active ?? true),
    createdAt: text(row.created_at),
    updatedAt: text(row.updated_at)
  };
}

function parseLifecycleOperation(row: Record<string, unknown>): TenantLifecycleOperation {
  return {
    requestId: text(row.request_id),
    command: text(row.command) as TenantLifecycleOperation["command"],
    entityType: text(row.entity_type) === "agency" ? "agency" : "business",
    entityId: text(row.entity_id),
    agencyId: text(row.agency_id),
    schemaName: text(row.schema_name),
    requestedBy: text(row.requested_by),
    status: text(row.status, "queued"),
    databricksRunId: text(row.databricks_run_id),
    errorMessage: text(row.error_message),
    requestedAt: text(row.requested_at),
    completedAt: text(row.completed_at)
  };
}

function parseRun(row: Record<string, unknown>): PipelineRun {
  const status = safeStatus(row.status);
  const emailSent = bool(row.email_sent);
  const dryRun = bool(row.dry_run);
  return {
    runId: text(row.run_id),
    agencyId: text(row.agency_id),
    clientId: text(row.client_id),
    runMode: text(row.run_mode, "client"),
    attributionModel: text(row.attribution_model, "last_touch"),
    status,
    dryRun,
    totalPipeline: number(row.total_pipeline),
    topChannel: text(row.top_channel, "Pending"),
    emailSent,
    deliverySuppressed: !dryRun && !emailSent && (status === "partial" || Boolean(text(row.warnings))),
    warnings: text(row.warnings),
    error: text(row.error),
    startedAt: text(row.started_at, new Date().toISOString()),
    finishedAt: text(row.finished_at),
    outputSchema: text(row.output_schema),
    sourceRows: {
      meta: number(row.meta_rows),
      google: number(row.google_rows),
      linkedin: number(row.linkedin_rows),
      hubspot: number(row.hubspot_rows),
      stripe: number(row.stripe_rows),
      normalized: number(row.normalized_ad_rows)
    }
  };
}

function parseCheckpoint(row: Record<string, unknown>): RunCheckpoint {
  return {
    checkpointId: text(row.checkpoint_id),
    runId: text(row.run_id),
    clientId: text(row.client_id),
    stepName: text(row.step_name),
    status: text(row.status, "unknown").toLowerCase(),
    startedAt: text(row.started_at),
    completedAt: text(row.completed_at),
    errorDetail: text(row.error_detail)
  };
}

function parseAlert(row: Record<string, unknown>): OperatorAlert {
  const severity = text(row.severity, "info").toLowerCase();
  return {
    alertId: text(row.alert_id),
    severity: severity === "critical" || severity === "warning" ? severity : "info",
    category: text(row.category),
    title: text(row.title, "Operator alert"),
    message: text(row.message),
    clientId: text(row.client_id),
    source: text(row.source),
    runId: text(row.run_id),
    actionRequired: text(row.action_required),
    status: text(row.status, "open"),
    eventTime: text(row.event_time, new Date().toISOString())
  };
}

function parseReport(row: Record<string, unknown>): InsightReport {
  return {
    reportId: text(row.report_id),
    clientId: text(row.client_id),
    agencyId: text(row.agency_id),
    reportMonth: text(row.report_month),
    narrative: text(row.narrative),
    keyFindings: stringList(row.key_findings),
    topChannel: text(row.top_channel),
    totalPipeline: number(row.total_pipeline),
    totalSpend: number(row.total_spend),
    overallRoi: number(row.overall_roi),
    trueRoi: number(row.true_roi),
    collectedRevenue: number(row.collected_revenue),
    refundRate: number(row.refund_rate),
    attributionModel: text(row.attribution_model),
    generatedAt: text(row.generated_at),
    runId: text(row.run_id),
    promptVersion: text(row.prompt_version),
    modelId: text(row.model_id),
    status: text(row.status, "generated")
  };
}

function buildRecommendations(runs: PipelineRun[], alerts: OperatorAlert[]): Recommendation[] {
  const recommendations: Recommendation[] = [];
  const partialRuns = runs.filter((run) => run.status === "partial" || run.deliverySuppressed);
  const failedRuns = runs.filter((run) => run.status === "failed");
  const criticalAlerts = alerts.filter((alert) => alert.severity === "critical");

  if (criticalAlerts.length) {
    recommendations.push({
      severity: "critical",
      title: "Resolve critical source failures",
      body: `${criticalAlerts.length} critical alert(s) are open. Keep live delivery disabled until they are cleared.`
    });
  }
  if (partialRuns.length) {
    recommendations.push({
      severity: "warning",
      title: "Review suppressed report deliveries",
      body: `${partialRuns.length} recent run(s) completed partially or suppressed email. Inspect the failed source before retrying.`
    });
  }
  if (failedRuns.length) {
    recommendations.push({
      severity: "warning",
      title: "Retry failed clients in preview mode",
      body: `${failedRuns.length} recent run(s) failed. Validate credentials and source row counts before a live retry.`
    });
  }
  if (!recommendations.length) {
    recommendations.push({
      severity: "info",
      title: "Fleet is clear for scheduled reporting",
      body: "No recent failures or delivery suppressions require operator intervention."
    });
  }
  return recommendations;
}

async function optionalQuery<T extends Record<string, unknown>>(
  label: string,
  statement: string,
  warnings: string[]
) {
  try {
    return await runSql<T>(statement);
  } catch (error) {
    warnings.push(`${label} unavailable: ${error instanceof Error ? error.message : "query failed"}`);
    return [];
  }
}

export async function getCommandCenterData(): Promise<CommandCenterData> {
  const capabilities = {
    databricks: hasDatabricksConfig(),
    pipelineExecution: hasPipelineExecutionConfig(),
    approvalActions: hasControlApiConfig(),
    tenantLifecycle: hasTenantLifecycleConfig()
  };
  if (!capabilities.databricks) {
    return {
      ...mockCommandCenterData,
      generatedAt: new Date().toISOString(),
      capabilities,
      warnings: ["DEMO ISOLATION ACTIVE — Databricks is not configured; no production records are shown."]
    };
  }

  const schema = opsSchema();
  const warnings: string[] = [];
  try {
    const [runRows, clientRows, alertRows] = await Promise.all([
      runSql<Record<string, unknown>>(`
        SELECT run_id, agency_id, client_id, run_mode, attribution_model, status,
               dry_run, meta_rows, google_rows, linkedin_rows, hubspot_rows,
               stripe_rows, normalized_ad_rows, total_pipeline, top_channel,
               email_sent, warnings, error, started_at, finished_at, output_schema
        FROM ${schema}.pipeline_runs ORDER BY started_at DESC LIMIT 100
      `),
      runSql<Record<string, unknown>>(`
        SELECT client_id, config_json, is_active, updated_at
        FROM ${schema}.client_registry WHERE is_active = true
        ORDER BY updated_at DESC LIMIT 100
      `),
      runSql<Record<string, unknown>>(`
        SELECT alert_id, severity, category, title, message, client_id, source,
               run_id, action_required, status, event_time
        FROM ${schema}.operator_alerts WHERE status = 'open'
        ORDER BY event_time DESC LIMIT 100
      `)
    ]);

    const [checkpointRows, reportRows, costRows, evalRows, auditRows, approvalRows, agencyRows, lifecycleRows] = await Promise.all([
      optionalQuery<Record<string, unknown>>(
        "Checkpoints",
        `SELECT checkpoint_id, run_id, client_id, step_name, status, started_at, completed_at, error_detail
         FROM ${schema}.pipeline_checkpoints ORDER BY started_at DESC LIMIT 250`,
        warnings
      ),
      optionalQuery<Record<string, unknown>>(
        "Reports",
        `SELECT report_id, client_id, agency_id, report_month, narrative, key_findings,
                top_channel, total_pipeline, total_spend, overall_roi, collected_revenue,
                refund_rate, true_roi, attribution_model, generated_at, run_id,
                prompt_version, model_id, status
         FROM ${schema}.insight_reports ORDER BY generated_at DESC LIMIT 50`,
        warnings
      ),
      optionalQuery<Record<string, unknown>>(
        "AI costs",
        `SELECT agency_id, SUM(input_tokens + output_tokens) AS total_tokens,
                SUM(cost_usd_estimate) AS cost_usd, MAX(event_time) AS last_event_at
         FROM ${schema}.cost_ledger
         WHERE event_time >= date_trunc('month', current_timestamp())
         GROUP BY agency_id ORDER BY cost_usd DESC`,
        warnings
      ),
      optionalQuery<Record<string, unknown>>(
        "Evaluations",
        `SELECT agent_name, score, passed, regression, run_at, prompt_version, model_id
         FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY agent_name ORDER BY run_at DESC) AS rn
               FROM ${schema}.eval_results) latest
         WHERE rn = 1 ORDER BY agent_name`,
        warnings
      ),
      optionalQuery<Record<string, unknown>>(
        "Audit log",
        `SELECT event_id, event_time, event_type, actor, client_id, resource, action, outcome, run_id
         FROM ${schema}.audit_log ORDER BY event_time DESC LIMIT 50`,
        warnings
      ),
      optionalQuery<Record<string, unknown>>(
        "Approvals",
        `SELECT action_id, created_at, actor, description, action_type, status, channel
         FROM ${schema}.approval_queue WHERE status = 'pending'
         ORDER BY created_at DESC LIMIT 50`,
        warnings
      ),
      optionalQuery<Record<string, unknown>>(
        "Agency registry",
        `SELECT agency_id, agency_name, is_active, created_at, updated_at
         FROM ${schema}.agency_registry WHERE is_active = TRUE
         ORDER BY agency_name`,
        warnings
      ),
      optionalQuery<Record<string, unknown>>(
        "Tenant lifecycle operations",
        `SELECT request_id, command, entity_type, entity_id, agency_id, schema_name,
                requested_by, status, databricks_run_id, error_message,
                requested_at, completed_at
         FROM ${schema}.tenant_lifecycle_operations
         ORDER BY requested_at DESC LIMIT 50`,
        warnings
      )
    ]);

    const runs = runRows.map(parseRun);
    const clients = clientRows.map(parseClient);
    const registeredAgencies = agencyRows.map(parseAgency);
    const agencies = registeredAgencies.length
      ? registeredAgencies
      : Array.from(new Set(clients.map((client) => client.agencyId).filter(Boolean)))
          .sort()
          .map((agencyId) => ({ agencyId, name: agencyId, active: true, createdAt: "", updatedAt: "" }));
    const alerts = alertRows.map(parseAlert);
    const costs: CostSummary[] = costRows.map((row) => ({
      agencyId: text(row.agency_id, "unassigned"),
      totalTokens: number(row.total_tokens),
      costUsd: number(row.cost_usd),
      lastEventAt: text(row.last_event_at)
    }));
    const evals: EvalScore[] = evalRows.map((row) => ({
      agentName: text(row.agent_name),
      score: number(row.score),
      passed: bool(row.passed),
      regression: bool(row.regression),
      runAt: text(row.run_at),
      promptVersion: text(row.prompt_version),
      modelId: text(row.model_id)
    }));
    const audits: AuditEvent[] = auditRows.map((row) => ({
      eventId: text(row.event_id),
      eventTime: text(row.event_time),
      eventType: text(row.event_type),
      actor: text(row.actor),
      clientId: text(row.client_id),
      resource: text(row.resource),
      action: text(row.action),
      outcome: text(row.outcome),
      runId: text(row.run_id)
    }));
    const approvals: ApprovalItem[] = approvalRows.map((row) => ({
      actionId: text(row.action_id),
      createdAt: text(row.created_at),
      actor: text(row.actor),
      description: text(row.description),
      actionType: text(row.action_type),
      status: text(row.status),
      channel: text(row.channel)
    }));
    const recentRuns = runs.filter((run) => Date.now() - Date.parse(run.startedAt) <= 30 * 86_400_000);
    const successfulRuns = recentRuns.filter((run) => run.status === "success").length;

    return {
      source: "databricks",
      generatedAt: new Date().toISOString(),
      summary: {
        activeClients: clients.length,
        runs30d: recentRuns.length,
        successRate: recentRuns.length ? successfulRuns / recentRuns.length : 0,
        attributedPipeline: recentRuns.reduce((sum, run) => sum + run.totalPipeline, 0),
        openAlerts: alerts.length,
        criticalAlerts: alerts.filter((alert) => alert.severity === "critical").length,
        activeRuns: runs.filter((run) => run.status === "running" || run.status === "queued").length,
        partialRuns: recentRuns.filter((run) => run.status === "partial").length,
        suppressedDeliveries: recentRuns.filter((run) => run.deliverySuppressed).length,
        aiSpendMonth: costs.reduce((sum, cost) => sum + cost.costUsd, 0)
      },
      agencies,
      clients,
      runs,
      checkpoints: checkpointRows.map(parseCheckpoint),
      alerts,
      reports: reportRows.map(parseReport),
      costs,
      evals,
      audits,
      approvals,
      lifecycleOperations: lifecycleRows.map(parseLifecycleOperation),
      recommendations: buildRecommendations(runs, alerts),
      capabilities,
      warnings
    };
  } catch (error) {
    return {
      ...mockCommandCenterData,
      generatedAt: new Date().toISOString(),
      capabilities,
      warnings: [
        "PRODUCTION QUERY FAILED — demo isolation is active and no live action should be inferred.",
        error instanceof Error ? error.message : "Could not query Databricks."
      ]
    };
  }
}
