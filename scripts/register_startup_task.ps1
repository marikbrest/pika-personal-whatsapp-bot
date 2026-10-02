# register_startup_task.ps1
#
# Registers a Task Scheduler task that runs start_assistant.ps1 automatically
# when you log on to Windows (AtLogOn), with a visible window so you can see
# at a glance that the bot is running (instead of running fully hidden).
#
# Note: AtLogOn + LogonType Interactive is intentional, not AtStartup + S4U —
# S4U/AtStartup tasks run in session 0 and cannot show a window on the
# desktop at all, even without -WindowStyle Hidden.
#
# Run once, as Administrator:
#   powershell -ExecutionPolicy Bypass -File .\scripts\register_startup_task.ps1

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ScriptPath = Join-Path $ProjectRoot "scripts\start_assistant.ps1"
$TaskName = "PersonalAssistantWhatsApp"

# No -WindowStyle Hidden here — so the window stays visible on the desktop
$ArgumentString = '-NoProfile -ExecutionPolicy Bypass -NoExit -File "{0}"' -f $ScriptPath

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $ArgumentString

$Trigger = New-ScheduledTaskTrigger -AtLogOn

$PrincipalParams = @{
    UserId    = "$env:USERDOMAIN\$env:USERNAME"
    LogonType = "Interactive"
    RunLevel  = "Limited"
}
$Principal = New-ScheduledTaskPrincipal @PrincipalParams

$SettingsParams = @{
    AllowStartIfOnBatteries    = $true
    DontStopIfGoingOnBatteries = $true
    StartWhenAvailable         = $true
    RestartCount               = 3
    RestartInterval            = (New-TimeSpan -Minutes 1)
    ExecutionTimeLimit          = (New-TimeSpan -Hours 0)   # 0 = no time limit
}
$Settings = New-ScheduledTaskSettingsSet @SettingsParams

$TaskParams = @{
    TaskName    = $TaskName
    Action      = $Action
    Trigger     = $Trigger
    Principal   = $Principal
    Settings    = $Settings
    Description = "Starts the personal WhatsApp bot server and Cloudflare Tunnel on every Windows logon, with a visible monitor window"
    Force       = $true
}
Register-ScheduledTask @TaskParams

Write-Host "Task '$TaskName' registered successfully in Task Scheduler."
Write-Host "It will run automatically on Windows logon, and open a visible monitor window."
Write-Host ""
Write-Host "To test immediately without logging out/restarting:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
