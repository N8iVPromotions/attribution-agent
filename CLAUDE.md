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

## Key Config Files

- `config/agency_config.py` — agency registry (branding, client list)
- `config/client_config.py` — client registry (data sources, report email)
- `.env` — secrets (GMAIL_SENDER, GMAIL_APP_PASSWORD, Databricks tokens)
