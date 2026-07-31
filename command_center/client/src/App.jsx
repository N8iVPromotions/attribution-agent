import React, { useEffect, useState } from "react";
import { api } from "./api.js";
import Overview from "./views/Overview.jsx";
import Clients from "./views/Clients.jsx";
import RunConsole from "./views/RunConsole.jsx";
import Reports from "./views/Reports.jsx";
import Health from "./views/Health.jsx";

const NAV = [
  { id: "overview", label: "Overview", Comp: Overview },
  { id: "clients", label: "Pilot roster", Comp: Clients },
  { id: "runs", label: "Run console", Comp: RunConsole },
  { id: "reports", label: "Reports", Comp: Reports },
  { id: "health", label: "Health & approvals", Comp: Health },
];

const PAGE_COPY = {
  overview: { title: "Overview", sub: "Portfolio-wide attribution across the pilot cohort" },
  clients: { title: "Pilot roster", sub: "Sources, models, and status for each pilot client" },
  runs: { title: "Run console", sub: "Trigger and monitor monthly attribution runs" },
  reports: { title: "Reports", sub: "AI-generated insight reports, ready for client delivery" },
  health: { title: "Health & approvals", sub: "System status and governance sign-off queue" },
};

export default function App() {
  const [tab, setTab] = useState("overview");
  const [mode, setMode] = useState("demo");
  const [status, setStatus] = useState(null);
  const [openClient, setOpenClient] = useState(null);

  useEffect(() => {
    api("/status").then((s) => {
      setStatus(s);
      setMode(s.default_mode);
    });
  }, []);

  const Active = NAV.find((n) => n.id === tab)?.Comp || Overview;
  const copy = PAGE_COPY[tab];

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">A</div>
          <div>
            <div className="brand-name">ARIE</div>
            <div className="brand-sub">Command Center</div>
          </div>
        </div>
        {NAV.map((n) => (
          <button
            key={n.id}
            className={`nav-item ${tab === n.id ? "active" : ""}`}
            onClick={() => setTab(n.id)}
          >
            <span className="dot" />
            {n.label}
          </button>
        ))}
        <div className="sidebar-foot">
          N8iV Promotions
          <br />
          Automatic Revenue Intelligence Engine
        </div>
      </aside>

      <main className="main">
        <div className="topbar">
          <div>
            <h1 className="page-title">{copy.title}</h1>
            <p className="page-sub">{copy.sub}</p>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            {mode === "demo" && <span className="demo-banner">● Demo data — sales mode</span>}
            <div className="mode-switch">
              <button className={mode === "demo" ? "on" : ""} onClick={() => setMode("demo")}>
                Demo
              </button>
              <button
                className={mode === "live" ? "on" : ""}
                disabled={status && !status.live_configured}
                title={status && !status.live_configured ? "Configure live secrets in Health tab" : ""}
                onClick={() => setMode("live")}
              >
                Live
              </button>
            </div>
          </div>
        </div>

        <Active
          mode={mode}
          onOpenClient={(id) => {
            setOpenClient(id);
            setTab("clients");
          }}
          initialOpen={tab === "clients" ? openClient : null}
        />
      </main>
    </div>
  );
}
