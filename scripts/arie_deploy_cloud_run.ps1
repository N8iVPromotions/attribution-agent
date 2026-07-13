param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [string]$Region = "us-central1",
    [string]$Repo = "attribution",
    [string]$RegistryBucket = "",
    [string]$OperatorPrincipal = "",
    [switch]$AllowUnauthenticatedUi
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

$image = "$Region-docker.pkg.dev/$ProjectId/$Repo/attribution-agent"
$serviceUi = "attribution-ui"
$jobPipeline = "attribution-pipeline"
$schedulerJob = "attribution-monthly"
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
    "API_KEY_ADMIN"
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

$gitSha = Get-GitSha
Invoke-Gcloud builds submit --tag "$image`:$gitSha" . --project $ProjectId
Invoke-Gcloud artifacts docker tags add "$image`:$gitSha" "$image`:latest" --project $ProjectId

$commonEnv = @(
    "ATTRIBUTION_CLIENT_REGISTRY_BACKEND=local",
    "ATTRIBUTION_CLIENT_REGISTRY_PATH=/mnt/registry/clients.json",
    "ATTRIBUTION_CATALOG=workspace",
    "ATTRIBUTION_OPS_SCHEMA=workspace.attribution_ops",
    "COMMS_PROVIDER=gmail",
    "GOOGLE_CLOUD_PROJECT=$ProjectId",
    "ATTRIBUTION_CLOUD_RUN_JOB=$jobPipeline",
    "ATTRIBUTION_CLOUD_RUN_REGION=$Region"
) -join ","

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
    "--max-retries", "0",
    "--tasks", "1",
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
    --member "serviceAccount:$schedulerSa" `
    --role roles/run.invoker `
    --project $ProjectId
Invoke-Gcloud run jobs add-iam-policy-binding $jobPipeline `
    --region $Region `
    --member "serviceAccount:$runtimeSa" `
    --role roles/run.invoker `
    --project $ProjectId

$uiAuthFlag = if ($AllowUnauthenticatedUi) { "--allow-unauthenticated" } else { "--no-allow-unauthenticated" }
if ($AllowUnauthenticatedUi) {
    Write-Host "WARN: attribution-ui will be public because -AllowUnauthenticatedUi was provided" -ForegroundColor Yellow
}

$serviceArgs = @(
    "run", "deploy", $serviceUi,
    "--image", "$image`:$gitSha",
    "--region", $Region,
    "--service-account", $runtimeSa,
    "--port", "8080",
    $uiAuthFlag,
    "--set-env-vars", $commonEnv,
    "--add-volume", "name=registry,type=cloud-storage,bucket=$RegistryBucket",
    "--add-volume-mount", "volume=registry,mount-path=/mnt/registry",
    "--timeout", "3600",
    "--session-affinity",
    "--min-instances", "1",
    "--max-instances", "1",
    "--no-cpu-throttling",
    "--memory", "2Gi",
    "--cpu", "2",
    "--project", $ProjectId
)
if ($secretFlags) {
    $serviceArgs += @("--set-secrets", $secretFlags)
}
Invoke-Gcloud @serviceArgs

if ($OperatorPrincipal) {
    Invoke-Gcloud run services add-iam-policy-binding $serviceUi `
        --region $Region `
        --member $OperatorPrincipal `
        --role roles/run.invoker `
        --project $ProjectId
    Write-Host "Granted $OperatorPrincipal access to $serviceUi" -ForegroundColor Green
} elseif (-not $AllowUnauthenticatedUi) {
    Write-Host "NOTE: attribution-ui is private. Pass -OperatorPrincipal user:you@example.com to grant access." -ForegroundColor Yellow
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
    --uri "https://run.googleapis.com/v2/projects/$ProjectId/locations/$Region/jobs/$jobPipeline`:run" `
    --http-method POST `
    --oauth-service-account-email $schedulerSa `
    --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform" `
    --attempt-deadline "300s" `
    --project $ProjectId

$serviceUrl = (& $gcloud.Source run services describe $serviceUi --region $Region --project $ProjectId --format="value(status.url)").Trim()

Write-Host ""
Write-Host "Deployed ARIE" -ForegroundColor Green
Write-Host "Image:     $image`:$gitSha"
Write-Host "UI:        $serviceUrl"
Write-Host "Job:       gcloud run jobs execute $jobPipeline --project $ProjectId --region $Region --args `"flows/agency_flow.py,--agency,demo_agency,--dry-run`" --wait"
Write-Host "Scheduler: $schedulerJob ($schedule America/New_York)"
