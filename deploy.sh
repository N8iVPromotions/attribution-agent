#!/usr/bin/env bash
# deploy.sh — build & deploy the attribution agent to GCP Cloud Run.
#
# Prereqs (one-time):
#   gcloud auth login
#   gcloud config set project <PROJECT_ID>
#   On Windows, run this from Git Bash. Keep LF line endings (.gitattributes enforces).
#
# Usage:
#   PROJECT_ID=my-project ./deploy.sh --seed-secrets   # one-time: push .env values to Secret Manager
#   PROJECT_ID=my-project ./deploy.sh                  # build image + deploy backend jobs/API/scheduler
#
# Per-agency manual run (execute-time args override the deployed ones):
#   gcloud run jobs execute attribution-launcher --region "$REGION" \
#     --args "flows/job_launcher.py,--agency,demo_agency,--dry-run" --wait
set -euo pipefail

# Git Bash (MSYS) rewrites unix-looking args (/mnt/... -> C:/...), mangling
# gcloud flag values like mount-path=/mnt/registry. Exclude ONLY those args by
# prefix — a blanket MSYS_NO_PATHCONV breaks gcloud's own /c/... wrapper path.
export MSYS2_ARG_CONV_EXCL='volume=;ATTRIBUTION_'

# ── Configuration ────────────────────────────────────────────────────────────
PROJECT_ID="${PROJECT_ID:?set PROJECT_ID, e.g. PROJECT_ID=my-project ./deploy.sh}"
REGION="${REGION:-us-central1}"
REPO="${REPO:-attribution}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/attribution-agent"
SERVICE_API="attribution-api"
JOB_PIPELINE="attribution-pipeline"
JOB_LAUNCHER="attribution-launcher"
JOB_FINALIZER="attribution-benchmark-finalizer"
JOB_MAINTENANCE="attribution-delta-maintenance"
JOB_OPERATOR_HEALTH="attribution-operator-health"
SCHEDULER_JOB="attribution-monthly"
SCHEDULER_MAINTENANCE_JOB="attribution-weekly-maintenance"
SCHEDULER_HEALTH_JOB="attribution-daily-health"
REGISTRY_BUCKET="${REGISTRY_BUCKET:-${PROJECT_ID}-attribution-registry}"
RAW_ARCHIVE_BUCKET="${RAW_ARCHIVE_BUCKET:-${PROJECT_ID}-attribution-raw}"
PIPELINE_TASKS="${PIPELINE_TASKS:-20}"
PIPELINE_PARALLELISM="${PIPELINE_PARALLELISM:-5}"
ALLOW_UNAUTHENTICATED_API="${ALLOW_UNAUTHENTICATED_API:-false}"
COMMAND_CENTER_SA="${COMMAND_CENTER_SA:-}"
SA_RUNTIME_NAME="attribution-runtime"
SA_SCHEDULER_NAME="attribution-scheduler"
RUNTIME_SA="${SA_RUNTIME_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="${SA_SCHEDULER_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
ENV_FILE="attribution_agent/attribution_agent/.env"

# Canonical secret set. Cloud Run injects each as an env var of the same name.
# DATABRICKS_* stay: the data layer still writes Delta via the SQL warehouse.
SECRET_KEYS=(
  META_ACCESS_TOKEN
  HUBSPOT_ACCESS_TOKEN
  STRIPE_SECRET_KEY
  GOOGLE_ADS_DEVELOPER_TOKEN
  GOOGLE_ADS_CLIENT_ID
  GOOGLE_ADS_CLIENT_SECRET
  GOOGLE_ADS_REFRESH_TOKEN
  GOOGLE_ADS_LOGIN_CUSTOMER_ID
  LINKEDIN_ACCESS_TOKEN
  TIKTOK_ACCESS_TOKEN
  ANTHROPIC_API_KEY
  OPENAI_API_KEY
  GMAIL_SENDER
  GMAIL_APP_PASSWORD
  SENDGRID_API_KEY
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
  DATABRICKS_SERVER_HOSTNAME
  DATABRICKS_HTTP_PATH
  DATABRICKS_TOKEN
  API_KEY_ADMIN
  ARIE_BOOTSTRAP_ADMIN_PASSWORD
)

# Non-secret runtime config shared by the Job and the UI service.
# Delta is the production system of record. The mounted JSON registry remains
# available as a read-only recovery/demo fallback if Databricks is unavailable.
COMMON_ENV="ATTRIBUTION_CLIENT_REGISTRY_BACKEND=delta"
COMMON_ENV+=",ATTRIBUTION_CLIENT_REGISTRY_PATH=/mnt/registry/clients.json"
COMMON_ENV+=",ATTRIBUTION_CATALOG=workspace"
COMMON_ENV+=",ATTRIBUTION_OPS_SCHEMA=workspace.attribution_ops"
COMMON_ENV+=",COMMS_PROVIDER=gmail"
COMMON_ENV+=",GOOGLE_CLOUD_PROJECT=${PROJECT_ID}"
COMMON_ENV+=",ATTRIBUTION_CLOUD_RUN_JOB=${JOB_PIPELINE}"
COMMON_ENV+=",ATTRIBUTION_LAUNCHER_CLOUD_RUN_JOB=${JOB_LAUNCHER}"
COMMON_ENV+=",ATTRIBUTION_CLOUD_RUN_REGION=${REGION}"
COMMON_ENV+=",ARIE_CLIENT_LOCK_BUCKET=${REGISTRY_BUCKET}"
COMMON_ENV+=",ARIE_CLIENT_LOCKS_ENABLED=true"
COMMON_ENV+=",ARIE_RAW_ARCHIVE_BUCKET=${RAW_ARCHIVE_BUCKET}"
COMMON_ENV+=",ARIE_WORK_MANIFEST_BUCKET=${REGISTRY_BUCKET}"
COMMON_ENV+=",ARIE_AI_BUDGET_BUCKET=${REGISTRY_BUCKET}"
COMMON_ENV+=",ARIE_DELIVERY_IDEMPOTENCY_BUCKET=${REGISTRY_BUCKET}"
COMMON_ENV+=",ARIE_INGEST_SOURCE_WORKERS=${ARIE_INGEST_SOURCE_WORKERS:-3}"
COMMON_ENV+=",ARIE_INGEST_BATCH_ROWS=${ARIE_INGEST_BATCH_ROWS:-5000}"
COMMON_ENV+=",ARIE_STREAMING_INGEST=true"
COMMON_ENV+=",ARIE_MODEL_BUDGET_FAIL_CLOSED=true"
COMMON_ENV+=",ARIE_ALLOW_GLOBAL_CONNECTOR_CREDENTIALS=${ARIE_ALLOW_GLOBAL_CONNECTOR_CREDENTIALS:-false}"
COMMON_ENV+=",ARIE_DAILY_AI_USD_LIMIT=${ARIE_DAILY_AI_USD_LIMIT:-25}"
COMMON_ENV+=",ARIE_MONTHLY_AI_USD_LIMIT=${ARIE_MONTHLY_AI_USD_LIMIT:-300}"
COMMON_ENV+=",ATTRIBUTION_BENCHMARK_FINALIZER_JOB=${JOB_FINALIZER}"
# NOTE: deliberately NOT set: ATTRIBUTION_JOB_NAME / ATTRIBUTION_JOB_ID /
# ATTRIBUTION_JOBS_API_ENABLED — these keep the old Databricks Jobs-API
# trigger paths dark.

# KEY=KEY:latest,... for --set-secrets — only for secrets that actually exist
# in Secret Manager (--set-secrets fails the deploy on a missing secret; keys
# absent from .env, e.g. unused ad channels, just stay unset in the container).
build_secret_flags() {
  local existing
  existing="$(gcloud secrets list --format 'value(name)')"
  SECRET_FLAGS=""
  for key in "${SECRET_KEYS[@]}"; do
    if grep -qx "$key" <<< "$existing"; then
      SECRET_FLAGS+="${SECRET_FLAGS:+,}${key}=${key}:latest"
    else
      echo "WARN: secret $key not in Secret Manager — env var will be unset"
    fi
  done
}

build_api_auth_flag() {
  if [ "$ALLOW_UNAUTHENTICATED_API" = "true" ]; then
    API_AUTH_FLAG="--allow-unauthenticated"
    echo "WARN: attribution-api will be public because ALLOW_UNAUTHENTICATED_API=true (X-API-Key still required at the app layer)"
  else
    API_AUTH_FLAG="--no-allow-unauthenticated"
  fi
}

# ── One-time: seed Secret Manager from local .env ────────────────────────────
seed_secrets() {
  [ -f "$ENV_FILE" ] || { echo "ERROR: $ENV_FILE not found"; exit 1; }
  echo "Seeding Secret Manager from $ENV_FILE ..."
  local seeded=0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"                                   # CRLF guard
    [[ "$line" =~ ^[[:space:]]*# || -z "${line// }" ]] && continue
    local key="${line%%=*}" val="${line#*=}"               # split on FIRST '=' only
    key="$(echo "$key" | xargs)"
    # strip inline comments only when preceded by whitespace, then surrounding quotes
    val="$(printf '%s' "$val" | sed -E 's/[[:space:]]+#.*$//; s/^"(.*)"$/\1/; s/^'\''(.*)'\''$/\1/')"
    [[ " ${SECRET_KEYS[*]} " == *" $key "* ]] || continue
    [ -n "$val" ] || { echo "  skip $key (empty)"; continue; }
    gcloud secrets describe "$key" >/dev/null 2>&1 \
      || gcloud secrets create "$key" --replication-policy automatic
    printf '%s' "$val" | gcloud secrets versions add "$key" --data-file=-
    echo "  seeded $key"
    seeded=$((seeded + 1))
  done < "$ENV_FILE"
  echo "Done — $seeded secrets seeded. Verify one:"
  echo "  gcloud secrets versions access latest --secret DATABRICKS_HTTP_PATH"
}

if [ "${1:-}" = "--seed-secrets" ]; then
  seed_secrets
  exit 0
fi

# ── 1. Enable APIs ───────────────────────────────────────────────────────────
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  storage.googleapis.com

# ── 2. Artifact Registry ─────────────────────────────────────────────────────
gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 \
  || gcloud artifacts repositories create "$REPO" \
       --repository-format=docker --location "$REGION" \
       --description "Attribution agent images"

# Cloud Build runs as the compute default SA and needs push + log rights.
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format 'value(projectNumber)')"
BUILD_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud artifacts repositories add-iam-policy-binding "$REPO" --location "$REGION" \
  --member "serviceAccount:${BUILD_SA}" --role roles/artifactregistry.writer >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:${BUILD_SA}" --role roles/logging.logWriter --quiet >/dev/null

# ── 3. Service accounts + IAM ────────────────────────────────────────────────
gcloud iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "$SA_RUNTIME_NAME" --display-name "Attribution runtime"
gcloud iam service-accounts describe "$SCHED_SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "$SA_SCHEDULER_NAME" --display-name "Attribution scheduler"

# The command center creates per-client credential secrets at onboarding time.
# For internal pilots this keeps onboarding fast; replace with a narrower custom
# role before opening self-service agency access.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member "serviceAccount:${RUNTIME_SA}" --role roles/secretmanager.admin --quiet >/dev/null

# Known global secrets still bind explicitly so deployments remain compatible
# if the runtime role is narrowed later.
for key in "${SECRET_KEYS[@]}"; do
  gcloud secrets add-iam-policy-binding "$key" \
    --member "serviceAccount:${RUNTIME_SA}" \
    --role roles/secretmanager.secretAccessor --quiet >/dev/null \
    || echo "WARN: could not bind $key (run --seed-secrets first?)"
done

build_secret_flags
build_api_auth_flag

# Client-registry bucket (objectAdmin: the admin portal writes clients.json)
gcloud storage buckets describe "gs://${REGISTRY_BUCKET}" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://${REGISTRY_BUCKET}" \
       --location "$REGION" --uniform-bucket-level-access
gcloud storage buckets add-iam-policy-binding "gs://${REGISTRY_BUCKET}" \
  --member "serviceAccount:${RUNTIME_SA}" --role roles/storage.objectAdmin >/dev/null

# Immutable raw vendor pages for replay and audit. Default retention is 90 days.
gcloud storage buckets describe "gs://${RAW_ARCHIVE_BUCKET}" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://${RAW_ARCHIVE_BUCKET}" \
       --location "$REGION" --uniform-bucket-level-access
gcloud storage buckets update "gs://${RAW_ARCHIVE_BUCKET}" \
  --lifecycle-file attribution_agent/attribution_agent/config/raw_archive_lifecycle.json
gcloud storage buckets add-iam-policy-binding "gs://${RAW_ARCHIVE_BUCKET}" \
  --member "serviceAccount:${RUNTIME_SA}" --role roles/storage.objectCreator >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://${RAW_ARCHIVE_BUCKET}" \
  --member "serviceAccount:${RUNTIME_SA}" --role roles/storage.objectViewer >/dev/null

# ── 4. Build & push (Cloud Build — no local Docker needed) ───────────────────
GIT_SHA="$(git rev-parse --short HEAD)"
COMMON_ENV+=",GIT_SHA=${GIT_SHA}"
gcloud builds submit --tag "${IMAGE}:${GIT_SHA}" .
gcloud artifacts docker tags add "${IMAGE}:${GIT_SHA}" "${IMAGE}:latest"

# ── 5. Cloud Run Job (pipeline) ──────────────────────────────────────────────
# --task-timeout 3600 matches the old bundle's timeout_seconds (default is 10m).
# Email claims and Delta MERGEs make one infrastructure retry safe.
gcloud run jobs deploy "$JOB_PIPELINE" \
  --image "${IMAGE}:${GIT_SHA}" \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --command python \
  --args "flows/agency_flow.py" \
  --set-secrets "$SECRET_FLAGS" \
  --set-env-vars "$COMMON_ENV" \
  --add-volume "name=registry,type=cloud-storage,bucket=${REGISTRY_BUCKET}" \
  --add-volume-mount "volume=registry,mount-path=/mnt/registry" \
  --task-timeout 3600 \
  --max-retries 1 \
  --tasks "$PIPELINE_TASKS" \
  --parallelism "$PIPELINE_PARALLELISM" \
  --memory 2Gi --cpu 2

# The launcher uses execution overrides, which require run.jobs.runWithOverrides.
gcloud run jobs add-iam-policy-binding "$JOB_PIPELINE" --region "$REGION" \
  --member "serviceAccount:${RUNTIME_SA}" --role roles/run.developer >/dev/null

gcloud run jobs deploy "$JOB_FINALIZER" \
  --image "${IMAGE}:${GIT_SHA}" \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --command python \
  --args "flows/benchmark_finalizer.py" \
  --set-secrets "$SECRET_FLAGS" \
  --set-env-vars "$COMMON_ENV" \
  --add-volume "name=registry,type=cloud-storage,bucket=${REGISTRY_BUCKET}" \
  --add-volume-mount "volume=registry,mount-path=/mnt/registry" \
  --task-timeout 3600 --max-retries 1 --tasks 1 --memory 1Gi --cpu 1
gcloud run jobs add-iam-policy-binding "$JOB_FINALIZER" --region "$REGION" \
  --member "serviceAccount:${RUNTIME_SA}" --role roles/run.developer >/dev/null

gcloud run jobs deploy "$JOB_LAUNCHER" \
  --image "${IMAGE}:${GIT_SHA}" \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --command python \
  --args "flows/job_launcher.py" \
  --set-secrets "$SECRET_FLAGS" \
  --set-env-vars "$COMMON_ENV" \
  --add-volume "name=registry,type=cloud-storage,bucket=${REGISTRY_BUCKET}" \
  --add-volume-mount "volume=registry,mount-path=/mnt/registry" \
  --task-timeout 9000 --max-retries 0 --tasks 1 --memory 1Gi --cpu 1
gcloud run jobs add-iam-policy-binding "$JOB_LAUNCHER" --region "$REGION" \
  --member "serviceAccount:${SCHED_SA}" --role roles/run.invoker >/dev/null

gcloud run jobs deploy "$JOB_MAINTENANCE" \
  --image "${IMAGE}:${GIT_SHA}" \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --command python \
  --args "flows/delta_maintenance.py" \
  --set-secrets "$SECRET_FLAGS" \
  --set-env-vars "$COMMON_ENV" \
  --add-volume "name=registry,type=cloud-storage,bucket=${REGISTRY_BUCKET}" \
  --add-volume-mount "volume=registry,mount-path=/mnt/registry" \
  --task-timeout 3600 --max-retries 1 --tasks 1 --memory 1Gi --cpu 1
gcloud run jobs add-iam-policy-binding "$JOB_MAINTENANCE" --region "$REGION" \
  --member "serviceAccount:${SCHED_SA}" --role roles/run.invoker >/dev/null

gcloud run jobs deploy "$JOB_OPERATOR_HEALTH" \
  --image "${IMAGE}:${GIT_SHA}" \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --command python \
  --args "flows/operator_health_check.py" \
  --set-secrets "$SECRET_FLAGS" \
  --set-env-vars "$COMMON_ENV" \
  --add-volume "name=registry,type=cloud-storage,bucket=${REGISTRY_BUCKET}" \
  --add-volume-mount "volume=registry,mount-path=/mnt/registry" \
  --task-timeout 900 --max-retries 1 --tasks 1 --memory 1Gi --cpu 1
gcloud run jobs add-iam-policy-binding "$JOB_OPERATOR_HEALTH" --region "$REGION" \
  --member "serviceAccount:${SCHED_SA}" --role roles/run.invoker >/dev/null

# ── 6. Cloud Run Service (FastAPI REST layer) ───────────────────────────────
# Same image, default FastAPI command. The Vercel Command Center can call this
# service for approval mutations when ARIE_API_BASE and ARIE_API_KEY are set.
gcloud run deploy "$SERVICE_API" \
  --image "${IMAGE}:${GIT_SHA}" \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --port 8080 \
  --command uvicorn \
  --args "api.main:app,--host,0.0.0.0,--port,8080" \
  "$API_AUTH_FLAG" \
  --set-secrets "$SECRET_FLAGS" \
  --set-env-vars "$COMMON_ENV" \
  --add-volume "name=registry,type=cloud-storage,bucket=${REGISTRY_BUCKET}" \
  --add-volume-mount "volume=registry,mount-path=/mnt/registry" \
  --timeout 300 \
  --max-instances 3 \
  --memory 1Gi --cpu 1

if [ -n "$COMMAND_CENTER_SA" ]; then
  gcloud run services add-iam-policy-binding "$SERVICE_API" --region "$REGION" \
    --member "serviceAccount:${COMMAND_CENTER_SA}" --role roles/run.invoker >/dev/null
  echo "Granted $COMMAND_CENTER_SA invoker access to $SERVICE_API"
elif [ "$ALLOW_UNAUTHENTICATED_API" != "true" ]; then
  echo "NOTE: attribution-api is private. Set COMMAND_CENTER_SA=name@project.iam.gserviceaccount.com on deploy to grant a trusted command-center service account access."
fi

# ── 7. Seed clients.json into the registry bucket (first deploy only) ────────
LOCAL_REGISTRY="attribution_agent/attribution_agent/config/client_registry.local.json"
if [ -f "$LOCAL_REGISTRY" ] \
   && ! gcloud storage objects describe "gs://${REGISTRY_BUCKET}/clients.json" >/dev/null 2>&1; then
  gcloud storage cp "$LOCAL_REGISTRY" "gs://${REGISTRY_BUCKET}/clients.json"
fi

# ── 8. Cloud Scheduler → Cloud Run Job ───────────────────────────────────────
# OAuth (not OIDC): the target is *.googleapis.com. The :run call returns
# immediately; the job's own --task-timeout governs the pipeline.
# MONTHLY, matching the old Databricks job (9am ET on the 1st) — client
# reports go out once a month. Override with SCHEDULE="0 2 * * *" for nightly.
SCHEDULE="${SCHEDULE:-0 9 1 * *}"
if gcloud scheduler jobs describe "$SCHEDULER_JOB" --location "$REGION" >/dev/null 2>&1; then
  SCHED_VERB="update"
else
  SCHED_VERB="create"
fi
gcloud scheduler jobs "$SCHED_VERB" http "$SCHEDULER_JOB" \
  --location "$REGION" \
  --schedule "$SCHEDULE" \
  --time-zone "America/New_York" \
  --uri "https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_LAUNCHER}:run" \
  --http-method POST \
  --oauth-service-account-email "$SCHED_SA" \
  --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform" \
  --attempt-deadline 300s

MAINTENANCE_SCHEDULE="${MAINTENANCE_SCHEDULE:-0 3 * * 0}"
if gcloud scheduler jobs describe "$SCHEDULER_MAINTENANCE_JOB" --location "$REGION" >/dev/null 2>&1; then
  MAINTENANCE_SCHED_VERB="update"
else
  MAINTENANCE_SCHED_VERB="create"
fi
gcloud scheduler jobs "$MAINTENANCE_SCHED_VERB" http "$SCHEDULER_MAINTENANCE_JOB" \
  --location "$REGION" \
  --schedule "$MAINTENANCE_SCHEDULE" \
  --time-zone "America/New_York" \
  --uri "https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_MAINTENANCE}:run" \
  --http-method POST \
  --oauth-service-account-email "$SCHED_SA" \
  --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform" \
  --attempt-deadline 300s

HEALTH_SCHEDULE="${HEALTH_SCHEDULE:-0 8 * * *}"
if gcloud scheduler jobs describe "$SCHEDULER_HEALTH_JOB" --location "$REGION" >/dev/null 2>&1; then
  HEALTH_SCHED_VERB="update"
else
  HEALTH_SCHED_VERB="create"
fi
gcloud scheduler jobs "$HEALTH_SCHED_VERB" http "$SCHEDULER_HEALTH_JOB" \
  --location "$REGION" \
  --schedule "$HEALTH_SCHEDULE" \
  --time-zone "America/New_York" \
  --uri "https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_OPERATOR_HEALTH}:run" \
  --http-method POST \
  --oauth-service-account-email "$SCHED_SA" \
  --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform" \
  --attempt-deadline 300s

echo ""
echo "── Deployed ──────────────────────────────────────────────"
echo "Image:     ${IMAGE}:${GIT_SHA}"
echo "API:       $(gcloud run services describe "$SERVICE_API" --region "$REGION" --format 'value(status.url)')"
echo "Job:       gcloud run jobs execute $JOB_LAUNCHER --region $REGION --args 'flows/job_launcher.py,--dry-run,--expected-client-count,20' --wait"
echo "Scheduler: $SCHEDULER_JOB ($SCHEDULE America/New_York)"
echo "Maintenance: $SCHEDULER_MAINTENANCE_JOB ($MAINTENANCE_SCHEDULE America/New_York)"
echo "Health:    $SCHEDULER_HEALTH_JOB ($HEALTH_SCHEDULE America/New_York)"
