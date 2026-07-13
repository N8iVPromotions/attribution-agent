# Onboarding a New Client in ARIE

Use this when adding a direct B2B client or an agency sub-account for the
ARIE Cloud Run command center.

## 1. Add the client

Preferred path:

- Open the Cloud Run Streamlit UI (`attribution-ui`).
- Go to Clients.
- Choose Add client.
- Enter business identity, agency, default attribution model, lookback window,
  Databricks schema, report email, source account IDs, and source credentials.
- Save.

The command center writes client config to the registry used by the pipeline.
In the deployed GCP path, `deploy.sh` mounts a GCS bucket at
`/mnt/registry` and stores the registry at `/mnt/registry/clients.json`.
The REST API (`POST /clients`, admin key required) writes through the same
client-config path.

Base clients can still be added in code through `BASE_CLIENT_REGISTRY` in
`attribution_agent/attribution_agent/config/client_config.py`, but that should
be reserved for demo/internal defaults.

## 2. Configure sources

| Source | Enable flag | Account field | Credential captured |
|---|---|---|---|
| Meta | `meta_enabled` | `meta_ad_account_id` (`act_...`) | Access token |
| Google Ads | `google_ads_enabled` | `google_ads_customer_id` | Refresh token |
| LinkedIn Ads | `linkedin_ads_enabled` | `linkedin_ads_account_id` | Access token |
| HubSpot | `hubspot_enabled` | `hubspot_pipeline_id` | Access token |
| Stripe | `stripe_enabled` | `stripe_account_id` | Secret key |

Credential values entered in the UI or API are never stored in the client
registry. On save, the app creates or updates a per-client GCP Secret Manager
secret and stores only the secret ID on `ClientConfig`.

Default secret IDs follow:

```text
attr-prod-<client-id>-<credential-name>
```

You can change the prefix with `ATTRIBUTION_CLIENT_SECRET_PREFIX` and the
environment segment with `ATTRIBUTION_ENV`.

When editing a client, leave a credential field blank to keep the existing
stored secret reference.

## 3. Seed global platform secrets

Per-client source credentials are handled by the command center. Global
platform credentials still come from `.env` and `deploy.sh --seed-secrets`:

```bash
PROJECT_ID=my-project ./deploy.sh --seed-secrets
```

Common global keys include:

```bash
DATABRICKS_SERVER_HOSTNAME
DATABRICKS_HTTP_PATH
DATABRICKS_TOKEN
ANTHROPIC_API_KEY
SENDGRID_API_KEY
GMAIL_SENDER
GMAIL_APP_PASSWORD
GOOGLE_ADS_DEVELOPER_TOKEN
GOOGLE_ADS_CLIENT_ID
GOOGLE_ADS_CLIENT_SECRET
GOOGLE_ADS_LOGIN_CUSTOMER_ID
API_KEY_ADMIN
```

Google Ads uses the global developer/client credentials plus the per-client
refresh token captured during onboarding.

## 4. Attach to an agency

For agency reporting, set the client's `agency_id` in the command center.
Agency metadata still lives in `config/agency_config.py` for v1:

```python
"acme_media": AgencyConfig(
    agency_id="acme_media",
    agency_name="Acme Media Group",
    client_ids=["acme_co"],
    brand_color="2B5EA7",
    brand_logo_url="https://cdn.acmemedia.com/logo.png",
    sender_name="Acme Media Analytics",
    sender_email="reports@acmemedia.com",
    reply_to="analytics@acmemedia.com",
),
```

## 5. Smoke test

Cloud Run dry run:

```bash
gcloud run jobs execute attribution-pipeline \
  --region us-central1 \
  --args "flows/agency_flow.py,--agency,acme_media,--dry-run" \
  --wait
```

Local dry run:

```bash
cd attribution_agent/attribution_agent
python flows/agency_flow.py --agency acme_media --dry-run
```

For local runs against Secret Manager, authenticate with application default
credentials and set `GOOGLE_CLOUD_PROJECT`. Without GCP access, the code falls
back to matching env vars such as `META_ACCESS_TOKEN` for local smoke tests.
