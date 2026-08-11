# ARIE Operator Runbook

Operational reference for ARIE, the Automatic Revenue Intelligence Engine.

## Components

| Component | What it is | Where defined |
|---|---|---|
| Vercel Command Center | Canonical internal operator UI | `arie-portal/` |
| `attribution-launcher` | Cloud Run Job that creates the work manifest and launches workers | `flows/job_launcher.py` |
| `attribution-pipeline` | Cloud Run task-array worker job | `flows/agency_flow.py` |
| `attribution-benchmark-finalizer` | Cloud Run finalizer job | `flows/benchmark_finalizer.py` |
| `attribution-delta-maintenance` | Weekly Delta maintenance job | `flows/delta_maintenance.py` |
| `attribution-api` | Optional FastAPI control service for approvals/API access | `api/` |
| `attribution-monthly` | Cloud Scheduler trigger for the launcher job | `deploy.sh` |

Databricks SQL/Delta is the durable system of record. Cloud Run is the
execution layer. Vercel is the only supported command center UI.

## Deploy Backend

```powershell
.\scripts\arie_pilot_preflight.ps1 -ProjectId <project>
.\scripts\arie_seed_secrets.ps1 -ProjectId <project>
.\scripts\arie_deploy_cloud_run.ps1 -ProjectId <project>
.\scripts\arie_pilot_preflight.ps1 -ProjectId <project> -AfterDeploy
```

The backend deploy script builds the Python image, deploys Cloud Run jobs,
deploys the optional FastAPI service, and configures Cloud Scheduler. It does
not deploy a command center UI.

## Deploy Command Center

```powershell
cd C:\Users\zajen\attribution-agent\arie-portal
npm.cmd run check
npx.cmd vercel deploy --yes
npx.cmd vercel promote <preview-url> --yes
```

The Vercel app needs the environment variables listed in
`arie-portal/README.md`, including Basic Auth, Databricks SQL, and GCP Workload
Identity Federation variables.

## Run The Pipeline

Preferred path:

- Open the Vercel ARIE Command Center.
- Choose agency, clients, attribution model, and dry-run/live mode.
- Live delivery requires typing `RUN LIVE`.
- Submit the run and monitor results in the command center.

Manual dry run:

```powershell
.\scripts\arie_cloud_run_dry_run.ps1 `
  -ProjectId <project> `
  -AgencyId <agency_id> `
  -AttributionModel w_shape `
  -ClientIds <client_id>
```

Direct launcher execution:

```powershell
gcloud run jobs execute attribution-launcher `
  --project <project> `
  --region us-central1 `
  --args "flows/job_launcher.py,--agency,<agency_id>,--client,<client_id>,--dry-run,--attribution-model,w_shape" `
  --wait
```

## Client Onboarding

Use the Vercel Command Center tenant/client lifecycle screens. The Command
Center writes registry metadata to Databricks through the lifecycle job and
stores source credentials in GCP Secret Manager.

For source-level onboarding details, see `docs/ONBOARDING_CLIENT.md`.

## Health Checks

```powershell
gcloud run jobs describe attribution-launcher --project <project> --region us-central1
gcloud run jobs describe attribution-pipeline --project <project> --region us-central1
gcloud run jobs executions list --job attribution-pipeline --project <project> --region us-central1
gcloud run services describe attribution-api --project <project> --region us-central1
gcloud logging read 'resource.type=cloud_run_job AND resource.labels.job_name=attribution-pipeline' --project <project> --limit 50
```

## Before A Live Report

- Dry run completed successfully.
- Row counts and warnings look reasonable.
- HubSpot closed-won revenue is present for the report period.
- Attribution output tables refreshed.
- Report preview explains the selected attribution model.
- Email delivery state is `ready`, not suppressed.

## Retired Paths

Do not deploy or operate the old Streamlit, Replit, desktop-launcher, or static
prototype command centers. The Vercel portal is the supported UI.
