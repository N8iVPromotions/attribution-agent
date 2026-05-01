# Attribution Agent

Automated closed-loop attribution pipeline for Meta Ads + HubSpot → Databricks Delta.

## Project Structure

```
attribution_agent/
├── .env.example              ← Copy to .env and fill in credentials
├── requirements.txt
├── config/
│   └── client_config.py      ← Add new clients here
├── agents/
│   └── ingest/
│       ├── meta_connector.py   ← Meta Marketing API
│       ├── hubspot_connector.py← HubSpot CRM API
│       └── validator.py        ← Data quality checks
├── utils/
│   └── databricks_writer.py  ← Delta table upserts
└── flows/
    └── ingest_flow.py        ← Prefect orchestration (run this)
```

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure credentials
cp .env.example .env
# Fill in META_ACCESS_TOKEN, HUBSPOT_ACCESS_TOKEN, DATABRICKS_* vars

# 3. Add your client in config/client_config.py
# Update CLIENT_REGISTRY with your client's ad account ID, pipeline ID, etc.

# 4. Run for a single client
python flows/ingest_flow.py --client demo_client

# 5. Run for all clients
python flows/ingest_flow.py
```

## Scheduling with Prefect

```bash
# Start Prefect server (local)
prefect server start

# Build + deploy with monthly schedule (1st of month, 6am)
prefect deployment build flows/ingest_flow.py:ingest_all_clients \
    --name "attribution-ingest-monthly" \
    --cron "0 6 1 * *"

prefect deployment apply ingest_all_clients-deployment.yaml

# Start a Prefect worker to execute runs
prefect worker start --pool default-agent-pool
```

## Databricks Tables Created

Per client schema (e.g. `attribution_demo_client`):
- `meta_ads_raw` — daily campaign-level spend, clicks, impressions, conversions
- `hubspot_deals_raw` — deals with associated contact UTM / source attribution

## Adding a New Client

1. Open `config/client_config.py`
2. Add an entry to `CLIENT_REGISTRY`
3. Set `META_AD_ACCOUNT_ID` and `HUBSPOT_PIPELINE_ID` as needed
4. Run `python flows/ingest_flow.py --client your_new_client`

The schema and tables are created automatically on first run.
