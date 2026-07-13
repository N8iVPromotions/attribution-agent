# ARIE Operator Runbook

Operational reference for running, deploying, and recovering the attribution
platform. ARIE stands for Automatic Revenue Intelligence Engine. For day-to-day
development see the repo root `CLAUDE.md`.

## Components

| Component | What it is | Where defined |
|-----------|------------|---------------|
| `attribution-pipeline` | Cloud Run **Job** — runs the ingest → insight → report flow | `Dockerfile` + `deploy.sh` |
| `attribution-ui` | Cloud Run **Service** — Streamlit UI | `Dockerfile` + `deploy.sh` |
| `attribution-monthly` | Cloud Scheduler trigger for the Job (9am ET on the 1st) | `deploy.sh` |
| REST API | FastAPI (`api/main.py`) — pipeline, clients, reports, approvals, A2A | `api/` |
| A2A node | Agent-to-agent HTTP surface (discovery + dispatch) | `agents/a2a/server.py` |
| MCP server | FastMCP stdio server, 7 tools | `mcp_server/server.py` |

Both workloads run the **same image** from Artifact Registry
(`$REGION-docker.pkg.dev/$PROJECT_ID/attribution/attribution-agent`); the Job
overrides the container command to `python flows/agency_flow.py`.

**Data layer**: still Databricks. Delta reads/writes go through
`utils/databricks_writer.py` via `databricks-sql-connector` to the SQL
warehouse (`https://8259555645755006.6.gcp.databricks.com`) over public HTTPS.
Compute was migrated off Databricks because its Serverless Egress Gateway
blocks DNS to `api.stripe.com` / `graph.facebook.com` / `api.hubapi.com`.

## Secrets

Global platform secrets are stored in GCP Secret Manager and injected into both
workloads as env vars via `--set-secrets` (see the `SECRET_KEYS` list in
`deploy.sh`). Per-client source credentials are created by the command center
or `POST /clients`; the client registry stores only Secret Manager IDs.

```bash
# Seed/rotate from local .env (updates create new secret versions)
PROJECT_ID=<project> ./deploy.sh --seed-secrets
```

Rotation note: deploy-time env secrets reference `:latest`. The Job picks up
new values on its next execution; the **UI needs a redeploy** (or instance
recycle) to see rotated env-injected secrets. Per-client source credentials are
read from Secret Manager by ID at runtime, so a newly added version is used on
the next run.

## Deploy

```powershell
# From repo root. Builds via Cloud Build (no local Docker needed),
# then deploys Job + UI + Scheduler. Idempotent - safe to re-run.
.\scripts\arie_deploy_cloud_run.ps1 `
  -ProjectId <project> `
  -OperatorPrincipal user:you@example.com
```

`attribution-ui` is private by default. Set
`-AllowUnauthenticatedUi` only for a temporary demo environment with no real
client credentials.

Rollback: images are tagged with the git SHA. Redeploy a known-good one:

```bash
gcloud run jobs deploy attribution-pipeline --region us-central1 --image "<IMAGE>:<old-sha>"
gcloud run deploy attribution-ui --region us-central1 --image "<IMAGE>:<old-sha>"
# Or roll the service back without rebuilding:
gcloud run services update-traffic attribution-ui --region us-central1 --to-revisions <revision>=100
```

## Run the pipeline

Preferred operator path:

- Open ARIE Command Center (`attribution-ui`).
- Choose agency or business scope, selected clients, attribution model, and dry-run/live mode.
- Click Run. ARIE submits the `attribution-pipeline` Cloud Run Job and shows the equivalent `gcloud run jobs execute` command.

```bash
# On demand (execute-time --args override the deployed ones)
gcloud run jobs execute attribution-pipeline --region us-central1 \
  --args "flows/agency_flow.py,--agency,demo_agency,--dry-run" --wait

# Monthly schedule — test-fire it (⚠️ runs the deployed args: all agencies, LIVE)
gcloud scheduler jobs run attribution-monthly --location us-central1

# Locally (runs in-process, not via Cloud Run)
cd attribution_agent/attribution_agent
python flows/agency_flow.py --agency demo_agency
python flows/agency_flow.py --agency demo_agency --dry-run    # skip email
python flows/agency_flow.py --agency demo_agency --resume-run-id <id>   # resume from checkpoint
```

## Pilot launch sequence

Use this path for the first paid pilot.

```powershell
# 1. Confirm local GCP tooling, APIs, and required secrets.
.\scripts\arie_pilot_preflight.ps1 -ProjectId <project>

# 2. Seed global platform secrets from attribution_agent/attribution_agent/.env.
.\scripts\arie_seed_secrets.ps1 -ProjectId <project>

# 3. Deploy private UI, pipeline Job, scheduler, service accounts, and registry bucket.
.\scripts\arie_deploy_cloud_run.ps1 `
  -ProjectId <project> `
  -OperatorPrincipal user:you@example.com

# 4. Confirm deployed resources.
.\scripts\arie_pilot_preflight.ps1 -ProjectId <project> -AfterDeploy

# 5. Open the private operator UI locally.
gcloud run services proxy attribution-ui --project <project> --region us-central1 --port 8080
```

Then open `http://127.0.0.1:8080`, add the pilot client in ARIE, enter source
credentials, and save. ARIE writes per-client credential values to GCP Secret
Manager and stores only secret IDs in the registry.

Run the first Cloud Run dry run:

```powershell
.\scripts\arie_cloud_run_dry_run.ps1 `
  -ProjectId <project> `
  -AgencyId <agency_id> `
  -AttributionModel w_shape `
  -ClientIds <client_id>
```

Before sending a live report, verify:

- Cloud Run execution succeeded.
- `ops.pipeline_runs` has row counts, selected model, warnings, and email state.
- Client schema has refreshed raw, normalized, and attribution tables.
- HubSpot closed-won revenue is present and aligned with the report period.
- Report preview language explains the selected attribution model.
- `source_failures` is empty or understood.

Live-send command after dry-run approval:

```bash
gcloud run jobs execute attribution-pipeline \
  --project <project> \
  --region us-central1 \
  --args "flows/agency_flow.py,--agency,<agency_id>,--client-filter,<client_id>,--attribution-model,w_shape,--run-mode,agency" \
  --wait
```

## Health checks & logs

```bash
# UI
gcloud run services describe attribution-ui --region us-central1 --format 'value(status.url)'

# Job executions
gcloud run jobs executions list --job attribution-pipeline --region us-central1

# Logs
gcloud logging read 'resource.type=cloud_run_job AND resource.labels.job_name=attribution-pipeline' --limit 50
gcloud logging read 'resource.type=cloud_run_revision AND resource.labels.service_name=attribution-ui' --limit 50

# REST API (if deployed)
curl https://<api-host>/health                         # {"status":"ok", ...}
curl https://<api-host>/.well-known/agent-cards        # A2A discovery
```

A partial ingest is reported in the run summary as `"status": "partial"` with a
`source_failures` map naming each failed source (e.g. `pull-linkedin-ads`). The
run still completes with the sources that succeeded — investigate the named
source's credentials/quota rather than re-running everything.

## Client registry

Custom clients live in `clients.json` on the GCS bucket
(`gs://$PROJECT_ID-attribution-registry`), FUSE-mounted at `/mnt/registry` in
both workloads (`ATTRIBUTION_CLIENT_REGISTRY_BACKEND=local`).

```bash
gcloud storage cat gs://<project>-attribution-registry/clients.json
```

GCS FUSE has no concurrent-writer safety — fine at one UI instance plus the
scheduled Job, but don't add more writers.

## Dependency pinning (known-good build set)

Pinned in `attribution_agent/attribution_agent/requirements.txt`. Unbounded
`>=` pins are dangerous — a fresh build once pulled pandas 3.0 / numpy 2.4 /
databricks-sql-connector 4.x and broke. Keep these bounds:

- `google-ads==31.0.0`
- `protobuf>=4.25.0,<6`
- `websockets>=10,<13`
- `stripe>=7.0.0,<15`  (v15 dropped dict subclassing on StripeObject → connector's `.get()` access raises `AttributeError('get')`)

## Databricks decommission (post-migration)

After Cloud Run verification passes, pause/delete the old resources so nothing
fires them accidentally (ARIE's remote trigger is env-gated off via
`ATTRIBUTION_JOBS_API_ENABLED`, but the job itself should not stay live):

- Databricks Job `500226442246561` (`[Attribution] Monthly Pipeline`), if still active
- Databricks App `attribution-pipeline-ui`, if still active

Keep the SQL warehouse — it is still the data layer.
