param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [string]$Region = "us-central1",
    [string]$Repo = "attribution",
    [string]$RegistryBucket = "",
    [string]$RawArchiveBucket = "",
    [int]$PipelineTasks = 20,
    [int]$PipelineParallelism = 5,
    [string]$CommandCenterServiceAccount = "",
    [switch]$AllowGlobalConnectorCredentials
)

$ErrorActionPreference = "Stop"

$gcloud = Get-Command gcloud.cmd -ErrorAction SilentlyContinue
if (-not $gcloud) {
    $gcloud = Get-Command gcloud -ErrorAction SilentlyContinue
}
if (-not $gcloud) {
    throw "gcloud is not on PATH. Install Google Cloud SDK or open a terminal where it is available."
}

if (-not $RegistryBucket) {
    $RegistryBucket = "$ProjectId-attribution-registry"
}
if (-not $RawArchiveBucket) {
    $RawArchiveBucket = "$ProjectId-attribution-raw"
}

$image = "$Region-docker.pkg.dev/$ProjectId/$Repo/attribution-agent"
$serviceApi = "attribution-api"
$jobPipeline = "attribution-pipeline"
$jobLauncher = "attribution-launcher"
$jobFinalizer = "attribution-benchmark-finalizer"
$jobMaintenance = "attribution-delta-maintenance"
$jobOperatorHealth = "attribution-operator-health"
$schedulerJob = "attribution-monthly"
$schedulerMaintenanceJob = "attribution-weekly-maintenance"
$schedulerHealthJob = "attribution-daily-health"
$runtimeSaName = "attribution-runtime"
$schedulerSaName = "attribution-scheduler"
$runtimeSa = "$runtimeSaName@$ProjectId.iam.gserviceaccount.com"
$schedulerSa = "$schedulerSaName@$ProjectId.iam.gserviceaccount.com"

$secretKeys = @(
    "META_ACCESS_TOKEN",
    "HUBSPOT_ACCESS_TOKEN",
    "STRIPE_SECRET_KEY",
    "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_ADS_CLIENT_ID",
    "GOOGLE_ADS_CLIENT_SECRET",
    "GOOGLE_ADS_REFRESH_TOKEN",
    "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
    "LINKEDIN_ACCESS_TOKEN",
    "TIKTOK_ACCESS_TOKEN",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GMAIL_SENDER",
    "GMAIL_APP_PASSWORD",
    "SENDGRID_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "DATABRICKS_SERVER_HOSTNAME",
    "DATABRICKS_HTTP_PATH",
    "DATABRICKS_TOKEN",
    "API_KEY_ADMIN",
    "ARIE_BOOTSTRAP_ADMIN_PASSWORD"
)

function Invoke-Gcloud {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $gcloud.Source @Args
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldPreference
    }
    if ($exitCode -ne 0) {
        throw "gcloud failed with exit code ${exitCode}: $($Args -join ' ')"
    }
}

function Invoke-GcloudQuiet {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $gcloud.Source @Args *> $null
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldPreference
    }
}

function Get-GitSha {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        $sha = (& $git.Source rev-parse --short HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $sha) {
            return $sha.Trim()
        }
    }
    return (Get-Date -Format "yyyyMMddHHmmss")
}

function Build-SecretFlags {
    $existing = @(& $gcloud.Source secrets list --project $ProjectId --format="value(name)")
    $flags = @()
    foreach ($key in $secretKeys) {
        if ($existing -contains $key) {
            $flags += "$key=$key`:latest"
        } else {
            Write-Host "WARN: secret $key not in Secret Manager - env var will be unset" -ForegroundColor Yellow
        }
    }
    return ($flags -join ",")
}

Write-Host ""
Write-Host "Deploying ARIE to Cloud Run" -ForegroundColor Cyan
Write-Host "Project: $ProjectId"
Write-Host "Region:  $Region"
Write-Host "Image:   $image"
Write-Host ""

Invoke-Gcloud services enable `
    run.googleapis.com `
    artifactregistry.googleapis.com `
    cloudbuild.googleapis.com `
    secretmanager.googleapis.com `
    cloudscheduler.googleapis.com `
    storage.googleapis.com `
    --project $ProjectId

if ((Invoke-GcloudQuiet artifacts repositories describe $Repo --location $Region --project $ProjectId) -ne 0) {
    Invoke-Gcloud artifacts repositories create $Repo `
        --repository-format=docker `
        --location $Region `
        --description "ARIE attribution agent images" `
        --project $ProjectId
}

$projectNumber = (& $gcloud.Source projects describe $ProjectId --format="value(projectNumber)").Trim()
if (-not $projectNumber) {
    throw "Could not resolve project number for $ProjectId"
}

$buildSa = "$projectNumber-compute@developer.gserviceaccount.com"
Invoke-Gcloud artifacts repositories add-iam-policy-binding $Repo `
    --location $Region `
    --member "serviceAccount:$buildSa" `
    --role roles/artifactregistry.writer `
    --project $ProjectId
Invoke-Gcloud projects add-iam-policy-binding $ProjectId `
    --member "serviceAccount:$buildSa" `
    --role roles/logging.logWriter `
    --quiet

if ((Invoke-GcloudQuiet iam service-accounts describe $runtimeSa --project $ProjectId) -ne 0) {
    Invoke-Gcloud iam service-accounts create $runtimeSaName `
        --display-name "Attribution runtime" `
        --project $ProjectId
}
if ((Invoke-GcloudQuiet iam service-accounts describe $schedulerSa --project $ProjectId) -ne 0) {
    Invoke-Gcloud iam service-accounts create $schedulerSaName `
        --display-name "Attribution scheduler" `
        --project $ProjectId
}

Invoke-Gcloud projects add-iam-policy-binding $ProjectId `
    --member "serviceAccount:$runtimeSa" `
    --role roles/secretmanager.admin `
    --quiet
Invoke-Gcloud projects add-iam-policy-binding $ProjectId `
    --member "serviceAccount:$runtimeSa" `
    --role roles/run.viewer `
    --quiet

foreach ($key in $secretKeys) {
    if ((Invoke-GcloudQuiet secrets describe $key --project $ProjectId) -eq 0) {
        Invoke-Gcloud secrets add-iam-policy-binding $key `
            --member "serviceAccount:$runtimeSa" `
            --role roles/secretmanager.secretAccessor `
            --project $ProjectId `
            --quiet
    }
}

$secretFlags = Build-SecretFlags

if ((Invoke-GcloudQuiet storage buckets describe "gs://$RegistryBucket" --project $ProjectId) -ne 0) {
    Invoke-Gcloud storage buckets create "gs://$RegistryBucket" `
        --location $Region `
        --uniform-bucket-level-access `
        --project $ProjectId
}
Invoke-Gcloud storage buckets add-iam-policy-binding "gs://$RegistryBucket" `
    --member "serviceAccount:$runtimeSa" `
    --role roles/storage.objectAdmin `
    --project $ProjectId

if ((Invoke-GcloudQuiet storage buckets describe "gs://$RawArchiveBucket" --project $ProjectId) -ne 0) {
    Invoke-Gcloud storage buckets create "gs://$RawArchiveBucket" `
        --location $Region `
        --uniform-bucket-level-access `
        --project $ProjectId
}
Invoke-Gcloud storage buckets update "gs://$RawArchiveBucket" `
    --lifecycle-file "attribution_agent/attribution_agent/config/raw_archive_lifecycle.json" `
    --project $ProjectId
Invoke-Gcloud storage buckets add-iam-policy-binding "gs://$RawArchiveBucket" `
    --member "serviceAccount:$runtimeSa" `
    --role roles/storage.objectCreator `
    --project $ProjectId
Invoke-Gcloud storage buckets add-iam-policy-binding "gs://$RawArchiveBucket" `
    --member "serviceAccount:$runtimeSa" `
    --role roles/storage.objectViewer `
    --project $ProjectId

$gitSha = Get-GitSha
Invoke-Gcloud builds submit --tag "$image`:$gitSha" . --project $ProjectId
Invoke-Gcloud artifacts docker tags add "$image`:$gitSha" "$image`:latest" --project $ProjectId
$allowGlobalCredentialsValue = $AllowGlobalConnectorCredentials.IsPresent.ToString().ToLowerInvariant()

$commonEnv = @(
    "ATTRIBUTION_CLIENT_REGISTRY_BACKEND=delta",
    "ATTRIBUTION_CLIENT_REGISTRY_PATH=/mnt/registry/clients.json",
    "ATTRIBUTION_CATALOG=workspace",
    "ATTRIBUTION_OPS_SCHEMA=workspace.attribution_ops",
    "COMMS_PROVIDER=gmail",
    "GOOGLE_CLOUD_PROJECT=$ProjectId",
    "ATTRIBUTION_CLOUD_RUN_JOB=$jobPipeline",
    "ATTRIBUTION_LAUNCHER_CLOUD_RUN_JOB=$jobLauncher",
    "ATTRIBUTION_CLOUD_RUN_REGION=$Region",
    "ARIE_CLIENT_LOCK_BUCKET=$RegistryBucket",
    "ARIE_CLIENT_LOCKS_ENABLED=true",
    "ARIE_RAW_ARCHIVE_BUCKET=$RawArchiveBucket",
    "ARIE_WORK_MANIFEST_BUCKET=$RegistryBucket",
    "ARIE_AI_BUDGET_BUCKET=$RegistryBucket",
    "ARIE_DELIVERY_IDEMPOTENCY_BUCKET=$RegistryBucket",
    "ARIE_INGEST_SOURCE_WORKERS=3",
    "ARIE_INGEST_BATCH_ROWS=5000",
    "ARIE_STREAMING_INGEST=true",
    "ARIE_MODEL_BUDGET_FAIL_CLOSED=true",
    "ARIE_ALLOW_GLOBAL_CONNECTOR_CREDENTIALS=$allowGlobalCredentialsValue",
    "ARIE_DAILY_AI_USD_LIMIT=25",
    "ARIE_MONTHLY_AI_USD_LIMIT=300",
    "ATTRIBUTION_BENCHMARK_FINALIZER_JOB=$jobFinalizer"
) -join ","
$commonEnv += ",GIT_SHA=$gitSha"

$jobArgs = @(
    "run", "jobs", "deploy", $jobPipeline,
    "--image", "$image`:$gitSha",
    "--region", $Region,
    "--service-account", $runtimeSa,
    "--command", "python",
    "--args", "flows/agency_flow.py",
    "--set-env-vars", $commonEnv,
    "--add-volume", "name=registry,type=cloud-storage,bucket=$RegistryBucket",
    "--add-volume-mount", "volume=registry,mount-path=/mnt/registry",
    "--task-timeout", "3600",
    "--max-retries", "1",
    "--tasks", "$PipelineTasks",
    "--parallelism", "$PipelineParallelism",
    "--memory", "2Gi",
    "--cpu", "2",
    "--project", $ProjectId
)
if ($secretFlags) {
    $jobArgs += @("--set-secrets", $secretFlags)
}
Invoke-Gcloud @jobArgs

Invoke-Gcloud run jobs add-iam-policy-binding $jobPipeline `
    --region $Region `
    --member "serviceAccount:$runtimeSa" `
    --role roles/run.developer `
    --project $ProjectId

$supportJobs = @(
    @{ Name = $jobFinalizer; Script = "flows/benchmark_finalizer.py"; Timeout = "3600"; Memory = "1Gi"; Retries = "1" },
    @{ Name = $jobLauncher; Script = "flows/job_launcher.py"; Timeout = "9000"; Memory = "1Gi"; Retries = "0" },
    @{ Name = $jobMaintenance; Script = "flows/delta_maintenance.py"; Timeout = "3600"; Memory = "1Gi"; Retries = "1" },
    @{ Name = $jobOperatorHealth; Script = "flows/operator_health_check.py"; Timeout = "900"; Memory = "1Gi"; Retries = "1" }
)
foreach ($job in $supportJobs) {
    $supportArgs = @(
        "run", "jobs", "deploy", $job.Name,
        "--image", "$image`:$gitSha",
        "--region", $Region,
        "--service-account", $runtimeSa,
        "--command", "python",
        "--args", $job.Script,
        "--set-env-vars", $commonEnv,
        "--add-volume", "name=registry,type=cloud-storage,bucket=$RegistryBucket",
        "--add-volume-mount", "volume=registry,mount-path=/mnt/registry",
        "--task-timeout", $job.Timeout,
        "--max-retries", $job.Retries,
        "--tasks", "1",
        "--memory", $job.Memory,
        "--cpu", "1",
        "--project", $ProjectId
    )
    if ($secretFlags) {
        $supportArgs += @("--set-secrets", $secretFlags)
    }
    Invoke-Gcloud @supportArgs
}
Invoke-Gcloud run jobs add-iam-policy-binding $jobFinalizer `
    --region $Region `
    --member "serviceAccount:$runtimeSa" `
    --role roles/run.developer `
    --project $ProjectId
foreach ($schedulerTarget in @($jobLauncher, $jobMaintenance, $jobOperatorHealth)) {
    Invoke-Gcloud run jobs add-iam-policy-binding $schedulerTarget `
        --region $Region `
        --member "serviceAccount:$schedulerSa" `
        --role roles/run.invoker `
        --project $ProjectId
}

$serviceArgs = @(
    "run", "deploy", $serviceApi,
    "--image", "$image`:$gitSha",
    "--region", $Region,
    "--service-account", $runtimeSa,
    "--port", "8080",
    "--command", "uvicorn",
    "--args", "api.main:app,--host,0.0.0.0,--port,8080",
    "--no-allow-unauthenticated",
    "--set-env-vars", $commonEnv,
    "--add-volume", "name=registry,type=cloud-storage,bucket=$RegistryBucket",
    "--add-volume-mount", "volume=registry,mount-path=/mnt/registry",
    "--timeout", "300",
    "--max-instances", "3",
    "--memory", "1Gi",
    "--cpu", "1",
    "--project", $ProjectId
)
if ($secretFlags) {
    $serviceArgs += @("--set-secrets", $secretFlags)
}
Invoke-Gcloud @serviceArgs

if ($CommandCenterServiceAccount) {
    Invoke-Gcloud run services add-iam-policy-binding $serviceApi `
        --region $Region `
        --member "serviceAccount:$CommandCenterServiceAccount" `
        --role roles/run.invoker `
        --project $ProjectId
    Write-Host "Granted $CommandCenterServiceAccount invoker access to $serviceApi" -ForegroundColor Green
} else {
    Write-Host "NOTE: attribution-api is private. Pass -CommandCenterServiceAccount name@project.iam.gserviceaccount.com if a trusted command-center service account needs access." -ForegroundColor Yellow
}

$localRegistry = "attribution_agent/attribution_agent/config/client_registry.local.json"
if ((Test-Path $localRegistry) -and (Invoke-GcloudQuiet storage objects describe "gs://$RegistryBucket/clients.json" --project $ProjectId) -ne 0) {
    Invoke-Gcloud storage cp $localRegistry "gs://$RegistryBucket/clients.json" --project $ProjectId
}

$schedule = if ($env:SCHEDULE) { $env:SCHEDULE } else { "0 9 1 * *" }
$schedulerVerb = if ((Invoke-GcloudQuiet scheduler jobs describe $schedulerJob --location $Region --project $ProjectId) -eq 0) { "update" } else { "create" }
Invoke-Gcloud scheduler jobs $schedulerVerb http $schedulerJob `
    --location $Region `
    --schedule $schedule `
    --time-zone "America/New_York" `
    --uri "https://run.googleapis.com/v2/projects/$ProjectId/locations/$Region/jobs/$jobLauncher`:run" `
    --http-method POST `
    --oauth-service-account-email $schedulerSa `
    --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform" `
    --attempt-deadline "300s" `
    --project $ProjectId

$maintenanceSchedule = if ($env:MAINTENANCE_SCHEDULE) { $env:MAINTENANCE_SCHEDULE } else { "0 3 * * 0" }
$maintenanceVerb = if ((Invoke-GcloudQuiet scheduler jobs describe $schedulerMaintenanceJob --location $Region --project $ProjectId) -eq 0) { "update" } else { "create" }
Invoke-Gcloud scheduler jobs $maintenanceVerb http $schedulerMaintenanceJob `
    --location $Region `
    --schedule $maintenanceSchedule `
    --time-zone "America/New_York" `
    --uri "https://run.googleapis.com/v2/projects/$ProjectId/locations/$Region/jobs/$jobMaintenance`:run" `
    --http-method POST `
    --oauth-service-account-email $schedulerSa `
    --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform" `
    --attempt-deadline "300s" `
    --project $ProjectId

$healthSchedule = if ($env:HEALTH_SCHEDULE) { $env:HEALTH_SCHEDULE } else { "0 8 * * *" }
$healthVerb = if ((Invoke-GcloudQuiet scheduler jobs describe $schedulerHealthJob --location $Region --project $ProjectId) -eq 0) { "update" } else { "create" }
Invoke-Gcloud scheduler jobs $healthVerb http $schedulerHealthJob `
    --location $Region `
    --schedule $healthSchedule `
    --time-zone "America/New_York" `
    --uri "https://run.googleapis.com/v2/projects/$ProjectId/locations/$Region/jobs/$jobOperatorHealth`:run" `
    --http-method POST `
    --oauth-service-account-email $schedulerSa `
    --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform" `
    --attempt-deadline "300s" `
    --project $ProjectId

$serviceUrl = (& $gcloud.Source run services describe $serviceApi --region $Region --project $ProjectId --format="value(status.url)").Trim()

Write-Host ""
Write-Host "Deployed ARIE" -ForegroundColor Green
Write-Host "Image:     $image`:$gitSha"
Write-Host "API:       $serviceUrl"
Write-Host "Job:       gcloud run jobs execute $jobLauncher --project $ProjectId --region $Region --args `"flows/job_launcher.py,--dry-run,--expected-client-count,20`" --wait"
Write-Host "Scheduler: $schedulerJob ($schedule America/New_York)"
Write-Host "Maintenance: $schedulerMaintenanceJob ($maintenanceSchedule America/New_York)"
Write-Host "Health:    $schedulerHealthJob ($healthSchedule America/New_York)"
