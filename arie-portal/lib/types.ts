export type RunStatus = "success" | "partial" | "failed" | "running" | "queued" | "warning";

export type ClientPlatformKey = "meta" | "google" | "linkedin" | "tiktok" | "hubspot" | "stripe";

export type ClientPlatformDetail = {
  accountId: string;
  credentialConfigured: boolean;
  expiresAt: string;
};

export type SourceRows = {
  meta: number;
  google: number;
  linkedin: number;
  tiktok: number;
  hubspot: number;
  stripe: number;
  normalized: number;
};

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
  deliverySuppressed: boolean;
  warnings: string;
  error: string;
  startedAt: string;
  finishedAt: string;
  outputSchema: string;
  sourceRows: SourceRows;
};

export type RunCheckpoint = {
  checkpointId: string;
  runId: string;
  clientId: string;
  stepName: string;
  status: string;
  startedAt: string;
  completedAt: string;
  errorDetail: string;
};

export type ClientAccount = {
  clientId: string;
  name: string;
  agencyId: string;
  attributionModel: string;
  reportEmail: string;
  active: boolean;
  lookbackDays: number;
  platforms: {
    meta: boolean;
    google: boolean;
    linkedin: boolean;
    tiktok: boolean;
    hubspot: boolean;
    stripe: boolean;
  };
  platformDetails?: Record<ClientPlatformKey, ClientPlatformDetail>;
  updatedAt: string;
};

export type PilotClientConfiguration = {
  clientId: string;
  clientName: string;
  agencyId: string;
  attributionModel: string;
  reportEmail: string;
  lookbackDays: number;
  databricksSchema: string;
  platforms: Record<
    ClientPlatformKey,
    ClientPlatformDetail & {
      enabled: boolean;
    }
  >;
};

export type PilotClientConfigurationResult = {
  ok: boolean;
  message: string;
  client?: PilotClientConfiguration;
  error?: string;
};

export type AgencyAccount = {
  agencyId: string;
  name: string;
  active: boolean;
  createdAt: string;
  updatedAt: string;
};

export type TenantLifecycleCommand =
  | "create_agency"
  | "create_business"
  | "delete_business"
  | "delete_agency";

export type TenantLifecycleOperation = {
  requestId: string;
  command: TenantLifecycleCommand;
  entityType: "agency" | "business";
  entityId: string;
  agencyId: string;
  schemaName: string;
  requestedBy: string;
  status: string;
  databricksRunId: string;
  errorMessage: string;
  requestedAt: string;
  completedAt: string;
};

export type OperatorAlert = {
  alertId: string;
  severity: "info" | "warning" | "critical";
  category: string;
  title: string;
  message: string;
  clientId: string;
  source: string;
  runId: string;
  actionRequired: string;
  status: string;
  eventTime: string;
};

export type InsightReport = {
  reportId: string;
  clientId: string;
  agencyId: string;
  reportMonth: string;
  narrative: string;
  keyFindings: string[];
  topChannel: string;
  totalPipeline: number;
  totalSpend: number;
  overallRoi: number;
  trueRoi: number;
  collectedRevenue: number;
  refundRate: number;
  attributionModel: string;
  generatedAt: string;
  runId: string;
  promptVersion: string;
  modelId: string;
  status: string;
};

export type CostSummary = {
  agencyId: string;
  totalTokens: number;
  costUsd: number;
  lastEventAt: string;
};

export type EvalScore = {
  agentName: string;
  score: number;
  passed: boolean;
  regression: boolean;
  runAt: string;
  promptVersion: string;
  modelId: string;
};

export type AuditEvent = {
  eventId: string;
  eventTime: string;
  eventType: string;
  actor: string;
  clientId: string;
  resource: string;
  action: string;
  outcome: string;
  runId: string;
};

export type ApprovalItem = {
  actionId: string;
  createdAt: string;
  actor: string;
  description: string;
  actionType: string;
  status: string;
  channel: string;
};

export type Recommendation = {
  title: string;
  body: string;
  severity: "info" | "warning" | "critical";
};

export type CommandCenterData = {
  source: "databricks" | "demo" | "unavailable";
  generatedAt: string;
  summary: {
    activeClients: number;
    runs30d: number;
    successRate: number;
    attributedPipeline: number;
    openAlerts: number;
    criticalAlerts: number;
    activeRuns: number;
    partialRuns: number;
    suppressedDeliveries: number;
    aiSpendMonth: number;
  };
  agencies: AgencyAccount[];
  clients: ClientAccount[];
  runs: PipelineRun[];
  checkpoints: RunCheckpoint[];
  alerts: OperatorAlert[];
  reports: InsightReport[];
  costs: CostSummary[];
  evals: EvalScore[];
  audits: AuditEvent[];
  approvals: ApprovalItem[];
  lifecycleOperations: TenantLifecycleOperation[];
  recommendations: Recommendation[];
  capabilities: {
    databricks: boolean;
    pipelineExecution: boolean;
    approvalActions: boolean;
    tenantLifecycle: boolean;
    clientConfiguration: boolean;
  };
  warnings: string[];
};

export type TenantLifecycleInput = {
  command: TenantLifecycleCommand;
  entityId: string;
  entityName?: string;
  agencyId?: string;
  reportEmail?: string;
  attributionModel?: string;
  confirmation?: string;
};

export type TenantLifecycleResult = {
  ok: boolean;
  message: string;
  requestId?: string;
  runId?: string;
  runUrl?: string;
};

export type PipelineTriggerInput = {
  agencyId: string;
  clientIds: string[];
  attributionModel: string;
  dryRun: boolean;
  runMode: string;
  confirmation?: string;
};

export type PipelineTriggerResult = {
  ok: boolean;
  message: string;
  operation?: string;
  command?: string;
};
