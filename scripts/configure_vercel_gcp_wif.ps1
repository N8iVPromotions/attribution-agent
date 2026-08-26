param(
    [string]$ProjectId = "n8iv-analytics-production",
    [string]$Region = "us-central1",
    [string]$JobName = "attribution-launcher",
    [string]$ApiServiceName = "attribution-api",
    [string]$VercelTeamSlug = "n8i-v-promotions",
    [string]$VercelProjectName = "arie-command-center",
    [ValidateSet("production", "preview", "development")]
    [string]$VercelEnvironment = "production",
    [string]$PoolId = "vercel",
    [string]$ProviderId = "vercel",
    [string]$ServiceAccountName = "vercel-arie-command-center",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"

$gcloudCommand = Get-Command gcloud.cmd -ErrorAction SilentlyContinue
if (-not $gcloudCommand) {
    $gcloudCommand = Get-Command gcloud -ErrorAction SilentlyContinue
}
if ($gcloudCommand) {
    $gcloudPath = $gcloudCommand.Source
} else {
    $candidate = Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
    if (Test-Path -LiteralPath $candidate) {
        $gcloudPath = $candidate
    }
}
if (-not $gcloudPath) {
    throw "gcloud was not found. Install Google Cloud CLI and authenticate first."
}

function Invoke-Gcloud {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    & $gcloudPath @Args
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud failed with exit code ${LASTEXITCODE}: $($Args -join ' ')"
    }
}

function Test-GcloudResource {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $gcloudPath @Args *> $null
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldPreference
    }
    return $exitCode -eq 0
}

$projectNumber = (& $gcloudPath projects describe $ProjectId --format="value(projectNumber)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $projectNumber) {
    throw "Could not resolve project number for $ProjectId."
}

if (-not (Test-GcloudResource run jobs describe $JobName --project $ProjectId --region $Region)) {
    throw "Cloud Run job $JobName does not exist in $ProjectId/$Region. Deploy the launcher before configuring Vercel access."
}
if (-not (Test-GcloudResource run services describe $ApiServiceName --project $ProjectId --region $Region)) {
    throw "Cloud Run service $ApiServiceName does not exist in $ProjectId/$Region. Deploy the control API before configuring Vercel access."
}

$issuer = "https://oidc.vercel.com/$VercelTeamSlug"
$subject = "owner:$VercelTeamSlug`:project:$VercelProjectName`:environment:$VercelEnvironment"
$condition = "assertion.sub=='$subject'"
$serviceAccountEmail = "$ServiceAccountName@$ProjectId.iam.gserviceaccount.com"
$principal = "principal://iam.googleapis.com/projects/$projectNumber/locations/global/workloadIdentityPools/$PoolId/subject/$subject"

Write-Host "Vercel to Cloud Run federation plan" -ForegroundColor Cyan
Write-Host "  Project:          $ProjectId ($projectNumber)"
Write-Host "  Cloud Run job:    $JobName ($Region)"
Write-Host "  Control API:      $ApiServiceName ($Region)"
Write-Host "  Issuer:           $issuer"
Write-Host "  Accepted subject: $subject"
Write-Host "  Service account:  $serviceAccountEmail"
Write-Host "  Job role:         roles/run.jobsExecutorWithOverrides"
Write-Host "  API role:         roles/run.invoker"

if (-not $Apply) {
    Write-Host "No resources changed. Re-run with -Apply after reviewing this production trust boundary." -ForegroundColor Yellow
    exit 0
}

Invoke-Gcloud services enable iamcredentials.googleapis.com sts.googleapis.com --project $ProjectId

if (-not (Test-GcloudResource iam service-accounts describe $serviceAccountEmail --project $ProjectId)) {
    Invoke-Gcloud iam service-accounts create $ServiceAccountName `
        --project $ProjectId `
        --display-name "Vercel ARIE Command Center"
}

if (-not (Test-GcloudResource iam workload-identity-pools describe $PoolId --project $ProjectId --location global)) {
    Invoke-Gcloud iam workload-identity-pools create $PoolId `
        --project $ProjectId `
        --location global `
        --display-name "Vercel Production"
}

if (-not (Test-GcloudResource iam workload-identity-pools providers describe $ProviderId --project $ProjectId --location global --workload-identity-pool $PoolId)) {
    Invoke-Gcloud iam workload-identity-pools providers create-oidc $ProviderId `
        --project $ProjectId `
        --location global `
        --workload-identity-pool $PoolId `
        --display-name "Vercel ARIE Production" `
        --issuer-uri $issuer `
        --attribute-mapping "google.subject=assertion.sub" `
        --attribute-condition $condition
} else {
    $providerJson = & $gcloudPath iam workload-identity-pools providers describe $ProviderId `
        --project $ProjectId `
        --location global `
        --workload-identity-pool $PoolId `
        --format json
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect the existing workload identity provider."
    }
    $provider = $providerJson | ConvertFrom-Json
    if ($provider.oidc.issuerUri -ne $issuer -or $provider.attributeCondition -ne $condition) {
        throw "Existing provider configuration differs from the requested production-only trust. Refusing to overwrite it."
    }
}

Invoke-Gcloud iam service-accounts add-iam-policy-binding $serviceAccountEmail `
    --project $ProjectId `
    --member $principal `
    --role roles/iam.workloadIdentityUser

Invoke-Gcloud run jobs add-iam-policy-binding $JobName `
    --project $ProjectId `
    --region $Region `
    --member "serviceAccount:$serviceAccountEmail" `
    --role roles/run.jobsExecutorWithOverrides

Invoke-Gcloud run services add-iam-policy-binding $ApiServiceName `
    --project $ProjectId `
    --region $Region `
    --member "serviceAccount:$serviceAccountEmail" `
    --role roles/run.invoker

Write-Host "Federation configured. Only the exact Vercel production subject can impersonate the service account and invoke ARIE." -ForegroundColor Green
