param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [Parameter(Mandatory = $true)]
    [string]$AgencyId,

    [string]$Region = "us-central1",
    [string]$AttributionModel = "w_shape",
    [string]$RunMode = "agency",
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
    "flows/agency_flow.py",
    "--agency",
    $AgencyId,
    "--dry-run",
    "--attribution-model",
    $AttributionModel,
    "--run-mode",
    $RunMode
)

if ($ClientIds.Count -gt 0) {
    $argsList += "--client-filter"
    $argsList += $ClientIds
}

$jobArgs = ($argsList -join ",")

Write-Host ""
Write-Host "Executing ARIE Cloud Run dry run" -ForegroundColor Cyan
Write-Host "Project: $ProjectId"
Write-Host "Region:  $Region"
Write-Host "Agency:  $AgencyId"
Write-Host "Model:   $AttributionModel"
if ($ClientIds.Count -gt 0) {
    Write-Host "Clients: $($ClientIds -join ', ')"
} else {
    Write-Host "Clients: all clients in agency"
}
Write-Host ""

& $gcloud.Source run jobs execute attribution-pipeline `
    --project $ProjectId `
    --region $Region `
    --args $jobArgs `
    --wait

if ($LASTEXITCODE -ne 0) {
    throw "Cloud Run dry run failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Dry run submitted and completed. Check ARIE run history and Cloud Logging for row counts, warnings, and report preview state." -ForegroundColor Green
