# Attribution Agent — Claude Code Instructions

## Project Overview

Closed-loop attribution pipeline for marketing agencies. Ingests Meta, Google,
LinkedIn, and TikTok Ads plus HubSpot and Stripe data, writes it to Databricks
Delta tables, generates AI-powered insight reports via Claude agents, and
emails white-labeled reports to clients.

- **Compute:** GCP Cloud Run (project `n8iv-analytics-production`, us-central1)
- **Data layer:** Databricks SQL warehouse (Delta tables, `databricks-sql-connector` over public HTTPS)
- **Entry point:** `attribution_agent/attribution_agent/flows/agency_flow.py`

## Current State (as of 2026-07-05, post PRs #60–#63)

- **Deployed & verified on Cloud Run 2026-07-02.** Dry-run execution green
  (`clients_failed=0`, Delta writes + governance review working). Databricks
  *compute* (Jobs + App) is decommissioned; only the SQL warehouse remains.
- **CI is healthy.** The 2026-07-02→05 GitHub Actions billing block is
  resolved, and CI now runs **once** per PR (PR #60 limited the `push`
  trigger to `main` — the double ~57-min test run that caused the billing
  overage is gone).
- **⚠️ Production runs the pre-#61 image until the next `./deploy.sh`.**
  PR #61 (credential redaction: Meta token scrubbed from exception messages,
  logs, and `source_failures`) is on main but deploys are manual.
- **Manual follow-up:** rotate the Meta access token — before PR #61, failing
  Meta pulls wrote the full request URL (token included) to Cloud Logging.
- **Deploys are manual.** `deploy.yml` only runs tests + a Telegram notify;
  Cloud Run CD via Workload Identity Federation is a TODO. Run `./deploy.sh`
  after merging.
- Monthly schedule confirmed: Cloud Scheduler `attribution-monthly`, 9am ET on
  the 1st (override via `SCHEDULE` env in `deploy.sh`).
- Known-absent credentials (fine — no client enables these channels; pulls
  fail non-fatally → `status: "partial"`): `GOOGLE_ADS_REFRESH_TOKEN`,
  `LINKEDIN_ACCESS_TOKEN`, `TIKTOK_ACCESS_TOKEN`.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — flowcharts of the full system: triggers, pipeline, ingest, AI layer, interfaces, deploy
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — deploy, rollback, health checks, log access, common failures
- [`docs/ONBOARDING_CLIENT.md`](docs/ONBOARDING_CLIENT.md) — add a new client/agency and provision secrets
- [`docs/A2A.md`](docs/A2A.md) — agent-to-agent local & network dispatch

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

A pre-push hook blocks direct pushes to `main`. Auto-merge has been observed
enabled on the repo — PRs may merge themselves the moment checks go green.

**⚠️ Before and after pushing to an open PR, verify
`gh pr view <n> --json headRefOid` matches your local HEAD.** A PR was once
merged while commits were still being pushed to its branch, silently dropping
them from `main` (they had to be re-landed in a follow-up PR).

## Architecture

13-layer production stack, all 6 build phases complete (2026-06-12):

- **4 versioned Claude agents** in `.claude/agents/*.md` (YAML frontmatter
  parsed by PromptLoader): data-quality (haiku), revenue-analyst (sonnet),
  executive-reporting (sonnet), governance-reviewer (haiku). Two-stage insight
  generation: revenue-analyst findings → executive-reporting client JSON.
- **ModelGateway** — model routing, cost ledger, semantic caching, guardrails
- **GuardrailsEngine** — injection detection, PII masking, JSON contract enforcement
- **FastAPI REST layer** — RBAC (4 roles, 10 permissions, X-API-Key auth)
- **Checkpointer + PipelineSaga** rollback in `flows/agency_flow.py`;
  resume with `--resume-run-id <id>`
- **MemoryStore + ClientContextAssembler** — cross-run context
- **GoldenDataset evals** + `evals/eval_runner.py` CI gate
- **FastMCP server** (`mcp_server/server.py`) — 7 tools, stdio transport
- **A2A foundation** — AgentCard registry + AgentDispatcher (local routing)
- **A/B testing** — deterministic hash assignment
- **Streamlit app** (`app.py`) — pipeline UI + Observability tab + Clients admin portal

**15 ops tables** in `workspace.attribution_ops`, created by
`ensure_ops_tables()` on startup: pipeline_runs, audit_log, insight_reports,
approval_queue, idempotency_store, pipeline_checkpoints, cost_ledger,
agent_memory, semantic_cache, eval_golden_dataset, eval_results,
ab_experiments, ab_assignments, client_registry.

Run the pipeline locally:
```
cd attribution_agent/attribution_agent
python flows/agency_flow.py --agency demo_agency
python flows/agency_flow.py --agency demo_agency --dry-run   # skip email
```

## GCP Cloud Run Deployment

One container image (repo-root `Dockerfile`, `python:3.11-slim`) serves both workloads:
- **Cloud Run Job** `attribution-pipeline` — `python flows/agency_flow.py`, triggered monthly by Cloud Scheduler (`attribution-monthly`)
- **Cloud Run Service** `attribution-ui` — Streamlit on port 8080 (default CMD)

The **data layer stays on Databricks**: Delta writes go through
`utils/databricks_writer.py` via `databricks-sql-connector` to the SQL
warehouse over public HTTPS (`DATABRICKS_HOST` / `DATABRICKS_HTTP_PATH` /
`DATABRICKS_TOKEN`). Compute moved off Databricks because its Serverless
Egress Gateway blocks DNS to api.stripe.com / graph.facebook.com /
api.hubapi.com.

The UI is **private** (org policy blocks `allUsers`) — access it via:
```bash
gcloud run services proxy attribution-ui --region us-central1
```

### Secrets — never paste tokens in chat or code
All credentials live in `attribution_agent/attribution_agent/.env` (gitignored).
Copy `.env.example` → `.env` and fill in real values before running anything locally.
In the cloud, secrets live in GCP Secret Manager and Cloud Run injects them as
env vars (`--set-secrets`). Code reads them from `os.environ` only
(`utils/secrets.py`).

```bash
# One-time: push .env values into Secret Manager
PROJECT_ID=<project> ./deploy.sh --seed-secrets
```

`.env` gotcha: the seeder strips only whitespace-prefixed `#` comments — a
`#comment` glued directly onto a token value gets preserved into the secret.

### Deploy (run after merging to main)
```bash
# From repo root, in Git Bash — builds via Cloud Build, deploys Job + UI + Scheduler
PROJECT_ID=n8iv-analytics-production ./deploy.sh
```

### Run the Job on demand
```bash
gcloud run jobs execute attribution-pipeline --region us-central1 \
  --args "flows/agency_flow.py,--agency,demo_agency,--dry-run" --wait
```

### Local dev
```bash
cd attribution_agent/attribution_agent
streamlit run app.py   # runs pipeline in-process, not via Cloud Run
```

### Deploy gotchas (already encoded in deploy.sh — don't regress them)
- Cloud Build's compute service account needs `artifactregistry.writer`
- Git Bash MSYS mangles `/mnt` paths — use targeted `MSYS2_ARG_CONV_EXCL`,
  NOT blanket `MSYS_NO_PATHCONV` (which breaks gcloud)
- The Secret Manager name is `DATABRICKS_SERVER_HOSTNAME` (not `_HOST`)
- `--set-secrets` must skip secrets that don't exist yet
- `.claude/agents/` must be COPY'd into the image and `N8IV_AGENTS_DIR` set —
  the prompt files are runtime dependencies
- Client registry on Cloud Run: GCS FUSE mount at `/mnt/registry`,
  `ATTRIBUTION_CLIENT_REGISTRY_BACKEND=local`

## CI (`.github/workflows/`)

- **ci.yml** — Lint (ruff, **pinned 0.15.19** + repo-root `ruff.toml`) and
  Tests (**~57 min** — that's normal, not a hang; uses mock env values, never
  live APIs). Eval Gate runs only when `.claude/agents/` files change.
- **deploy.yml** (push to main) — pytest + Telegram notify only. It does NOT
  deploy; run `./deploy.sh` manually.
- Before pushing: match `ruff format` output and run `ruff check` clean at
  0.15.19. `ruff.toml` carries per-file `E402` ignores for the 5 modules that
  intentionally `sys.path`-insert before first-party imports.
- All 7 repo secrets are set (Databricks, Telegram, Anthropic).

Run tests and lint locally like CI does (from repo root):
```bash
ruff format --check attribution_agent/attribution_agent/
ruff check attribution_agent/attribution_agent/
python -m pytest attribution_agent/attribution_agent/tests/ -v --tb=short
```

## Code Gotchas

- **`stripe` must stay `<15`** — v15 dropped dict subclassing on
  `StripeObject`, which breaks the connector's `.get()` calls (PR #58)
- **`streamlit>=1.50`** required — `altair_chart(width=...)` kwarg (PR #59)
- **Databricks SQL connector can't bind pandas/numpy scalars** — coerce
  params via `_sql_param` in `utils/databricks_writer.py`
- **`__file__` may be unset in entry scripts** — flows fall back to
  `inspect.currentframe()` for path resolution; keep that pattern
- **dbutils widget parsing** is gated on `DATABRICKS_RUNTIME_VERSION`;
  ARIE remote trigger is gated behind `ATTRIBUTION_JOBS_API_ENABLED`
- **Never let credentials reach exception messages/logs** — Meta's token
  rides in URL query params and `requests` copies full URLs into exception
  text. Use `utils/secrets.redact_secrets()` on anything derived from a
  connector exception (see PR #61).

## Key Config Files

- `Dockerfile` + `deploy.sh` — container build and full GCP deploy (Artifact
  Registry, Secret Manager seed, Cloud Run Job/Service, Cloud Scheduler)
- `config/agency_config.py` — agency registry (branding, client list)
- `config/client_config.py` — client registry (data sources, report email);
  60s read cache, writes invalidate. Custom clients persist via the app's
  admin portal, REST `/clients`, or MCP (soft deletes; read failure falls
  back to local JSON with a warning)
- `config/rbac_config.py` — roles + permissions
- `config/budget_config.py` — monthly/per-run token budgets
- `ruff.toml` — lint config (per-file E402 ignores)
- `.env` — secrets (GMAIL_SENDER, GMAIL_APP_PASSWORD, Databricks tokens)
