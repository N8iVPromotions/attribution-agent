# arie_stop.ps1 - Stop the background ARIE process
$pidFile = "C:\Users\zajen\attribution-agent\arie.pid"

if (-not (Test-Path $pidFile)) {
    Write-Output "No PID file found - ARIE may not be running."
    exit 0
}

$targetPid = (Get-Content $pidFile -Raw -ErrorAction SilentlyContinue).Trim()
if (-not $targetPid) {
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    Write-Output "Empty PID file removed - ARIE was not running."
    exit 0
}

$proc = Get-Process -Id ([int]$targetPid) -ErrorAction SilentlyContinue
if ($proc) {
    Stop-Process -Id ([int]$targetPid) -Force
    Write-Output "ARIE stopped (PID $targetPid)"
} else {
    Write-Output "PID $targetPid not found - ARIE was not running."
}

Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
