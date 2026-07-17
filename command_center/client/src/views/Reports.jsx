import React, { useEffect, useState } from "react";
import { api, fmtMoney, fmtRoi, fmtPct, fmtMonth, fmtDate } from "../api.js";
import { StatusBadge } from "../components/ui.jsx";

export default function Reports({ mode }) {
  const [clients, setClients] = useState([]);
  const [clientId, setClientId] = useState("");
  const [reports, setReports] = useState([]);
  const [genBusy, setGenBusy] = useState(false);
  const [genMsg, setGenMsg] = useState(null);

  useEffect(() => {
    api("/clients", { mode }).then((cs) => {
      setClients(cs);
      if (cs.length) setClientId(cs[0].client_id);
    });
  }, [mode]);

  useEffect(() => {
    if (!clientId) return;
    setReports([]);
    api(`/reports/${clientId}?limit=12`, { mode })
      .then(setReports)
      .catch(() => setReports([]));
  }, [clientId, mode]);

  async function generate() {
    setGenBusy(true);
    setGenMsg(null);
    try {
      const res = await api(`/reports/${clientId}/generate`, { mode, method: "POST", body: {} });
      setGenMsg(res.message || "Report generation started.");
    } catch (e) {
      setGenMsg(e.message);
    } finally {
      setGenBusy(false);
    }
  }

  const latest = reports[0];

  return (
    <div className="grid" style={{ gap: 18 }}>
      <div className="panel panel-pad" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <select value={clientId} onChange={(e) => setClientId(e.target.value)}>
          {clients.map((c) => (
            <option key={c.client_id} value={c.client_id}>
              {c.client_display_name || c.client_name}
            </option>
          ))}
        </select>
        <button className="btn" onClick={generate} disabled={genBusy || !clientId}>
          {genBusy ? "Requesting…" : "Regenerate latest report"}
        </button>
        {genMsg && <span className="panel-note">{genMsg}</span>}
      </div>

      {latest ? (
        <div className="panel panel-pad">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
            <h2 style={{ fontFamily: "var(--font-display)", fontSize: 21, margin: 0 }}>
              {fmtMonth(latest.report_month)} attribution report
            </h2>
            <StatusBadge status="pending" label={`${latest.attribution_model} · ${latest.model_id}`} />
          </div>
          <p className="panel-note" style={{ marginTop: 4 }}>
            Generated {fmtDate(latest.generated_at)} · run {latest.run_id} · prompt {latest.prompt_version}
          </p>

          <div className="grid grid-tiles" style={{ margin: "18px 0" }}>
            <div className="tile" style={{ padding: 0 }}>
              <div className="tile-label">Attributed pipeline</div>
              <div className="tile-value" style={{ fontSize: 22 }}>{fmtMoney(latest.total_pipeline)}</div>
            </div>
            <div className="tile" style={{ padding: 0 }}>
              <div className="tile-label">Spend</div>
              <div className="tile-value" style={{ fontSize: 22 }}>{fmtMoney(latest.total_spend)}</div>
            </div>
            <div className="tile" style={{ padding: 0 }}>
              <div className="tile-label">ROI</div>
              <div className="tile-value" style={{ fontSize: 22 }}>{fmtRoi(latest.overall_roi)}</div>
            </div>
            <div className="tile" style={{ padding: 0 }}>
              <div className="tile-label">True ROI (net refunds)</div>
              <div className="tile-value" style={{ fontSize: 22 }}>{fmtRoi(latest.true_roi)}</div>
            </div>
            <div className="tile" style={{ padding: 0 }}>
              <div className="tile-label">Refund rate</div>
              <div className="tile-value" style={{ fontSize: 22 }}>{fmtPct(latest.refund_rate)}</div>
            </div>
          </div>

          <h4 className="panel-title">Narrative</h4>
          <p className="report-narrative">{latest.narrative}</p>

          {latest.key_findings?.length > 0 && (
            <>
              <h4 className="panel-title" style={{ marginTop: 20 }}>Key findings</h4>
              {latest.key_findings.map((f, i) => (
                <div className="finding" key={i}>
                  <span className="finding-n">{i + 1}</span>
                  <span>{f}</span>
                </div>
              ))}
            </>
          )}
        </div>
      ) : (
        <div className="panel panel-pad empty">No reports yet for this client.</div>
      )}

      {reports.length > 1 && (
        <div className="panel">
          <div className="panel-pad" style={{ paddingBottom: 8 }}>
            <h3 className="panel-title">Report history</h3>
          </div>
          <div className="table-scroll">
            <table className="data">
              <thead>
                <tr>
                  <th>Month</th>
                  <th className="num">Pipeline</th>
                  <th className="num">Spend</th>
                  <th className="num">ROI</th>
                  <th>Top channel</th>
                </tr>
              </thead>
              <tbody>
                {reports.map((r) => (
                  <tr key={r.report_id}>
                    <td>{fmtMonth(r.report_month)}</td>
                    <td className="num">{fmtMoney(r.total_pipeline)}</td>
                    <td className="num">{fmtMoney(r.total_spend)}</td>
                    <td className="num">{fmtRoi(r.overall_roi)}</td>
                    <td>{r.top_channel}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
