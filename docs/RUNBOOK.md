# Operator Runbook

Operational reference for running, deploying, and recovering the attribution
platform. For day-to-day development see the repo root `CLAUDE.md`.

## Components

| Component | What it is | Where defined |
|-----------|------------|---------------|
| `attribution_pipeline` | Databricks Job — runs the ingest → insight → report flow | `databricks.yml` |
| `attribution-pipeline-ui` | Databricks App — Streamlit UI | `databricks.yml` |
| REST API | FastAPI (`api/main.py`) — pipeline, clients, reports, approvals, A2A | `api/` |
| A2A node | Agent-to-agent HTTP surface (discovery + dispatch) | `agents/a2a/server.py` |
| MCP server | FastMCP stdio server, 7 tools | `mcp_server/server.py` |

Workspace: `https://8259555645755006.6.gcp.databricks.com` (GCP).

## CLI auth profiles

The **DEFAULT** profile is a PAT. Some operations (notably `apps logs`) require
**OAuth** — the PAT returns `OAuth Token not supported for current auth type pat`.
Create a separate OAuth profile once:

```bash
databricks auth login --host https://8259555645755006.6.gcp.databricks.com --profile oauth
# Complete the browser flow, then verify:
databricks auth profiles                 # expect an `oauth` row
```

With both profiles present, **every CLI call must pass `--profile`** or it errors
with "multiple profiles matched".

## Deploy

```bash
# 1. Bundle (Job + App definitions)
databricks bundle deploy --target production

# 2. App code (Streamlit) — point at the bundled source
databricks apps deploy attribution-pipeline-ui --profile DEFAULT \
  --source-code-path "/Workspace/Users/zajen@n8ivpromotions.com/.bundle/attribution-agent/production/files/attribution_agent/attribution_agent"
```

> ⚠️ **A failed app deploy takes the running app DOWN — there is no auto-rollback.**
> `databricks apps deploy` makes the new deployment active even if its build
> fails, replacing the last-good one (the app goes `UNAVAILABLE`). Never redeploy
> a healthy app without a reason. If a deploy fails you must get a *successful*
> build to recover — see Rollback below.

## Run the pipeline

```bash
# On demand via Jobs
databricks bundle run attribution_pipeline

# Locally (runs in-process, not via Jobs API)
cd attribution_agent/attribution_agent
python flows/agency_flow.py --agency demo_agency
python flows/agency_flow.py --agency demo_agency --dry-run    # skip email
python flows/agency_flow.py --agency demo_agency --resume-run-id <id>   # resume from checkpoint
```

## Health checks

```bash
# REST API
curl https://<api-host>/health                         # {"status":"ok", ...}
curl https://<api-host>/.well-known/agent-cards        # A2A discovery

# App logs (OAuth profile required)
databricks apps logs attribution-pipeline-ui --profile oauth
```

A partial ingest is reported in the run summary as `"status": "partial"` with a
`source_failures` map naming each failed source (e.g. `pull-linkedin-ads`). The
run still completes with the sources that succeeded — investigate the named
source's credentials/quota rather than re-running everything.

## Rollback (during a build-plane / PyPI outage)

The app build installs `requirements.txt` from PyPI and needs egress to
`pypi.org` + `files.pythonhosted.org`. The build plane fails transiently with
`pypi.org [Errno 101] Network is unreachable` even when egress policy is
`FULL_ACCESS` — a Databricks Apps build-plane issue; retry later.

When fresh source uploads keep failing on this, redeploy from a previous
SUCCEEDED deployment's immutable artifact snapshot (Databricks reuses that
build's cached env — no PyPI download):

```bash
# Find a known-good deployment's artifact path
databricks apps get-deployment attribution-pipeline-ui <deployment-id> --profile DEFAULT
#   → .deployment_artifacts.source_code_path

# Redeploy from it
databricks apps deploy attribution-pipeline-ui --profile DEFAULT \
  --source-code-path /Workspace/Users/<uid>/src/<old-deployment-id>
```

## Dependency pinning (known-good build set)

Pinned in `attribution_agent/attribution_agent/requirements.txt`. Unbounded
`>=` pins are dangerous — a fresh build once pulled pandas 3.0 / numpy 2.4 /
databricks-sql-connector 4.x and broke. Keep these bounds:

- `google-ads==31.0.0`
- `protobuf>=4.25.0,<6`  (mlflow-skinny in the base image needs protobuf<6)
- `websockets>=10,<13`   (gradio-client needs <13)

## Egress note

The app's serverless egress is restrictive. Data-source APIs
(`graph.facebook.com`, `api.hubapi.com`, `api.stripe.com`, `api.telegram.org`)
show as DROP in the egress audit — the pipeline code assumes no outbound
internet from the app and runs analysis locally. Live ingestion runs in the
Databricks **Job**, not the App.
