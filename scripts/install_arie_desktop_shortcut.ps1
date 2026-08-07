param(
    [ValidateSet("Local", "CloudProxy", "CloudUrl")]
    [string]$Mode = "Local",

    [string]$ProjectId = "n8iv-analytics-production",
    [string]$Region = "us-central1",
    [string]$ServiceName = "attribution-ui",
    [switch]$StartBot,
    [string]$ShortcutName = "ARIE Command Center"
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$launcher = Join-Path $repoRoot "scripts\arie_open_command_center.ps1"
if (-not (Test-Path $launcher)) {
    throw "Launcher script not found: $launcher"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "$ShortcutName.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

$args = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$launcher`"",
    "-Mode", $Mode,
    "-ProjectId", $ProjectId,
    "-Region", $Region,
    "-ServiceName", $ServiceName
)
if ($StartBot) {
    $args += "-StartBot"
}

$shortcut.Arguments = ($args -join " ")
$shortcut.WorkingDirectory = $repoRoot
$shortcut.Description = "Open ARIE Command Center"
$shortcut.IconLocation = "$env:SystemRoot\System32\SHELL32.dll,220"
$shortcut.Save()

Write-Output "Created desktop shortcut: $shortcutPath"
