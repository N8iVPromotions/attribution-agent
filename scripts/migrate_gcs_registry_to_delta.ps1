param(
    [string]$ProjectId = "n8iv-analytics-production",
    [string]$RegistryUri = "gs://n8iv-analytics-production-attribution-registry/clients.json",
    [string]$OpsSchema = "workspace.attribution_ops",
    [string]$DatabricksHost = $env:DATABRICKS_SERVER_HOSTNAME,
    [string]$DatabricksHttpPath = $env:DATABRICKS_HTTP_PATH,
    [string]$DatabricksToken = $env:DATABRICKS_TOKEN,
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$slugPattern = '^[a-z][a-z0-9_]{0,62}$'
$schemaPattern = '^workspace\.attribution_[a-z][a-z0-9_]{0,62}$'

Write-Host "ARIE registry migration: GCS JSON -> Databricks Delta" -ForegroundColor Cyan
Write-Host "Source: $RegistryUri"
Write-Host "Target: $OpsSchema.client_registry and $OpsSchema.agency_registry"
Write-Host "Mode:   $(if ($Apply) { 'APPLY (upsert only)' } else { 'PLAN ONLY' })"
if (-not $Apply) {
    Write-Host "The migration never drops schemas or tables. Re-run with -Apply to read GCS and upsert validated rows." -ForegroundColor Yellow
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

if (-not $DatabricksHost) { $DatabricksHost = Read-GcpSecret "DATABRICKS_SERVER_HOSTNAME" }
if (-not $DatabricksHttpPath) { $DatabricksHttpPath = Read-GcpSecret "DATABRICKS_HTTP_PATH" }
if (-not $DatabricksToken) { $DatabricksToken = Read-GcpSecret "DATABRICKS_TOKEN" }
if ($DatabricksHttpPath -notmatch 'warehouses/([^/]+)') {
    throw "DATABRICKS_HTTP_PATH does not contain a SQL warehouse ID"
}
$warehouseId = $Matches[1]
$DatabricksHost = $DatabricksHost.Trim().TrimEnd('/').Replace('https://', '').Replace('http://', '')
$headers = @{ Authorization = "Bearer $DatabricksToken"; "Content-Type" = "application/json" }

function ConvertTo-SqlLiteral {
    param([AllowEmptyString()][string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

function Invoke-DatabricksApi {
    param([Parameter(Mandatory = $true)][string]$Path, [ValidateSet("GET", "POST")][string]$Method = "GET", [object]$Body)
    $arguments = @{ Uri = "https://$DatabricksHost$Path"; Method = $Method; Headers = $headers }
    if ($null -ne $Body) { $arguments.Body = ($Body | ConvertTo-Json -Depth 20 -Compress) }
    return Invoke-RestMethod @arguments
}

function Invoke-DatabricksSql {
    param([Parameter(Mandatory = $true)][string]$Statement)
    $response = Invoke-DatabricksApi -Path "/api/2.0/sql/statements" -Method POST -Body @{
        warehouse_id = $warehouseId
        statement = $Statement
        wait_timeout = "30s"
        disposition = "INLINE"
        format = "JSON_ARRAY"
    }
    for ($attempt = 0; $attempt -lt 30 -and $response.status.state -in @("PENDING", "RUNNING"); $attempt++) {
        Start-Sleep -Seconds 1
        $response = Invoke-DatabricksApi -Path "/api/2.0/sql/statements/$($response.statement_id)"
    }
    if ($response.status.state -ne "SUCCEEDED") {
        throw "Databricks SQL failed ($($response.status.state)): $($response.status.error.message)"
    }
}

$rawRegistry = & gcloud storage cat $RegistryUri --project $ProjectId 2>$null
if ($LASTEXITCODE -ne 0 -or -not $rawRegistry) {
    throw "Could not read registry object $RegistryUri"
}
$registry = (($rawRegistry -join "`n") | ConvertFrom-Json)
$clientContainer = if ($registry.clients) { $registry.clients } else { $registry }
$clientProperties = @($clientContainer.PSObject.Properties)
if (-not $clientProperties.Count) { throw "Registry contains no client records" }

$validatedClients = @()
$agencyNames = @{}
foreach ($property in $clientProperties) {
    $clientId = [string]$property.Name
    $config = $property.Value
    if ($clientId -cnotmatch $slugPattern) { throw "Unsafe or noncanonical client_id: $clientId" }
    $expectedSchema = "workspace.attribution_$clientId"
    $storedSchema = [string]$config.databricks_schema
    if ($storedSchema -and ($storedSchema -cnotmatch $schemaPattern -or $storedSchema -cne $expectedSchema)) {
        throw "Client $clientId has unexpected schema '$storedSchema'; expected '$expectedSchema'"
    }
    if (-not $storedSchema) { $config | Add-Member -NotePropertyName databricks_schema -NotePropertyValue $expectedSchema -Force }
    $agencyId = [string]$config.agency_id
    if ($agencyId) {
        if ($agencyId -cnotmatch $slugPattern) { throw "Unsafe or noncanonical agency_id: $agencyId" }
        $agencyNames[$agencyId] = if ([string]$config.agency_name) { [string]$config.agency_name } elseif ([string]$config.client_name -and $agencyId -eq $clientId) { [string]$config.client_name } else { (Get-Culture).TextInfo.ToTitleCase($agencyId.Replace('_', ' ')) }
    }
    $validatedClients += @{ client_id = $clientId; config = $config }
}

Invoke-DatabricksSql "CREATE SCHEMA IF NOT EXISTS $OpsSchema"
Invoke-DatabricksSql "CREATE TABLE IF NOT EXISTS $OpsSchema.client_registry (client_id STRING, config_json STRING, is_active BOOLEAN, updated_at TIMESTAMP) USING DELTA"
Invoke-DatabricksSql "CREATE TABLE IF NOT EXISTS $OpsSchema.agency_registry (agency_id STRING, agency_name STRING, config_json STRING, is_active BOOLEAN, created_at TIMESTAMP, updated_at TIMESTAMP) USING DELTA"
Invoke-DatabricksSql "CREATE TABLE IF NOT EXISTS $OpsSchema.tenant_lifecycle_operations (request_id STRING, command STRING, entity_type STRING, entity_id STRING, agency_id STRING, schema_name STRING, requested_by STRING, requested_at TIMESTAMP, started_at TIMESTAMP, completed_at TIMESTAMP, status STRING, databricks_run_id STRING, ddl_statement STRING, error_message STRING, result_json STRING) USING DELTA"

foreach ($agencyId in $agencyNames.Keys) {
    $agencyName = [string]$agencyNames[$agencyId]
    $agencyJson = @{ agency_id = $agencyId; agency_name = $agencyName; client_ids = @() } | ConvertTo-Json -Compress
    Invoke-DatabricksSql "MERGE INTO $OpsSchema.agency_registry AS target USING (SELECT $(ConvertTo-SqlLiteral $agencyId) AS agency_id) AS source ON target.agency_id = source.agency_id WHEN MATCHED THEN UPDATE SET agency_name = $(ConvertTo-SqlLiteral $agencyName), config_json = $(ConvertTo-SqlLiteral $agencyJson), is_active = TRUE, updated_at = current_timestamp() WHEN NOT MATCHED THEN INSERT (agency_id, agency_name, config_json, is_active, created_at, updated_at) VALUES ($(ConvertTo-SqlLiteral $agencyId), $(ConvertTo-SqlLiteral $agencyName), $(ConvertTo-SqlLiteral $agencyJson), TRUE, current_timestamp(), current_timestamp())"
}

foreach ($client in $validatedClients) {
    $clientId = [string]$client.client_id
    $configJson = $client.config | ConvertTo-Json -Depth 20 -Compress
    Invoke-DatabricksSql "MERGE INTO $OpsSchema.client_registry AS target USING (SELECT $(ConvertTo-SqlLiteral $clientId) AS client_id) AS source ON target.client_id = source.client_id WHEN MATCHED THEN UPDATE SET config_json = $(ConvertTo-SqlLiteral $configJson), is_active = TRUE, updated_at = current_timestamp() WHEN NOT MATCHED THEN INSERT (client_id, config_json, is_active, updated_at) VALUES ($(ConvertTo-SqlLiteral $clientId), $(ConvertTo-SqlLiteral $configJson), TRUE, current_timestamp())"
}

Write-Host "Migrated $($validatedClients.Count) client(s) and $($agencyNames.Count) agency record(s) to Delta." -ForegroundColor Green
Write-Host "No source object, schema, or table was deleted."
