import React, { useEffect, useRef, useState } from "react";
import { api, fmtMoney, fmtDate } from "../api.js";
import { StatusBadge, StageList } from "../components/ui.jsx";

const MODELS = [
  { id: "last_touch", label: "Last touch" },
  { id: "linear", label: "Linear" },
  { id: "time_decay", label: "Time decay" },
  { id: "u_shape", label: "U-shape" },
  { id: "w_shape", label: "W-shape" },
];

export default function RunConsole({ mode }) {
  const [clients, setClients] = useState([]);
  const [selected, setSelected] = useState(new Set());
  const [dryRun, setDryRun] = useState(true);
  const [model, setModel] = useState("");
  const [runs, setRuns] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [activeIds, setActiveIds] = useState([]);
  const pollRef = useRef(null);

  useEffect(() => {
    api("/clients", { mode }).then(setClients).catch(() => setClients([]));
    refreshRuns();
    return () => clearInterval(pollRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  useEffect(() => {
    clearInterval(pollRef.current);
    if (!activeIds.length) return;
    pollRef.current = setInterval(refreshRuns, 900);
    return () => clearInterval(pollRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIds, mode]);

  function refreshRuns() {
    api("/pipeline/runs?limit=30", { mode })
      .then((rows) => {
        setRuns(rows);
        setActiveIds(rows.filter((r) => r.status === "running").map((r) => r.run_id));
      })
      .catch(() => {});
  }

  function toggle(id) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function trigger() {
    setSubmitting(true);
    setError(null);
    try {
      const body = {
        agency_id: "n8iv_promotions",
        client_ids: selected.size ? [...selected] : null,
        dry_run: dryRun,
      };
      if (model) body.attribution_model = model;
      await api("/pipeline/run", { mode, method: "POST", body });
      refreshRuns();
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="grid" style={{ gap: 18 }}>
      <div className="panel panel-pad">
        <h3 className="panel-title">Trigger a run</h3>
        <p className="panel-note">
          {mode === "demo"
            ? "Simulated run — no live credentials touched. Great for walking a prospect through the pipeline."
            : "Submits to the Cloud Run Job (or Databricks Jobs fallback) via the ARIE API."}
        </p>

        <div className="grid" style={{ gridTemplateColumns: "1fr", gap: 12, marginBottom: 16 }}>
          {clients.map((c) => (
            <label className="checkline" key={c.client_id}>
              <input
                type="checkbox"
                checked={selected.has(c.client_id)}
                onChange={() => toggle(c.client_id)}
              />
              {c.client_display_name || c.client_name}
            </label>
          ))}
          {!clients.length && <span className="panel-note">No clients found.</span>}
        </div>

        <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
          <label className="checkline">
            <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
            Dry run (skip email delivery)
          </label>
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="">Attribution model: per-client default</option>
            {MODELS.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
          <button className="btn btn-primary" onClick={trigger} disabled={submitting}>
            {submitting
              ? "Submitting…"
              : selected.size
                ? `Run ${selected.size} client${selected.size > 1 ? "s" : ""}`
                : "Run all pilots"}
          </button>
        </div>
        {error && (
          <p className="panel-note" style={{ color: "var(--err)", marginTop: 10 }}>
            {error}
          </p>
        )}
        {!dryRun && (
          <p className="panel-note" style={{ color: "var(--warn)", marginTop: 10 }}>
            Live run — client reports will be emailed on completion.
          </p>
        )}
      </div>

      <div className="panel">
        <div className="panel-pad" style={{ paddingBottom: 8 }}>
          <h3 className="panel-title">Recent runs</h3>
        </div>
        <div className="table-scroll">
          <table className="data">
            <thead>
              <tr>
                <th>Run</th>
                <th>Client</th>
                <th>Mode</th>
                <th>Status</th>
                <th className="num">Pipeline</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <React.Fragment key={r.run_id}>
                  <tr>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{r.run_id}</td>
                    <td>{r.client_id}</td>
                    <td>{r.dry_run ? "Dry run" : "Live"}</td>
                    <td><StatusBadge status={r.status} /></td>
                    <td className="num">{fmtMoney(r.total_pipeline)}</td>
                    <td>{fmtDate(r.started_at)}</td>
                  </tr>
                  {r.status === "running" && mode === "demo" && (
                    <tr>
                      <td colSpan="6" style={{ background: "var(--panel-tint)" }}>
                        <RunStages runId={r.run_id} mode={mode} />
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
              {!runs.length && (
                <tr>
                  <td colSpan="6" className="empty">No runs yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function RunStages({ runId, mode }) {
  const [detail, setDetail] = useState(null);
  useEffect(() => {
    let live = true;
    const poll = () =>
      api(`/pipeline/runs/${runId}`, { mode })
        .then((d) => live && setDetail(d))
        .catch(() => {});
    poll();
    const id = setInterval(poll, 700);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [runId, mode]);
  if (!detail?.stage_states) return null;
  return <StageList stages={detail.stage_states} />;
}
