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

## Databricks Deployment

The pipeline runs as a Databricks Job and the UI is hosted as a Databricks App.
Both are defined in `databricks.yml` (Databricks Asset Bundle).

### Secrets — never paste tokens in chat or code
All credentials live in `attribution_agent/attribution_agent/.env` (gitignored).
Copy `.env.example` → `.env` and fill in real values before running anything locally.
For CI / cloud sessions, export them as environment variables:
```bash
export DATABRICKS_HOST=https://8259555645755006.6.gcp.databricks.com
export DATABRICKS_TOKEN=<from .env>
```

### First-time secret setup (run once)
```bash
# Install the Databricks CLI v2 (Asset Bundles)
curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh

# Configure auth — reads DATABRICKS_HOST + DATABRICKS_TOKEN from env, or prompts interactively
databricks auth login

# Push all secrets into the "attribution" scope
databricks secrets create-scope attribution
databricks secrets put --scope attribution --key META_ACCESS_TOKEN
databricks secrets put --scope attribution --key HUBSPOT_ACCESS_TOKEN
databricks secrets put --scope attribution --key ANTHROPIC_API_KEY
databricks secrets put --scope attribution --key GMAIL_SENDER
databricks secrets put --scope attribution --key GMAIL_APP_PASSWORD
databricks secrets put --scope attribution --key STRIPE_SECRET_KEY
databricks secrets put --scope attribution --key GOOGLE_ADS_REFRESH_TOKEN
databricks secrets put --scope attribution --key LINKEDIN_ACCESS_TOKEN
databricks secrets put --scope attribution --key TIKTOK_ACCESS_TOKEN
```

### Deploy (run after merging to main)
```bash
# From repo root — credentials are picked up from env vars or ~/.databrickscfg
databricks bundle deploy

# Verify
databricks bundle run attribution_pipeline --dry-run
```

### Run the Job on demand
```bash
databricks bundle run attribution_pipeline
```

### Local dev
```bash
cd attribution_agent/attribution_agent
streamlit run app.py   # runs pipeline in-process, not via Jobs API
```

## Key Config Files

- `config/agency_config.py` — agency registry (branding, client list)
- `config/client_config.py` — client registry (data sources, report email)
- `.env` — secrets (GMAIL_SENDER, GMAIL_APP_PASSWORD, Databricks tokens)
