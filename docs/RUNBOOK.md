# Operator Runbook

Operational reference for running, deploying, and recovering the attribution
platform. For day-to-day development see the repo root `CLAUDE.md`.

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

Stored in GCP Secret Manager, injected into both workloads as env vars via
`--set-secrets` (see the `SECRET_KEYS` list in `deploy.sh`). Code reads
`os.environ` only.

```bash
# Seed/rotate from local .env (updates create new secret versions)
PROJECT_ID=<project> ./deploy.sh --seed-secrets
```

Rotation note: workloads reference `:latest`. The Job picks up new values on
its next execution; the **UI needs a redeploy** (or instance recycle) to see
rotated secrets.

## Deploy

```bash
# From repo root, in Git Bash. Builds via Cloud Build (no local Docker needed),
# then deploys Job + UI + Scheduler. Idempotent — safe to re-run.
PROJECT_ID=<project> ./deploy.sh
```

Rollback: images are tagged with the git SHA. Redeploy a known-good one:

```bash
gcloud run jobs deploy attribution-pipeline --region us-central1 --image "<IMAGE>:<old-sha>"
gcloud run deploy attribution-ui --region us-central1 --image "<IMAGE>:<old-sha>"
# Or roll the service back without rebuilding:
gcloud run services update-traffic attribution-ui --region us-central1 --to-revisions <revision>=100
```

## Run the pipeline

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

## Databricks decommission (post-migration)

After Cloud Run verification passes, pause/delete the old resources so nothing
fires them accidentally (ARIE's remote trigger is env-gated off via
`ATTRIBUTION_JOBS_API_ENABLED`, but the job itself should not stay live):

- Databricks Job `500226442246561` (`[Attribution] Monthly Pipeline`)
- Databricks App `attribution-pipeline-ui`

Keep the SQL warehouse — it is still the data layer.
