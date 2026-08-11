param(
    [string]$ProjectId = "n8iv-analytics-production",
    [string]$JobName = "ARIE Tenant Lifecycle",
    [string]$NotebookPath = "/Shared/ARIE/tenant_lifecycle",
    [string]$DatabricksHost = $env:DATABRICKS_SERVER_HOSTNAME,
    [string]$DatabricksToken = $env:DATABRICKS_TOKEN,
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$sourcePath = Join-Path $PSScriptRoot "..\attribution_agent\attribution_agent\flows\tenant_lifecycle.py"
$sourcePath = [IO.Path]::GetFullPath($sourcePath)

if (-not (Test-Path -LiteralPath $sourcePath)) {
    throw "Tenant lifecycle notebook source was not found: $sourcePath"
}

Write-Host "ARIE Databricks tenant lifecycle deployment" -ForegroundColor Cyan
Write-Host "Notebook: $sourcePath -> $NotebookPath"
Write-Host "Job:      $JobName"
Write-Host "Mode:     $(if ($Apply) { 'APPLY' } else { 'PLAN ONLY' })"

if (-not $Apply) {
    Write-Host "No workspace changes were made. Re-run with -Apply after reviewing this plan." -ForegroundColor Yellow
    exit 0
}

function Read-GcpSecret {
    param([Parameter(Mandatory = $true)][string]$Name)
    $value = & gcloud secrets versions access latest --secret $Name --project $ProjectId 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $value) {
        throw "Could not read required GCP secret $Name from project $ProjectId"
    }
    return ($value -join "`n").Trim()
}

if (-not $DatabricksHost) {
    $DatabricksHost = Read-GcpSecret -Name "DATABRICKS_SERVER_HOSTNAME"
}
if (-not $DatabricksToken) {
    $DatabricksToken = Read-GcpSecret -Name "DATABRICKS_TOKEN"
}
$DatabricksHost = $DatabricksHost.Trim().TrimEnd('/').Replace('https://', '').Replace('http://', '')
$headers = @{ Authorization = "Bearer $DatabricksToken"; "Content-Type" = "application/json" }

function Invoke-DatabricksApi {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [ValidateSet("GET", "POST")][string]$Method = "GET",
        [object]$Body
    )
    $arguments = @{
        Uri = "https://$DatabricksHost$Path"
        Method = $Method
        Headers = $headers
    }
    if ($null -ne $Body) {
        $arguments.Body = ($Body | ConvertTo-Json -Depth 20 -Compress)
    }
    return Invoke-RestMethod @arguments
}

$content = [Convert]::ToBase64String([IO.File]::ReadAllBytes($sourcePath))
$parentSeparator = $NotebookPath.LastIndexOf('/')
if ($parentSeparator -le 0) {
    throw "NotebookPath must include an absolute workspace parent folder"
}
$notebookParent = $NotebookPath.Substring(0, $parentSeparator)
Invoke-DatabricksApi -Path "/api/2.0/workspace/mkdirs" -Method POST -Body @{
    path = $notebookParent
} | Out-Null
Invoke-DatabricksApi -Path "/api/2.0/workspace/import" -Method POST -Body @{
    path = $NotebookPath
    format = "SOURCE"
    language = "PYTHON"
    content = $content
    overwrite = $true
} | Out-Null

$parameters = @(
    @{ name = "command"; default = "" },
    @{ name = "entity_id"; default = "" },
    @{ name = "entity_name"; default = "" },
    @{ name = "agency_id"; default = "" },
    @{ name = "report_email"; default = "" },
    @{ name = "attribution_model"; default = "last_touch" },
    @{ name = "confirmation"; default = "" },
    @{ name = "request_id"; default = "" },
    @{ name = "requested_by"; default = "arie-command-center" }
)
$baseParameters = @{}
foreach ($parameter in $parameters) {
    $baseParameters[$parameter.name] = "{{job.parameters.$($parameter.name)}}"
}
$baseParameters.databricks_run_id = "{{job.run_id}}"

$settings = @{
    name = $JobName
    max_concurrent_runs = 1
    queue = @{ enabled = $true }
    performance_target = "STANDARD"
    parameters = $parameters
    tasks = @(
        @{
            task_key = "tenant_lifecycle"
            timeout_seconds = 1800
            max_retries = 0
            notebook_task = @{
                notebook_path = $NotebookPath
                source = "WORKSPACE"
                base_parameters = $baseParameters
            }
        }
    )
}

$jobs = Invoke-DatabricksApi -Path "/api/2.2/jobs/list?limit=100&name=$([Uri]::EscapeDataString($JobName))"
$matchingJobs = @($jobs.jobs | Where-Object { $_.settings.name -eq $JobName })
if ($matchingJobs.Count -gt 1) {
    throw "Multiple Databricks jobs named '$JobName' exist; refusing an ambiguous update"
}
if ($matchingJobs.Count -eq 1) {
    $jobId = [long]$matchingJobs[0].job_id
    Invoke-DatabricksApi -Path "/api/2.2/jobs/reset" -Method POST -Body @{
        job_id = $jobId
        new_settings = $settings
    } | Out-Null
    Write-Host "Updated Databricks job $jobId" -ForegroundColor Green
} else {
    $created = Invoke-DatabricksApi -Path "/api/2.2/jobs/create" -Method POST -Body $settings
    $jobId = [long]$created.job_id
    Write-Host "Created Databricks job $jobId" -ForegroundColor Green
}

Write-Host "Set this production-only Vercel variable, then redeploy the portal:"
Write-Host "DATABRICKS_TENANT_LIFECYCLE_JOB_ID=$jobId"
