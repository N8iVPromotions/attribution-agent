import type { CommandCenterData } from "@/lib/types";

export const mockCommandCenterData: CommandCenterData = {
  source: "demo",
  generatedAt: new Date().toISOString(),
  summary: {
    activeClients: 3,
    runs30d: 18,
    successRate: 0.89,
    attributedPipeline: 284500,
    openAlerts: 3,
    criticalAlerts: 1
  },
  clients: [
    {
      clientId: "n8iv_promotions",
      name: "N8iV Promotions",
      agencyId: "demo_agency",
      attributionModel: "w_shape",
      reportEmail: "zajen@n8ivpromotions.com",
      active: true,
      platforms: {
        meta: true,
        google: true,
        linkedin: true,
        hubspot: true,
        stripe: false
      },
      updatedAt: new Date().toISOString()
    },
    {
      clientId: "agency_pilot",
      name: "Agency Pilot Account",
      agencyId: "demo_agency",
      attributionModel: "linear",
      reportEmail: "ops@example.com",
      active: true,
      platforms: {
        meta: true,
        google: false,
        linkedin: true,
        hubspot: true,
        stripe: true
      },
      updatedAt: new Date(Date.now() - 86400000).toISOString()
    },
    {
      clientId: "b2b_saas",
      name: "B2B SaaS Demo",
      agencyId: "demo_agency",
      attributionModel: "time_decay",
      reportEmail: "revenue@example.com",
      active: true,
      platforms: {
        meta: false,
        google: true,
        linkedin: true,
        hubspot: true,
        stripe: true
      },
      updatedAt: new Date(Date.now() - 172800000).toISOString()
    }
  ],
  runs: [
    {
      runId: "demo-run-01",
      agencyId: "demo_agency",
      clientId: "n8iv_promotions",
      runMode: "client",
      attributionModel: "w_shape",
      status: "success",
      dryRun: true,
      totalPipeline: 124000,
      topChannel: "LinkedIn Ads",
      emailSent: false,
      warnings: "Stripe not configured; collected revenue enrichment skipped.",
      error: "",
      startedAt: new Date(Date.now() - 3600000).toISOString(),
      finishedAt: new Date(Date.now() - 3300000).toISOString(),
      sourceRows: {
        meta: 830,
        google: 410,
        linkedin: 520,
        hubspot: 74,
        stripe: 0
      }
    },
    {
      runId: "demo-run-02",
      agencyId: "demo_agency",
      clientId: "agency_pilot",
      runMode: "client",
      attributionModel: "linear",
      status: "warning",
      dryRun: true,
      totalPipeline: 92500,
      topChannel: "Meta Ads",
      emailSent: false,
      warnings: "UTM coverage below target on 18% of touchpoints.",
      error: "",
      startedAt: new Date(Date.now() - 90000000).toISOString(),
      finishedAt: new Date(Date.now() - 89700000).toISOString(),
      sourceRows: {
        meta: 620,
        google: 0,
        linkedin: 190,
        hubspot: 46,
        stripe: 21
      }
    }
  ],
  alerts: [
    {
      severity: "critical",
      category: "credential_missing",
      title: "Google Ads credential missing",
      message: "Google Ads is enabled for a pilot client but no refresh token is available.",
      clientId: "agency_pilot",
      source: "google_ads",
      actionRequired: "Reconnect Google Ads before the next attribution run.",
      status: "open",
      eventTime: new Date(Date.now() - 7200000).toISOString()
    },
    {
      severity: "warning",
      category: "attribution_accuracy",
      title: "High unattributed revenue",
      message: "31% of closed-won revenue did not match a known ad touchpoint.",
      clientId: "n8iv_promotions",
      source: "hubspot",
      actionRequired: "Review UTMs, contact emails, and HubSpot source fields.",
      status: "open",
      eventTime: new Date(Date.now() - 10800000).toISOString()
    }
  ],
  recommendations: [
    {
      severity: "warning",
      title: "Tighten UTM governance",
      body: "Prioritize UTM consistency before selling attribution reports as decision-grade output."
    },
    {
      severity: "info",
      title: "Use W-shape for N8iV pilot reporting",
      body: "The current B2B sales motion benefits from credit across first touch, lead conversion, and deal close."
    }
  ],
  warnings: [
    "Demo data is shown until Databricks environment variables are configured in Vercel."
  ]
};
