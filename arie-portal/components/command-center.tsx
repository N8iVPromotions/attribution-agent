"use client";

import { useMemo, useState, type ReactNode } from "react";
import type { CommandCenterData, PipelineTriggerResult } from "@/lib/types";

type Props = {
  initialData: CommandCenterData;
};

const modelLabels: Record<string, string> = {
  last_touch: "Last Touch",
  first_touch: "First Touch",
  linear: "Linear",
  time_decay: "Time Decay",
  u_shape: "U-Shape",
  w_shape: "W-Shape"
};

function money(value: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0
  }).format(value);
}

function percent(value: number) {
  return `${Math.round(value * 100)}%`;
}

function shortDate(value: string) {
  if (!value) {
    return "Pending";
  }
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit"
  }).format(new Date(value));
}

export function CommandCenter({ initialData }: Props) {
  const [activeView, setActiveView] = useState("Overview");
  const [selectedAgency, setSelectedAgency] = useState("demo_agency");
  const [selectedModel, setSelectedModel] = useState("w_shape");
  const [triggerResult, setTriggerResult] = useState<PipelineTriggerResult | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const agencies = useMemo(() => {
    const ids = new Set(initialData.clients.map((client) => client.agencyId).filter(Boolean));
    ids.add("demo_agency");
    return Array.from(ids);
  }, [initialData.clients]);

  async function runPipeline() {
    setIsSubmitting(true);
    setTriggerResult(null);
    try {
      const response = await fetch("/api/execute", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          agencyId: selectedAgency,
          attributionModel: selectedModel,
          dryRun: true,
          runMode: "agency"
        })
      });
      setTriggerResult((await response.json()) as PipelineTriggerResult);
    } catch (error) {
      setTriggerResult({
        ok: false,
        message: error instanceof Error ? error.message : "Pipeline request failed."
      });
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">AR</div>
          <div>
            <div className="brand-title">ARIE</div>
            <div className="brand-subtitle">N8iV Promotions</div>
          </div>
        </div>

        <nav className="nav">
          {["Overview", "Clients", "Runs", "Alerts", "Recommendations"].map((view) => (
            <button
              className={activeView === view ? "nav-item active" : "nav-item"}
              key={view}
              onClick={() => setActiveView(view)}
              type="button"
            >
              <span>{view}</span>
            </button>
          ))}
        </nav>

        <div className="sidebar-card">
          <span className="eyebrow">Data source</span>
          <strong>{initialData.source === "databricks" ? "Databricks live" : "Demo fallback"}</strong>
          <p>Generated {shortDate(initialData.generatedAt)}</p>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Automatic Revenue Intelligence Engine</p>
            <h1>Command Center</h1>
          </div>
          <div className="topbar-actions">
            <select value={selectedAgency} onChange={(event) => setSelectedAgency(event.target.value)}>
              {agencies.map((agency) => (
                <option key={agency} value={agency}>
                  {agency}
                </option>
              ))}
            </select>
            <select value={selectedModel} onChange={(event) => setSelectedModel(event.target.value)}>
              {Object.entries(modelLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <button className="primary-button" disabled={isSubmitting} onClick={runPipeline} type="button">
              {isSubmitting ? "Submitting" : "Run dry run"}
            </button>
          </div>
        </header>

        {initialData.warnings.length > 0 ? (
          <div className="notice">
            {initialData.warnings.map((warning) => (
              <span key={warning}>{warning}</span>
            ))}
          </div>
        ) : null}

        {triggerResult ? (
          <div className={triggerResult.ok ? "notice success" : "notice warning"}>
            <span>{triggerResult.message}</span>
            {triggerResult.command ? <code>{triggerResult.command}</code> : null}
          </div>
        ) : null}

        {activeView === "Overview" ? (
          <>
            <section className="metric-grid">
              <Metric label="Active clients" value={String(initialData.summary.activeClients)} detail="Configured accounts" />
              <Metric label="30 day runs" value={String(initialData.summary.runs30d)} detail="Cloud Run executions" />
              <Metric label="Success rate" value={percent(initialData.summary.successRate)} detail="Recent execution health" />
              <Metric label="Attributed pipeline" value={money(initialData.summary.attributedPipeline)} detail="Recent closed-revenue output" />
              <Metric label="Open alerts" value={String(initialData.summary.openAlerts)} detail={`${initialData.summary.criticalAlerts} critical`} />
            </section>
            <section className="split">
              <Panel title="Recent Executions">
                <RunList runs={initialData.runs.slice(0, 5)} />
              </Panel>
              <Panel title="Platform Coverage">
                <PlatformMatrix data={initialData} />
              </Panel>
            </section>
          </>
        ) : null}

        {activeView === "Clients" ? (
          <Panel title="Client Portfolio">
            <ClientTable data={initialData} />
          </Panel>
        ) : null}

        {activeView === "Runs" ? (
          <Panel title="Execution History">
            <RunList runs={initialData.runs} />
          </Panel>
        ) : null}

        {activeView === "Alerts" ? (
          <Panel title="Operator Alerts">
            <AlertList data={initialData} />
          </Panel>
        ) : null}

        {activeView === "Recommendations" ? (
          <Panel title="Recommendations">
            <RecommendationList data={initialData} />
          </Panel>
        ) : null}
      </section>
    </main>
  );
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <p>{detail}</p>
    </article>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <h2>{title}</h2>
      </div>
      {children}
    </section>
  );
}

function RunList({ runs }: { runs: CommandCenterData["runs"] }) {
  if (!runs.length) {
    return <div className="empty">No attribution executions have been recorded yet.</div>;
  }

  return (
    <div className="run-list">
      {runs.map((run) => (
        <article className="run-row" key={run.runId}>
          <div>
            <strong>{run.clientId || "Portfolio run"}</strong>
            <span>
              {modelLabels[run.attributionModel] || run.attributionModel} / {run.dryRun ? "Dry run" : "Live"}
            </span>
          </div>
          <div>
            <strong>{money(run.totalPipeline)}</strong>
            <span>{run.topChannel}</span>
          </div>
          <div>
            <span className={`status ${run.status}`}>{run.status}</span>
            <span>{shortDate(run.startedAt)}</span>
          </div>
        </article>
      ))}
    </div>
  );
}

function ClientTable({ data }: { data: CommandCenterData }) {
  return (
    <div className="client-grid">
      {data.clients.map((client) => (
        <article className="client-card" key={client.clientId}>
          <div className="client-head">
            <div>
              <strong>{client.name}</strong>
              <span>{client.clientId}</span>
            </div>
            <span className="status success">{client.active ? "active" : "inactive"}</span>
          </div>
          <div className="mini-grid">
            <span>Agency</span>
            <strong>{client.agencyId}</strong>
            <span>Model</span>
            <strong>{modelLabels[client.attributionModel] || client.attributionModel}</strong>
            <span>Report</span>
            <strong>{client.reportEmail || "Not set"}</strong>
          </div>
          <PlatformPills platforms={client.platforms} />
        </article>
      ))}
    </div>
  );
}

function PlatformMatrix({ data }: { data: CommandCenterData }) {
  return (
    <div className="platform-list">
      {data.clients.map((client) => (
        <div className="platform-row" key={client.clientId}>
          <span>{client.name}</span>
          <PlatformPills platforms={client.platforms} />
        </div>
      ))}
    </div>
  );
}

function PlatformPills({ platforms }: { platforms: CommandCenterData["clients"][number]["platforms"] }) {
  return (
    <div className="pill-row">
      {Object.entries(platforms).map(([name, enabled]) => (
        <span className={enabled ? "platform-pill enabled" : "platform-pill"} key={name}>
          {name}
        </span>
      ))}
    </div>
  );
}

function AlertList({ data }: { data: CommandCenterData }) {
  if (!data.alerts.length) {
    return <div className="empty">No open operator alerts.</div>;
  }

  return (
    <div className="alert-list">
      {data.alerts.map((alert) => (
        <article className={`alert-card ${alert.severity}`} key={`${alert.eventTime}-${alert.title}`}>
          <span>{alert.severity}</span>
          <strong>{alert.title}</strong>
          <p>{alert.message}</p>
          <small>{alert.actionRequired || "Review in ARIE."}</small>
        </article>
      ))}
    </div>
  );
}

function RecommendationList({ data }: { data: CommandCenterData }) {
  return (
    <div className="recommendation-list">
      {data.recommendations.map((recommendation) => (
        <article className={`alert-card ${recommendation.severity}`} key={recommendation.title}>
          <span>{recommendation.severity}</span>
          <strong>{recommendation.title}</strong>
          <p>{recommendation.body}</p>
        </article>
      ))}
    </div>
  );
}
