export type RunStatus = "success" | "failed" | "running" | "queued" | "warning";

export type PipelineRun = {
  runId: string;
  agencyId: string;
  clientId: string;
  runMode: string;
  attributionModel: string;
  status: RunStatus;
  dryRun: boolean;
  totalPipeline: number;
  topChannel: string;
  emailSent: boolean;
  warnings: string;
  error: string;
  startedAt: string;
  finishedAt: string;
  sourceRows: {
    meta: number;
    google: number;
    linkedin: number;
    hubspot: number;
    stripe: number;
  };
};

export type ClientAccount = {
  clientId: string;
  name: string;
  agencyId: string;
  attributionModel: string;
  reportEmail: string;
  active: boolean;
  platforms: {
    meta: boolean;
    google: boolean;
    linkedin: boolean;
    hubspot: boolean;
    stripe: boolean;
  };
  updatedAt: string;
};

export type OperatorAlert = {
  severity: "info" | "warning" | "critical";
  category: string;
  title: string;
  message: string;
  clientId: string;
  source: string;
  actionRequired: string;
  status: string;
  eventTime: string;
};

export type Recommendation = {
  title: string;
  body: string;
  severity: "info" | "warning" | "critical";
};

export type CommandCenterData = {
  source: "databricks" | "demo";
  generatedAt: string;
  summary: {
    activeClients: number;
    runs30d: number;
    successRate: number;
    attributedPipeline: number;
    openAlerts: number;
    criticalAlerts: number;
  };
  clients: ClientAccount[];
  runs: PipelineRun[];
  alerts: OperatorAlert[];
  recommendations: Recommendation[];
  warnings: string[];
};

export type PipelineTriggerInput = {
  agencyId: string;
  clientIds: string[];
  attributionModel: string;
  dryRun: boolean;
  runMode: string;
};

export type PipelineTriggerResult = {
  ok: boolean;
  message: string;
  operation?: string;
  command?: string;
};
