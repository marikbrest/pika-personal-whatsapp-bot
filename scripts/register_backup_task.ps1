# Registers a daily 03:00 backup task in Task Scheduler.
# Run once, as the regular user (no admin needed).

$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $projectDir "scripts\backup_db.ps1"
$taskName = "PersonalAssistantBackup"

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$trigger = New-ScheduledTaskTrigger -Daily -At "03:00"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed existing task"
}

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings | Out-Null
Write-Host "Task '$taskName' registered - daily backup at 03:00" -ForegroundColor Green
Write-Host "StartWhenAvailable is on: if the PC was off at 03:00, backup runs on next boot"
