import type { PipelineTriggerInput, PipelineTriggerResult } from "@/lib/types";
import { hasGcpCloudRunOidcConfig, triggerCloudRunJobWithOidc } from "@/lib/gcp-cloud-run";

type TriggerPayload = {
  operation?: unknown;
  cloud_run_operation?: unknown;
  name?: unknown;
  metadata?: {
    name?: unknown;
  };
  message?: unknown;
  detail?: unknown;
};

function pickText(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return undefined;
}

function pipelineCommand(input: PipelineTriggerInput) {
  const clientArgs = input.clientIds.length ? `,--client-filter,${input.clientIds.join(",")}` : "";
  const dryRun = input.dryRun ? ",--dry-run" : "";
  return [
    "gcloud run jobs execute attribution-pipeline",
    "--project n8iv-analytics-production",
    "--region us-central1",
    `--args "flows/agency_flow.py,--agency,${input.agencyId}${clientArgs}${dryRun},--attribution-model,${input.attributionModel},--run-mode,${input.runMode}"`,
    "--wait"
  ].join(" ");
}

function triggerBody(input: PipelineTriggerInput) {
  return {
    agency_id: input.agencyId,
    client_ids: input.clientIds,
    dry_run: input.dryRun,
    attribution_model: input.attributionModel,
    run_mode: input.runMode
  };
}

export async function triggerPipelineRun(input: PipelineTriggerInput): Promise<PipelineTriggerResult> {
  const triggerUrl = (process.env.ARIE_PIPELINE_TRIGGER_URL || "").trim();

  if (hasGcpCloudRunOidcConfig()) {
    const result = await triggerCloudRunJobWithOidc(input);
    if (result.ok) {
      return result;
    }

    if (!triggerUrl) {
      return {
        ...result,
        command: pipelineCommand(input)
      };
    }
  }

  if (!triggerUrl) {
    return {
      ok: false,
      message:
        "Vercel is deployed, but Cloud Run job execution is not wired yet. Configure ARIE_PIPELINE_TRIGGER_URL or run the command below.",
      command: pipelineCommand(input)
    };
  }

  const response = await fetch(triggerUrl, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(process.env.ARIE_PIPELINE_TRIGGER_TOKEN
        ? {
            authorization: `Bearer ${process.env.ARIE_PIPELINE_TRIGGER_TOKEN}`,
            "x-api-key": process.env.ARIE_PIPELINE_TRIGGER_TOKEN
          }
        : {})
    },
    body: JSON.stringify(triggerBody(input)),
    cache: "no-store"
  });

  const payload = (await response.json().catch(() => ({}))) as TriggerPayload;
  if (!response.ok) {
    return {
      ok: false,
      message:
        pickText(payload.message, payload.detail) || `Pipeline trigger failed with HTTP ${response.status}.`,
      operation: pickText(payload.operation, payload.cloud_run_operation, payload.name, payload.metadata?.name),
      command: pipelineCommand(input)
    };
  }

  return {
    ok: true,
    message: pickText(payload.message) || "Pipeline execution request submitted.",
    operation: pickText(payload.operation, payload.cloud_run_operation, payload.name, payload.metadata?.name)
  };
}
