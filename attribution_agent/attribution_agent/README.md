# ARIE

Automatic Revenue Intelligence Engine. ARIE is a managed B2B attribution
service for connecting paid media touchpoints to closed-won CRM revenue. The v1
production path is:

- Vercel-hosted ARIE Command Center for operator runs and client onboarding.
- Cloud Run Job for scheduled/one-off attribution execution.
- GCP Secret Manager for global platform secrets and per-client source
  credentials.
- Databricks SQL/Delta as the durable data and reporting layer.
- Email reports as the client-facing output.

## What Runs

```text
api/                      FastAPI admin/reporting surface
flows/job_launcher.py     Cloud Run launcher for task-array executions
flows/agency_flow.py      Agency/client pipeline orchestration
flows/ingest_flow.py      Source ingest, validation, normalization, attribution
attribution_engine.py     First, last, linear, time-decay, U, and W models
agents/ingest/            Meta, Google Ads, LinkedIn, HubSpot, Stripe connectors
utils/databricks_writer.py Databricks SQL writes and ops tables
utils/secrets.py          Env + GCP Secret Manager secret resolution
```

## Local Setup

```bash
cd attribution_agent/attribution_agent
pip install -r requirements.txt
cp .env.example .env
```

For local Secret Manager access, authenticate with application default
credentials and set `GOOGLE_CLOUD_PROJECT`. Without GCP access, source tokens
fall back to matching `.env` keys such as `META_ACCESS_TOKEN`.

## Add a Client

Use the Vercel Command Center Tenants flow. The form captures account IDs,
report settings, default attribution model, and source credentials. Credential
values are written to GCP Secret Manager; only secret IDs are stored in client
config.

See [docs/ONBOARDING_CLIENT.md](../../docs/ONBOARDING_CLIENT.md) for the full
operator flow.

## Run

```bash
# Single-client ingest/attribution smoke test
python flows/ingest_flow.py --client demo_client

# Agency dry run, no email delivery
python flows/agency_flow.py --agency demo_agency --dry-run

# All configured clients
python flows/agency_flow.py --dry-run
```

## Deploy

From the repository root:

```bash
PROJECT_ID=<project> ./deploy.sh --seed-secrets
PROJECT_ID=<project> ./deploy.sh
```

`deploy.sh` builds the image, deploys the `attribution-api` control service,
deploys the launcher/pipeline Cloud Run Jobs, mounts the GCS-backed client
registry, and creates the Cloud Scheduler monthly trigger. The operator UI is
deployed separately from `arie-portal` to Vercel.
