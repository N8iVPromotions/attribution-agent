# ARIE Internal Command Center

Production operations console for ARIE, the Automatic Revenue Intelligence
Engine. This is the canonical internal control surface. The synthetic sales
demo remains a separate application.

## Operator capabilities

- Fleet overview for 15–20 agency tenants with explicit success, partial,
  failed, running, and queued states.
- Guarded Cloud Run attribution launches by agency, tenant, and attribution
  model. Live delivery requires an exact `RUN LIVE` confirmation.
- Guided Pilot Room for creating or updating a client, sending platform
  credentials to GCP Secret Manager, checking source readiness, and launching
  a no-email attribution preview during a sales conversation.
- Source row counts, output schemas, pipeline checkpoints, errors, warnings,
  and partial-run email suppression visibility.
- Executive insight report previews and delivery state.
- Operator alerts, recommended interventions, AI cost ledger, golden-dataset
  evaluation results, governance approvals, and audit history.
- Twelve-second live refresh while the browser tab is visible.
- Agency and business creation/deletion through a serialized, audited
  Databricks Workflow with exact destructive confirmations.
- Fail-closed production authentication and read-only capability degradation.

Databricks remains the system of record. Cloud Run Jobs remain the execution
layer. The browser never receives Databricks, GCP, or ARIE API credentials.

## Local verification

```powershell
cd C:\Users\zajen\attribution-agent\arie-portal
npm.cmd install
npm.cmd run check
npm.cmd run dev
```

When Databricks is not configured, the UI displays an explicit `ISOLATED DEMO`
state. Production actions are disabled; demo records cannot trigger Cloud Run
or approval mutations.

For a local or Vercel Preview sales rehearsal, set `ARIE_ISOLATED_DEMO=true`.
This exposes synthetic telemetry while every external mutation remains locked.
The flag is ignored when `VERCEL_ENV=production`, so the deployed internal
Command Center continues to fail closed instead of showing sample client data.

## Required production environment

### Authentication

- `ARIE_BASIC_AUTH_USER`
- `ARIE_BASIC_AUTH_PASSWORD`

Production fails closed with HTTP 503 if either value is missing. These shared
credentials are appropriate for the initial single-operator deployment. Move
to Google Workspace OIDC/IAP before granting access to a broader team.

### Databricks telemetry

- `DATABRICKS_SERVER_HOSTNAME`
- `DATABRICKS_HTTP_PATH` or `DATABRICKS_WAREHOUSE_ID`
- `DATABRICKS_TOKEN`
- `ATTRIBUTION_OPS_SCHEMA`

### Pipeline execution

Preferred keyless path:

- `GCP_PROJECT_ID`
- `GCP_PROJECT_NUMBER`
- `GCP_SERVICE_ACCOUNT_EMAIL`
- `GCP_WORKLOAD_IDENTITY_POOL_ID`
- `GCP_WORKLOAD_IDENTITY_POOL_PROVIDER_ID`
- `ARIE_CLOUD_RUN_REGION`
- `ARIE_CLOUD_RUN_JOB`

Production uses the dedicated launcher rather than invoking a tenant worker
directly:

```text
GCP_PROJECT_ID=n8iv-analytics-production
GCP_PROJECT_NUMBER=348643002075
GCP_SERVICE_ACCOUNT_EMAIL=vercel-arie-command-center@n8iv-analytics-production.iam.gserviceaccount.com
GCP_WORKLOAD_IDENTITY_POOL_ID=vercel
GCP_WORKLOAD_IDENTITY_POOL_PROVIDER_ID=vercel
ARIE_CLOUD_RUN_REGION=us-central1
ARIE_CLOUD_RUN_JOB=attribution-launcher
```

Provision the trust boundary with
`scripts/configure_vercel_gcp_wif.ps1`. Its default mode is read-only; `-Apply`
creates the resources. The provider accepts only the Vercel subject
`owner:n8i-v-promotions:project:arie-command-center:environment:production`,
and the service account receives `roles/run.jobsExecutorWithOverrides` only on
the `attribution-launcher` job. Preview and development deployments are not
trusted.

FastAPI fallback:

- `ARIE_PIPELINE_TRIGGER_URL`
- `ARIE_PIPELINE_TRIGGER_TOKEN`

### Agency and business lifecycle

- `DATABRICKS_TENANT_LIFECYCLE_JOB_ID`

The lifecycle job uses the existing Databricks host/token and is deployed with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/deploy_databricks_tenant_lifecycle.ps1
# Review the plan, then:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/deploy_databricks_tenant_lifecycle.ps1 -Apply
```

Before switching Cloud Run to the Delta registry, seed the existing GCS record:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/migrate_gcs_registry_to_delta.ps1
# Review the plan, then:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/migrate_gcs_registry_to_delta.ps1 -Apply
```

Both scripts are no-op plans unless `-Apply` is explicitly provided. The
migration is upsert-only. Destructive operations can only be requested from
the authenticated Command Center and are independently revalidated by the
Databricks notebook.

### Client configuration and approval actions

- `ARIE_API_BASE`
- `ARIE_API_KEY`

The configured API key must have the ARIE admin role. Pilot client setup and
approval mutations are disabled when these variables are absent. The Pilot
Room calls the ARIE API from a server-side Vercel route; it never sends the
admin key, Databricks credentials, or GCP credentials to the browser.

When `ARIE_API_BASE` is a private Cloud Run URL, the portal uses the existing
Vercel workload identity variables to mint a short-lived Google ID token. The
command-center service account must have `roles/run.invoker` on the control API
service. The ARIE API key remains required as a second authorization layer.

Client platform credentials are accepted only during save and are written by
the ARIE API to client-specific GCP Secret Manager secrets. Databricks stores
the resulting secret names alongside non-sensitive account metadata. Existing
credential values are never returned to the Command Center.

For Google Ads, the pilot provides the customer ID and refresh token. ARIE's
shared Google developer token and OAuth client credentials must already be
configured on the Cloud Run execution environment.

## Pilot workflow

1. Select the N8iV agency in the Command Center and open **Pilot Room**.
2. Enter the business, report recipient, attribution model, and sales-cycle
   lookback. Ninety days is the default B2B pilot window.
3. Connect HubSpot as the closed-won revenue anchor and at least one ad source.
   Stripe can be added as optional collected-revenue enrichment.
4. Review and save. ARIE writes new secret versions and refreshes the
   Databricks client registry.
5. Launch the protected preview. The normal ingestion, validation,
   attribution, and report-generation path runs with email delivery suppressed.
6. Use **Pipeline** to inspect checkpoints, source row counts, warnings, and
   attributed revenue as the run completes.

## Deployment

```powershell
npx.cmd vercel deploy --yes
# verify the preview
npx.cmd vercel promote <preview-url> --yes
```

Security headers, `noindex`, private/no-store API responses, server-side secret
handling, execution input validation, and exact live-run confirmation are
enabled by default.
