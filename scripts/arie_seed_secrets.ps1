param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [string]$EnvFile = "attribution_agent/attribution_agent/.env"
)

$ErrorActionPreference = "Stop"

$gcloud = Get-Command gcloud.cmd -ErrorAction SilentlyContinue
if (-not $gcloud) {
    $gcloud = Get-Command gcloud -ErrorAction SilentlyContinue
}
if (-not $gcloud) {
    throw "gcloud is not on PATH. Install Google Cloud SDK or open a terminal where it is available."
}

if (-not (Test-Path $EnvFile)) {
    throw "Env file not found: $EnvFile"
}

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

Write-Host ""
Write-Host "Seeding ARIE secrets from $EnvFile" -ForegroundColor Cyan
Write-Host "Project: $ProjectId"
Write-Host ""

$seeded = 0
foreach ($line in Get-Content -LiteralPath $EnvFile) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
        continue
    }

    $parts = $trimmed.Split("=", 2)
    $key = $parts[0].Trim()
    $value = $parts[1].Trim()

    if ($value.StartsWith('"') -and $value.EndsWith('"')) {
        $value = $value.Substring(1, $value.Length - 2)
    } elseif ($value.StartsWith("'") -and $value.EndsWith("'")) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    $value = $value.Trim()

    if ($secretKeys -notcontains $key) {
        continue
    }
    if (-not $value) {
        Write-Host "  skip $key (empty)" -ForegroundColor DarkYellow
        continue
    }

    & $gcloud.Source secrets describe $key --project $ProjectId *> $null
    if ($LASTEXITCODE -ne 0) {
        & $gcloud.Source secrets create $key `
            --project $ProjectId `
            --replication-policy=automatic
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create secret: $key"
        }
    }

    $tmp = New-TemporaryFile
    try {
        $tmpPath = $tmp.FullName
        $utf8NoBom = New-Object System.Text.UTF8Encoding -ArgumentList $false
        [System.IO.File]::WriteAllText($tmpPath, $value, $utf8NoBom)

        & $gcloud.Source secrets versions add $key `
            --project $ProjectId `
            --data-file=$tmpPath
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to add secret version: $key"
        }
    } finally {
        Remove-Item -LiteralPath $tmp.FullName -Force -ErrorAction SilentlyContinue
    }

    Write-Host "  seeded $key" -ForegroundColor Green
    $seeded += 1
}

Write-Host ""
Write-Host "Done - $seeded secret value(s) seeded." -ForegroundColor Green
