// ARIE Command Center server.
//
// One Express app serves the built SPA and a mode-aware JSON API:
//   /api/...?mode=demo  -> in-process demo dataset + simulated runs (no secrets)
//   /api/...?mode=live  -> proxied to the private attribution-api on Cloud Run
//
// The client never talks to Cloud Run or holds credentials — all live calls
// go through this server, which adds the Google ID token + X-API-Key.

import express from "express";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { arieFetch, liveConfigured, liveConfigHint } from "./live.js";
import {
  DEMO_CLIENTS,
  DEMO_REPORTS,
  startDemoRun,
  getDemoRun,
  listDemoRuns,
} from "./demo/engine.js";
import { buildDemoData, demoAgency, MONTHS, CHANNELS } from "./demo/fixtures.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
app.use(express.json());

const { seriesByClient } = buildDemoData();

const isDemo = (req) => (req.query.mode || "demo") !== "live";

const wrap = (fn) => async (req, res) => {
  try {
    await fn(req, res);
  } catch (err) {
    const status = err.status || 500;
    res.status(status).json({ error: err.message || "Internal error" });
  }
};

// ── Status / config ──────────────────────────────────────────────────────────

app.get("/api/status", (req, res) => {
  res.json({
    live_configured: liveConfigured(),
    live_missing: liveConfigHint(),
    default_mode: liveConfigured() ? "live" : "demo",
    agency: demoAgency(),
    months: MONTHS,
    channels: CHANNELS,
  });
});

// ── Clients ──────────────────────────────────────────────────────────────────

app.get(
  "/api/clients",
  wrap(async (req, res) => {
    if (isDemo(req)) return res.json(DEMO_CLIENTS);
    res.json(await arieFetch("/api/v1/clients"));
  })
);

// ── Pipeline runs ────────────────────────────────────────────────────────────

app.post(
  "/api/pipeline/run",
  wrap(async (req, res) => {
    const { agency_id, client_ids, dry_run = true, attribution_model } = req.body || {};
    if (isDemo(req)) {
      const result = startDemoRun({
        clientIds: client_ids,
        dryRun: dry_run,
        attributionModel: attribution_model,
      });
      return res.json({ status: "submitted", ...result });
    }
    const payload = {
      agency_id: agency_id || "n8iv_promotions",
      client_ids: client_ids && client_ids.length ? client_ids : null,
      dry_run,
      run_mode: "agency",
    };
    if (attribution_model) payload.attribution_model = attribution_model;
    res.json(await arieFetch("/api/v1/pipeline/run", { method: "POST", body: payload }));
  })
);

app.get(
  "/api/pipeline/runs",
  wrap(async (req, res) => {
    const limit = Math.min(parseInt(req.query.limit || "30", 10), 100);
    if (isDemo(req)) return res.json(listDemoRuns(limit));
    res.json(await arieFetch(`/api/v1/pipeline/runs?limit=${limit}`));
  })
);

app.get(
  "/api/pipeline/runs/:runId",
  wrap(async (req, res) => {
    if (isDemo(req)) {
      const run = getDemoRun(req.params.runId);
      if (!run) return res.status(404).json({ error: "Run not found" });
      const { simStart, stages, stageStates, ...rest } = run;
      return res.json({ ...rest, stage_states: stageStates || null });
    }
    res.json(await arieFetch(`/api/v1/pipeline/runs/${encodeURIComponent(req.params.runId)}`));
  })
);

// ── Reports ──────────────────────────────────────────────────────────────────

app.get(
  "/api/reports/:clientId",
  wrap(async (req, res) => {
    const limit = Math.min(parseInt(req.query.limit || "12", 10), 50);
    if (isDemo(req)) {
      return res.json((DEMO_REPORTS[req.params.clientId] || []).slice(0, limit));
    }
    res.json(
      await arieFetch(`/api/v1/reports/${encodeURIComponent(req.params.clientId)}?limit=${limit}`)
    );
  })
);

// ── Approvals ────────────────────────────────────────────────────────────────

const demoApprovals = [
  {
    action_id: "apr_001",
    created_at: "2026-07-16T14:12:00Z",
    actor: "governance-reviewer",
    description:
      "Luxe Aesthetics MedSpa June report: verify refund-rate framing before client delivery",
    action_type: "report_release",
    status: "pending",
    channel: "email",
  },
];

app.get(
  "/api/approvals",
  wrap(async (req, res) => {
    if (isDemo(req)) return res.json(demoApprovals.filter((a) => a.status === "pending"));
    res.json(await arieFetch("/api/v1/approvals"));
  })
);

app.post(
  "/api/approvals/:actionId/resolve",
  wrap(async (req, res) => {
    const { resolution, resolution_note = "" } = req.body || {};
    if (isDemo(req)) {
      const item = demoApprovals.find((a) => a.action_id === req.params.actionId);
      if (!item) return res.status(404).json({ error: "Approval not found" });
      item.status = resolution;
      return res.json({ action_id: item.action_id, status: resolution });
    }
    res.json(
      await arieFetch(`/api/v1/approvals/${encodeURIComponent(req.params.actionId)}/resolve`, {
        method: "POST",
        body: { resolution, resolution_note },
      })
    );
  })
);

// ── Health / overview ────────────────────────────────────────────────────────

app.get(
  "/api/health",
  wrap(async (req, res) => {
    if (isDemo(req)) {
      return res.json({
        status: "ok",
        version: "demo",
        git_sha: "demo",
        deploy_target: "demo",
      });
    }
    res.json(await arieFetch("/health"));
  })
);

// Aggregates for the overview dashboard. Demo mode computes from fixtures;
// live mode aggregates the latest report per client.
app.get(
  "/api/overview",
  wrap(async (req, res) => {
    if (isDemo(req)) {
      const latest = DEMO_CLIENTS.map((c) => DEMO_REPORTS[c.client_id][0]);
      const monthly = MONTHS.map((month) => {
        const rows = Object.values(seriesByClient)
          .flat()
          .filter((r) => r.month === month);
        const byChannel = {};
        for (const r of rows) {
          byChannel[r.channel] = byChannel[r.channel] || { spend: 0, revenue: 0 };
          byChannel[r.channel].spend += r.spend;
          byChannel[r.channel].revenue += r.revenue;
        }
        return {
          month,
          spend: rows.reduce((s, r) => s + r.spend, 0),
          revenue: rows.reduce((s, r) => s + r.revenue, 0),
          by_channel: byChannel,
        };
      });
      return res.json({ latest_reports: latest, monthly });
    }

    const clients = await arieFetch("/api/v1/clients");
    const latest = [];
    for (const c of clients) {
      try {
        const reports = await arieFetch(
          `/api/v1/reports/${encodeURIComponent(c.client_id)}?limit=1`
        );
        if (reports.length) latest.push(reports[0]);
      } catch {
        // client without reports yet — fine during pilot ramp-up
      }
    }
    res.json({ latest_reports: latest, monthly: null });
  })
);

// Per-client channel series for charts (demo only; live channel series comes
// with a later Databricks aggregation endpoint).
app.get("/api/series/:clientId", (req, res) => {
  const series = seriesByClient[req.params.clientId];
  if (!series) return res.status(404).json({ error: "No series for client" });
  res.json(series);
});

// ── SPA ──────────────────────────────────────────────────────────────────────

const publicDir = path.join(__dirname, "public");
app.use(express.static(publicDir));
app.get("*", (req, res, next) => {
  if (req.path.startsWith("/api/")) return next();
  res.sendFile(path.join(publicDir, "index.html"), (err) => {
    if (err) res.status(503).send("UI not built yet — run: npm run build");
  });
});

const port = process.env.PORT || 3000;
app.listen(port, "0.0.0.0", () => {
  console.log(`ARIE Command Center listening on :${port}`);
  console.log(`Live mode configured: ${liveConfigured()}`);
});
