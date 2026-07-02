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
#   PROJECT_ID=my-project ./deploy.sh                  # build image + deploy job, UI, scheduler
#
# Per-agency manual run (execute-time args override the deployed ones):
#   gcloud run jobs execute attribution-pipeline --region "$REGION" \
#     --args "flows/agency_flow.py,--agency,demo_agency,--dry-run" --wait
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────
PROJECT_ID="${PROJECT_ID:?set PROJECT_ID, e.g. PROJECT_ID=my-project ./deploy.sh}"
REGION="${REGION:-us-central1}"
REPO="${REPO:-attribution}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/attribution-agent"
SERVICE_UI="attribution-ui"
JOB_PIPELINE="attribution-pipeline"
SCHEDULER_JOB="attribution-monthly"
REGISTRY_BUCKET="${REGISTRY_BUCKET:-${PROJECT_ID}-attribution-registry}"
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
)

# Non-secret runtime config shared by the Job and the UI service.
# Registry lives on a GCS FUSE mount so it survives restarts; backend=local
# makes client_config read/write the JSON file instead of the Delta table.
COMMON_ENV="ATTRIBUTION_CLIENT_REGISTRY_BACKEND=local"
COMMON_ENV+=",ATTRIBUTION_CLIENT_REGISTRY_PATH=/mnt/registry/clients.json"
COMMON_ENV+=",ATTRIBUTION_CATALOG=workspace"
COMMON_ENV+=",ATTRIBUTION_OPS_SCHEMA=workspace.attribution_ops"
COMMON_ENV+=",COMMS_PROVIDER=gmail"
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

# ── 3. Service accounts + IAM ────────────────────────────────────────────────
gcloud iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "$SA_RUNTIME_NAME" --display-name "Attribution runtime"
gcloud iam service-accounts describe "$SCHED_SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "$SA_SCHEDULER_NAME" --display-name "Attribution scheduler"

# Least privilege: per-secret accessor bindings, not project-wide
for key in "${SECRET_KEYS[@]}"; do
  gcloud secrets add-iam-policy-binding "$key" \
    --member "serviceAccount:${RUNTIME_SA}" \
    --role roles/secretmanager.secretAccessor --quiet >/dev/null \
    || echo "WARN: could not bind $key (run --seed-secrets first?)"
done

build_secret_flags

# Client-registry bucket (objectAdmin: the admin portal writes clients.json)
gcloud storage buckets describe "gs://${REGISTRY_BUCKET}" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://${REGISTRY_BUCKET}" \
       --location "$REGION" --uniform-bucket-level-access
gcloud storage buckets add-iam-policy-binding "gs://${REGISTRY_BUCKET}" \
  --member "serviceAccount:${RUNTIME_SA}" --role roles/storage.objectAdmin >/dev/null

# ── 4. Build & push (Cloud Build — no local Docker needed) ───────────────────
GIT_SHA="$(git rev-parse --short HEAD)"
gcloud builds submit --tag "${IMAGE}:${GIT_SHA}" .
gcloud artifacts docker tags add "${IMAGE}:${GIT_SHA}" "${IMAGE}:latest"

# ── 5. Cloud Run Job (pipeline) ──────────────────────────────────────────────
# --task-timeout 3600 matches the old bundle's timeout_seconds (default is 10m).
# --max-retries 0: a retry after partial success would double-send emails.
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
  --max-retries 0 \
  --tasks 1 \
  --memory 2Gi --cpu 2

# Scheduler SA may execute the job (job-scoped, not project-wide)
gcloud run jobs add-iam-policy-binding "$JOB_PIPELINE" --region "$REGION" \
  --member "serviceAccount:${SCHED_SA}" --role roles/run.invoker >/dev/null

# ── 6. Cloud Run Service (Streamlit UI) ──────────────────────────────────────
# Streamlit needs: long request timeout (websocket), sticky single instance
# (session state is per-instance), and CPU outside requests for the ARIE
# Telegram long-poll thread + in-process pipeline runs. min-instances=1 +
# no-cpu-throttling switches billing to instance-based — drop both if ARIE
# in the UI is expendable and cold starts are acceptable.
gcloud run deploy "$SERVICE_UI" \
  --image "${IMAGE}:${GIT_SHA}" \
  --region "$REGION" \
  --service-account "$RUNTIME_SA" \
  --port 8080 \
  --allow-unauthenticated \
  --set-secrets "$SECRET_FLAGS" \
  --set-env-vars "$COMMON_ENV" \
  --add-volume "name=registry,type=cloud-storage,bucket=${REGISTRY_BUCKET}" \
  --add-volume-mount "volume=registry,mount-path=/mnt/registry" \
  --timeout 3600 \
  --session-affinity \
  --min-instances 1 \
  --max-instances 1 \
  --no-cpu-throttling \
  --memory 2Gi --cpu 2

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
  --uri "https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_PIPELINE}:run" \
  --http-method POST \
  --oauth-service-account-email "$SCHED_SA" \
  --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform" \
  --attempt-deadline 300s

echo ""
echo "── Deployed ──────────────────────────────────────────────"
echo "Image:     ${IMAGE}:${GIT_SHA}"
echo "UI:        $(gcloud run services describe "$SERVICE_UI" --region "$REGION" --format 'value(status.url)')"
echo "Job:       gcloud run jobs execute $JOB_PIPELINE --region $REGION --args 'flows/agency_flow.py,--agency,demo_agency,--dry-run' --wait"
echo "Scheduler: $SCHEDULER_JOB ($SCHEDULE America/New_York)"
