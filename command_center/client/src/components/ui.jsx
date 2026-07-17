import React from "react";
import { channelHex, channelLabel } from "../channels.js";

export function StatTile({ label, value, delta, deltaDir }) {
  return (
    <div className="panel tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {delta && <div className={`tile-delta ${deltaDir || ""}`}>{delta}</div>}
    </div>
  );
}

const STATUS_STYLE = {
  success: { cls: "ok", icon: "✓", label: "Success" },
  partial: { cls: "warn", icon: "◐", label: "Partial" },
  warn: { cls: "warn", icon: "◐", label: "Warning" },
  running: { cls: "run", icon: "●", label: "Running" },
  submitted: { cls: "run", icon: "●", label: "Submitted" },
  failed: { cls: "err", icon: "✕", label: "Failed" },
  error: { cls: "err", icon: "✕", label: "Error" },
  pending: { cls: "neutral", icon: "○", label: "Pending" },
  ok: { cls: "ok", icon: "✓", label: "Healthy" },
};

export function StatusBadge({ status, label }) {
  const s = STATUS_STYLE[status] || { cls: "neutral", icon: "○", label: status || "—" };
  return (
    <span className={`badge ${s.cls}`}>
      <span aria-hidden="true">{s.icon}</span> {label || s.label}
    </span>
  );
}

export function ChannelChip({ id }) {
  return (
    <span className="chip">
      <span className="swatch" style={{ background: channelHex(id) }} />
      {channelLabel(id)}
    </span>
  );
}

export function Legend({ ids }) {
  return (
    <div className="legend" role="list" aria-label="Channels">
      {ids.map((id) => (
        <ChannelChip key={id} id={id} />
      ))}
    </div>
  );
}

export function StageList({ stages }) {
  if (!stages) return null;
  return (
    <div>
      {stages.map((s) => (
        <div key={s.id} className={`stage-row ${s.state}`}>
          <span className={`stage-icon ${s.state}`}>
            {s.state === "done" ? "✓" : s.state === "running" ? <span className="spin">●</span> : "○"}
          </span>
          {s.label}
        </div>
      ))}
    </div>
  );
}
