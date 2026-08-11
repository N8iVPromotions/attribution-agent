import { databricksHost, hasDatabricksApiConfig, requestDatabricks } from "@/lib/databricks";
import type { TenantLifecycleInput, TenantLifecycleResult } from "@/lib/types";

type RunNowResponse = {
  run_id?: number;
  number_in_job?: number;
};

function lifecycleJobId() {
  const value = Number((process.env.DATABRICKS_TENANT_LIFECYCLE_JOB_ID || "").trim());
  return Number.isSafeInteger(value) && value > 0 ? value : 0;
}

export function hasTenantLifecycleConfig() {
  return hasDatabricksApiConfig() && Boolean(lifecycleJobId());
}

export async function triggerTenantLifecycle(
  input: TenantLifecycleInput,
  requestId: string,
  requestedBy: string
): Promise<TenantLifecycleResult> {
  const jobId = lifecycleJobId();
  if (!hasTenantLifecycleConfig() || !jobId) {
    throw new Error("The Databricks tenant lifecycle job is not configured.");
  }

  const payload = await requestDatabricks<RunNowResponse>("/api/2.2/jobs/run-now", {
    method: "POST",
    body: JSON.stringify({
      job_id: jobId,
      idempotency_token: requestId,
      job_parameters: {
        command: input.command,
        entity_id: input.entityId,
        entity_name: input.entityName || "",
        agency_id: input.agencyId || "",
        report_email: input.reportEmail || "",
        attribution_model: input.attributionModel || "last_touch",
        confirmation: input.confirmation || "",
        request_id: requestId,
        requested_by: requestedBy
      }
    })
  });
  const runId = String(payload.run_id || "");
  if (!runId) {
    throw new Error("Databricks accepted the request without returning a run ID.");
  }
  return {
    ok: true,
    message: "Databricks accepted the tenant lifecycle command.",
    requestId,
    runId,
    runUrl: `https://${databricksHost()}/#job/${jobId}/run/${runId}`
  };
}
