# ARIE Command Center

An owner-facing web app for running and monitoring the ARIE attribution
pipeline, and a **Demo mode** for sales pitches that needs zero credentials.

- **Overview** — portfolio-wide spend/pipeline/ROI across all pilot clients
- **Pilot roster** — per-client sources, models, secret status, channel trends
- **Run console** — trigger a dry-run or live pipeline run, watch it progress
  stage by stage, see run history
- **Reports** — the latest AI-generated insight report per client (narrative,
  key findings, ROI), plus report history and a "regenerate" button
- **Health & approvals** — API health, live-mode readiness, and the
  governance approval queue

## Two modes, one UI

The mode switch top-right toggles what the app talks to:

- **Demo** — an internally-consistent, six-month dataset for five fictional
  pilot clients (`command_center/server/demo/`), served entirely in-process.
  No GCP, no Databricks, no client data ever touched. Use this for sales
  pitches — pipeline runs "execute" through simulated stages over ~10
  seconds so a prospect can watch the whole thing happen.
- **Live** — proxies to the real `attribution-api` Cloud Run service (see
  below), which fronts the actual pipeline, client registry, and insight
  reports for the real pilot clients.

Live mode only unlocks once its three secrets are present (Health tab shows
exactly what's missing).

## Run on Replit

1. Import this repo into Replit (**Create Repl → Import from GitHub**).
   `.replit` at the repo root already points at `command_center/` — first
   run installs both the server and client, builds the React app, and starts
   the Express server on port 3000. No manual config needed for Demo mode.
2. To use it for a sales pitch: open the Repl, leave it on **Demo** mode,
   walk the prospect through Overview → Pilot roster → Run console (trigger
   a run live) → Reports.
3. To enable **Live** mode, add these as Replit **Secrets** (padlock icon),
   then restart the Repl:

   | Secret | Value |
   |---|---|
   | `ARIE_API_BASE` | The `attribution-api` Cloud Run URL, e.g. `https://attribution-api-xxxxx-uc.a.run.app` |
   | `ARIE_API_KEY` | The `API_KEY_ADMIN` value from Secret Manager (admin role — full RBAC) |
   | `GCP_SA_KEY` | JSON key for a service account with `run.invoker` on `attribution-api` (see below) |

## One-time GCP setup for Live mode

The FastAPI REST layer (`attribution_agent/attribution_agent/api/`) already
exists in this repo but isn't deployed by default — only the Streamlit UI and
the pipeline Job are. `deploy.sh` now also deploys it as a third, private
Cloud Run service, `attribution-api`.

```bash
# 1. Create a service account for the Command Center and a key for GCP_SA_KEY
gcloud iam service-accounts create command-center --display-name "ARIE Command Center (Replit)"
gcloud iam service-accounts keys create command-center-key.json \
  --iam-account command-center@<PROJECT_ID>.iam.gserviceaccount.com
# Paste the contents of command-center-key.json into the Replit GCP_SA_KEY secret,
# then delete the local file — it's a live credential.

# 2. Deploy (or redeploy) with the API service wired to that service account
PROJECT_ID=<PROJECT_ID> COMMAND_CENTER_SA=command-center@<PROJECT_ID>.iam.gserviceaccount.com \
  ./deploy.sh

# 3. Grab the API URL and the admin key for the Replit secrets
gcloud run services describe attribution-api --region us-central1 --format 'value(status.url)'
gcloud secrets versions access latest --secret API_KEY_ADMIN
```

`ALLOW_UNAUTHENTICATED_API=true` exists for local testing but should stay
`false` in production — the X-API-Key check is app-layer RBAC, not a
substitute for Cloud Run IAM.

## Local dev (outside Replit)

```bash
cd command_center
npm run setup          # installs server + client deps
npm run dev             # http://localhost:3000, demo mode works with no env vars
# or, for hot reload on the client:
npm --prefix client run dev   # http://localhost:5173, proxies /api to :3000
```

## First pilot month checklist

1. Confirm all 5 pilot clients are registered and their channel secrets show
   green in **Pilot roster** (or in Live mode's `/api/v1/clients`).
2. Run a **dry run** for all pilots from **Run console** and confirm every
   stage completes with no `error` field set and `clients_failed=0`.
3. Check **Health & approvals** — clear the governance queue before the live
   send.
4. Switch **dry run off** for the scheduled live run, or let
   Cloud Scheduler's `attribution-monthly` trigger fire on the 1st (see
   `docs/RUNBOOK.md`).
5. Confirm each pilot's report lands in **Reports** and the client's inbox.
