# ARIE Command Center Portal

Vercel-hosted operator portal for ARIE, the Automatic Revenue Intelligence Engine.

This app is the polished web layer. Databricks remains the system of record and
Cloud Run remains the execution layer for pipeline runs.

## Local

```powershell
cd C:\Users\zajen\attribution-agent\arie-portal
npm.cmd install
npm.cmd run dev
```

## Required production environment

- `ARIE_BASIC_AUTH_USER`
- `ARIE_BASIC_AUTH_PASSWORD`
- `DATABRICKS_SERVER_HOSTNAME`
- `DATABRICKS_HTTP_PATH`
- `DATABRICKS_TOKEN`
- `ATTRIBUTION_OPS_SCHEMA`

Optional:

- `ARIE_PIPELINE_TRIGGER_URL`
- `ARIE_PIPELINE_TRIGGER_TOKEN`
- `GCP_PROJECT_ID`
- `GCP_PROJECT_NUMBER`
- `GCP_SERVICE_ACCOUNT_EMAIL`
- `GCP_WORKLOAD_IDENTITY_POOL_ID`
- `GCP_WORKLOAD_IDENTITY_POOL_PROVIDER_ID`
- `ARIE_CLOUD_RUN_REGION`
- `ARIE_CLOUD_RUN_JOB`

`ARIE_PIPELINE_TRIGGER_URL` should point to the ARIE FastAPI pipeline route,
for example `https://<cloud-run-api-url>/api/v1/pipeline/run`.
`ARIE_PIPELINE_TRIGGER_TOKEN` is sent as `X-API-Key`.

For production, prefer the keyless GCP path: configure Vercel OIDC federation in
GCP and set the `GCP_*` plus `ARIE_CLOUD_RUN_*` variables above. The portal will
submit Cloud Run Jobs directly with short-lived credentials.

When Databricks env vars are missing, the UI falls back to safe demo data.
