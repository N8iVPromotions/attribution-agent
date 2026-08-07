param(
    [ValidateSet("Local", "CloudProxy", "CloudUrl")]
    [string]$Mode = "Local",

    [string]$ProjectId = "n8iv-analytics-production",
    [string]$Region = "us-central1",
    [string]$ServiceName = "attribution-ui",
    [int]$LocalPort = 8501,
    [int]$CloudProxyPort = 8080,
    [switch]$StartBot
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$appDir = Join-Path $repoRoot "attribution_agent\attribution_agent"
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$streamlitPid = Join-Path $repoRoot "streamlit.pid"
$streamlitOut = Join-Path $repoRoot "streamlit_out.log"
$streamlitErr = Join-Path $repoRoot "streamlit_err.log"
$proxyPid = Join-Path $repoRoot "cloudrun_proxy.pid"
$proxyOut = Join-Path $repoRoot "cloudrun_proxy_out.log"
$proxyErr = Join-Path $repoRoot "cloudrun_proxy_err.log"

function Normalize-ProcessPath {
    $processPath = [Environment]::GetEnvironmentVariable("Path", "Process")
    if (-not $processPath) {
        $processPath = [Environment]::GetEnvironmentVariable("PATH", "Process")
    }
    [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    [Environment]::SetEnvironmentVariable("Path", $processPath, "Process")
}

function Test-LocalPort {
    param([int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(500, $false)) {
            return $false
        }
        $client.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-ForPort {
    param([int]$Port, [int]$Seconds = 30)
    for ($i = 0; $i -lt $Seconds; $i++) {
        if (Test-LocalPort -Port $Port) {
            return $true
        }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Open-AppWindow {
    param([string]$Url)
    $browserCandidates = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
    )
    $browser = $browserCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if ($browser) {
        Start-Process -FilePath $browser -ArgumentList @("--app=$Url")
    } else {
        Start-Process $Url
    }
}

function Start-LocalCommandCenter {
    if (-not (Test-Path $python)) {
        throw "Project virtualenv was not found at $python. Start ARIE once from the repo setup before using the desktop launcher."
    }
    if (Test-LocalPort -Port $LocalPort) {
        return "http://127.0.0.1:$LocalPort"
    }

    "" | Out-File $streamlitOut -Encoding utf8
    "" | Out-File $streamlitErr -Encoding utf8
    Normalize-ProcessPath
    $proc = Start-Process `
        -FilePath $python `
        -ArgumentList @(
            "-m", "streamlit", "run", "app.py",
            "--server.port", "$LocalPort",
            "--server.address", "127.0.0.1",
            "--server.headless", "true"
        ) `
        -WorkingDirectory $appDir `
        -RedirectStandardOutput $streamlitOut `
        -RedirectStandardError $streamlitErr `
        -WindowStyle Hidden `
        -PassThru
    $proc.Id | Out-File $streamlitPid -Encoding utf8

    if (-not (Wait-ForPort -Port $LocalPort -Seconds 35)) {
        throw "ARIE Command Center did not start on port $LocalPort. Check $streamlitErr."
    }
    return "http://127.0.0.1:$LocalPort"
}

function Start-CloudRunProxy {
    if (Test-LocalPort -Port $CloudProxyPort) {
        return "http://127.0.0.1:$CloudProxyPort"
    }

    $gcloud = Get-Command gcloud.cmd -ErrorAction SilentlyContinue
    if (-not $gcloud) {
        $gcloud = Get-Command gcloud -ErrorAction SilentlyContinue
    }
    if (-not $gcloud) {
        throw "gcloud is not on PATH. Open Google Cloud SDK Shell or install gcloud before using CloudProxy mode."
    }

    "" | Out-File $proxyOut -Encoding utf8
    "" | Out-File $proxyErr -Encoding utf8
    Normalize-ProcessPath
    $proc = Start-Process `
        -FilePath $gcloud.Source `
        -ArgumentList @(
            "run", "services", "proxy", $ServiceName,
            "--project", $ProjectId,
            "--region", $Region,
            "--port", "$CloudProxyPort"
        ) `
        -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $proxyOut `
        -RedirectStandardError $proxyErr `
        -WindowStyle Hidden `
        -PassThru
    $proc.Id | Out-File $proxyPid -Encoding utf8

    if (-not (Wait-ForPort -Port $CloudProxyPort -Seconds 45)) {
        throw "Cloud Run proxy did not start on port $CloudProxyPort. Check $proxyErr."
    }
    return "http://127.0.0.1:$CloudProxyPort"
}

function Get-CloudRunUrl {
    $gcloud = Get-Command gcloud.cmd -ErrorAction SilentlyContinue
    if (-not $gcloud) {
        $gcloud = Get-Command gcloud -ErrorAction SilentlyContinue
    }
    if (-not $gcloud) {
        throw "gcloud is not on PATH. Cannot resolve Cloud Run URL."
    }
    $url = (& $gcloud.Source run services describe $ServiceName --project $ProjectId --region $Region --format "value(status.url)").Trim()
    if (-not $url) {
        throw "Could not resolve Cloud Run URL for $ServiceName."
    }
    return $url
}

if ($StartBot) {
    $botStart = Join-Path $repoRoot "arie_start.ps1"
    if (Test-Path $botStart) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $botStart | Out-Null
    }
}

$url = switch ($Mode) {
    "Local" { Start-LocalCommandCenter }
    "CloudProxy" { Start-CloudRunProxy }
    "CloudUrl" { Get-CloudRunUrl }
}

Open-AppWindow -Url $url
Write-Output "ARIE Command Center opened: $url"
