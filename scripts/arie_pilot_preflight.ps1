param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [string]$Region = "us-central1",
    [switch]$AfterDeploy
)

$ErrorActionPreference = "Stop"

$failures = 0
$script:GcloudCommand = $null

function Invoke-GcloudQuiet([string[]]$Arguments) {
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $script:GcloudCommand @Arguments 2>$null
        return [pscustomobject]@{
            ExitCode = $LASTEXITCODE
            Output = $output
        }
    } finally {
        $ErrorActionPreference = $oldPreference
    }
}

function Write-Pass($Message) {
    Write-Host "[OK]   $Message" -ForegroundColor Green
}

function Write-Warn($Message) {
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-Fail($Message) {
    $script:failures += 1
    Write-Host "[FAIL] $Message" -ForegroundColor Red
}

function Test-Gcloud() {
    $cmd = Get-Command gcloud.cmd -ErrorAction SilentlyContinue
    if (-not $cmd) {
        $cmd = Get-Command gcloud -ErrorAction SilentlyContinue
    }
    if (-not $cmd) {
        Write-Fail "gcloud is not on PATH. Install Google Cloud SDK or open a terminal where it is available."
        return $false
    }
    $script:GcloudCommand = $cmd.Source
    Write-Pass "gcloud found ($($cmd.Name))"
    return $true
}

function Test-Secret($Name, [switch]$Required) {
    $result = Invoke-GcloudQuiet @("secrets", "describe", $Name, "--project", $ProjectId)
    if ($result.ExitCode -eq 0) {
        Write-Pass "Secret exists: $Name"
        return
    }
    if ($Required) {
        Write-Fail "Missing required secret: $Name"
    } else {
        Write-Warn "Optional secret not found: $Name"
    }
}

function Test-Api($Name, $EnabledApis) {
    if ($EnabledApis -contains $Name) {
        Write-Pass "API enabled: $Name"
    } else {
        Write-Fail "API not enabled: $Name"
    }
}

Write-Host ""
Write-Host "ARIE Pilot Preflight" -ForegroundColor Cyan
Write-Host "Project: $ProjectId | Region: $Region"
Write-Host ""

if (-not (Test-Gcloud)) {
    exit 1
}

$accountResult = Invoke-GcloudQuiet @("auth", "list", "--filter=status:ACTIVE", "--format=value(account)")
$account = ($accountResult.Output | Select-Object -First 1)
if ($account) {
    Write-Pass "Active gcloud account: $account"
} else {
    Write-Fail "No active gcloud account. Run: gcloud auth login"
}

$configuredProjectResult = Invoke-GcloudQuiet @("config", "get-value", "project")
$configuredProject = $configuredProjectResult.Output
if ($configuredProject -eq $ProjectId) {
    Write-Pass "gcloud project is set to $ProjectId"
} else {
    Write-Warn "gcloud active project is '$configuredProject'. Deploy commands should pass PROJECT_ID=$ProjectId."
}

$envPath = Join-Path $PWD "attribution_agent/attribution_agent/.env"
if (Test-Path $envPath) {
    Write-Pass ".env file found at $envPath"
} else {
    Write-Warn ".env file not found. Secret seeding uses attribution_agent/attribution_agent/.env."
}

$enabledApisResult = Invoke-GcloudQuiet @("services", "list", "--enabled", "--project", $ProjectId, "--format=value(config.name)")
$enabledApis = @($enabledApisResult.Output)
foreach ($api in @(
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudscheduler.googleapis.com",
    "storage.googleapis.com"
)) {
    Test-Api $api $enabledApis
}

foreach ($secret in @(
    "DATABRICKS_SERVER_HOSTNAME",
    "DATABRICKS_HTTP_PATH",
    "DATABRICKS_TOKEN",
    "ANTHROPIC_API_KEY",
    "API_KEY_ADMIN"
)) {
    Test-Secret $secret -Required
}

foreach ($secret in @(
    "SENDGRID_API_KEY",
    "GMAIL_SENDER",
    "GMAIL_APP_PASSWORD",
    "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_ADS_CLIENT_ID",
    "GOOGLE_ADS_CLIENT_SECRET",
    "GOOGLE_ADS_LOGIN_CUSTOMER_ID"
)) {
    Test-Secret $secret
}

if ($AfterDeploy) {
    $serviceResult = Invoke-GcloudQuiet @("run", "services", "describe", "attribution-ui", "--project", $ProjectId, "--region", $Region)
    if ($serviceResult.ExitCode -eq 0) { Write-Pass "Cloud Run service exists: attribution-ui" } else { Write-Fail "Cloud Run service missing: attribution-ui" }

    $jobResult = Invoke-GcloudQuiet @("run", "jobs", "describe", "attribution-pipeline", "--project", $ProjectId, "--region", $Region)
    if ($jobResult.ExitCode -eq 0) { Write-Pass "Cloud Run job exists: attribution-pipeline" } else { Write-Fail "Cloud Run job missing: attribution-pipeline" }

    $schedulerResult = Invoke-GcloudQuiet @("scheduler", "jobs", "describe", "attribution-monthly", "--project", $ProjectId, "--location", $Region)
    if ($schedulerResult.ExitCode -eq 0) { Write-Pass "Cloud Scheduler job exists: attribution-monthly" } else { Write-Warn "Cloud Scheduler job missing: attribution-monthly" }

    $bucket = "$ProjectId-attribution-registry"
    $bucketResult = Invoke-GcloudQuiet @("storage", "buckets", "describe", "gs://$bucket", "--project", $ProjectId)
    if ($bucketResult.ExitCode -eq 0) { Write-Pass "Registry bucket exists: gs://$bucket" } else { Write-Fail "Registry bucket missing: gs://$bucket" }
}

Write-Host ""
if ($failures -gt 0) {
    Write-Host "[FAIL] $failures blocking preflight check(s) failed." -ForegroundColor Red
    exit 1
}

Write-Pass "Preflight passed."
Write-Host ""
Write-Host "Next:"
Write-Host "  PROJECT_ID=$ProjectId ./deploy.sh --seed-secrets"
Write-Host "  OPERATOR_PRINCIPAL=user:you@example.com PROJECT_ID=$ProjectId ./deploy.sh"
Write-Host "  .\scripts\arie_cloud_run_dry_run.ps1 -ProjectId $ProjectId -AgencyId <agency_id>"
