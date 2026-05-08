# Attribution Agent — Claude Code Instructions

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

Attribution pipeline for marketing agencies. Ingests Meta Ads, HubSpot, and Stripe data into Databricks, generates AI-powered insight reports via Claude, and emails white-labeled reports to clients.

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

### First-time secret setup (run once)
```bash
# Install the Databricks CLI
pip install databricks-cli

# Configure auth
databricks configure --token
# Host: https://8259555645755006.6.gcp.databricks.com
# Token: <your DATABRICKS_TOKEN from .env>

# Push all secrets into the "attribution" scope
databricks secrets create-scope attribution
databricks secrets put --scope attribution --key META_ACCESS_TOKEN
databricks secrets put --scope attribution --key HUBSPOT_ACCESS_TOKEN
databricks secrets put --scope attribution --key ANTHROPIC_API_KEY
databricks secrets put --scope attribution --key GMAIL_SENDER
databricks secrets put --scope attribution --key GMAIL_APP_PASSWORD
databricks secrets put --scope attribution --key STRIPE_SECRET_KEY
```

### Deploy (run after merging to main)
```bash
# Install the Databricks CLI v2 (Asset Bundles)
pip install databricks-cli

# Deploy the Job + App to production
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
