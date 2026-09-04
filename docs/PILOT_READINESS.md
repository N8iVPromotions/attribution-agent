# ARIE Pilot Readiness

This is the operator checklist for moving ARIE from internal build to paid pilot.

## Current Canonical Runtime

- Command Center: Vercel `arie-portal`
- Pipeline trigger: Cloud Run Job `attribution-launcher`
- Pipeline workers: Cloud Run Job `attribution-pipeline`
- Data system of record: Databricks Delta, `workspace.attribution_ops`
- Secrets: GCP Secret Manager
- Alerts: Databricks `operator_alerts`, Telegram, optional Genie One webhook

## Verify Before Selling The First Pilot

1. Vercel environment variables include the GCP Workload Identity values:
   - `GCP_PROJECT_ID`
   - `GCP_PROJECT_NUMBER`
   - `GCP_SERVICE_ACCOUNT_EMAIL`
   - `GCP_WORKLOAD_IDENTITY_POOL_ID`
   - `GCP_WORKLOAD_IDENTITY_POOL_PROVIDER_ID`
   - `ARIE_CLOUD_RUN_REGION`
   - `ARIE_CLOUD_RUN_JOB=attribution-launcher`

2. GCP Secret Manager has current enabled credentials for N8iV:
   - Meta token version is enabled and not expired.
   - HubSpot token is present if HubSpot is enabled.
   - Stripe key is present only if Stripe enrichment should run.

3. Revenue identity contracts are satisfied:
   - N8iV's exact internal HubSpot closed-won stage IDs are saved on the client.
   - Every enabled ad account bills/reports in USD; ARIE does not convert FX.
   - Monetary HubSpot deals and Stripe PaymentIntents use USD. Missing or
     non-USD revenue currency must fail validation and suppress delivery.
   - `stripe_history_start_date` covers the earliest possible N8iV payment.
   - `stripe_account_id` exactly matches the account resolved by the saved key,
     and live delivery uses only live-mode keys and PaymentIntents.
   - Attributable Stripe PaymentIntents carry `metadata.hubspot_deal_id` or
     `metadata.deal_id`; customer email alone is not an attribution key.

4. Cloud Run jobs exist after deploy:
   - `attribution-launcher`
   - `attribution-pipeline`
   - `attribution-benchmark-finalizer`
   - `attribution-delta-maintenance`
   - `attribution-operator-health`

5. Cloud Scheduler jobs exist:
   - `attribution-monthly`
   - `attribution-weekly-maintenance`
   - `attribution-daily-health`

6. Run one N8iV preview from the Command Center:
   - Agency: `n8iv_promotions`
   - Client: `n8iv_promotions`
   - Dry run: enabled
   - Model: CRM Source Match
   - Report month: the previous completed New York calendar month
   - Lookback: 90 days

7. Confirm Databricks output after the dry run:
   - `workspace.attribution_ops.pipeline_runs`
   - `workspace.attribution_ops.pipeline_checkpoints`
   - `workspace.attribution_ops.operator_alerts`
   - `workspace.attribution_n8iv_promotions.ad_spend_normalized`
   - `workspace.attribution_n8iv_promotions.channel_performance`
   - Stripe validation shows nonzero exact deal-ID coverage when Stripe rows exist.
   - HubSpot paid-source detail values resolve only to the expected platform and
     exact campaign; unmatched revenue remains explicitly unattributed.
   - The report remains `generated` and the exact report ID, recipient, and
     delivery-config fingerprint appear in the approval queue; no email is sent.

## Known Follow-Ups Before Agency Owner Logins

- Replace Vercel Basic Auth with per-user authentication and tenant-scoped roles.
- Split the runtime service account into separate pipeline runner and secret writer identities.
- Move Cloud Run deployment from manual scripts into GitHub Actions with Workload Identity Federation.
- Add agency-facing terms, support, and billing workflow before external self-serve access.
