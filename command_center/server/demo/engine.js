// Simulated pipeline runs for demo mode.
//
// A demo run advances through the same stages as the production flow
// (per-source ingest -> normalize -> attribution -> two-stage insights ->
// governance review -> email). Progress is computed from elapsed wall-clock
// time on every read, so there are no timers to leak and polling any time
// gives a consistent picture. A full run takes ~8s per client — brisk enough
// for a live pitch, slow enough to watch happen.

import { buildDemoData, channelLabel } from "./fixtures.js";

const { clients: DEMO_CLIENTS, reports: DEMO_REPORTS } = buildDemoData();

const STAGE_SECONDS = 1.15; // pacing per stage

const runs = new Map(); // run_id -> run record
let runCounter = 0;

// Seed history so the Runs view isn't empty before the first demo trigger:
// last month's scheduled run plus a recent verification dry-run.
function seedHistory() {
  const seedRun = (idOffset, startedIso, dryRun) => {
    for (const c of DEMO_CLIENTS) {
      const report = DEMO_REPORTS[c.client_id][0];
      const runId = `sched${idOffset}_${c.client_id.slice(0, 8)}`;
      runs.set(runId, {
        run_id: runId,
        agency_id: c.agency_id,
        client_id: c.client_id,
        run_mode: "agency",
        attribution_model: c.attribution_model,
        status: "success",
        dry_run: dryRun,
        stages: null, // historical: no live stage detail
        meta_rows: c.meta_enabled ? 1240 + idOffset * 37 : 0,
        google_rows: c.google_ads_enabled ? 980 + idOffset * 21 : 0,
        linkedin_rows: c.linkedin_ads_enabled ? 310 + idOffset * 9 : 0,
        hubspot_rows: c.hubspot_enabled ? 462 + idOffset * 11 : 0,
        stripe_rows: c.stripe_enabled ? 388 + idOffset * 13 : 0,
        normalized_ad_rows: 1800 + idOffset * 41,
        total_pipeline: report.total_pipeline,
        top_channel: report.top_channel,
        email_sent: !dryRun,
        warnings: "",
        error: "",
        started_at: startedIso,
        finished_at: new Date(new Date(startedIso).getTime() + 6 * 60000).toISOString(),
        output_schema: c.databricks_schema,
      });
    }
  };
  seedRun(1, "2026-07-01T13:00:00Z", false); // monthly scheduled run
  seedRun(2, "2026-07-14T16:22:00Z", true); // pre-pilot verification dry-run
}
seedHistory();

function stagesFor(client, dryRun) {
  const stages = [];
  if (client.meta_enabled) stages.push({ id: "pull_meta", label: "Ingest Meta Ads" });
  if (client.google_ads_enabled) stages.push({ id: "pull_google", label: "Ingest Google Ads" });
  if (client.linkedin_ads_enabled) stages.push({ id: "pull_linkedin", label: "Ingest LinkedIn Ads" });
  if (client.tiktok_ads_enabled) stages.push({ id: "pull_tiktok", label: "Ingest TikTok Ads" });
  if (client.hubspot_enabled) stages.push({ id: "pull_hubspot", label: "Ingest HubSpot CRM" });
  if (client.stripe_enabled) stages.push({ id: "pull_stripe", label: "Ingest Stripe revenue" });
  stages.push(
    { id: "normalize", label: "Normalize to Delta tables" },
    { id: "attribution", label: `Multi-touch attribution (${client.attribution_model})` },
    { id: "revenue_analyst", label: "Revenue analyst agent" },
    { id: "executive_report", label: "Executive report agent" },
    { id: "governance", label: "Governance review" },
    dryRun
      ? { id: "email", label: "Email delivery (skipped — dry run)" }
      : { id: "email", label: "Send white-labeled report" }
  );
  return stages;
}

const ROW_COUNTS = {
  pull_meta: ["meta_rows", 1200, 2400],
  pull_google: ["google_rows", 700, 1900],
  pull_linkedin: ["linkedin_rows", 200, 600],
  pull_tiktok: ["tiktok_rows", 300, 1100],
  pull_hubspot: ["hubspot_rows", 250, 800],
  pull_stripe: ["stripe_rows", 150, 700],
};

function rowsFor(stageId, clientId) {
  const spec = ROW_COUNTS[stageId];
  if (!spec) return null;
  // stable pseudo-random per client+stage
  let h = 0;
  for (const ch of clientId + stageId) h = (h * 31 + ch.charCodeAt(0)) % 9973;
  const [field, lo, hi] = spec;
  return { field, rows: lo + (h % (hi - lo)) };
}

export function startDemoRun({ clientIds, dryRun = true, attributionModel }) {
  runCounter += 1;
  const batchId = `live${String(runCounter).padStart(3, "0")}`;
  const targets = DEMO_CLIENTS.filter(
    (c) => !clientIds || clientIds.length === 0 || clientIds.includes(c.client_id)
  );
  const created = [];
  const now = Date.now();
  targets.forEach((client, idx) => {
    const runId = `${batchId}_${client.client_id.slice(0, 8)}`;
    const stages = stagesFor(client, dryRun);
    runs.set(runId, {
      run_id: runId,
      batch_id: batchId,
      agency_id: client.agency_id,
      client_id: client.client_id,
      run_mode: "agency",
      attribution_model: attributionModel || client.attribution_model,
      status: "running",
      dry_run: dryRun,
      simStart: now + idx * 1500, // clients kick off staggered
      stages,
      meta_rows: 0,
      google_rows: 0,
      linkedin_rows: 0,
      hubspot_rows: 0,
      stripe_rows: 0,
      normalized_ad_rows: 0,
      total_pipeline: 0,
      top_channel: "",
      email_sent: false,
      warnings: "",
      error: "",
      started_at: new Date(now).toISOString(),
      finished_at: null,
      output_schema: client.databricks_schema,
    });
    created.push(runId);
  });
  return { batch_id: batchId, run_ids: created };
}

// Materialize time-based progress into the run record.
function tick(run) {
  if (!run.stages || run.status !== "running") return run;
  const elapsed = (Date.now() - run.simStart) / 1000;
  const doneCount = Math.max(0, Math.floor(elapsed / STAGE_SECONDS));
  const report = DEMO_REPORTS[run.client_id]?.[0];

  run.stageStates = run.stages.map((s, i) => ({
    ...s,
    state: i < doneCount ? "done" : i === doneCount ? "running" : "pending",
  }));

  run.stages.forEach((s, i) => {
    if (i >= doneCount) return;
    const rc = rowsFor(s.id, run.client_id);
    if (rc && rc.field in run) run[rc.field] = rc.rows;
    if (s.id === "normalize")
      run.normalized_ad_rows =
        run.meta_rows + run.google_rows + run.linkedin_rows + (run.tiktok_rows || 0);
    if (s.id === "attribution" && report) {
      run.total_pipeline = report.total_pipeline;
      run.top_channel = report.top_channel;
    }
    if (s.id === "email") run.email_sent = !run.dry_run;
  });

  if (doneCount >= run.stages.length) {
    run.status = "success";
    run.finished_at = new Date(run.simStart + run.stages.length * STAGE_SECONDS * 1000).toISOString();
    run.stageStates = run.stages.map((s) => ({ ...s, state: "done" }));
  }
  return run;
}

export function getDemoRun(runId) {
  const run = runs.get(runId);
  return run ? tick(run) : null;
}

export function listDemoRuns(limit = 30) {
  const all = [...runs.values()].map(tick);
  all.sort((a, b) => new Date(b.started_at) - new Date(a.started_at));
  return all.slice(0, limit).map(({ simStart, stages, stageStates, ...rest }) => rest);
}

export { DEMO_CLIENTS, DEMO_REPORTS, channelLabel };
