"use client";

import { useState } from "react";
import type {
  ClientAccount,
  ClientPlatformKey,
  CommandCenterData,
  PilotClientConfiguration,
  PilotClientConfigurationResult,
  PipelineTriggerResult
} from "@/lib/types";

type Props = {
  data: CommandCenterData;
  clients: ClientAccount[];
  agencyId: string;
  onRefresh: (quiet?: boolean) => Promise<void>;
  onOpenPipeline: () => void;
};

type PlatformDraft = {
  enabled: boolean;
  accountId: string;
  credential: string;
  expiresAt: string;
};

type PilotDraft = {
  clientId: string;
  clientName: string;
  reportEmail: string;
  attributionModel: string;
  lookbackDays: number;
  reportMonth: string;
  stripeHistoryStartDate: string;
  hubspotClosedWonStageIds: string;
  platforms: Record<ClientPlatformKey, PlatformDraft>;
};

const platformKeys: ClientPlatformKey[] = ["hubspot", "meta", "google", "linkedin", "tiktok", "stripe"];
const adPlatformKeys: ClientPlatformKey[] = ["meta", "google", "linkedin", "tiktok"];

const platformDefinitions: Array<{
  key: ClientPlatformKey;
  mark: string;
  name: string;
  role: string;
  accountLabel: string;
  accountPlaceholder: string;
  credentialLabel: string;
  accountOptional?: boolean;
}> = [
  {
    key: "hubspot",
    mark: "HS",
    name: "HubSpot",
    role: "Closed-won revenue anchor",
    accountLabel: "Pipeline ID",
    accountPlaceholder: "Leave blank for default pipeline",
    credentialLabel: "Private app access token",
    accountOptional: true
  },
  {
    key: "meta",
    mark: "M",
    name: "Meta Ads",
    role: "Paid social touchpoints and spend",
    accountLabel: "Ad account ID",
    accountPlaceholder: "act_123456789",
    credentialLabel: "Long-lived access token"
  },
  {
    key: "google",
    mark: "G",
    name: "Google Ads",
    role: "Paid search touchpoints and spend",
    accountLabel: "Customer ID",
    accountPlaceholder: "1234567890",
    credentialLabel: "OAuth refresh token"
  },
  {
    key: "linkedin",
    mark: "in",
    name: "LinkedIn Ads",
    role: "B2B paid social touchpoints and spend",
    accountLabel: "Sponsored account ID",
    accountPlaceholder: "123456789 or account URN",
    credentialLabel: "Advertising API access token"
  },
  {
    key: "tiktok",
    mark: "TT",
    name: "TikTok Ads",
    role: "Optional paid social source",
    accountLabel: "Advertiser ID",
    accountPlaceholder: "Advertiser account ID",
    credentialLabel: "Marketing API access token"
  },
  {
    key: "stripe",
    mark: "S",
    name: "Stripe",
    role: "Optional collected-revenue enrichment",
    accountLabel: "Account ID",
    accountPlaceholder: "acct_... (optional)",
    credentialLabel: "Restricted secret key",
    accountOptional: false
  }
];
const platformDefinitionByKey = new Map(platformDefinitions.map((definition) => [definition.key, definition]));
const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0
});

const modelLabels: Record<string, string> = {
  last_touch: "CRM Source Match",
  first_touch: "First Touch",
  linear: "Linear",
  time_decay: "Time Decay",
  u_shape: "U-Shape",
  w_shape: "W-Shape"
};

function previousCompletedMonth() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit"
  }).formatToParts(new Date());
  const year = Number(parts.find((part) => part.type === "year")?.value);
  const month = Number(parts.find((part) => part.type === "month")?.value);
  return new Date(Date.UTC(year, month - 2, 1)).toISOString().slice(0, 7);
}

function validHistoryStartDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) &&
    parsed.toISOString().slice(0, 10) === value &&
    value <= new Date().toISOString().slice(0, 10);
}

function normalizedHubspotClosedWonStageIds(value: string): string[] {
  const rawValues = value.split(/[\n,]+/);
  if (!rawValues.length || rawValues.length > 20) return [];
  const normalized = rawValues.map((rawValue) => {
    if (rawValue.length > 100) return "";
    return rawValue.trim().toLowerCase().replace(/[^a-z0-9]+/g, "");
  });
  if (normalized.some((stageId) => !stageId)) return [];
  return [...new Set(normalized)];
}

function emptyPlatforms(): Record<ClientPlatformKey, PlatformDraft> {
  return {
    meta: { enabled: false, accountId: "", credential: "", expiresAt: "" },
    google: { enabled: false, accountId: "", credential: "", expiresAt: "" },
    linkedin: { enabled: false, accountId: "", credential: "", expiresAt: "" },
    tiktok: { enabled: false, accountId: "", credential: "", expiresAt: "" },
    hubspot: { enabled: true, accountId: "", credential: "", expiresAt: "" },
    stripe: { enabled: false, accountId: "", credential: "", expiresAt: "" }
  };
}

function emptyCredentials(): Record<ClientPlatformKey, boolean> {
  return { meta: false, google: false, linkedin: false, tiktok: false, hubspot: false, stripe: false };
}

function emptyDraft(): PilotDraft {
  return {
    clientId: "",
    clientName: "",
    reportEmail: "",
    attributionModel: "last_touch",
    lookbackDays: 90,
    reportMonth: previousCompletedMonth(),
    stripeHistoryStartDate: "2010-01-01",
    hubspotClosedWonStageIds: "closedwon, won",
    platforms: emptyPlatforms()
  };
}

function slugify(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 63);
}

function clientDraft(client: ClientAccount): PilotDraft {
  const platforms = emptyPlatforms();
  for (const key of platformKeys) {
    platforms[key] = {
      enabled: client.platforms[key],
      accountId: client.platformDetails?.[key]?.accountId || "",
      credential: "",
      expiresAt: client.platformDetails?.[key]?.expiresAt || ""
    };
  }
  return {
    clientId: client.clientId,
    clientName: client.name,
    reportEmail: client.reportEmail,
    attributionModel: client.attributionModel === "last_touch" ? "last_touch" : "",
    lookbackDays: client.lookbackDays,
    reportMonth: previousCompletedMonth(),
    stripeHistoryStartDate: client.stripeHistoryStartDate || "2010-01-01",
    hubspotClosedWonStageIds: client.hubspotClosedWonStageIds.join(", "),
    platforms
  };
}

function configuredCredentials(client?: ClientAccount) {
  const configured = emptyCredentials();
  if (!client) return configured;
  for (const key of platformKeys) configured[key] = Boolean(client.platformDetails?.[key]?.credentialConfigured);
  return configured;
}

function configuredFromResult(client: PilotClientConfiguration) {
  const configured = emptyCredentials();
  for (const key of platformKeys) configured[key] = client.platforms[key].credentialConfigured;
  return configured;
}

function platformComplete(
  key: ClientPlatformKey,
  draft: PilotDraft,
  storedCredentials: Record<ClientPlatformKey, boolean>
) {
  const definition = platformDefinitionByKey.get(key);
  const source = draft.platforms[key];
  if (!source.enabled) return true;
  const hasAccount = definition?.accountOptional || Boolean(source.accountId.trim());
  const historyReady = key !== "stripe" || validHistoryStartDate(draft.stripeHistoryStartDate);
  const wonStagesReady = key !== "hubspot" ||
    normalizedHubspotClosedWonStageIds(draft.hubspotClosedWonStageIds).length > 0;
  return Boolean(
    hasAccount && historyReady && wonStagesReady &&
    (source.credential.trim() || storedCredentials[key])
  );
}

export function PilotRoom({ data, clients, agencyId, onRefresh, onOpenPipeline }: Props) {
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<PilotDraft>(emptyDraft);
  const [storedCredentials, setStoredCredentials] = useState<Record<ClientPlatformKey, boolean>>(emptyCredentials);
  const [saving, setSaving] = useState(false);
  const [saveResult, setSaveResult] = useState<PilotClientConfigurationResult | null>(null);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<PipelineTriggerResult | null>(null);

  const selectedClient = clients.find((client) => client.clientId === draft.clientId);
  const businessReady = draft.clientName.trim().length >= 2 &&
    emailPattern.test(draft.reportEmail) &&
    draft.attributionModel === "last_touch";
  const reportMonthReady = /^\d{4}-(0[1-9]|1[0-2])$/.test(draft.reportMonth) && draft.reportMonth <= previousCompletedMonth();
  const enabledPlatforms = platformKeys.filter((key) => draft.platforms[key].enabled);
  const incompletePlatforms = enabledPlatforms.filter((key) => !platformComplete(key, draft, storedCredentials));
  const revenueReady = draft.platforms.hubspot.enabled && platformComplete("hubspot", draft, storedCredentials);
  const adsReady = adPlatformKeys.some(
    (key) => draft.platforms[key].enabled && platformComplete(key, draft, storedCredentials)
  );
  const pilotReady = businessReady && revenueReady && adsReady && incompletePlatforms.length === 0;
  const savedClientId = saveResult?.client?.clientId || draft.clientId;
  const latestRun = data.runs.find((run) => run.clientId === savedClientId);
  const clientIdPreview = draft.clientId || slugify(draft.clientName) || "new_client";

  const readiness = [
    { label: "Business profile", ready: businessReady, detail: businessReady ? "Complete" : "Name, report email, and CRM Source Match required" },
    { label: "Revenue source", ready: revenueReady, detail: revenueReady ? "HubSpot ready" : "HubSpot credential required" },
    { label: "Paid media", ready: adsReady, detail: adsReady ? "At least one source ready" : "Connect one ad platform" },
    ...(draft.platforms.stripe.enabled ? [{
      label: "Cash linkage",
      ready: platformComplete("stripe", draft, storedCredentials),
      detail: "PaymentIntents require exact hubspot_deal_id or deal_id metadata"
    }] : []),
    { label: "Secure storage", ready: Boolean(saveResult?.ok), detail: saveResult?.ok ? "Secret references recorded" : "Pending save" }
  ];

  function selectClient(clientId: string) {
    const client = clients.find((item) => item.clientId === clientId);
    setDraft(client ? clientDraft(client) : emptyDraft());
    setStoredCredentials(configuredCredentials(client));
    setSaveResult(null);
    setRunResult(null);
    setStep(0);
  }

  function updatePlatform(key: ClientPlatformKey, change: Partial<PlatformDraft>) {
    setDraft((current) => ({
      ...current,
      platforms: {
        ...current.platforms,
        [key]: { ...current.platforms[key], ...change }
      }
    }));
    setSaveResult(null);
  }

  function updateProfile(change: Partial<Omit<PilotDraft, "platforms">>) {
    setDraft((current) => ({ ...current, ...change }));
    setSaveResult(null);
  }

  async function savePilot() {
    setSaving(true);
    setSaveResult(null);
    setRunResult(null);
    try {
      const response = await fetch("/api/pilot/clients", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          clientId: draft.clientId || undefined,
          clientName: draft.clientName,
          agencyId,
          reportEmail: draft.reportEmail,
          attributionModel: draft.attributionModel,
          lookbackDays: draft.lookbackDays,
          stripeHistoryStartDate: draft.stripeHistoryStartDate,
          hubspotClosedWonStageIds: draft.hubspotClosedWonStageIds,
          platforms: draft.platforms
        })
      });
      const payload = (await response.json()) as PilotClientConfigurationResult;
      if (!response.ok || !payload.client) throw new Error(payload.error || payload.message);
      setSaveResult(payload);
      setStoredCredentials(configuredFromResult(payload.client));
      setDraft((current) => ({
        ...current,
        clientId: payload.client?.clientId || current.clientId,
        hubspotClosedWonStageIds: payload.client?.hubspotClosedWonStageIds.join(", ") ||
          current.hubspotClosedWonStageIds,
        platforms: Object.fromEntries(
          platformKeys.map((key) => [key, { ...current.platforms[key], credential: "" }])
        ) as Record<ClientPlatformKey, PlatformDraft>
      }));
      setStep(3);
      await onRefresh(true);
    } catch (error) {
      setSaveResult({
        ok: false,
        message: "Pilot configuration was not saved.",
        error: error instanceof Error ? error.message : "Secure configuration failed."
      });
    } finally {
      setSaving(false);
    }
  }

  async function launchPreview() {
    if (!savedClientId || !reportMonthReady || draft.attributionModel !== "last_touch") {
      setRunResult({ ok: false, message: "Select CRM Source Match and a completed report month." });
      return;
    }
    setRunning(true);
    setRunResult(null);
    try {
      const response = await fetch("/api/execute", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          agencyId,
          clientIds: [savedClientId],
          attributionModel: draft.attributionModel,
          reportMonth: draft.reportMonth,
          dryRun: true
        })
      });
      const payload = (await response.json()) as PipelineTriggerResult;
      setRunResult(payload);
      if (response.ok) window.setTimeout(() => void onRefresh(true), 1500);
    } catch (error) {
      setRunResult({ ok: false, message: error instanceof Error ? error.message : "Preview launch failed." });
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="pilot-room reveal">
      <section className="pilot-masthead">
        <div>
          <span className="eyebrow">Pilot room / live client setup</span>
          <h2>From platform keys to revenue proof.</h2>
          <p>Configure a protected client workspace, launch a no-email preview, and inspect the same pipeline telemetry used in production.</p>
        </div>
        <label className="field pilot-client-select">
          <span>Pilot workspace</span>
          <select value={draft.clientId} onChange={(event) => selectClient(event.target.value)}>
            <option value="">New pilot client</option>
            {clients.map((client) => <option value={client.clientId} key={client.clientId}>{client.name}</option>)}
          </select>
        </label>
      </section>

      {!data.capabilities.clientConfiguration && (
        <div className="inline-warning">Client configuration is locked until ARIE_API_BASE and ARIE_API_KEY are configured for the Vercel portal.</div>
      )}

      <div className="pilot-layout">
        <aside className="pilot-rail" aria-label="Pilot setup progress">
          {["Business", "Sources", "Review", "Validate"].map((label, index) => (
            <button
              className={step === index ? "pilot-step active" : step > index ? "pilot-step complete" : "pilot-step"}
              disabled={index > step || (index === 3 && !saveResult?.ok)}
              key={label}
              onClick={() => setStep(index)}
              type="button"
            >
              <span>{String(index + 1).padStart(2, "0")}</span>
              <strong>{label}</strong>
            </button>
          ))}
          <div className="pilot-readiness">
            <span className="eyebrow">Readiness</span>
            {readiness.map((item) => (
              <div key={item.label} className={item.ready ? "ready" : "pending"}>
                <i />
                <span><strong>{item.label}</strong><small>{item.detail}</small></span>
              </div>
            ))}
          </div>
        </aside>

        <section className="pilot-stage">
          {step === 0 && (
            <div className="pilot-section">
              <header><span className="eyebrow">01 / Business profile</span><h2>{selectedClient ? "Update pilot context" : "Create the client workspace"}</h2></header>
              <div className="pilot-form-grid">
                <label className="field"><span>Business name</span><input value={draft.clientName} onChange={(event) => updateProfile({ clientName: event.target.value })} placeholder="Acme B2B" autoFocus /></label>
                <label className="field"><span>Client ID</span><input value={clientIdPreview} disabled /><small>Generated once and used for Databricks isolation.</small></label>
                <label className="field"><span>Report recipient</span><input type="email" value={draft.reportEmail} onChange={(event) => updateProfile({ reportEmail: event.target.value })} placeholder="revenue@client.com" /></label>
                <label className="field"><span>Attribution model</span><select value={draft.attributionModel} onChange={(event) => updateProfile({ attributionModel: event.target.value })}><option value="" disabled>Select production model</option><option value="last_touch">CRM Source Match</option></select><small>Production credits the CRM-recorded paid source without inferred multi-touch journeys.</small></label>
                <label className="field"><span>Sales-cycle lookback</span><select value={draft.lookbackDays} onChange={(event) => updateProfile({ lookbackDays: Number(event.target.value) })}><option value={30}>30 days / short cycle</option><option value={60}>60 days / considered purchase</option><option value={90}>90 days / B2B pilot</option><option value={180}>180 days / enterprise</option><option value={365}>365 days / long enterprise</option></select></label>
                <label className="field"><span>Completed report month</span><input type="month" max={previousCompletedMonth()} value={draft.reportMonth} onChange={(event) => updateProfile({ reportMonth: event.target.value })} /><small>Preview runs are locked to a fully closed calendar month.</small></label>
                <div className="pilot-agency"><span>Agency owner</span><strong>{agencyId || "No agency selected"}</strong><small>Stored in the Databricks tenant registry</small></div>
              </div>
              <footer><span /><button className="button primary" disabled={!businessReady || !agencyId} onClick={() => setStep(1)} type="button">Continue to sources</button></footer>
            </div>
          )}

          {step === 1 && (
            <div className="pilot-section">
              <header><span className="eyebrow">02 / Source credentials</span><h2>Connect the revenue journey</h2><p>Existing keys are never displayed. Enter a new value only when adding or rotating a credential. Every enabled ad, CRM, and payment account must report in USD; FX conversion is not supported.</p></header>
              <div className="platform-config-list">
                {platformDefinitions.map((definition) => {
                  const source = draft.platforms[definition.key];
                  const stored = storedCredentials[definition.key];
                  const complete = platformComplete(definition.key, draft, storedCredentials);
                  return (
                    <article className={source.enabled ? "platform-config enabled" : "platform-config"} key={definition.key}>
                      <header>
                        <span className={`platform-mark ${definition.key}`}>{definition.mark}</span>
                        <div><strong>{definition.name}</strong><small>{definition.role}</small></div>
                        <span className={source.enabled && complete ? "source-state ready" : source.enabled ? "source-state attention" : "source-state"}>{source.enabled && complete ? (stored ? "Stored" : "Ready") : source.enabled ? "Needs setup" : "Off"}</span>
                        <label className="switch"><input type="checkbox" checked={source.enabled} onChange={(event) => updatePlatform(definition.key, { enabled: event.target.checked })} /><span /></label>
                      </header>
                      {source.enabled && (
                        <div className="platform-fields">
                          <label className="field"><span>{definition.accountLabel}</span><input value={source.accountId} onChange={(event) => updatePlatform(definition.key, { accountId: event.target.value })} placeholder={definition.accountPlaceholder} /></label>
                          <label className="field"><span>{definition.credentialLabel}</span><input type="password" value={source.credential} onChange={(event) => updatePlatform(definition.key, { credential: event.target.value })} placeholder={stored ? "Stored securely — enter to rotate" : "Paste credential"} autoComplete="new-password" /><small>{stored ? "A Secret Manager version already exists." : "Sent directly to the ARIE control API."}</small></label>
                          <label className="field"><span>Expires on</span><input type="date" value={source.expiresAt} onChange={(event) => updatePlatform(definition.key, { expiresAt: event.target.value })} /><small>Optional; enables proactive token alerts.</small></label>
                          {definition.key === "hubspot" && <label className="field"><span>Closed-won stage IDs</span><input value={draft.hubspotClosedWonStageIds} onChange={(event) => updateProfile({ hubspotClosedWonStageIds: event.target.value })} placeholder="closedwon, won, custom_stage_id" required /><small>Comma-separated internal HubSpot stage IDs. Matching is exact after case and punctuation normalization.</small></label>}
                          {definition.key === "stripe" && <label className="field"><span>Payment history starts</span><input type="date" max={new Date().toISOString().slice(0, 10)} value={draft.stripeHistoryStartDate} onChange={(event) => updateProfile({ stripeHistoryStartDate: event.target.value })} required /><small>Earliest possible payment date. Account ID must exactly match the key; live delivery requires a live-mode key. PaymentIntents need exact hubspot_deal_id (or deal_id) metadata.</small></label>}
                        </div>
                      )}
                    </article>
                  );
                })}
              </div>
              <footer><button className="button ghost" onClick={() => setStep(0)} type="button">Back</button><button className="button primary" disabled={!revenueReady || !adsReady || incompletePlatforms.length > 0} onClick={() => setStep(2)} type="button">Review pilot</button></footer>
            </div>
          )}

          {step === 2 && (
            <div className="pilot-section">
              <header><span className="eyebrow">03 / Configuration manifest</span><h2>Ready for protected storage</h2></header>
              <div className="pilot-manifest">
                <div><span>Business</span><strong>{draft.clientName}</strong><small>{clientIdPreview} / {agencyId}</small></div>
                <div><span>Measurement</span><strong>{modelLabels[draft.attributionModel]}</strong><small>{draft.lookbackDays}-day lookback · USD only</small></div>
                <div><span>Delivery</span><strong>{draft.reportEmail}</strong><small>Suppressed during preview runs</small></div>
              </div>
              <div className="source-manifest">
                {platformDefinitions.filter((item) => draft.platforms[item.key].enabled).map((item) => (
                  <div key={item.key}><span className={`platform-mark ${item.key}`}>{item.mark}</span><span><strong>{item.name}</strong><small>{item.key === "stripe" ? `History from ${draft.stripeHistoryStartDate} · exact deal ID metadata required` : item.key === "hubspot" ? `Won stages: ${normalizedHubspotClosedWonStageIds(draft.hubspotClosedWonStageIds).join(", ")}` : draft.platforms[item.key].accountId || "Default account scope"}</small></span><b>{storedCredentials[item.key] ? "Stored" : "New key"}</b></div>
                ))}
              </div>
              <div className="security-band"><strong>Credential boundary</strong><span>Raw values are excluded from Databricks, API responses, browser refresh data, and audit messages. Only GCP Secret Manager references are retained.</span></div>
              {saveResult && !saveResult.ok && <div className="notice warning"><strong>Save blocked</strong><span>{saveResult.error || saveResult.message}</span></div>}
              <footer><button className="button ghost" onClick={() => setStep(1)} disabled={saving} type="button">Back</button><button className="button primary" onClick={() => void savePilot()} disabled={!pilotReady || saving || !data.capabilities.clientConfiguration} type="button">{saving ? "Securing credentials…" : selectedClient ? "Update pilot workspace" : "Create pilot workspace"}</button></footer>
            </div>
          )}

          {step === 3 && saveResult?.client && (
            <div className="pilot-section pilot-validation">
              <header><span className="eyebrow">04 / Dry-run validation</span><h2>{saveResult.client.clientName} is staged.</h2><p>{saveResult.message}</p></header>
              <div className="validation-strip">
                <div><span>Databricks schema</span><code>{saveResult.client.databricksSchema}</code></div>
                <div><span>Model</span><strong>{modelLabels[saveResult.client.attributionModel]}</strong></div>
                <div><span>Report month</span><strong>{draft.reportMonth}</strong></div>
                <div><span>Lookback</span><strong>{saveResult.client.lookbackDays} days</strong></div>
                <div><span>Delivery</span><strong>Suppressed</strong></div>
              </div>
              <div className="preview-action">
                <div><span className="eyebrow">Protected execution</span><strong>Ingest → validate → attribute → report preview</strong><small>No client email is sent during this run.</small></div>
                <button className="button primary" disabled={running || !reportMonthReady || draft.attributionModel !== "last_touch" || !data.capabilities.pipelineExecution} onClick={() => void launchPreview()} type="button">{running ? "Launching preview…" : `Launch ${draft.reportMonth} preview`}</button>
              </div>
              {!data.capabilities.pipelineExecution && <div className="inline-warning">Cloud Run execution is offline. The client is saved, but the preview cannot launch yet.</div>}
              {runResult && <div className={runResult.ok ? "notice success" : "notice warning"}><strong>{runResult.ok ? "Preview accepted" : "Preview blocked"}</strong><span>{runResult.message}</span>{runResult.operation && <code>{runResult.operation}</code>}</div>}
              {latestRun && (
                <div className="pilot-run-snapshot">
                  <div><span>Status</span><strong>{latestRun.status}</strong></div>
                  <div><span>Ad rows</span><strong>{latestRun.sourceRows.normalized.toLocaleString()}</strong></div>
                  <div><span>HubSpot rows</span><strong>{latestRun.sourceRows.hubspot.toLocaleString()}</strong></div>
                  <div><span>Attributed pipeline</span><strong>{usd.format(latestRun.totalPipeline)}</strong></div>
                </div>
              )}
              <footer><button className="button ghost" onClick={() => setStep(1)} type="button">Rotate credentials</button><button className="button ghost" onClick={onOpenPipeline} type="button">Open execution history</button></footer>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
