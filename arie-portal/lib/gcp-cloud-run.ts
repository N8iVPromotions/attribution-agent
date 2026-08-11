import { getVercelOidcToken } from "@vercel/oidc";
import { ExternalAccountClient } from "google-auth-library";
import type { PipelineTriggerInput, PipelineTriggerResult } from "@/lib/types";

type CloudRunRunResponse = {
  name?: string;
  metadata?: {
    name?: string;
  };
};

function clean(value: string | undefined) {
  return (value || "").trim();
}

function gcpConfig() {
  return {
    projectId: clean(process.env.GCP_PROJECT_ID || process.env.GOOGLE_CLOUD_PROJECT),
    projectNumber: clean(process.env.GCP_PROJECT_NUMBER),
    serviceAccountEmail: clean(process.env.GCP_SERVICE_ACCOUNT_EMAIL),
    workloadIdentityPoolId: clean(process.env.GCP_WORKLOAD_IDENTITY_POOL_ID),
    workloadIdentityPoolProviderId: clean(process.env.GCP_WORKLOAD_IDENTITY_POOL_PROVIDER_ID),
    region: clean(process.env.ARIE_CLOUD_RUN_REGION) || "us-central1",
    jobName: clean(process.env.ARIE_CLOUD_RUN_JOB) || "attribution-launcher"
  };
}

export function hasGcpCloudRunOidcConfig() {
  const config = gcpConfig();
  return Boolean(
    config.projectId &&
      config.projectNumber &&
      config.serviceAccountEmail &&
      config.workloadIdentityPoolId &&
      config.workloadIdentityPoolProviderId &&
      config.region &&
      config.jobName
  );
}

function pipelineArgs(input: PipelineTriggerInput) {
  const args = ["flows/job_launcher.py", "--agency", input.agencyId];
  for (const clientId of input.clientIds) {
    args.push("--client", clientId);
  }
  if (input.dryRun) {
    args.push("--dry-run");
  }
  args.push("--attribution-model", input.attributionModel);
  return args;
}

export async function triggerCloudRunJobWithOidc(input: PipelineTriggerInput): Promise<PipelineTriggerResult> {
  const config = gcpConfig();
  if (!hasGcpCloudRunOidcConfig()) {
    return {
      ok: false,
      message: "GCP OIDC is not configured for Cloud Run job execution."
    };
  }

  const authClient = ExternalAccountClient.fromJSON({
    type: "external_account",
    audience: `//iam.googleapis.com/projects/${config.projectNumber}/locations/global/workloadIdentityPools/${config.workloadIdentityPoolId}/providers/${config.workloadIdentityPoolProviderId}`,
    subject_token_type: "urn:ietf:params:oauth:token-type:jwt",
    token_url: "https://sts.googleapis.com/v1/token",
    service_account_impersonation_url: `https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${config.serviceAccountEmail}:generateAccessToken`,
    subject_token_supplier: {
      getSubjectToken: getVercelOidcToken
    }
  });

  if (!authClient) {
    return {
      ok: false,
      message: "Could not initialize GCP external account auth client."
    };
  }

  const headers = await authClient.getRequestHeaders();
  const authorization = headers.get("authorization");
  if (!authorization) {
    return {
      ok: false,
      message: "Could not create a GCP authorization header from Vercel OIDC."
    };
  }

  const url = `https://run.googleapis.com/v2/projects/${config.projectId}/locations/${config.region}/jobs/${config.jobName}:run`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      authorization,
      "content-type": "application/json"
    },
    body: JSON.stringify({
      overrides: {
        containerOverrides: [
          {
            args: pipelineArgs(input)
          }
        ]
      }
    }),
    cache: "no-store"
  });

  const payload = (await response.json().catch(() => ({}))) as CloudRunRunResponse & {
    error?: {
      message?: string;
    };
  };

  if (!response.ok) {
    return {
      ok: false,
      message: payload.error?.message || `Cloud Run Jobs API returned HTTP ${response.status}.`
    };
  }

  return {
    ok: true,
    message: "Cloud Run job execution submitted.",
    operation: payload.name || payload.metadata?.name || config.jobName
  };
}
