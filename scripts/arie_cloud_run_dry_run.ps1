param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [Parameter(Mandatory = $true)]
    [string]$AgencyId,

    [string]$Region = "us-central1",
    [string]$AttributionModel = "last_touch",
    [string]$ReportMonth = "",
    [string[]]$ClientIds = @()
)

$ErrorActionPreference = "Stop"
$slugPattern = '^[a-z][a-z0-9_]{0,62}$'

if ($AgencyId -cnotmatch $slugPattern) {
    throw "AgencyId must be a canonical lowercase slug."
}
if ($ReportMonth -and $ReportMonth -cnotmatch '^\d{4}-(0[1-9]|1[0-2])$') {
    throw "ReportMonth must use YYYY-MM format."
}
if ($AttributionModel.Contains(',') -or $AttributionModel.Contains("`r") -or $AttributionModel.Contains("`n")) {
    throw "AttributionModel cannot contain commas or line breaks."
}

$normalizedClientIds = [Collections.Generic.List[string]]::new()
$seenClientIds = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
foreach ($clientId in $ClientIds) {
    if ($clientId -cnotmatch $slugPattern) {
        throw "Each ClientIds value must be a canonical lowercase slug."
    }
    if ($seenClientIds.Add($clientId)) {
        $normalizedClientIds.Add($clientId)
    }
}

$gcloud = Get-Command gcloud.cmd -ErrorAction SilentlyContinue
if (-not $gcloud) {
    $gcloud = Get-Command gcloud -ErrorAction SilentlyContinue
}
if (-not $gcloud) {
    throw "gcloud is not on PATH. Install Google Cloud SDK or open a terminal where it is available."
}

$argsList = @(
    "flows/job_launcher.py",
    "--agency=$AgencyId",
    "--dry-run",
    "--attribution-model=$AttributionModel"
)

if ($ReportMonth) {
    $argsList += "--report-month=$ReportMonth"
}

if ($normalizedClientIds.Count -gt 0) {
    foreach ($clientId in $normalizedClientIds) {
        $argsList += "--client=$clientId"
    }
    $argsList += "--expected-client-count=$($normalizedClientIds.Count)"
}

$jobArgs = ($argsList -join ",")

Write-Host ""
Write-Host "Executing ARIE Cloud Run dry run" -ForegroundColor Cyan
Write-Host "Project: $ProjectId"
Write-Host "Region:  $Region"
Write-Host "Agency:  $AgencyId"
Write-Host "Model:   $AttributionModel"
Write-Host "Period:  $(if ($ReportMonth) { $ReportMonth } else { 'previous completed month' })"
if ($normalizedClientIds.Count -gt 0) {
    Write-Host "Clients: $($normalizedClientIds -join ', ')"
} else {
    Write-Host "Clients: all clients in agency"
}
Write-Host ""

& $gcloud.Source run jobs execute attribution-launcher `
    --project $ProjectId `
    --region $Region `
    --args $jobArgs `
    --wait

if ($LASTEXITCODE -ne 0) {
    throw "Cloud Run dry run failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Dry run submitted and completed. Check ARIE run history and Cloud Logging for row counts, warnings, and report preview state." -ForegroundColor Green
