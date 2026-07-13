# ARIE Architecture & Data Flow

Flowcharts of ARIE, the Automatic Revenue Intelligence Engine, from triggers
down to the individual pipeline steps. Diagrams are Mermaid — GitHub renders
them inline.

Verified against the code on 2026-07-06 (`flows/agency_flow.py`,
`flows/ingest_flow.py`, `agents/`, `api/`, `mcp_server/`, `deploy.sh`).

## 1. System context — how a run starts and where data goes

```mermaid
flowchart TD
    subgraph triggers["Triggers"]
        SCHED["Cloud Scheduler<br/>attribution-monthly<br/>9am ET, 1st of month"]
        UI["Streamlit UI (attribution-ui)<br/>private — gcloud run services proxy"]
        API["FastAPI REST<br/>X-API-Key + RBAC<br/>POST /pipeline/run"]
        MCP["FastMCP server<br/>7 tools, stdio"]
        CLI["CLI<br/>python flows/agency_flow.py --agency ..."]
    end

    subgraph compute["GCP Cloud Run — n8iv-analytics-production (us-central1)"]
        JOB["Cloud Run Job<br/>attribution-pipeline"]
        SVC["Cloud Run Service<br/>attribution-ui (Streamlit :8080)"]
        PIPE["run_agency_pipeline()<br/>flows/agency_flow.py"]
    end

    subgraph secrets["Config & secrets"]
        SM["GCP Secret Manager<br/>global env secrets + per-client credential refs"]
        GCS["GCS FUSE mount /mnt/registry<br/>clients.json (client registry)"]
    end

    subgraph sources["External data sources"]
        META["Meta Graph API"]
        GOOG["Google Ads"]
        LI["LinkedIn Ads"]
        TT["TikTok Ads"]
        HS["HubSpot"]
        STR["Stripe"]
    end

    subgraph data["Data layer — Databricks SQL warehouse"]
        DELTA["Client Delta tables<br/>(per-client schema)"]
        OPS["workspace.attribution_ops<br/>15 ops tables"]
    end

    subgraph outputs["Outputs"]
        CLAUDE["Claude API<br/>(via ModelGateway)"]
        GMAIL["Gmail SMTP<br/>white-labeled report email"]
        TG["Telegram<br/>run notifications"]
    end

    SCHED --> JOB
    CLI --> PIPE
    UI --> SVC --> PIPE
    API --> PIPE
    MCP --> PIPE
    JOB --> PIPE
    SM --> JOB
    SM --> SVC
    GCS --> SVC
    PIPE --> sources
    sources --> PIPE
    PIPE --> DELTA
    PIPE --> OPS
    PIPE --> CLAUDE
    PIPE --> GMAIL
    PIPE --> TG
```

Compute lives on Cloud Run because the Databricks Serverless Egress Gateway
blocks DNS to api.stripe.com / graph.facebook.com / api.hubapi.com. All Delta
reads/writes go through `utils/databricks_writer.py` using
`databricks-sql-connector` over public HTTPS.

## 2. Agency pipeline — `run_agency_pipeline()` per-client loop

```mermaid
flowchart TD
    START(["run_agency_pipeline(agency_id, dry_run, model, resume_run_id)"])
    START --> RESUME{"resume_run_id<br/>given?"}
    RESUME -- yes --> RID["run_id = resume_run_id<br/>load checkpoints"]
    RESUME -- no --> NEWID["run_id = uuid4()"]
    RID --> NOTIF1["Telegram: pipeline started"]
    NEWID --> NOTIF1
    NOTIF1 --> LOOP["for each client (sequential,<br/>avoids API rate limits)"]

    LOOP --> DONE{"checkpoint says<br/>pipeline_complete?"}
    DONE -- yes --> SKIP["skip client"] --> LOOP
    DONE -- no --> SAGA["open PipelineSaga<br/>(rollback on failure)"]

    SAGA --> S1["1 · ingest_flow(client_id)<br/>(checkpointed — see §3)"]
    S1 --> S2["2 · run_client_attribution_sql<br/>refresh attributed revenue for model"]
    S2 --> S3["3 · generate_insight_report<br/>(two-stage agents — see §4)"]
    S3 --> S3B["3b · governance-reviewer agent<br/>ADVISORY — warnings logged,<br/>never blocks"]
    S3B --> DRY{"dry_run?"}
    DRY -- no --> S4["4 · send_agency_report<br/>white-labeled email via Gmail SMTP<br/>+ Power BI link"]
    DRY -- yes --> SKIPMAIL["skip email"]
    S4 --> CKPT["checkpoint: pipeline_complete"]
    SKIPMAIL --> CKPT
    CKPT --> MEM["MemoryStore.remember<br/>pipeline_summary (cross-run context)"]
    MEM --> WRITEOK["write_pipeline_run → ops.pipeline_runs<br/>status=success + row counts"]
    WRITEOK --> NOTIFOK["Telegram: client complete ✅"]
    NOTIFOK --> LOOP

    S1 -. exception .-> FAIL
    S2 -. exception .-> FAIL
    S3 -. exception .-> FAIL
    S4 -. exception .-> FAIL
    FAIL["saga rollback +<br/>checkpointer.fail_step"]
    FAIL --> WRITEFAIL["write_pipeline_run status=failed"]
    FAIL --> NOTIFBAD["Telegram: client failed ❌"]
    NOTIFBAD --> LOOP

    LOOP -- all clients done --> BENCH{"results ≥ 1<br/>and not dry_run?"}
    BENCH -- yes --> BSQL["run_agency_benchmark_sql<br/>cross-client benchmark (best-effort)"]
    BENCH -- no --> SUMM
    BSQL --> SUMM["summary: clients_processed,<br/>clients_failed, results, errors"]
    SUMM --> NOTIF2["Telegram: pipeline complete"]
    NOTIF2 --> END([return summary])
```

Every numbered step is checkpointed to `ops.pipeline_checkpoints`; a resumed
run (`--resume-run-id`) skips steps already marked complete.

## 3. Ingest flow — `ingest_flow(client_id)`

```mermaid
flowchart TD
    START(["ingest_flow(client_id)"])
    START --> CFG["get_client(client_id)<br/>+ resolve per-client source tokens<br/>(Secret Manager refs / env fallback)"]
    CFG --> SETUP["step_setup — ensure_schema + ensure_tables<br/>(with retry)"]

    SETUP --> POOL["ThreadPoolExecutor (6 workers)<br/>each pull: 3 retries, 30s delay"]
    POOL --> PMETA["pull Meta"]
    POOL --> PGOOG["pull Google Ads"]
    POOL --> PLI["pull LinkedIn"]
    POOL --> PTT["pull TikTok"]
    POOL --> PHS["pull HubSpot"]
    POOL --> PSTR["pull Stripe"]

    PMETA --> COLLECT
    PGOOG --> COLLECT
    PLI --> COLLECT
    PTT --> COLLECT
    PHS --> COLLECT
    PSTR --> COLLECT
    COLLECT["_collect: a failed source degrades to<br/>no-data (None) and is recorded in<br/>source_failures — run continues"]

    COLLECT --> VAL["validate Meta / HubSpot / Stripe<br/>(spend-drop + zero-spend alerts,<br/>schema checks)"]
    VAL --> WRITES["write_meta_data / write_hubspot_data /<br/>write_stripe_data → client Delta tables<br/>(validation failure ⇒ write skipped)"]
    WRITES --> NORM["normalize each ad source →<br/>combine_normalized_ads →<br/>write_normalized_ad_data"]
    NORM --> ATTR["attribution_engine.run_attribution<br/>(ads + HubSpot + Stripe →<br/>attributed / unattributed revenue)<br/>write_attribution_results"]
    ATTR --> ALERT["step_alert — log validation issues"]
    ALERT --> DQ["data-quality agent (haiku)<br/>classifies findings into<br/>issues / warnings / escalations<br/>NON-FATAL on error"]
    DQ --> SUMM(["summary: row counts, attribution,<br/>source_failures,<br/>status = complete | partial"])
```

Disabled channels are skipped. An enabled channel with a missing or expired
credential fails non-fatally, lands in `source_failures`, and the run reports
`status: "partial"`.

## 4. AI layer — two-stage insight generation and governance

```mermaid
flowchart TD
    GIR(["generate_insight_report(client_id, model)"])
    GIR --> FETCH["_fetch_channel_performance<br/>attributed revenue + collected revenue<br/>(Stripe cash, refund rate, true ROI)"]
    FETCH --> GW

    subgraph GW["ModelGateway (every Claude call goes through this)"]
        GUARD["GuardrailsEngine<br/>prompt-injection detection ·<br/>PII masking · JSON contract"]
        ROUTE["model routing by task_type<br/>(haiku = triage, sonnet = quality)"]
        CACHE["semantic cache"]
        LEDGER["cost ledger →<br/>ops.cost_ledger"]
        AB["A/B assignment<br/>(deterministic hash)"]
    end

    GW --> STAGE1["Stage 1 · revenue-analyst (sonnet)<br/>channel data → structured findings"]
    STAGE1 --> STAGE2["Stage 2 · executive-reporting (sonnet)<br/>findings → client-facing report JSON"]
    STAGE2 --> REPORT["InsightReport<br/>narrative + top_channel + total_pipeline"]
    STAGE2 -. agent path fails .-> FB["_call_claude fallback<br/>single-shot prompt, same governance"]
    FB --> REPORT

    REPORT --> GOV["governance-reviewer (haiku)<br/>evidence quality · attribution≠causality ·<br/>privacy · tone — advisory warnings only"]
    REPORT --> INSTORE["ops.insight_reports"]

    PROMPTS["PromptLoader<br/>.claude/agents/*.md (YAML frontmatter)<br/>COPY'd into the image, N8IV_AGENTS_DIR"]
    PROMPTS --> STAGE1
    PROMPTS --> STAGE2
    PROMPTS --> GOV

    EVAL["GoldenDataset evals<br/>eval_runner.py — CI gate when<br/>.claude/agents/ changes"]
    EVAL -.-> PROMPTS
```

The data-quality agent (§3) is the fourth versioned agent and rides inside
`ingest_flow`, not the report path.

## 5. Interfaces — REST, MCP, UI

```mermaid
flowchart LR
    subgraph FASTAPI["FastAPI (api/) — X-API-Key auth, RBAC: 4 roles / 10 permissions"]
        H["GET /health"]
        PR["POST /pipeline/run"]
        PRUNS["GET /pipeline/runs<br/>GET /pipeline/runs/{id}"]
        REP["GET /reports/{client}/latest<br/>GET /reports/{client}<br/>POST /reports/{client}/generate"]
        CL["GET/POST/PUT/DELETE /clients"]
        APPR["GET /approvals<br/>POST /approvals/{id}/resolve"]
    end

    subgraph MCPS["FastMCP server (mcp_server/server.py) — 7 tools, stdio"]
        T1["run_ingest_flow"]
        T2["other tools: reports, clients,<br/>runs, ops queries"]
    end

    subgraph APP["Streamlit app (app.py)"]
        RUNTAB["Run pipeline tab<br/>(in-process run_agency_pipeline)"]
        OBS["Observability tab<br/>cost · health · eval scores · audit"]
        ADMIN["Clients admin portal<br/>persists to client registry"]
    end

    REG["Client registry<br/>Cloud Run: clients.json on GCS FUSE (local backend)<br/>Databricks: ops.client_registry (delta backend)<br/>60s read cache, writes invalidate"]

    CL --> REG
    ADMIN --> REG
    T2 --> REG
```

## 6. Build, CI, and deploy

```mermaid
flowchart TD
    DEV["branch + PR<br/>(pre-push hook blocks direct main pushes)"]
    DEV --> CI

    subgraph CI["GitHub Actions — ci.yml (PRs + main)"]
        LINT["Lint — ruff 0.15.19 pinned<br/>format --check + check"]
        TEST["Tests — pytest, ~57 min<br/>mock env, no live APIs"]
        EVALG["Eval Gate — only when<br/>.claude/agents/ changes"]
    end

    CI --> MERGE["merge to main<br/>(auto-merge observed on green)"]
    MERGE --> DEPLOYWF["deploy.yml — pytest + Telegram notify<br/>DOES NOT DEPLOY (WIF CD is a TODO)"]
    MERGE --> MANUAL["manual: PROJECT_ID=... ./deploy.sh<br/>(Git Bash, repo root)"]

    subgraph GCPD["deploy.sh"]
        CB["Cloud Build → container image<br/>(root Dockerfile, python:3.11-slim)"]
        AR["Artifact Registry"]
        SEED["--seed-secrets: .env →<br/>Secret Manager"]
        RJOB["Cloud Run Job attribution-pipeline"]
        RSVC["Cloud Run Service attribution-ui"]
        CS["Cloud Scheduler attribution-monthly<br/>(SCHEDULE env overrides)"]
    end

    MANUAL --> CB --> AR
    AR --> RJOB
    AR --> RSVC
    MANUAL --> SEED
    MANUAL --> CS
```

## 7. Ops tables (`workspace.attribution_ops`)

Created idempotently by `ensure_ops_tables()` at startup:

| Concern | Tables |
|---|---|
| Run tracking | `pipeline_runs`, `pipeline_checkpoints`, `idempotency_store` |
| Reporting | `insight_reports`, `approval_queue` |
| Governance & audit | `audit_log` |
| Cost & caching | `cost_ledger`, `semantic_cache` |
| Agent context | `agent_memory` |
| Evals | `eval_golden_dataset`, `eval_results` |
| Experiments | `ab_experiments`, `ab_assignments` |
| Clients | `client_registry` |
