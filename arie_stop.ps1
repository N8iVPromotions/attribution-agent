# arie_stop.ps1 — Stop the background ARIE process
$pidFile = "C:\Users\zajen\attribution-agent\arie.pid"

if (-not (Test-Path $pidFile)) {
    Write-Output "No PID file found — ARIE may not be running."
    exit 0
}

$targetPid = Get-Content $pidFile -Raw
$proc = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
if ($proc) {
    Stop-Process -Id $targetPid -Force
    Write-Output "ARIE stopped (PID $targetPid)"
} else {
    Write-Output "PID $targetPid not found — ARIE was not running."
}
Remove-Item $pidFile -Force
