import React, { useEffect, useState } from "react";
import { api, fmtMoney, fmtRoi, fmtMonth } from "../api.js";
import { StatTile, StatusBadge } from "../components/ui.jsx";
import { StackedBars } from "../components/charts.jsx";

export default function Overview({ mode, onOpenClient }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    setData(null);
    setErr(null);
    api("/overview", { mode })
      .then(setData)
      .catch((e) => setErr(e.message));
  }, [mode]);

  if (err) return <div className="panel panel-pad empty">Could not load overview: {err}</div>;
  if (!data) return <div className="panel panel-pad empty">Loading portfolio…</div>;

  const reports = data.latest_reports || [];
  const spend = reports.reduce((s, r) => s + (r.total_spend || 0), 0);
  const pipeline = reports.reduce((s, r) => s + (r.total_pipeline || 0), 0);
  const collected = reports.reduce((s, r) => s + (r.collected_revenue || 0), 0);
  const roi = spend ? pipeline / spend : 0;
  const month = reports[0]?.report_month;

  return (
    <div className="grid" style={{ gap: 18 }}>
      <div className="grid grid-tiles">
        <StatTile label={`Attributed pipeline · ${fmtMonth(month)}`} value={fmtMoney(pipeline)} />
        <StatTile label="Ad spend" value={fmtMoney(spend)} />
        <StatTile label="Blended ROI" value={fmtRoi(roi)} />
        <StatTile label="Collected revenue" value={fmtMoney(collected)} />
        <StatTile label="Active pilots" value={String(reports.length)} />
      </div>

      {data.monthly && (
        <div className="panel panel-pad">
          <h3 className="panel-title">Attributed revenue by channel</h3>
          <p className="panel-note">All pilot clients, trailing six months</p>
          <StackedBars monthly={data.monthly} />
        </div>
      )}

      <div className="panel">
        <div className="panel-pad" style={{ paddingBottom: 8 }}>
          <h3 className="panel-title">Pilot portfolio · latest reports</h3>
        </div>
        <div className="table-scroll">
          <table className="data">
            <thead>
              <tr>
                <th>Client</th>
                <th>Month</th>
                <th>Top channel</th>
                <th className="num">Spend</th>
                <th className="num">Pipeline</th>
                <th className="num">ROI</th>
                <th className="num">True ROI</th>
                <th>Model</th>
              </tr>
            </thead>
            <tbody>
              {reports.map((r) => (
                <tr key={r.client_id} onClick={() => onOpenClient?.(r.client_id)} style={{ cursor: "pointer" }}>
                  <td style={{ fontWeight: 600 }}>{r.client_id}</td>
                  <td>{fmtMonth(r.report_month)}</td>
                  <td>{r.top_channel || "—"}</td>
                  <td className="num">{fmtMoney(r.total_spend)}</td>
                  <td className="num">{fmtMoney(r.total_pipeline)}</td>
                  <td className="num">{fmtRoi(r.overall_roi)}</td>
                  <td className="num">{fmtRoi(r.true_roi)}</td>
                  <td>
                    <StatusBadge status="pending" label={r.attribution_model} />
                  </td>
                </tr>
              ))}
              {!reports.length && (
                <tr>
                  <td colSpan="8" className="empty">
                    No reports yet — run the pipeline from the Run Console.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
