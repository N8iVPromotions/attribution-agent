# Attribution Agent — Claude Code Instructions

## Documentation

- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — deploy, rollback, health checks, log access, common failures
- [`docs/ONBOARDING_CLIENT.md`](docs/ONBOARDING_CLIENT.md) — add a new client/agency and provision secrets
- [`docs/A2A.md`](docs/A2A.md) — agent-to-agent local & network dispatch

## Git Workflow

All changes must go through a pull request. Never push directly to `main`.

**Branch naming:**
- `feature/short-description` — new features
- `fix/short-description` — bug fixes
- `chore/short-description` — config, deps, non-code changes

**Process for every change:**
1. Create a branch: `git checkout -b feature/your-description`
2. Make changes and commit
3. Push the branch: `git push -u origin feature/your-description`
4. Open a PR targeting `main`

A pre-push hook blocks direct pushes to `main`.

## Project Overview

Attribution pipeline for marketing agencies. Ingests Meta, Google, LinkedIn, and TikTok Ads plus HubSpot and Stripe data into Databricks, generates AI-powered insight reports via Claude, and emails white-labeled reports to clients.

**Entry point:** `attribution_agent/attribution_agent/flows/agency_flow.py`

Run the pipeline:
```
cd attribution_agent/attribution_agent
python flows/agency_flow.py --agency demo_agency
python flows/agency_flow.py --agency demo_agency --dry-run   # skip email
```

## GCP Cloud Run Deployment

One container image (repo-root `Dockerfile`, `python:3.11-slim`) serves both workloads:
- **Cloud Run Job** `attribution-pipeline` — `python flows/agency_flow.py`, triggered nightly by Cloud Scheduler (`attribution-nightly`)
- **Cloud Run Service** `attribution-ui` — Streamlit on port 8080 (default CMD)

The **data layer stays on Databricks**: Delta writes go through
`utils/databricks_writer.py` via `databricks-sql-connector` to the SQL
warehouse over public HTTPS (`DATABRICKS_HOST` / `DATABRICKS_HTTP_PATH` /
`DATABRICKS_TOKEN`). Compute moved off Databricks because its Serverless
Egress Gateway blocks DNS to api.stripe.com / graph.facebook.com /
api.hubapi.com.

### Secrets — never paste tokens in chat or code
All credentials live in `attribution_agent/attribution_agent/.env` (gitignored).
Copy `.env.example` → `.env` and fill in real values before running anything locally.
In the cloud, secrets live in GCP Secret Manager and Cloud Run injects them as
env vars (`--set-secrets`). Code reads them from `os.environ` only
(`utils/secrets.py`).

```bash
# One-time: push .env values into Secret Manager
PROJECT_ID=<project> ./deploy.sh --seed-secrets
```

### Deploy (run after merging to main)
```bash
# From repo root, in Git Bash — builds via Cloud Build, deploys Job + UI + Scheduler
PROJECT_ID=<project> ./deploy.sh
```

### Run the Job on demand
```bash
gcloud run jobs execute attribution-pipeline --region us-central1 \
  --args "flows/agency_flow.py,--agency,demo_agency,--dry-run" --wait
```

### Local dev
```bash
cd attribution_agent/attribution_agent
streamlit run app.py   # runs pipeline in-process, not via Cloud Run
```

## Key Config Files

- `Dockerfile` + `deploy.sh` — container build and full GCP deploy (Artifact
  Registry, Secret Manager seed, Cloud Run Job/Service, Cloud Scheduler)
- `config/agency_config.py` — agency registry (branding, client list)
- `config/client_config.py` — client registry (data sources, report email).
  On Cloud Run, custom clients added via the app's admin portal persist to
  `clients.json` on a GCS bucket mounted at `/mnt/registry`
  (`ATTRIBUTION_CLIENT_REGISTRY_BACKEND=local`); the Delta-table backend
  still exists behind `ATTRIBUTION_CLIENT_REGISTRY_BACKEND`
- `.env` — secrets (GMAIL_SENDER, GMAIL_APP_PASSWORD, Databricks tokens)
