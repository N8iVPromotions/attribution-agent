import React, { useEffect, useState } from "react";
import { api, fmtMoney, fmtRoi, fmtMonth } from "../api.js";
import { ChannelChip, StatusBadge } from "../components/ui.jsx";
import { ChannelLines } from "../components/charts.jsx";
import { CHANNEL_ORDER, SOURCE_LABELS } from "../channels.js";

function enabledChannels(c) {
  return CHANNEL_ORDER.filter(
    (ch) =>
      (ch === "meta" && c.meta_enabled) ||
      (ch === "google_ads" && c.google_ads_enabled) ||
      (ch === "linkedin_ads" && c.linkedin_ads_enabled) ||
      (ch === "tiktok_ads" && c.tiktok_ads_enabled)
  );
}

function secretReady(c, ch) {
  return {
    meta: c.meta_secret_configured,
    google_ads: c.google_ads_secret_configured,
    linkedin_ads: c.linkedin_ads_secret_configured,
    tiktok_ads: c.tiktok_secret_configured,
  }[ch];
}

function ClientCard({ client, mode, expanded, onToggle }) {
  const [report, setReport] = useState(null);
  const [series, setSeries] = useState(null);
  const [months, setMonths] = useState([]);

  useEffect(() => {
    if (!expanded) return;
    api(`/reports/${client.client_id}?limit=1`, { mode })
      .then((r) => setReport(r[0] || null))
      .catch(() => setReport(null));
    if (mode === "demo") {
      Promise.all([api(`/series/${client.client_id}`, { mode }), api("/status", { mode })])
        .then(([s, st]) => {
          setSeries(s);
          setMonths(st.months);
        })
        .catch(() => setSeries(null));
    }
  }, [expanded, client.client_id, mode]);

  const chans = enabledChannels(client);
  const missingSecrets = chans.filter((ch) => !secretReady(client, ch));

  return (
    <div className="panel">
      <div
        className="panel-pad"
        style={{ display: "flex", alignItems: "center", gap: 14, cursor: "pointer" }}
        onClick={onToggle}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
            <span style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: 16 }}>
              {client.client_display_name || client.client_name}
            </span>
            {client.vertical && (
              <span style={{ fontSize: 12, color: "var(--text-3)" }}>{client.vertical}</span>
            )}
          </div>
          <div style={{ display: "flex", gap: 14, marginTop: 6, flexWrap: "wrap" }}>
            {chans.map((ch) => (
              <ChannelChip key={ch} id={ch} />
            ))}
            {Object.entries(SOURCE_LABELS).map(
              ([k, label]) =>
                client[`${k}_enabled`] && (
                  <span key={k} className="chip" style={{ color: "var(--text-3)" }}>
                    ⬡ {label}
                  </span>
                )
            )}
          </div>
        </div>
        {missingSecrets.length ? (
          <StatusBadge status="partial" label={`${missingSecrets.length} secret(s) missing`} />
        ) : (
          <StatusBadge status="success" label="Sources ready" />
        )}
        <span style={{ color: "var(--text-3)" }}>{expanded ? "▾" : "▸"}</span>
      </div>

      {expanded && (
        <div style={{ borderTop: "1px solid var(--border)", padding: "18px 22px" }}>
          <div className="grid grid-2">
            <div>
              {series && months.length > 1 ? (
                <>
                  <h4 className="panel-title">Attributed revenue by channel</h4>
                  <ChannelLines series={series} months={months} />
                </>
              ) : (
                <p className="panel-note">
                  Channel trend charts populate from the Databricks warehouse after the first
                  monthly run.
                </p>
              )}
            </div>
            <div>
              <h4 className="panel-title">Latest report</h4>
              {report ? (
                <table className="data">
                  <tbody>
                    <tr><td>Month</td><td className="num">{fmtMonth(report.report_month)}</td></tr>
                    <tr><td>Spend</td><td className="num">{fmtMoney(report.total_spend)}</td></tr>
                    <tr><td>Pipeline</td><td className="num">{fmtMoney(report.total_pipeline)}</td></tr>
                    <tr><td>ROI</td><td className="num">{fmtRoi(report.overall_roi)}</td></tr>
                    <tr><td>True ROI (net refunds)</td><td className="num">{fmtRoi(report.true_roi)}</td></tr>
                    <tr><td>Top channel</td><td className="num">{report.top_channel}</td></tr>
                    <tr><td>Report email</td><td className="num">{client.client_report_email || "—"}</td></tr>
                    <tr><td>Model</td><td className="num">{client.attribution_model} · {client.lookback_days}d</td></tr>
                  </tbody>
                </table>
              ) : (
                <p className="panel-note">No reports yet for this client.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Clients({ mode, initialOpen }) {
  const [clients, setClients] = useState(null);
  const [err, setErr] = useState(null);
  const [open, setOpen] = useState(initialOpen || null);

  useEffect(() => {
    setClients(null);
    setErr(null);
    api("/clients", { mode })
      .then(setClients)
      .catch((e) => setErr(e.message));
  }, [mode]);

  if (err) return <div className="panel panel-pad empty">Could not load clients: {err}</div>;
  if (!clients) return <div className="panel panel-pad empty">Loading pilot roster…</div>;

  return (
    <div className="grid" style={{ gap: 14 }}>
      {clients.map((c) => (
        <ClientCard
          key={c.client_id}
          client={c}
          mode={mode}
          expanded={open === c.client_id}
          onToggle={() => setOpen(open === c.client_id ? null : c.client_id)}
        />
      ))}
      {!clients.length && (
        <div className="panel panel-pad empty">
          No clients registered. Onboard pilots via docs/ONBOARDING_CLIENT.md.
        </div>
      )}
    </div>
  );
}
