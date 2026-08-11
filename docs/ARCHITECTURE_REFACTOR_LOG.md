# Architecture Refactor Log

This log records architecture hardening work for scaling ARIE to 15–20
multi-tenant agency clients. It is the operational reference for what changed,
why it changed, rollout requirements, and verification evidence.

## 2026-08-09 — Scaling hardening tranche 1

### Scope

- Cloud Run task-indexed client execution.
- Distributed per-client execution leases.
- Accurate partial-run status and report-delivery policy.
- Bounded Databricks SQL staging and date-pruned Delta merges.
- Dataset- and prompt-versioned model cache keys.
- Daily, monthly, and per-run model spend controls.
- Durable raw API page archival in GCS.

### Architecture decisions

1. Cloud Run tasks map deterministically to sorted `(agency_id, client_id)`
   work items. Extra tasks exit successfully; an undersized task count fails
   before processing to avoid silently skipping clients.
2. Array tasks do not run cross-client benchmark transforms. Those transforms
   remain enabled for sequential runs and must move to a follow-up finalizer
   before array execution becomes the only production path.
3. GCS conditional object creation is the distributed lock primitive. A lock
   is scoped to `client_id`, carries an expiry, and is released with a matching
   object generation.
4. Partial ingestion is persisted as `partial`. Live email delivery is blocked
   by default unless `ARIE_ALLOW_PARTIAL_REPORT_DELIVERY=true`.
5. Date predicates are added only to MERGEs whose logical key includes the
   date. ID-only HubSpot and Stripe MERGEs remain global to avoid duplicate
   inserts for updated historical records.
6. Raw vendor responses are archived by page before application parsing when
   the connector exposes response bytes. SDK/protobuf connectors archive the
   closest lossless provider representation available before normalization.
7. Model cache identity includes tenant, prompt content/version, response
   schema, and dataset version. TTL remains a cleanup mechanism, not the
   primary invalidation mechanism.
8. Model limits are evaluated before a provider call. Budget-store failures
   remain configurable for local development but fail closed in production.

### Rollout flags and environment

| Variable | Default | Purpose |
|---|---:|---|
| `ARIE_CLIENT_LOCKS_ENABLED` | `true` on Cloud Run | Enable GCS client leases |
| `ARIE_CLIENT_LOCK_TTL_SECONDS` | `300` | Client lease lifetime |
| `ARIE_ALLOW_PARTIAL_REPORT_DELIVERY` | `false` | Permit live email from partial data |
| `ARIE_RAW_ARCHIVE_BUCKET` | unset | Enables raw page archival when configured |
| `ARIE_MODEL_BUDGET_FAIL_CLOSED` | `true` on Cloud Run | Block model calls when budget state is unavailable |
| `ARIE_DAILY_AI_USD_LIMIT` | `25` | Default agency daily AI spend limit |
| `ARIE_MONTHLY_AI_USD_LIMIT` | `300` | Default agency monthly AI spend limit |
| `PIPELINE_TASKS` | `20` | Deployed Cloud Run task count |
| `PIPELINE_PARALLELISM` | `5` | Maximum concurrently running client tasks |

### Migration and rollout notes

- Keep Cloud Run Job retries at zero until email idempotency is deployed and
  verified.
- The job's configured task count must be at least the number of active work
  items. A persisted run manifest remains the next hardening step if client
  registry edits must be allowed during an execution.
- Raw archive objects should receive a lifecycle policy appropriate to client
  contracts and privacy requirements.
- Existing Delta tables are not automatically converted from date partitions
  to Liquid Clustering in this tranche. That migration requires a measured,
  reversible table-by-table rollout.
- Claude list prices were checked on 2026-08-09. Haiku 4.5 is accounted at
  $1/$5 per million input/output tokens and Sonnet 4.6 at $3/$15. Five-minute
  prompt-cache writes and reads are also included at 1.25x and 0.10x the model
  input price. See Anthropic's
  [current model price sheet](https://www-cdn.anthropic.com/files/4zrzovbb/website/5678bc2f5978e5bcd4f1fe7c14b2c72284dcf9f8.pdf)
  before changing routed models.

### Implementation map

| Area | Files | Implemented behavior |
|---|---|---|
| Task isolation | `flows/agency_flow.py`, deployment scripts | One deterministic client assignment per Cloud Run task; extra tasks return `no_work`; undersized arrays fail |
| Run exclusion | `utils/client_lock.py` | GCS generation-guarded client lease with TTL heartbeat and generation-matched release |
| Partial runs | `flows/ingest_flow.py`, `flows/agency_flow.py`, `utils/checkpoint.py` | Source failures remain non-fatal, persist as `partial`, block delivery by default, and retain source failure details across resume |
| Raw archive | `utils/raw_archive.py`, six ingest connectors | Gzipped, checksummed, create-only vendor pages under source/client/date/run object prefixes |
| Delta write path | `utils/databricks_writer.py` | SQL Connector staging is sent in 1,000-row batches; date-partitioned MERGEs receive target-side min/max date predicates |
| Model controls | `utils/model_gateway.py`, `config/budget_config.py` | Tenant/prompt/schema/data-version cache identity plus pre-call daily, monthly, and per-run limits |
| Regression gate | `evals/`, `.github/workflows/ci.yml` | AI behavior changes trigger the golden-dataset runner and empty sample sets fail the gate |
| Deployment | `deploy.sh`, `scripts/arie_deploy_cloud_run.ps1` | 20 tasks, parallelism 5, raw archive bucket/lifecycle, lock configuration, budget flags, and deployed Git SHA |

The formatter also normalized five pre-existing Python files required by the
repository's pinned Ruff CI check. Those edits are layout-only and do not
change runtime behavior.

### Verification evidence

- `ruff format --check attribution_agent/attribution_agent/` — 95 files
  already formatted.
- `ruff check attribution_agent/attribution_agent/` — all checks passed.
- `pytest attribution_agent/attribution_agent/tests -q` — 130 passed, one
  upstream Starlette/httpx deprecation warning.
- GitHub Actions workflow YAML parsed successfully.
- `scripts/arie_deploy_cloud_run.ps1` parsed successfully with the PowerShell
  AST parser.
- `deploy.sh` passed `bash -n` using Git Bash.

### Deferred gaps for tranche 2

1. Connectors still aggregate each source into pandas DataFrames. Raw archival
   improves replayability but does not by itself bound connector memory. Add a
   page iterator contract and write validated Arrow/Delta micro-batches during
   historical backfills.
2. The task assignment list is rebuilt from the registry in every task. Store
   an immutable execution manifest before allowing registry changes during an
   active execution, and add a finalizer task for agency benchmark transforms.
3. Email has no provider-level idempotency key. Job retries therefore remain
   zero until delivery receipts and an outbox/idempotency table are implemented.
4. The model budget check is a ledger read followed by a provider call and a
   ledger write. Parallel tasks can overshoot by their in-flight requests.
   Introduce an atomic reservation/settlement record before raising task
   parallelism or spend limits.
5. Existing Delta tables do not yet enable deletion vectors or Liquid
   Clustering, and no scheduled `OPTIMIZE`/`VACUUM` maintenance job was added.
   Benchmark table sizes and query profiles before choosing those settings.
6. Seed at least one reviewed active golden sample for every gated agent before
   merging AI behavior changes. Expand evaluators beyond field presence and
   numeric tolerance to cover narrative accuracy and policy compliance.
7. Stripe archival uses the SDK's recursive object representation because the
   connector does not expose response bytes. Capture the exact wire response if
   contractual audit requirements demand byte-for-byte source payloads.

## 2026-08-09 — Tranche 2 implementation

### 2.1 Page-to-Arrow streaming — complete

**Diff highlights**

- Added `agents/ingest/batches.py` with a configurable, enforced 5,000–10,000
  row `pyarrow.RecordBatch` contract.
- Added generator APIs to Meta, Google Ads, LinkedIn, TikTok, HubSpot, and
  Stripe connectors. Legacy `pull_*_data()` functions remain DataFrame adapters
  for local/demo compatibility.
- HubSpot now resolves associated contacts per deal page; Stripe no longer
  retains prior payment-intent pages.
- Added a Cloud Run-default streaming branch in `flows/ingest_flow.py` that
  validates and writes one batch at a time. The local path remains unchanged
  unless `ARIE_STREAMING_INGEST=true` is set.

**Affected files**

- `agents/ingest/batches.py`, all six connector modules, `validator.py`
- `flows/ingest_flow.py`, `utils/databricks_writer.py`, `requirements.txt`
- `tests/test_streaming_ingest.py`

**Verification**

- 39 streaming, connector, isolation, and Databricks writer tests passed.
- Ruff formatting and lint checks passed for the affected Python files.

### 2.2 Atomic AI budget reservations — complete

**Diff highlights**

- Added `utils/budget_reservation.py`, which stores monthly agency budget state
  in GCS and updates it with object-generation compare-and-swap retries.
- Pre-call reservations include all active in-flight estimates in daily and
  monthly cap decisions. Anthropic failures cancel capacity; successful calls
  settle estimated cost to provider-reported actual cost before ledger writes.
- Unsettled reservations remain conservative for 24 hours by default. Local
  execution without `ARIE_AI_BUDGET_BUCKET` retains the existing ledger check.

**Affected files**

- `utils/budget_reservation.py`, `utils/model_gateway.py`
- `tests/test_budget_reservation.py`, `tests/test_model_gateway.py`

**Verification**

- 8 budget reservation and model gateway tests passed.
- Ruff formatting and lint checks passed for the affected Python files.

### 2.3 Work manifest launcher and benchmark finalizer — complete

**Diff highlights**

- Added an immutable GCS work manifest containing contiguous task indexes and
  unique `(agency_id, client_id)` assignments. Creation uses
  `if_generation_match=0`.
- Added `flows/job_launcher.py`. It snapshots the manifest, invokes the tenant
  job with a matching `taskCount` override, waits for every task to succeed,
  and only then invokes the finalizer.
- Added `flows/benchmark_finalizer.py`. Tenant tasks now set
  `run_benchmarks=False`; the finalizer runs each affected agency benchmark
  once and fails fast. Dry runs explicitly skip benchmark writes.
- Both deployment scripts now schedule the launcher instead of the tenant job
  and grant the runtime identity `run.jobs.runWithOverrides` through
  `roles/run.developer` on the two downstream jobs.

**Affected files**

- `utils/work_manifest.py`, `flows/job_launcher.py`
- `flows/benchmark_finalizer.py`, `flows/agency_flow.py`
- `deploy.sh`, `scripts/arie_deploy_cloud_run.ps1`
- `tests/test_work_manifest.py`

**Verification**

- Unit coverage confirms duplicate client assignments are rejected, tenant
  tasks read the immutable manifest, task-count/environment overrides are
  applied, and the dry-run finalizer does not write benchmarks.

### 2.4 Report email idempotency — complete

**Diff highlights**

- Added the deterministic key
  `report:{client_id}:{report_month}:{attribution_model}` for every email
  dispatch.
- Cloud runs claim the key in GCS with `if_generation_match=0` before calling
  the email provider. A successful provider call retains the claim; a
  definitive provider failure releases it for retry. This deliberately favors
  at-most-once delivery when the provider outcome is ambiguous.
- The local SQLite/demo path uses a process-local claim and requires no GCS.
  With the cloud claim in place, tenant job retries are set to one.

**Affected files**

- `utils/idempotency.py`, `flows/agency_flow.py`
- `deploy.sh`, `scripts/arie_deploy_cloud_run.ps1`, `.env.example`
- `tests/test_delivery_idempotency.py`

**Verification**

- Unit coverage verifies exact key stability, duplicate suppression, and claim
  release after a failed attempt.

### 2.5 Delta `_v2` liquid clustering and maintenance — complete locally

**Diff highlights**

- Fresh `meta_ads_raw` and `ad_spend_normalized` tables now declare
  `CLUSTER BY (date, campaign_id)` and
  `delta.enableDeletionVectors = true`.
- Added a DBR 18.1+ one-time conversion script and a guarded Python migration
  that issue `REPLACE PARTITIONED BY WITH CLUSTER BY` for existing tables.
  The migration is never executed implicitly during ingestion.
- Added a weekly maintenance job that runs `OPTIMIZE` and
  `VACUUM ... RETAIN 168 HOURS` for the two allow-listed tables in every active
  client schema. Identifiers are validated and shorter retention is rejected.
- Both deployment scripts create a dedicated maintenance Cloud Run Job and a
  Sunday 03:00 America/New_York scheduler trigger.

**Affected files**

- `utils/databricks_writer.py`, `flows/delta_maintenance.py`
- `transforms/migrations/002_liquid_clustering_v2.sql`
- deployment scripts and `tests/test_delta_maintenance.py`

**Verification**

- Unit coverage asserts exact migration, `OPTIMIZE`, and 168-hour `VACUUM`
  statements and rejects unsafe schema/table input. The live migration remains
  intentionally pending a staging DBR version check and table benchmark.

### 2.6 Golden dataset calibration and seed path — complete locally

**Diff highlights**

- Removed the 500-character truncation. Inputs, expected outputs, and expected
  fields are PII-masked before persistence.
- Added four full, representative masked multi-channel seeds covering data
  quality, revenue analysis, executive reporting, and governance review.
- JSON columns are accepted as either database-returned dictionaries or JSON
  strings. Numeric fields use configured relative tolerances; strings,
  booleans, arrays, and objects use exact equality.
- The gate now fails if any expected field in any sample fails. CI idempotently
  seeds the reviewed fixture set before running required-sample evaluation.

**Affected files**

- `evals/golden_dataset.py`, `evals/eval_runner.py`
- `evals/seeds/multichannel_reporting.json`, `.github/workflows/ci.yml`
- `tests/test_golden_dataset.py`

**Verification**

- Unit coverage confirms full-input persistence, PII removal, dictionary/JSON
  conversion, numeric tolerance, and exact categorical regression failure.

### Staging dry-run validation status — blocked by environment prerequisites

No live cloud resource was modified during this implementation session. The
staging validation code and runbook are ready, but the live proof is not yet
available because this workstation has no `gcloud` executable, Application
Default Credentials cannot be resolved, no staging project/bucket variables
are configured, and the local registry currently contains two unique clients
rather than the required twenty.

Added `flows/staging_validation.py` to validate:

1. exactly 20 contiguous, unique manifest assignments;
2. generation-zero lock acquisition, duplicate rejection, and
   generation-matched release;
3. `.json.gz` round-trip and matching SHA-256 object metadata; and
4. execution logs containing both `status="partial"` and
   `PARTIAL_INGESTION_REPORT_SUPPRESSED` after explicit staging-only vendor
   fault injection.

The fault injection is gated behind `ARIE_STAGING_VALIDATION=true`, is only
accepted by the launcher with `--dry-run`, and can be scoped to one client.

```bash
# 1. Deploy to a staging project only, with a 20-client staging registry.
PROJECT_ID=<staging-project> REGISTRY_BUCKET=<staging-registry> \
RAW_ARCHIVE_BUCKET=<staging-raw> ./deploy.sh

# 2. Launch the immutable 20-task dry run and simulate one enabled Meta error.
gcloud run jobs execute attribution-launcher --region us-central1 \
  --args "flows/job_launcher.py,--dry-run,--expected-client-count,20,--simulate-vendor-error,meta,--simulate-vendor-error-client,<client_id>" \
  --wait

# 3. Verify the emitted manifest, GCS probes, and tenant execution logs.
python flows/staging_validation.py \
  --manifest-uri gs://<staging-registry>/work-manifests/run_id=<run_id>/manifest.json \
  --lock-bucket <staging-registry> \
  --raw-bucket <staging-raw> \
  --project-id <staging-project> \
  --execution-name <tenant-execution-name> \
  --expected-client-count 20 \
  --require-partial-log
```

The lock probe deletes its temporary lock. The raw checksum probe is retained
under `source=staging-probe` so the archive proof remains auditable and expires
under the staging bucket lifecycle policy.

The tranche-1 "Deferred gaps for tranche 2" list above is retained as the
historical baseline. Items 1–6 are superseded by the implementations recorded
in this section; live staging proof remains outstanding as described here.

### Tranche 2 final local verification

- `ruff format attribution_agent/attribution_agent/` — 109 Python files
  formatted; the final pass changed one file.
- `ruff check attribution_agent/attribution_agent/` — all checks passed.
- `pytest attribution_agent/attribution_agent/tests/ -q` — 154 passed with one
  upstream Starlette/httpx deprecation warning. The first sandboxed run used a
  protected host `%TEMP%`; rerunning with an isolated workspace `--basetemp`
  produced the clean result above.
- Focused tranche test set — 34 passed.
- `deploy.sh` passed `bash -n` with Git Bash.
- `scripts/arie_deploy_cloud_run.ps1` produced zero PowerShell AST parse errors.
- CI workflow YAML and the golden seed JSON parsed successfully.
- Local registry resolution produced two work items (`demo_client` and
  `n8iv_promotions`), confirming local/demo compatibility but not the required
  20-client staging array.
- Live validation prerequisite check: `gcloud` unavailable; project and bucket
  variables unset; `google.auth.default()` raised
  `DefaultCredentialsError`. Therefore no GCS, Cloud Run, Databricks staging,
  provider API, or live email operation was executed.

## 2026-08-09 — Enterprise platform demonstration harness

### Scope and evidence policy

- Added a deterministic, credential-free five-act board demonstration using
  production attribution, PII masking, cache-key, Delta date-predicate,
  operator-alert, sandbox, and white-label email-rendering code paths.
- Generated a 20-task manifest, Command Center portfolio state, a Meta HTTP 429
  partial-run trace, a real local gzip/SHA-256 raw archive, multi-model closed
  revenue allocations, an HTML executive report, and Telegram alert text.
- Cloud Run, GCS, Databricks, Anthropic, Telegram, and email trace lines are
  explicitly marked simulated. The harness makes zero external calls and does
  not claim unavailable live staging proof.
- The requested `$2.50/tenant/day`, `<$1,150/month`, and `$50k+/year` savings
  claims are not presented as established facts. Current budget enforcement is
  agency-scoped with `$25/day` and `$300/month` defaults; `$2.50 × 20 × 30`
  equals `$1,500`. The TCO example is labeled an illustrative scenario pending
  contracted-price validation.

### Affected files and artifacts

- `demo/enterprise_demo.py`
- `tests/test_enterprise_demo.py`
- `reports/enterprise_demo/BOARD_DEMO_WALKTHROUGH.md` and its JSON, SQL, gzip,
  log, alert, and HTML evidence bundle

### Verification

- Focused enterprise demo, attribution, sandbox, and PII tests: 38 passed.
- Full suite: 156 passed with one upstream Starlette/httpx deprecation warning.
- Ruff: 111 Python files formatted; all checks passed.
- Command Center visualization: 20 selectable tenants rendered at 736px and
  360px, failed-tenant selection updated correctly, no JavaScript errors, and
  no root-level horizontal overflow.
## 2026-08-09 — Internal Command Center consolidation

### Scope

- Designated `arie-portal` as the canonical internal ARIE operations console.
- Preserved the Streamlit application as an administrative fallback and kept
  customer-facing sales demos isolated from operational controls.

### Diff highlights

- Rebuilt the Next.js interface around seven operator surfaces: Overview,
  Pipeline, Tenants, Reports, Alerts, Governance, and Audit.
- Expanded Databricks telemetry to include pipeline checkpoints, report
  delivery state, AI cost ledger entries, evaluation scores, pending approvals,
  and audit history. Optional tables degrade independently instead of forcing a
  whole-application demo fallback.
- Added explicit `partial` status and `deliverySuppressed` semantics so a yellow
  run cannot be confused with a successful client delivery.
- Added tenant-selectable Cloud Run launches, per-source row inspection, output
  schema visibility, and exact `RUN LIVE` server-side confirmation.
- Added twelve-second foreground polling and safe capability flags for
  Databricks, pipeline execution, and approval mutations.
- Added a server-side approval adapter, strict action validation, fail-closed
  production Basic Auth, security headers, `noindex`, and private/no-store API
  responses.
- Migrated the deprecated Next.js `middleware.ts` convention to `proxy.ts`.

### Verification proof

- `npm run lint`: pass.
- `npm run typecheck`: pass.
- `npm run build`: pass under Next.js 16.3.0; all application and API routes
  generated successfully.
- Production-mode browser verification at 1440×1000: HTTP 200, all seven views
  rendered, demo execution correctly disabled, no framework overlay, zero
  console errors, and zero failed HTTP responses.
- Vercel production deployment `dpl_EqMbEsADPPsp4ELMpvZtWAtXLxZ1` reached
  `READY` and was aliased to `https://arie-command-center.vercel.app`.
- A credential-free production request returned HTTP 401 with the expected
  `ARIE Internal Command Center` authentication challenge.

### Production activation note

- Encrypted production Basic Auth and Databricks variables are configured in
  Vercel. Cloud Run execution and approval mutations remain read-only until
  either Vercel-to-GCP workload identity or the ARIE control API variables are
  provisioned. The UI exposes this state explicitly and will not simulate a
  successful operation.

## 2026-08-09 â€” Cloud Run Command Center activation preparation

### Current production inventory

- Confirmed project `n8iv-analytics-production` (`348643002075`) and region
  `us-central1`.
- Confirmed the existing `attribution-pipeline` job is healthy but still uses
  one task and the pre-Tranche-2 image `332e165`.
- Confirmed `attribution-launcher` and `attribution-benchmark-finalizer` are not
  deployed yet.
- Confirmed no `vercel` workload identity pool/provider or dedicated
  `vercel-arie-command-center` service account exists. No production IAM
  resource was changed during this preparation step.

### Command Center routing correction

- Changed the Vercel OIDC execution target from `attribution-pipeline` to
  `attribution-launcher`.
- Changed execution overrides from `flows/agency_flow.py --client-filter ...`
  to the launcher's supported `flows/job_launcher.py --client ...` arguments.
- This preserves the Tranche-2 sequence: immutable manifest, indexed tenant
  array, successful-array check, then benchmark finalizer.

### Federation plan and rollback

- Added `scripts/configure_vercel_gcp_wif.ps1`, which is read-only unless
  invoked with `-Apply`.
- The planned provider trusts only
  `owner:n8i-v-promotions:project:arie-command-center:environment:production`
  from `https://oidc.vercel.com/n8i-v-promotions`.
- The planned service account has no project-wide Cloud Run role. It receives
  `roles/run.jobsExecutorWithOverrides` only on `attribution-launcher`.
- Rollback removes the launcher job binding and service-account impersonation
  binding first; the dedicated provider, pool, and service account can then be
  deleted after confirming no other policy references them.

### Verification proof

- `arie-portal`: ESLint, TypeScript, and Next.js 16.3.0 production build pass.
- Python suite: 156 passed with one upstream Starlette/httpx deprecation
  warning using an isolated workspace pytest temp directory.
- Production IAM/deployment remains pending explicit approval of the
  Vercel-to-GCP trust boundary and production job changes.

### Production activation completed

- Approval was received for the production IAM federation and Cloud Run
  deployment.
- Created `gs://n8iv-analytics-production-attribution-raw` in `us-central1`
  with uniform bucket-level access, a 90-day deletion lifecycle, and
  object-creator/viewer access for `attribution-runtime`.
- Reduced the Cloud Build context from 22,615 files / 572 MB to 129 files /
  0.74 MB by excluding the frontend workspace and local agent artifacts from
  the Python image context.
- Cloud Build `b46acd73-730f-4862-8c94-3af23f3fb7be` produced immutable image
  digest `sha256:e09a229550b1313aba62e4eb6b54826ee1902b972bd0d71186030d9a799a84f9`
  with release tag `prod-20260809-t2`.
- Deployed `attribution-pipeline` with 20 tasks, parallelism 5, 2 GiB memory,
  2 CPUs, one idempotent retry, streaming ingestion, client locks, raw
  archival, work manifests, delivery idempotency, and fail-closed AI budgets.
- Deployed one-task `attribution-benchmark-finalizer` and
  `attribution-launcher` jobs from the same immutable digest.
- Granted `attribution-runtime` job-scoped execution-with-overrides and viewer
  roles only on the worker and finalizer jobs so the launcher can execute and
  monitor them.
- Enabled IAM Credentials and Security Token Service APIs; created the
  dedicated `vercel` pool/provider and
  `vercel-arie-command-center@n8iv-analytics-production.iam.gserviceaccount.com`.
- The provider is ACTIVE and accepts only
  `owner:n8i-v-promotions:project:arie-command-center:environment:production`.
  The service account has `roles/run.jobsExecutorWithOverrides` only on
  `attribution-launcher`.
- Added the seven non-secret GCP federation coordinates to the Vercel
  production environment and deployed Vercel production deployment
  `dpl_DK4nvX3FHAi3FhwZ9gXQ8xq4Gq8b`, READY at
  `https://arie-command-center.vercel.app`.

The final audit confirmed all three jobs are Ready and pinned to the immutable
digest, the worker is configured 20Ã—5, the raw bucket lifecycle is active, and
the exact job/service-account IAM policies match the plan. No Cloud Run
execution was started. A `validateOnly` request was deliberately not sent
because it still targets the production `jobs:run` endpoint; the first actual
end-to-end token exchange should be observed during an operator-approved dry
run from the authenticated Command Center.

## 2026-08-09 — Databricks tenant lifecycle control plane

### Decision and safety boundary

- Databricks Delta is now the canonical production registry for agencies and
  businesses. GCS `clients.json` remains a recovery/demo fallback, not a
  second writable source of truth.
- The Command Center sends one of four named commands to a dedicated
  Databricks Workflow: `create_agency`, `create_business`, `delete_business`,
  or `delete_agency`. It never accepts a schema name or SQL from the browser.
- Schema ownership is deterministic: `workspace.agency_<agency_id>` for an
  agency and `workspace.attribution_<business_id>` for a business.
- Delete commands use a guarded `DROP SCHEMA IF EXISTS ... CASCADE`. The job
  derives the exact schema from a canonical slug, verifies a business registry
  schema matches that value, refuses deletion during active/queued client runs,
  and refuses agency deletion while active businesses remain.
- `n8iv_promotions`, `demo_agency`, and `demo_client` are protected in both the
  portal API and Databricks notebook. Deletion additionally requires the exact
  phrase `DELETE BUSINESS <id>` or `DELETE AGENCY <id>`.
- The Databricks job is configured with `max_concurrent_runs=1`, queueing, zero
  automatic retries, and Jobs API idempotency tokens. This serializes registry
  mutation and prevents duplicate runs from HTTP retries.

### Diff highlights

| Area | Affected files | Change |
|---|---|---|
| Lifecycle job | `flows/tenant_lifecycle.py` | Self-contained Databricks SOURCE notebook, strict command/slug validation, guarded schema DDL, registry MERGEs, active-run checks, and synchronous success/failure recording |
| Registry model | `config/agency_config.py`, `utils/databricks_writer.py` | Dynamic Delta-backed agency registry, agency/client bootstrap DDL, and `tenant_lifecycle_operations` audit table |
| Command Center | `arie-portal/components/command-center.tsx`, `lib/data.ts`, `lib/types.ts`, `lib/tenant-lifecycle.ts`, `app/api/tenants/lifecycle/route.ts`, `app/globals.css` | Agency-backed selector, create/delete dialogs, exact confirmation, server-side Jobs API 2.2 trigger, Databricks run link, and lifecycle ledger |
| Deployment | `scripts/deploy_databricks_tenant_lifecycle.ps1` | Plan-first notebook import and create/reset of the serialized Databricks Workflow |
| Registry migration | `scripts/migrate_gcs_registry_to_delta.ps1` | Plan-first, upsert-only migration from GCS JSON to validated Delta agency/client rows |
| Cloud Run | `deploy.sh`, `scripts/arie_deploy_cloud_run.ps1` | Production registry backend default changed from `local` to `delta`; mounted GCS JSON remains available as fallback |

### Rollout sequence

1. Run `scripts/migrate_gcs_registry_to_delta.ps1` without `-Apply`, review the
   target, then apply it. This migration performs no drop or source deletion.
2. Run `scripts/deploy_databricks_tenant_lifecycle.ps1` without `-Apply`, then
   apply it and capture the returned Databricks job ID.
3. Set `DATABRICKS_TENANT_LIFECYCLE_JOB_ID` in the Vercel production
   environment and redeploy the portal.
4. Redeploy Cloud Run with the Delta registry backend, then verify that the
   launcher manifest and Command Center show the same active client set.
5. Validate create/delete behavior first with disposable staging IDs. Confirm
   both the Databricks run output and `tenant_lifecycle_operations` before
   allowing production deletion.

### Verification proof

- New lifecycle safety tests: 12 passed, including injection rejection,
  protected-tenant checks, exact confirmation, schema mismatch refusal,
  active-child refusal, exact DROP generation, and successful-request
  idempotency.
- Full Python architecture suite: 168 passed with one upstream
  Starlette/httpx deprecation warning.
- Ruff lint and format checks pass for the affected Python files.
- Command Center ESLint, TypeScript, and Next.js 16.3.0 production build pass;
  `/api/tenants/lifecycle` is emitted as a server-rendered route.
- Both PowerShell scripts pass AST parsing and their default plan-only mode.
- Live Databricks/GCP/Vercel application is pending because the external tool
  quota is unavailable until 2026-08-15 19:49 local time. No production job,
  registry mutation, schema creation, or DROP statement was executed in this
  implementation session.
