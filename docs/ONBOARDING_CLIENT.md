# Onboarding a New Client

How to add a client (and, if needed, an agency) so the pipeline picks it up.
All config lives in `attribution_agent/attribution_agent/config/`.

## 1. Add the client config

Two ways to register a client:

- **Admin portal (preferred):** use the client manager in the Databricks App
  (Clients → Add client). Saves persist to the
  `attribution_ops.client_registry` Delta table, so they survive app
  redeploys and are visible to the pipeline Job. The REST API
  (`POST /clients`, admin key required) writes to the same table.
- **Code (base clients):** add a `ClientConfig` entry to `BASE_CLIENT_REGISTRY`
  in `config/client_config.py`.

Storage backend is auto-detected: the Delta table inside Databricks (App or
Job), a local JSON file (`client_registry.local.json`) elsewhere. Override with
`ATTRIBUTION_CLIENT_REGISTRY_BACKEND=delta|local` — e.g. export `delta` locally
to run the pipeline against clients created in the portal.

Minimal `ClientConfig` for a Meta + HubSpot + Stripe client:

```python
"acme_co": ClientConfig(
    client_id="acme_co",                 # short slug — also the Job widget value
    client_name="Acme Co",
    attribution_model="last_touch",      # last_touch|first_touch|linear|time_decay|u_shape|w_shape
    meta_enabled=True,
    meta_ad_account_id="act_123456789",  # Meta ad account ID (act_ prefix optional)
    hubspot_enabled=True,
    hubspot_pipeline_id="",              # blank = default pipeline
    stripe_enabled=True,
    databricks_schema=f"{_catalog()}.attribution_acme_co",  # one schema per client
    lookback_days=30,
    agency_id="acme_media",              # "" for a direct (non-agency) client
    client_report_email="reports@acme.co",
    client_display_name="Acme Co",
),
```

Per-source enable flags and IDs:

| Source | Enable flag | ID field |
|--------|-------------|----------|
| Meta | `meta_enabled` | `meta_ad_account_id` (`act_…`) |
| Google Ads | `google_ads_enabled` | `google_ads_customer_id` (numeric, no dashes) |
| LinkedIn | `linkedin_ads_enabled` | `linkedin_ads_account_id` (ID or `urn:li:sponsoredAccount:…`) |
| HubSpot | `hubspot_enabled` | `hubspot_pipeline_id` (blank = default) |
| Stripe | `stripe_enabled` | `stripe_account_id` (reference only) |

A source left disabled is skipped cleanly. A source that is enabled but fails at
runtime no longer aborts the run — it is recorded in the run summary's
`source_failures` and the status becomes `partial` (see RUNBOOK → Health checks).

## 2. Attach to an agency (optional)

For white-labeled agency reports, add the client to an `AgencyConfig` in
`config/agency_config.py` and set the client's `agency_id` to match:

```python
"acme_media": AgencyConfig(
    agency_id="acme_media",
    agency_name="Acme Media Group",
    client_ids=["acme_co"],             # list every client under this agency
    brand_color="2B5EA7",               # hex, no '#'
    brand_logo_url="https://cdn.acmemedia.com/logo.png",
    sender_name="Acme Media Analytics",
    sender_email="reports@acmemedia.com",   # overrides GMAIL_SENDER
    reply_to="analytics@acmemedia.com",
),
```

A client with `agency_id=""` is treated as direct and uses the default sender.

## 3. Provision credentials

Secrets are read from the Databricks **`attribution`** secret scope at runtime,
falling back to env vars locally. They are shared across clients — add a new key
only if the client needs a distinct credential. Core keys:

```bash
databricks secrets put --scope attribution --key META_ACCESS_TOKEN
databricks secrets put --scope attribution --key HUBSPOT_ACCESS_TOKEN
databricks secrets put --scope attribution --key STRIPE_SECRET_KEY
databricks secrets put --scope attribution --key GOOGLE_ADS_REFRESH_TOKEN
databricks secrets put --scope attribution --key LINKEDIN_ACCESS_TOKEN
databricks secrets put --scope attribution --key TIKTOK_ACCESS_TOKEN
databricks secrets put --scope attribution --key ANTHROPIC_API_KEY
databricks secrets put --scope attribution --key GMAIL_SENDER
databricks secrets put --scope attribution --key GMAIL_APP_PASSWORD
```

Google Ads also needs `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CLIENT_ID`,
`GOOGLE_ADS_CLIENT_SECRET`, and optionally `GOOGLE_ADS_LOGIN_CUSTOMER_ID`.

> Never paste tokens in chat or code. Locally, copy `.env.example` → `.env`.

## 4. Smoke test

```bash
cd attribution_agent/attribution_agent

# Dry run for just this client's agency (no email sent)
python flows/agency_flow.py --agency acme_media --dry-run

# Or ingest only
python -c "from flows.ingest_flow import ingest_flow; print(ingest_flow('acme_co'))"
```

Confirm the summary shows non-zero rows for each enabled source and
`"status": "complete"`. If `"status": "partial"`, check the named source in
`source_failures` (usually a missing/expired credential).
