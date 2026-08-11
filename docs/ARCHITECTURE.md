# ARIE Architecture

ARIE is a managed B2B attribution system. The current production architecture
uses Vercel for the operator UI, Cloud Run for execution, GCP Secret Manager for
credentials, and Databricks SQL/Delta for persistence.

## System Context

```mermaid
flowchart TD
    UI["Vercel Command Center<br/>arie-portal"]
    SCHED["Cloud Scheduler<br/>attribution-monthly"]
    CLI["Operator CLI / gcloud"]

    subgraph GCP["GCP Cloud Run"]
        LAUNCHER["Job: attribution-launcher<br/>flows/job_launcher.py"]
        PIPELINE["Task-array Job: attribution-pipeline<br/>flows/agency_flow.py"]
        FINALIZER["Job: attribution-benchmark-finalizer"]
        MAINT["Job: attribution-delta-maintenance"]
        API["Service: attribution-api<br/>FastAPI control surface"]
    end

    subgraph DATA["Databricks SQL / Delta"]
        OPS["workspace.attribution_ops"]
        CLIENT["Per-client schemas"]
    end

    subgraph SECRETS["GCP Secret Manager"]
        GLOBAL["Global platform secrets"]
        CLIENTCREDS["Per-client source credentials"]
    end

    subgraph SOURCES["External Sources"]
        META["Meta Ads"]
        GOOGLE["Google Ads"]
        LINKEDIN["LinkedIn Ads"]
        HUBSPOT["HubSpot"]
        STRIPE["Stripe"]
    end

    UI -- Vercel OIDC --> LAUNCHER
    UI -- optional admin API --> API
    SCHED --> LAUNCHER
    CLI --> LAUNCHER
    LAUNCHER --> PIPELINE
    PIPELINE --> FINALIZER
    SCHED --> MAINT
    SECRETS --> PIPELINE
    PIPELINE --> SOURCES
    SOURCES --> PIPELINE
    PIPELINE --> CLIENT
    PIPELINE --> OPS
    FINALIZER --> OPS
    MAINT --> OPS
```

## Operator UI

`arie-portal/` is the only supported command center. It provides:

- Agency and tenant overview.
- Guarded dry-run/live attribution execution.
- Tenant lifecycle requests.
- Run history, warnings, alerts, report previews, and governance views.
- Server-side Databricks reads.
- Server-side Cloud Run job execution through Vercel OIDC and GCP Workload
  Identity Federation.

The browser never receives Databricks, GCP, or ARIE API credentials.

## Execution Flow

```mermaid
flowchart TD
    START["Operator submits run from Vercel"]
    VALIDATE["Validate agency, clients, model, dry-run/live confirmation"]
    LAUNCH["Run attribution-launcher"]
    MANIFEST["Create work manifest"]
    WORKERS["Run attribution-pipeline task array"]
    INGEST["Ingest ad, CRM, and payment sources"]
    ATTR["Apply selected attribution model"]
    WRITE["Write attributed revenue and ops tables"]
    REPORT["Generate report preview / email when live"]
    FINALIZE["Run benchmark finalizer"]
    DONE["Command Center shows updated state"]

    START --> VALIDATE --> LAUNCH --> MANIFEST --> WORKERS
    WORKERS --> INGEST --> ATTR --> WRITE --> REPORT --> FINALIZE --> DONE
```

## Data Layer

Databricks remains the durable system of record:

- `workspace.attribution_ops` stores run history, checkpoints, reports,
  approvals, alerts, audit events, cost ledger entries, client registry records,
  and tenant lifecycle state.
- Per-client schemas store raw source data, normalized ad data, attribution
  outputs, and channel performance tables.

## Credential Boundary

- Vercel uses Basic Auth for the internal pilot UI.
- Vercel-to-GCP execution uses Workload Identity Federation, not service-account
  JSON keys.
- Global platform secrets and per-client source credentials live in GCP Secret
  Manager.
- Client registry records store secret IDs, not credential values.

## Retired UI Paths

The Streamlit command center, Replit command center, desktop launcher, and
static prototype have been retired. New UI work should go into `arie-portal/`.
