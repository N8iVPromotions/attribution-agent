"use client";

import Image from "next/image";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { PilotRoom } from "@/components/pilot-room";
import type {
  ClientAccount,
  CommandCenterData,
  PipelineRun,
  PipelineTriggerResult,
  RunStatus,
  TenantLifecycleCommand,
  TenantLifecycleResult
} from "@/lib/types";

type Props = { initialData: CommandCenterData };
type View = "Overview" | "Pilot Room" | "Pipeline" | "Tenants" | "Reports" | "Alerts" | "Governance" | "Audit";
type ReportStatusPresentation = { status: RunStatus; label: string };

const views: Array<{ id: View; code: string; description: string }> = [
  { id: "Overview", code: "01", description: "Fleet health" },
  { id: "Pilot Room", code: "02", description: "Configure & prove" },
  { id: "Pipeline", code: "03", description: "Execute & inspect" },
  { id: "Tenants", code: "04", description: "Source readiness" },
  { id: "Reports", code: "05", description: "Revenue intelligence" },
  { id: "Alerts", code: "06", description: "Operator action" },
  { id: "Governance", code: "07", description: "Cost & quality" },
  { id: "Audit", code: "08", description: "Control history" }
];

const modelLabels: Record<string, string> = {
  last_touch: "Last Touch",
  first_touch: "First Touch",
  linear: "Linear",
  time_decay: "Time Decay",
  u_shape: "U-Shape",
  w_shape: "W-Shape"
};

const sourceLabels: Record<string, string> = {
  meta: "Meta",
  google: "Google",
  linkedin: "LinkedIn",
  tiktok: "TikTok",
  hubspot: "HubSpot",
  stripe: "Stripe",
  normalized: "Ad rows"
};

const sourceOrder: Array<keyof PipelineRun["sourceRows"]> = [
  "meta",
  "google",
  "linkedin",
  "tiktok",
  "hubspot",
  "stripe",
  "normalized"
];

const money = (value: number, decimals = 0) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals
  }).format(value || 0);

const compact = (value: number) =>
  new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value || 0);

const percent = (value: number) => `${Math.round((value || 0) * 100)}%`;
const roi = (value: number) => `${Number(value || 0).toFixed(1)}×`;

function environmentCopy(source: CommandCenterData["source"]) {
  if (source === "databricks") {
    return { title: "Production telemetry", detail: "Databricks system of record" };
  }
  if (source === "unavailable") {
    return { title: "Live data unavailable", detail: "Sample data suppressed" };
  }
  return { title: "Isolated demo", detail: "No live data or actions" };
}

export function reportStatusPresentation(
  reportStatus: string,
  deliverySuppressed = false
): ReportStatusPresentation {
  if (deliverySuppressed || reportStatus === "suppressed") {
    return { status: "partial", label: "Delivery suppressed" };
  }
  return {
    status: reportStatus === "delivered" ? "success" : "queued",
    label: reportStatus
  };
}

function shortDate(value: string) {
  if (!value) return "Pending";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return value;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(parsed);
}

function duration(run: PipelineRun) {
  if (!run.startedAt) return "—";
  const end = run.finishedAt ? Date.parse(run.finishedAt) : Date.now();
  const seconds = Math.max(0, Math.round((end - Date.parse(run.startedAt)) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function CommandCenter({ initialData }: Props) {
  const [data, setData] = useState(initialData);
  const [activeView, setActiveView] = useState<View>("Overview");
  const [selectedAgency, setSelectedAgency] = useState(
    initialData.agencies[0]?.agencyId || initialData.clients.find((client) => client.agencyId)?.agencyId || ""
  );
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState("");

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setRefreshing(true);
    try {
      const response = await fetch("/api/summary", { cache: "no-store" });
      if (!response.ok) throw new Error(`Refresh failed with HTTP ${response.status}`);
      setData((await response.json()) as CommandCenterData);
      setRefreshError("");
    } catch (error) {
      setRefreshError(error instanceof Error ? error.message : "Refresh failed");
    } finally {
      if (!quiet) setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const interval = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh(true);
    }, 12_000);
    return () => window.clearInterval(interval);
  }, [refresh]);

  const agencies = data.agencies;

  const activeAgency = agencies.some((agency) => agency.agencyId === selectedAgency)
    ? selectedAgency
    : agencies[0]?.agencyId || selectedAgency;

  const scoped = useMemo(() => {
    const clients = data.clients.filter((client) => client.agencyId === activeAgency);
    const clientIds = new Set(clients.map((client) => client.clientId));
    return {
      clients,
      runs: data.runs.filter((run) => run.agencyId === activeAgency || clientIds.has(run.clientId)),
      reports: data.reports.filter((report) => report.agencyId === activeAgency || clientIds.has(report.clientId)),
      alerts: data.alerts.filter((alert) => !alert.clientId || clientIds.has(alert.clientId)),
      costs: data.costs.filter((cost) => cost.agencyId === activeAgency),
      approvals: data.approvals,
      audits: data.audits.filter((event) => !event.clientId || clientIds.has(event.clientId))
    };
  }, [activeAgency, data]);

  const activeRuns = scoped.runs.filter((run) => run.status === "running" || run.status === "queued").length;
  const partialRuns = scoped.runs.filter((run) => run.status === "partial").length;
  const suppressed = scoped.runs.filter((run) => run.deliverySuppressed).length;
  const aiSpend = scoped.costs.reduce((sum, item) => sum + item.costUsd, 0);
  const environment = environmentCopy(data.source);

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-wordmark">
            <Image
              src="/n8iv-promotions-wordmark.png"
              alt="N8iV Promotions"
              width={1050}
              height={600}
              priority
              unoptimized
            />
          </div>
          <div className="brand-product">
            <span>ARIE</span>
            <small>Revenue intelligence command</small>
          </div>
        </div>

        <div className={`environment ${data.source}`}>
          <span className="pulse" />
          <div>
            <strong>{environment.title}</strong>
            <small>{environment.detail}</small>
          </div>
        </div>

        <nav className="nav" aria-label="Command Center sections">
          {views.map((view) => (
            <button
              className={activeView === view.id ? "nav-item active" : "nav-item"}
              key={view.id}
              onClick={() => setActiveView(view.id)}
              type="button"
            >
              <span className="nav-code">{view.code}</span>
              <span><strong>{view.id}</strong><small>{view.description}</small></span>
            </button>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div><span>DB</span><strong>{data.source === "unavailable" ? "DOWN" : data.capabilities.databricks ? "ONLINE" : "DEMO"}</strong></div>
          <div><span>EXEC</span><strong>{data.capabilities.pipelineExecution ? "ARMED" : "OFFLINE"}</strong></div>
          <div><span>CTRL</span><strong>{data.capabilities.approvalActions ? "ONLINE" : "READ ONLY"}</strong></div>
          <div><span>CFG</span><strong>{data.capabilities.clientConfiguration ? "SECURE" : "LOCKED"}</strong></div>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div className="view-identity">
            <div className="brand-symbol" aria-hidden="true">
              <Image
                src="/n8iv-logo-transparent.png"
                alt=""
                width={3000}
                height={650}
                priority
                unoptimized
              />
            </div>
            <div>
              <p className="eyebrow">N8iV / Automatic Revenue Intelligence Engine</p>
              <h1>{activeView}</h1>
            </div>
          </div>
          <div className="topbar-actions">
            <label className="field compact-field">
              <span>Active agency</span>
              <select value={activeAgency} onChange={(event) => setSelectedAgency(event.target.value)}>
                {!agencies.length && <option value="">No active agencies</option>}
                {agencies.map((agency) => <option key={agency.agencyId} value={agency.agencyId}>{agency.name} · {agency.agencyId}</option>)}
              </select>
            </label>
            <div className="refresh-block">
              <small>Updated {shortDate(data.generatedAt)}</small>
              <button className="button ghost" onClick={() => void refresh()} disabled={refreshing} type="button">
                {refreshing ? "Refreshing…" : "Refresh data"}
              </button>
            </div>
          </div>
        </header>

        {data.warnings.length || refreshError ? (
          <div className="notice warning" role="status">
            <strong>Operator notice</strong>
            {[...data.warnings, refreshError].filter(Boolean).map((warning) => <span key={warning}>{warning}</span>)}
          </div>
        ) : null}

        {activeView === "Overview" && (
          <Overview
            data={data}
            clients={scoped.clients}
            runs={scoped.runs}
            alerts={scoped.alerts}
            activeRuns={activeRuns}
            partialRuns={partialRuns}
            suppressed={suppressed}
            aiSpend={aiSpend}
            onNavigate={setActiveView}
          />
        )}
        {activeView === "Pipeline" && (
          <PipelineView key={activeAgency} data={data} clients={scoped.clients} runs={scoped.runs} agencyId={activeAgency} onRefresh={refresh} />
        )}
        {activeView === "Pilot Room" && (
          <PilotRoom
            key={activeAgency}
            data={data}
            clients={scoped.clients}
            agencyId={activeAgency}
            onRefresh={refresh}
            onOpenPipeline={() => setActiveView("Pipeline")}
          />
        )}
        {activeView === "Tenants" && <TenantView data={data} clients={scoped.clients} runs={scoped.runs} agencyId={activeAgency} onRefresh={refresh} />}
        {activeView === "Reports" && <ReportsView data={data} reports={scoped.reports} />}
        {activeView === "Alerts" && <AlertsView data={data} alerts={scoped.alerts} onRefresh={refresh} />}
        {activeView === "Governance" && (
          <GovernanceView data={data} costs={scoped.costs} approvals={scoped.approvals} onRefresh={refresh} />
        )}
        {activeView === "Audit" && <AuditView data={data} audits={scoped.audits} />}
      </section>
    </main>
  );
}

function Overview({
  data,
  clients,
  runs,
  alerts,
  activeRuns,
  partialRuns,
  suppressed,
  aiSpend,
  onNavigate
}: {
  data: CommandCenterData;
  clients: ClientAccount[];
  runs: PipelineRun[];
  alerts: CommandCenterData["alerts"];
  activeRuns: number;
  partialRuns: number;
  suppressed: number;
  aiSpend: number;
  onNavigate: (view: View) => void;
}) {
  const referenceTime = Date.parse(data.generatedAt);
  const recent = runs.filter((run) => referenceTime - Date.parse(run.startedAt) < 30 * 86_400_000);
  const successRate = recent.length ? recent.filter((run) => run.status === "success").length / recent.length : 0;
  const pipeline = recent.reduce((sum, run) => sum + run.totalPipeline, 0);

  return (
    <div className="view-stack reveal">
      <section className="metric-grid">
        <Metric label="Tenant fleet" value={String(clients.length)} detail="Active accounts" signal="neutral" />
        <Metric label="Active executions" value={String(activeRuns)} detail="Queued or running" signal={activeRuns ? "live" : "neutral"} />
        <Metric label="30-day success" value={percent(successRate)} detail={`${recent.length} tenant runs`} signal={successRate >= 0.9 ? "good" : "warn"} />
        <Metric label="Attributed pipeline" value={money(pipeline)} detail="Last 30 days" signal="good" />
        <Metric label="Partial runs" value={String(partialRuns)} detail={`${suppressed} delivery suppressed`} signal={partialRuns ? "warn" : "good"} />
        <Metric label="AI spend / month" value={money(aiSpend, 2)} detail="Atomic cost ledger" signal="neutral" />
      </section>

      <section className="overview-grid">
        <Panel title="Tenant execution matrix" meta="Latest recorded state per tenant" action={<button className="text-button" onClick={() => onNavigate("Pipeline")}>Open pipeline →</button>}>
          <FleetMatrix clients={clients} runs={runs} />
        </Panel>
        <Panel title="Intervention queue" meta={`${alerts.length} open operator alerts`} action={<button className="text-button" onClick={() => onNavigate("Alerts")}>Review all →</button>}>
          <CompactAlerts alerts={alerts.slice(0, 4)} />
        </Panel>
      </section>

      <section className="overview-grid lower">
        <Panel title="Active checkpoint telemetry" meta="Real pipeline checkpoint records">
          <CheckpointRail data={data} runs={runs} />
        </Panel>
        <Panel title="Operating recommendations" meta="Derived from run and alert state">
          <RecommendationList recommendations={data.recommendations} />
        </Panel>
      </section>
    </div>
  );
}

function PipelineView({
  data,
  clients,
  runs,
  agencyId,
  onRefresh
}: {
  data: CommandCenterData;
  clients: ClientAccount[];
  runs: PipelineRun[];
  agencyId: string;
  onRefresh: (quiet?: boolean) => Promise<void>;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set(clients.map((client) => client.clientId)));
  const [model, setModel] = useState("w_shape");
  const [dryRun, setDryRun] = useState(true);
  const [confirmation, setConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<PipelineTriggerResult | null>(null);

  function toggle(clientId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(clientId)) next.delete(clientId);
      else next.add(clientId);
      return next;
    });
  }

  async function execute() {
    setSubmitting(true);
    setResult(null);
    try {
      const response = await fetch("/api/execute", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          agencyId,
          clientIds: [...selected],
          attributionModel: model,
          dryRun,
          confirmation: dryRun ? undefined : confirmation
        })
      });
      const payload = (await response.json()) as PipelineTriggerResult;
      setResult(payload);
      if (response.ok) window.setTimeout(() => void onRefresh(true), 1500);
    } catch (error) {
      setResult({ ok: false, message: error instanceof Error ? error.message : "Execution request failed." });
    } finally {
      setSubmitting(false);
    }
  }

  const armed = data.capabilities.pipelineExecution && selected.size > 0 && (dryRun || confirmation === "RUN LIVE");

  return (
    <div className="pipeline-layout reveal">
      <Panel title="Execution control" meta="Cloud Run task-array launcher">
        <div className="control-form">
          <div className="control-section">
            <div className="section-label"><span>01</span><strong>Select tenants</strong><button className="text-button" onClick={() => setSelected(new Set(clients.map((client) => client.clientId)))}>Select all</button></div>
            <div className="tenant-selector">
              {clients.map((client) => (
                <label className={selected.has(client.clientId) ? "tenant-option selected" : "tenant-option"} key={client.clientId}>
                  <input type="checkbox" checked={selected.has(client.clientId)} onChange={() => toggle(client.clientId)} />
                  <span><strong>{client.name}</strong><small>{client.clientId}</small></span>
                  <StatusChip status={latestRun(client.clientId, runs)?.status || "queued"} />
                </label>
              ))}
            </div>
          </div>

          <div className="control-section">
            <div className="section-label"><span>02</span><strong>Configure run</strong></div>
            <div className="form-grid">
              <label className="field"><span>Attribution model</span><select value={model} onChange={(event) => setModel(event.target.value)}>{Object.entries(modelLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
              <label className="mode-toggle"><input type="checkbox" checked={!dryRun} onChange={(event) => { setDryRun(!event.target.checked); setConfirmation(""); }} /><span><strong>{dryRun ? "Preview mode" : "Live delivery mode"}</strong><small>{dryRun ? "Generate output; suppress email" : "Client email may be delivered"}</small></span></label>
            </div>
            {!dryRun && (
              <label className="field danger-field"><span>Type RUN LIVE to arm delivery</span><input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder="RUN LIVE" autoComplete="off" /></label>
            )}
          </div>

          <div className="execution-footer">
            <div><span className="eyebrow">Execution manifest</span><strong>{selected.size} tenant{selected.size === 1 ? "" : "s"} / {modelLabels[model]} / {dryRun ? "preview" : "live"}</strong></div>
            <button className={dryRun ? "button primary" : "button danger"} disabled={!armed || submitting} onClick={() => void execute()} type="button">
              {submitting ? "Submitting job…" : dryRun ? "Launch preview run" : "Launch live run"}
            </button>
          </div>
          {!data.capabilities.pipelineExecution && <div className="inline-warning">Execution is read-only until Cloud Run OIDC or the ARIE pipeline API is configured.</div>}
          {result && <div className={result.ok ? "notice success" : "notice warning"}><strong>{result.ok ? "Execution accepted" : "Execution blocked"}</strong><span>{result.message}</span>{result.operation && <code>{result.operation}</code>}</div>}
        </div>
      </Panel>

      <Panel title="Execution history" meta={`${runs.length} recent tenant runs`}>
        <RunTable runs={runs} data={data} />
      </Panel>
    </div>
  );
}

type LifecycleDialogState = {
  command: TenantLifecycleCommand;
  entityId?: string;
  entityName?: string;
};

const protectedTenantIds = new Set(["demo_agency", "demo_client", "n8iv_promotions"]);

function lifecycleRunStatus(status: string): RunStatus {
  if (status === "running" || status === "queued" || status === "failed") return status;
  return status === "succeeded" ? "success" : "warning";
}

function TenantView({
  data,
  clients,
  runs,
  agencyId,
  onRefresh
}: {
  data: CommandCenterData;
  clients: ClientAccount[];
  runs: PipelineRun[];
  agencyId: string;
  onRefresh: (quiet?: boolean) => Promise<void>;
}) {
  const [dialog, setDialog] = useState<LifecycleDialogState | null>(null);
  const [lastResult, setLastResult] = useState<TenantLifecycleResult | null>(null);
  const agency = data.agencies.find((item) => item.agencyId === agencyId);
  const operations = data.lifecycleOperations.filter(
    (operation) => operation.agencyId === agencyId || operation.entityId === agencyId
  );
  const lifecycleEnabled = data.capabilities.tenantLifecycle;

  return (
    <div className="tenant-workspace reveal">
      <section className="registry-control">
        <div>
          <span className="eyebrow">Databricks tenant control plane</span>
          <h2>{agency?.name || "Agency registry"}</h2>
          <p>Lifecycle commands run as a serialized Databricks Workflow and are recorded in the operations ledger.</p>
        </div>
        <div className="registry-actions">
          <button className="button ghost" disabled={!lifecycleEnabled} onClick={() => setDialog({ command: "create_agency" })} type="button">New agency</button>
          <button className="button primary" disabled={!lifecycleEnabled || !agencyId} onClick={() => setDialog({ command: "create_business" })} type="button">New business</button>
          <button className="button danger-outline" disabled={!lifecycleEnabled || !agencyId || clients.length > 0 || protectedTenantIds.has(agencyId)} onClick={() => setDialog({ command: "delete_agency", entityId: agencyId, entityName: agency?.name })} type="button">Delete agency</button>
        </div>
      </section>

      {!lifecycleEnabled && <div className="inline-warning">Tenant controls are read-only until DATABRICKS_TENANT_LIFECYCLE_JOB_ID is configured.</div>}
      {agencyId && clients.length > 0 && !protectedTenantIds.has(agencyId) && <div className="registry-rule">Agency deletion is locked while {clients.length} active business{clients.length === 1 ? " remains" : "es remain"}.</div>}
      {lastResult && <div className={lastResult.ok ? "notice success" : "notice warning"}><strong>{lastResult.ok ? "Lifecycle command accepted" : "Lifecycle command blocked"}</strong><span>{lastResult.message}</span>{lastResult.runUrl && <a href={lastResult.runUrl} target="_blank" rel="noreferrer">Open Databricks run {lastResult.runId} ↗</a>}</div>}

      <div className="tenant-grid">
        {clients.map((client) => {
          const run = latestRun(client.clientId, runs);
          const enabled = Object.entries(client.platforms).filter(([, value]) => value).length;
          return (
            <article className="tenant-card" key={client.clientId}>
              <header><div><span className="eyebrow">{client.clientId}</span><h2>{client.name}</h2></div><StatusChip status={run?.status || "queued"} /></header>
              <div className="tenant-metrics"><div><span>Model</span><strong>{modelLabels[client.attributionModel] || client.attributionModel}</strong></div><div><span>Lookback</span><strong>{client.lookbackDays} days</strong></div><div><span>Sources</span><strong>{enabled} / 6</strong></div></div>
              <PlatformPills platforms={client.platforms} />
              <div className="tenant-run"><span>Latest execution</span><strong>{run ? shortDate(run.startedAt) : "No run recorded"}</strong>{run?.deliverySuppressed && <small className="danger-copy">Report delivery suppressed</small>}</div>
              <footer><span>{client.reportEmail || "No report recipient"}</span><span>{run ? money(run.totalPipeline) : "—"}</span></footer>
              <button className="tenant-delete" disabled={!lifecycleEnabled || protectedTenantIds.has(client.clientId) || Boolean(run && (run.status === "running" || run.status === "queued"))} onClick={() => setDialog({ command: "delete_business", entityId: client.clientId, entityName: client.name })} type="button">Delete business</button>
            </article>
          );
        })}
      </div>

      {!clients.length && agencyId && <Empty title="No active businesses" body="Create the first business for this agency to begin source configuration." />}

      <Panel title="Lifecycle operations" meta="Latest Databricks job state">
        {operations.length ? <div className="lifecycle-list">{operations.slice(0, 12).map((operation) => <article key={operation.requestId}><StatusChip status={lifecycleRunStatus(operation.status)} label={operation.status} /><div><strong>{operation.command.replaceAll("_", " ")} · {operation.entityId}</strong><small>{shortDate(operation.requestedAt)} · {operation.requestedBy}</small>{operation.errorMessage && <span className="danger-copy">{operation.errorMessage}</span>}</div><code>{operation.databricksRunId ? `run ${operation.databricksRunId}` : operation.requestId}</code></article>)}</div> : <Empty title="No lifecycle operations" body="Create and deletion commands will be recorded here." />}
      </Panel>

      {dialog && <LifecycleDialog key={`${dialog.command}-${dialog.entityId || "new"}`} state={dialog} agencyId={agencyId} onClose={() => setDialog(null)} onAccepted={async (result) => { setLastResult(result); setDialog(null); await onRefresh(true); }} />}
    </div>
  );
}

function slugify(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 63);
}

function LifecycleDialog({ state, agencyId, onClose, onAccepted }: { state: LifecycleDialogState; agencyId: string; onClose: () => void; onAccepted: (result: TenantLifecycleResult) => Promise<void> }) {
  const creating = state.command.startsWith("create_");
  const agencyCommand = state.command.endsWith("agency");
  const [entityName, setEntityName] = useState(state.entityName || "");
  const [entityId, setEntityId] = useState(state.entityId || "");
  const [reportEmail, setReportEmail] = useState("");
  const [attributionModel, setAttributionModel] = useState("last_touch");
  const [confirmation, setConfirmation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const expectedConfirmation = `DELETE ${agencyCommand ? "AGENCY" : "BUSINESS"} ${entityId}`;

  async function submit() {
    setSubmitting(true);
    setError("");
    try {
      const response = await fetch("/api/tenants/lifecycle", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ command: state.command, entityId, entityName, agencyId, reportEmail, attributionModel, confirmation }) });
      const payload = (await response.json()) as TenantLifecycleResult & { error?: string };
      if (!response.ok) throw new Error(payload.error || payload.message || "Lifecycle command failed.");
      await onAccepted(payload);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Lifecycle command failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="lifecycle-dialog" role="dialog" aria-modal="true" aria-labelledby="lifecycle-dialog-title">
        <header><div><span className="eyebrow">Controlled Databricks operation</span><h2 id="lifecycle-dialog-title">{state.command.replaceAll("_", " ")}</h2></div><button className="dialog-close" onClick={onClose} type="button" aria-label="Close">×</button></header>
        {creating ? <div className="dialog-form">
          <label className="field"><span>{agencyCommand ? "Agency" : "Business"} name</span><input value={entityName} onChange={(event) => { const name = event.target.value; setEntityName(name); if (!entityId || entityId === slugify(entityName)) setEntityId(slugify(name)); }} placeholder="Acme Media" autoFocus /></label>
          <label className="field"><span>Immutable ID</span><input value={entityId} onChange={(event) => setEntityId(event.target.value.toLowerCase())} placeholder="acme_media" /><small>Lowercase letters, numbers, and underscores. This determines the Databricks schema name.</small></label>
          {!agencyCommand && <><label className="field"><span>Agency</span><input value={agencyId} disabled /></label><label className="field"><span>Report email</span><input type="email" value={reportEmail} onChange={(event) => setReportEmail(event.target.value)} placeholder="reports@example.com" /></label><label className="field"><span>Attribution model</span><select value={attributionModel} onChange={(event) => setAttributionModel(event.target.value)}>{Object.entries(modelLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label></>}
        </div> : <div className="delete-confirmation">
          <div className="destructive-warning"><strong>This drops the managed Databricks schema and all tables inside it.</strong><span>The registry entry remains as an inactive audit record. This action cannot be launched while pipeline work is active.</span></div>
          <label className="field danger-field"><span>Enter <code>{expectedConfirmation}</code></span><input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" autoFocus /></label>
        </div>}
        {error && <div className="inline-warning">{error}</div>}
        <footer><button className="button ghost" onClick={onClose} disabled={submitting} type="button">Cancel</button><button className={creating ? "button primary" : "button danger"} onClick={() => void submit()} disabled={submitting || !entityId || (creating ? entityName.length < 2 : confirmation !== expectedConfirmation)} type="button">{submitting ? "Submitting…" : creating ? "Create through Databricks" : "Execute guarded deletion"}</button></footer>
      </section>
    </div>
  );
}

function ReportsView({ data, reports }: { data: CommandCenterData; reports: CommandCenterData["reports"] }) {
  const [selectedId, setSelectedId] = useState(reports[0]?.reportId || "");
  const effectiveSelectedId = reports.some((report) => report.reportId === selectedId) ? selectedId : reports[0]?.reportId || "";
  const selected = reports.find((report) => report.reportId === effectiveSelectedId);
  if (!selected) return <Empty title="No insight reports" body="Reports will appear after a successful attribution run completes." />;
  const run = data.runs.find((item) => item.runId === selected.runId);
  const statusPresentation = reportStatusPresentation(selected.status, run?.deliverySuppressed);

  return (
    <div className="report-layout reveal">
      <Panel title="Report ledger" meta={`${reports.length} generated reports`}>
        <div className="report-list">{reports.map((report) => <button className={effectiveSelectedId === report.reportId ? "report-item active" : "report-item"} onClick={() => setSelectedId(report.reportId)} key={report.reportId}><span><strong>{clientName(report.clientId, data.clients)}</strong><small>{report.reportMonth} / {modelLabels[report.attributionModel] || report.attributionModel}</small></span><span><strong>{money(report.totalPipeline)}</strong><small>{report.status}</small></span></button>)}</div>
      </Panel>
      <article className="report-sheet">
        <header><div><span className="eyebrow">Executive revenue intelligence / {selected.reportMonth}</span><h2>{clientName(selected.clientId, data.clients)}</h2><p>Generated {shortDate(selected.generatedAt)} · {selected.modelId} · {selected.promptVersion}</p></div><StatusChip status={statusPresentation.status} label={statusPresentation.label} /></header>
        <section className="report-metrics"><div><span>Pipeline</span><strong>{money(selected.totalPipeline)}</strong></div><div><span>Spend</span><strong>{money(selected.totalSpend)}</strong></div><div><span>Attributed ROI</span><strong>{roi(selected.overallRoi)}</strong></div><div><span>True ROI</span><strong>{roi(selected.trueRoi)}</strong></div></section>
        <section className="narrative"><span className="eyebrow">Operator preview</span><p>{selected.narrative}</p></section>
        <section><span className="eyebrow">Key findings</span><ol className="findings">{selected.keyFindings.map((finding) => <li key={finding}>{finding}</li>)}</ol></section>
      </article>
    </div>
  );
}

function AlertsView({
  data,
  alerts,
  onRefresh
}: {
  data: CommandCenterData;
  alerts: CommandCenterData["alerts"];
  onRefresh: (quiet?: boolean) => Promise<void>;
}) {
  const [busyAlertId, setBusyAlertId] = useState("");
  const [error, setError] = useState("");

  async function resolveAlert(alertId: string) {
    setBusyAlertId(alertId);
    setError("");
    try {
      const response = await fetch(`/api/alerts/${encodeURIComponent(alertId)}/resolve`, {
        method: "POST"
      });
      const payload = (await response.json().catch(() => ({}))) as { error?: string };
      if (!response.ok) {
        throw new Error(payload.error || "Alert resolution failed.");
      }
      await onRefresh();
    } catch (resolveError) {
      setError(resolveError instanceof Error ? resolveError.message : "Alert resolution failed.");
    } finally {
      setBusyAlertId("");
    }
  }

  if (!alerts.length) return <Empty title="No open alerts" body="The selected agency has no operator interventions waiting." />;
  return (
    <div className="alert-board reveal">
      {error && <div className="inline-warning">{error}</div>}
      {alerts.map((alert) => {
        const alertKey = alert.alertId || `${alert.eventTime}-${alert.title}`;
        const resolving = busyAlertId === alert.alertId;
        return (
          <article className={`alert-row ${alert.severity}`} key={alertKey}>
            <div className="alert-index">{alert.severity === "critical" ? "!!" : "!"}</div>
            <div>
              <div className="alert-meta">
                <StatusChip status={alert.severity === "critical" ? "failed" : "partial"} label={alert.severity} />
                <span>{alert.source || "platform"}</span>
                <span>{shortDate(alert.eventTime)}</span>
              </div>
              <h2>{alert.title}</h2>
              <p>{alert.message}</p>
              <strong className="action-copy">Next action: {alert.actionRequired || "Review the run record."}</strong>
            </div>
            <div className="alert-context">
              <span>Tenant</span>
              <strong>{clientName(alert.clientId, data.clients)}</strong>
              <span>Run</span>
              <strong>{alert.runId || "—"}</strong>
              <button
                className="button ghost alert-resolve"
                disabled={!alert.alertId || data.source !== "databricks" || resolving}
                onClick={() => void resolveAlert(alert.alertId)}
                type="button"
              >
                {resolving ? "Resolving..." : "Mark resolved"}
              </button>
            </div>
          </article>
        );
      })}
      {data.source !== "databricks" && (
        <div className="inline-warning">Alert resolution is available only when ARIE is reading live Databricks telemetry.</div>
      )}
    </div>
  );
}

function GovernanceView({ data, costs, approvals, onRefresh }: { data: CommandCenterData; costs: CommandCenterData["costs"]; approvals: CommandCenterData["approvals"]; onRefresh: (quiet?: boolean) => Promise<void> }) {
  const [busy, setBusy] = useState("");
  async function resolve(actionId: string, resolution: "approved" | "rejected") {
    setBusy(actionId);
    try {
      const response = await fetch(`/api/approvals/${encodeURIComponent(actionId)}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ resolution }) });
      if (!response.ok) throw new Error(((await response.json()) as { error?: string }).error || "Approval update failed");
      await onRefresh();
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="governance-grid reveal">
      <Panel title="AI cost ledger" meta="Current calendar month">
        <div className="ledger">{costs.length ? costs.map((cost) => <div className="ledger-row" key={cost.agencyId}><div><strong>{cost.agencyId}</strong><small>Last activity {shortDate(cost.lastEventAt)}</small></div><div><strong>{compact(cost.totalTokens)}</strong><small>tokens</small></div><div><strong>{money(cost.costUsd, 2)}</strong><small>estimated spend</small></div></div>) : <Empty title="No cost events" body="Model usage will appear after the first AI report run." />}</div>
      </Panel>
      <Panel title="Golden dataset evaluations" meta="Latest score per agent">
        <div className="eval-list">{data.evals.map((item) => <div className="eval-row" key={item.agentName}><div><strong>{item.agentName}</strong><small>{item.promptVersion} / {item.modelId}</small></div><div className="score"><span style={{ width: `${Math.min(100, item.score * 100)}%` }} /></div><strong>{percent(item.score)}</strong><StatusChip status={item.regression || !item.passed ? "failed" : "success"} label={item.regression ? "Regression" : item.passed ? "Passed" : "Failed"} /></div>)}</div>
      </Panel>
      <Panel title="Approval queue" meta={`${approvals.length} pending actions`}>
        <div className="approval-list">{approvals.length ? approvals.map((approval) => <article className="approval-row" key={approval.actionId}><div><span className="eyebrow">{approval.actionType} / {approval.channel}</span><strong>{approval.description}</strong><small>Raised by {approval.actor} · {shortDate(approval.createdAt)}</small></div><div className="approval-actions"><button className="button ghost" disabled={busy === approval.actionId || !data.capabilities.approvalActions} onClick={() => void resolve(approval.actionId, "rejected")}>Reject</button><button className="button primary" disabled={busy === approval.actionId || !data.capabilities.approvalActions} onClick={() => void resolve(approval.actionId, "approved")}>Approve</button></div></article>) : <Empty title="Queue clear" body="No reports or operator actions are waiting for approval." />}</div>
        {!data.capabilities.approvalActions && approvals.length > 0 && <div className="inline-warning">Approval actions are read-only until ARIE_API_BASE and ARIE_API_KEY are configured.</div>}
      </Panel>
    </div>
  );
}

function AuditView({ data, audits }: { data: CommandCenterData; audits: CommandCenterData["audits"] }) {
  return (
    <Panel title="Immutable operator trail" meta={`${audits.length} most recent events`}>
      <div className="audit-table table-scroll"><table><thead><tr><th>Time</th><th>Event</th><th>Actor</th><th>Tenant</th><th>Action</th><th>Outcome</th><th>Run</th></tr></thead><tbody>{audits.map((event) => <tr key={event.eventId}><td>{shortDate(event.eventTime)}</td><td><code>{event.eventType}</code></td><td>{event.actor}</td><td>{clientName(event.clientId, data.clients)}</td><td>{event.action}</td><td><StatusChip status={event.outcome === "failed" ? "failed" : event.outcome.includes("partial") ? "partial" : "success"} label={event.outcome} /></td><td><code>{event.runId || "—"}</code></td></tr>)}</tbody></table></div>
    </Panel>
  );
}

function Metric({ label, value, detail, signal }: { label: string; value: string; detail: string; signal: "neutral" | "live" | "good" | "warn" }) {
  return <article className={`metric-card ${signal}`}><span>{label}</span><strong>{value}</strong><p>{detail}</p></article>;
}

function Panel({ title, meta, action, children }: { title: string; meta?: string; action?: ReactNode; children: ReactNode }) {
  return <section className="panel"><header className="panel-heading"><div><h2>{title}</h2>{meta && <p>{meta}</p>}</div>{action}</header>{children}</section>;
}

function StatusChip({ status, label }: { status: RunStatus | "critical"; label?: string }) {
  return <span className={`status ${status}`}><i />{label || status}</span>;
}

function latestRun(clientId: string, runs: PipelineRun[]) {
  return runs.find((run) => run.clientId === clientId);
}

function clientName(clientId: string, clients: ClientAccount[]) {
  return clients.find((client) => client.clientId === clientId)?.name || clientId || "Portfolio";
}

function hasSourceIssue(run: PipelineRun, source: keyof PipelineRun["sourceRows"]) {
  const haystack = `${run.warnings} ${run.error}`.toLowerCase();
  const aliases: Record<string, string[]> = {
    google: ["google", "google-ads"],
    linkedin: ["linkedin", "linkedin-ads"],
    meta: ["meta"],
    hubspot: ["hubspot"],
    stripe: ["stripe"],
    normalized: ["normalized"]
  };
  return (aliases[source] || [source]).some((alias) => haystack.includes(alias));
}

function sourceReadiness(
  run: PipelineRun,
  client: ClientAccount | undefined,
  source: keyof PipelineRun["sourceRows"]
) {
  const rows = run.sourceRows[source] || 0;
  if (source === "normalized") {
    return rows > 0 ? rows.toLocaleString() : "No ad rows";
  }
  const platformKey = source as keyof ClientAccount["platforms"];
  if (!client?.platforms[platformKey]) return "Disabled";
  if (rows > 0) return rows.toLocaleString();
  return hasSourceIssue(run, source) ? "Issue" : "No data";
}

function FleetMatrix({ clients, runs }: { clients: ClientAccount[]; runs: PipelineRun[] }) {
  return <div className="fleet-list">{clients.map((client) => { const run = latestRun(client.clientId, runs); return <article className="fleet-row" key={client.clientId}><div><strong>{client.name}</strong><small>{client.clientId}</small></div><PlatformPills platforms={client.platforms} compactMode /><div><span>Last run</span><strong>{run ? shortDate(run.startedAt) : "Never"}</strong></div><div><span>Pipeline</span><strong>{run ? money(run.totalPipeline) : "—"}</strong></div><div>{run?.deliverySuppressed && <small className="danger-copy">EMAIL SUPPRESSED</small>}<StatusChip status={run?.status || "queued"} /></div></article>; })}</div>;
}

function CompactAlerts({ alerts }: { alerts: CommandCenterData["alerts"] }) {
  if (!alerts.length) return <Empty title="Queue clear" body="No operator alerts require intervention." />;
  return <div className="compact-alerts">{alerts.map((alert) => <article key={alert.alertId || alert.title}><StatusChip status={alert.severity === "critical" ? "failed" : "partial"} label={alert.severity} /><div><strong>{alert.title}</strong><small>{alert.clientId || "Platform"} / {alert.source || "system"}</small></div></article>)}</div>;
}

function CheckpointRail({ data, runs }: { data: CommandCenterData; runs: PipelineRun[] }) {
  const active = runs.find((run) => run.status === "running" || run.status === "queued") || runs[0];
  if (!active) return <Empty title="No execution telemetry" body="Checkpoint events appear while and after runs execute." />;
  const checkpoints = data.checkpoints.filter((checkpoint) => checkpoint.runId === active.runId);
  if (!checkpoints.length) return <Empty title={`Run ${active.runId}`} body="No checkpoint records have been written for this run." />;
  return <div className="checkpoint-list"><div className="checkpoint-run"><span className="eyebrow">Run {active.runId}</span><strong>{clientName(active.clientId, data.clients)}</strong></div>{[...checkpoints].reverse().map((checkpoint) => <div className="checkpoint" key={checkpoint.checkpointId}><StatusChip status={checkpoint.status === "failed" ? "failed" : checkpoint.status === "running" ? "running" : checkpoint.status === "suppressed" ? "partial" : "success"} label={checkpoint.status} /><div><strong>{checkpoint.stepName.replaceAll("_", " ")}</strong><small>{checkpoint.errorDetail || (checkpoint.completedAt ? `Completed ${shortDate(checkpoint.completedAt)}` : `Started ${shortDate(checkpoint.startedAt)}`)}</small></div></div>)}</div>;
}

function RunTable({ runs, data }: { runs: PipelineRun[]; data: CommandCenterData }) {
  const [open, setOpen] = useState("");
  if (!runs.length) return <Empty title="No execution history" body="Launch a preview run to create the first operational record." />;
  return <div className="run-table">{runs.map((run) => { const expanded = open === run.runId; const checkpoints = data.checkpoints.filter((item) => item.runId === run.runId); const client = data.clients.find((item) => item.clientId === run.clientId); return <article className={`run-record ${expanded ? "expanded" : ""}`} key={`${run.runId}-${run.clientId}`}><button className="run-summary" onClick={() => setOpen(expanded ? "" : run.runId)}><div><strong>{clientName(run.clientId, data.clients)}</strong><small>{run.runId} / {modelLabels[run.attributionModel] || run.attributionModel}</small></div><div><span>Started</span><strong>{shortDate(run.startedAt)}</strong></div><div><span>Duration</span><strong>{duration(run)}</strong></div><div><span>Pipeline</span><strong>{money(run.totalPipeline)}</strong></div><div><StatusChip status={run.status} />{run.deliverySuppressed && <small className="danger-copy">EMAIL SUPPRESSED</small>}</div><b>{expanded ? "−" : "+"}</b></button>{expanded && <div className="run-detail"><div className="source-counts">{sourceOrder.map((source) => <div key={source}><span>{sourceLabels[source] || source}</span><strong>{sourceReadiness(run, client, source)}</strong></div>)}</div><div className="run-facts"><span>Output schema</span><code>{run.outputSchema || "—"}</code><span>Delivery</span><strong>{run.dryRun ? "Preview — not delivered" : run.emailSent ? "Email sent" : run.deliverySuppressed ? "Suppressed by partial-run policy" : "Not sent"}</strong><span>Top channel</span><strong>{run.topChannel}</strong></div>{(run.warnings || run.error) && <div className="run-message"><strong>{run.error ? "Execution error" : "Run warning"}</strong><p>{run.error || run.warnings}</p></div>}<div className="mini-checkpoints">{checkpoints.map((item) => <span key={item.checkpointId} className={item.status}>{item.stepName.replaceAll("_", " ")}</span>)}</div></div>}</article>; })}</div>;
}

function PlatformPills({ platforms, compactMode = false }: { platforms: ClientAccount["platforms"]; compactMode?: boolean }) {
  return <div className={compactMode ? "pill-row compact" : "pill-row"}>{Object.entries(platforms).map(([name, enabled]) => <span className={enabled ? "platform-pill enabled" : "platform-pill"} key={name}>{compactMode ? name.slice(0, 2).toUpperCase() : sourceLabels[name] || name}</span>)}</div>;
}

function RecommendationList({ recommendations }: { recommendations: CommandCenterData["recommendations"] }) {
  return <div className="recommendation-list">{recommendations.map((item, index) => <article key={item.title}><span className={`recommendation-number ${item.severity}`}>{String(index + 1).padStart(2, "0")}</span><div><strong>{item.title}</strong><p>{item.body}</p></div></article>)}</div>;
}

function Empty({ title, body }: { title: string; body: string }) {
  return <div className="empty"><span>—</span><strong>{title}</strong><p>{body}</p></div>;
}
