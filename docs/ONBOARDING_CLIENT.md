# Onboarding a New Client in ARIE

Use this when adding a direct B2B client or an agency sub-account for the
ARIE Vercel Command Center.

## 1. Add the client

Preferred path:

- Open the Vercel ARIE Command Center.
- Go to Tenants.
- Choose Add client.
- Enter business identity, agency, CRM Source Match, lookback window, report
  email, source account IDs, and source credentials. The system derives safe
  Databricks schema names from validated tenant IDs.
- Save.

The Command Center submits tenant lifecycle requests to Databricks. The
pipeline reads the resulting tenant/client registry records from the durable
ops schema.

Base clients can still be added in code through `BASE_CLIENT_REGISTRY` in
`attribution_agent/attribution_agent/config/client_config.py`, but that should
be reserved for demo/internal defaults.

## 2. Configure sources

| Source | Enable flag | Account field | Credential captured |
|---|---|---|---|
| Meta | `meta_enabled` | `meta_ad_account_id` (`act_...`) | Access token |
| Google Ads | `google_ads_enabled` | `google_ads_customer_id` | Refresh token |
| LinkedIn Ads | `linkedin_ads_enabled` | `linkedin_ads_account_id` | Access token |
| HubSpot | `hubspot_enabled` | `hubspot_pipeline_id`; exact closed-won stage IDs | Access token |
| Stripe | `stripe_enabled` | `stripe_account_id`; earliest payment-history date | Secret key |

For HubSpot, enter the pipeline's exact internal closed-won stage IDs. The
standard defaults are `closedwon` and `won`; custom pipelines commonly use
different internal IDs. ARIE normalizes and matches only the configured IDs so
similarly named non-won stages cannot be counted.

For Stripe, every attributable PaymentIntent must include the exact HubSpot
deal ID in `metadata.hubspot_deal_id` (preferred) or `metadata.deal_id`. Email
addresses are diagnostic only and are never used to infer a revenue join. Set
the history start date to the earliest possible payment so late refunds and
payments for the reporting month's won deals are refreshed correctly.

ARIE currently has a strict USD-only reporting contract; it does not perform
foreign-exchange conversion. Before enabling a source, verify that every ad
account reports spend in USD, HubSpot monetary deals use `USD`, and Stripe
PaymentIntents use `usd`. Missing or non-USD CRM/payment currency fails source
validation and suppresses live delivery. Treat a currency migration as a new
onboarding review rather than combining unlike amounts in one report.

Credential values entered in the UI are never stored in the client
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
gcloud run jobs execute attribution-launcher \
  --region us-central1 \
  --args "flows/job_launcher.py,--agency,acme_media,--dry-run,--attribution-model,last_touch,--report-month,2026-08" \
  --wait
```

Local dry run:

```bash
cd attribution_agent/attribution_agent
python flows/agency_flow.py --agency acme_media --dry-run --report-month 2026-08
```

For local runs against Secret Manager, authenticate with application default
credentials and set `GOOGLE_CLOUD_PROJECT`. Without GCP access, the code falls
back to matching env vars such as `META_ACCESS_TOKEN` for local smoke tests.
