import React, { useEffect, useState } from "react";
import { api, fmtDate } from "../api.js";
import { StatusBadge } from "../components/ui.jsx";

export default function Health({ mode }) {
  const [health, setHealth] = useState(null);
  const [status, setStatus] = useState(null);
  const [approvals, setApprovals] = useState([]);
  const [busy, setBusy] = useState(null);

  function refresh() {
    api("/health", { mode }).then(setHealth).catch(() => setHealth(null));
    api("/status").then(setStatus).catch(() => setStatus(null));
    api("/approvals", { mode }).then(setApprovals).catch(() => setApprovals([]));
  }

  useEffect(refresh, [mode]);

  async function resolve(actionId, resolution) {
    setBusy(actionId);
    try {
      await api(`/approvals/${actionId}/resolve`, { mode, method: "POST", body: { resolution } });
      refresh();
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="grid" style={{ gap: 18 }}>
      <div className="grid grid-2">
        <div className="panel panel-pad">
          <h3 className="panel-title">ARIE API</h3>
          {health ? (
            <table className="data">
              <tbody>
                <tr><td>Status</td><td><StatusBadge status={health.status} /></td></tr>
                <tr><td>Version</td><td className="num">{health.version}</td></tr>
                <tr><td>Git SHA</td><td className="num">{health.git_sha}</td></tr>
                <tr><td>Deploy target</td><td className="num">{health.deploy_target}</td></tr>
              </tbody>
            </table>
          ) : (
            <p className="panel-note">Health check unavailable.</p>
          )}
        </div>
        <div className="panel panel-pad">
          <h3 className="panel-title">Live mode readiness</h3>
          {status ? (
            status.live_configured ? (
              <p className="panel-note">
                <StatusBadge status="success" label="Configured" /> All required secrets are
                present in this Repl. Switch the mode toggle to Live to run against Cloud Run.
              </p>
            ) : (
              <>
                <p className="panel-note">
                  <StatusBadge status="warn" label="Not configured" /> Missing secrets:
                </p>
                <ul style={{ margin: "8px 0 0", paddingLeft: 20, fontSize: 13 }}>
                  {status.live_missing.map((m) => (
                    <li key={m}><code>{m}</code></li>
                  ))}
                </ul>
              </>
            )
          ) : (
            <p className="panel-note">Loading…</p>
          )}
        </div>
      </div>

      <div className="panel">
        <div className="panel-pad" style={{ paddingBottom: 8 }}>
          <h3 className="panel-title">Pending approvals</h3>
          <p className="panel-note">Governance-flagged reports awaiting sign-off before client delivery.</p>
        </div>
        <div className="table-scroll">
          <table className="data">
            <thead>
              <tr>
                <th>Description</th>
                <th>Type</th>
                <th>Raised by</th>
                <th>Raised</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {approvals.map((a) => (
                <tr key={a.action_id}>
                  <td>{a.description}</td>
                  <td>{a.action_type}</td>
                  <td>{a.actor}</td>
                  <td>{fmtDate(a.created_at)}</td>
                  <td style={{ display: "flex", gap: 8 }}>
                    <button
                      className="btn"
                      disabled={busy === a.action_id}
                      onClick={() => resolve(a.action_id, "approved")}
                    >
                      Approve
                    </button>
                    <button
                      className="btn"
                      disabled={busy === a.action_id}
                      onClick={() => resolve(a.action_id, "rejected")}
                    >
                      Reject
                    </button>
                  </td>
                </tr>
              ))}
              {!approvals.length && (
                <tr>
                  <td colSpan="5" className="empty">Nothing pending — clear to send.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
