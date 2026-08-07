import { runSql, hasDatabricksConfig, opsSchema } from "@/lib/databricks";
import { mockCommandCenterData } from "@/lib/mock-data";
import type {
  ClientAccount,
  CommandCenterData,
  OperatorAlert,
  PipelineRun,
  Recommendation,
  RunStatus
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
  if (status === "success" || status === "failed" || status === "running" || status === "queued") {
    return status;
  }
  return "warning";
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
    name: text(config.client_name, text(config.client_display_name, text(row.client_id, "Unnamed client"))),
    agencyId: text(config.agency_id, "demo_agency"),
    attributionModel: text(config.attribution_model, "last_touch"),
    reportEmail: text(config.client_report_email),
    active: bool(row.is_active ?? true),
    platforms: {
      meta: bool(config.meta_enabled),
      google: bool(config.google_ads_enabled),
      linkedin: bool(config.linkedin_ads_enabled),
      hubspot: bool(config.hubspot_enabled),
      stripe: bool(config.stripe_enabled)
    },
    updatedAt: text(row.updated_at, new Date().toISOString())
  };
}

function parseRun(row: Record<string, unknown>): PipelineRun {
  return {
    runId: text(row.run_id),
    agencyId: text(row.agency_id),
    clientId: text(row.client_id),
    runMode: text(row.run_mode, "client"),
    attributionModel: text(row.attribution_model, "last_touch"),
    status: safeStatus(row.status),
    dryRun: bool(row.dry_run),
    totalPipeline: number(row.total_pipeline),
    topChannel: text(row.top_channel, "Unknown"),
    emailSent: bool(row.email_sent),
    warnings: text(row.warnings),
    error: text(row.error),
    startedAt: text(row.started_at, new Date().toISOString()),
    finishedAt: text(row.finished_at),
    sourceRows: {
      meta: number(row.meta_rows),
      google: number(row.google_rows),
      linkedin: number(row.linkedin_rows),
      hubspot: number(row.hubspot_rows),
      stripe: number(row.stripe_rows)
    }
  };
}

function parseAlert(row: Record<string, unknown>): OperatorAlert {
  const severity = text(row.severity, "info").toLowerCase();
  return {
    severity: severity === "critical" || severity === "warning" ? severity : "info",
    category: text(row.category),
    title: text(row.title, "Operator alert"),
    message: text(row.message),
    clientId: text(row.client_id),
    source: text(row.source),
    actionRequired: text(row.action_required),
    status: text(row.status, "open"),
    eventTime: text(row.event_time, new Date().toISOString())
  };
}

function buildRecommendations(runs: PipelineRun[], alerts: OperatorAlert[]): Recommendation[] {
  const recommendations: Recommendation[] = [];
  const failedRuns = runs.filter((run) => run.status === "failed");
  const criticalAlerts = alerts.filter((alert) => alert.severity === "critical");
  const warningRuns = runs.filter((run) => run.status === "warning" || run.warnings);

  if (criticalAlerts.length) {
    recommendations.push({
      severity: "critical",
      title: "Resolve critical connector issues first",
      body: `${criticalAlerts.length} critical alert(s) are open. Fix credentials or source access before running live reports.`
    });
  }

  if (failedRuns.length) {
    recommendations.push({
      severity: "warning",
      title: "Review failed attribution runs",
      body: `${failedRuns.length} recent run(s) failed. Check source row counts and rerun in dry-run mode before sending reports.`
    });
  }

  if (warningRuns.length) {
    recommendations.push({
      severity: "warning",
      title: "Treat warnings as pilot onboarding work",
      body: "Warnings usually point to UTM gaps, missing CRM fields, or incomplete payment enrichment."
    });
  }

  if (!recommendations.length) {
    recommendations.push({
      severity: "info",
      title: "Portfolio is ready for pilot reporting",
      body: "Recent runs are healthy. Use dry-run reports to validate language before client delivery."
    });
  }

  return recommendations;
}

export async function getCommandCenterData(): Promise<CommandCenterData> {
  if (!hasDatabricksConfig()) {
    return { ...mockCommandCenterData, generatedAt: new Date().toISOString() };
  }

  const schema = opsSchema();
  const warnings: string[] = [];

  try {
    const [runRows, clientRows, alertRows] = await Promise.all([
      runSql<Record<string, unknown>>(`
        SELECT run_id, agency_id, client_id, run_mode, attribution_model, status,
               dry_run, meta_rows, google_rows, linkedin_rows, hubspot_rows,
               stripe_rows, total_pipeline, top_channel, email_sent, warnings,
               error, started_at, finished_at
        FROM ${schema}.pipeline_runs
        ORDER BY started_at DESC
        LIMIT 20
      `),
      runSql<Record<string, unknown>>(`
        SELECT client_id, config_json, is_active, updated_at
        FROM ${schema}.client_registry
        WHERE is_active = true
        ORDER BY updated_at DESC
        LIMIT 50
      `),
      runSql<Record<string, unknown>>(`
        SELECT severity, category, title, message, client_id, source,
               action_required, status, event_time
        FROM ${schema}.operator_alerts
        WHERE status = 'open'
        ORDER BY event_time DESC
        LIMIT 20
      `)
    ]);

    const runs = runRows.map(parseRun);
    const clients = clientRows.map(parseClient);
    const alerts = alertRows.map(parseAlert);
    const recentRuns = runs.filter((run) => Date.now() - Date.parse(run.startedAt) <= 30 * 86400000);
    const successfulRuns = recentRuns.filter((run) => run.status === "success").length;

    return {
      source: "databricks",
      generatedAt: new Date().toISOString(),
      summary: {
        activeClients: clients.length,
        runs30d: recentRuns.length,
        successRate: recentRuns.length ? successfulRuns / recentRuns.length : 0,
        attributedPipeline: runs.reduce((sum, run) => sum + run.totalPipeline, 0),
        openAlerts: alerts.length,
        criticalAlerts: alerts.filter((alert) => alert.severity === "critical").length
      },
      clients,
      runs,
      alerts,
      recommendations: buildRecommendations(runs, alerts),
      warnings
    };
  } catch (error) {
    warnings.push(error instanceof Error ? error.message : "Could not query Databricks.");
    return {
      ...mockCommandCenterData,
      source: "demo",
      generatedAt: new Date().toISOString(),
      warnings: [
        "Databricks query failed, so ARIE is showing demo data.",
        ...warnings
      ]
    };
  }
}
