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

$gcloud = Get-Command gcloud.cmd -ErrorAction SilentlyContinue
if (-not $gcloud) {
    $gcloud = Get-Command gcloud -ErrorAction SilentlyContinue
}
if (-not $gcloud) {
    throw "gcloud is not on PATH. Install Google Cloud SDK or open a terminal where it is available."
}

$argsList = @(
    "flows/job_launcher.py",
    "--agency",
    $AgencyId,
    "--dry-run",
    "--attribution-model",
    $AttributionModel
)

if ($ReportMonth) {
    $argsList += "--report-month"
    $argsList += $ReportMonth
}

if ($ClientIds.Count -gt 0) {
    foreach ($clientId in $ClientIds) {
        $argsList += "--client"
        $argsList += $clientId
    }
}

$jobArgs = ($argsList -join ",")

Write-Host ""
Write-Host "Executing ARIE Cloud Run dry run" -ForegroundColor Cyan
Write-Host "Project: $ProjectId"
Write-Host "Region:  $Region"
Write-Host "Agency:  $AgencyId"
Write-Host "Model:   $AttributionModel"
Write-Host "Period:  $(if ($ReportMonth) { $ReportMonth } else { 'previous completed month' })"
if ($ClientIds.Count -gt 0) {
    Write-Host "Clients: $($ClientIds -join ', ')"
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
