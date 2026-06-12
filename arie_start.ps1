# arie_start.ps1 — Start ARIE in the background (no console window)
# Usage: .\arie_start.ps1
# Logs go to: arie_out.log / arie_err.log in the repo root

$workDir  = "C:\Users\zajen\attribution-agent\attribution_agent\attribution_agent"
$pythonw  = "C:\Users\zajen\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\pythonw.exe"
$repoRoot = "C:\Users\zajen\attribution-agent"
$pidFile  = "$repoRoot\arie.pid"
$logOut   = "$repoRoot\arie_out.log"
$logErr   = "$repoRoot\arie_err.log"

# Check if already running
if (Test-Path $pidFile) {
    $oldPid = Get-Content $pidFile -Raw
    $running = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
    if ($running) {
        Write-Output "ARIE already running (PID $oldPid). Use arie_stop.ps1 to stop it first."
        exit 0
    }
    Remove-Item $pidFile -Force
}

"" | Out-File $logOut -Encoding utf8
"" | Out-File $logErr -Encoding utf8

$env:PYTHONPATH = $workDir

$proc = Start-Process `
    -FilePath $pythonw `
    -ArgumentList "agents\control\arie_bot.py" `
    -WorkingDirectory $workDir `
    -RedirectStandardOutput $logOut `
    -RedirectStandardError $logErr `
    -PassThru

$proc.Id | Out-File $pidFile -Encoding utf8
Write-Output "ARIE started — PID $($proc.Id)"
Write-Output "Logs: $logOut / $logErr"
Write-Output "Stop with: .\arie_stop.ps1"
