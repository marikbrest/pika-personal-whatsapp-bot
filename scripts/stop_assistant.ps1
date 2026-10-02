# stop_assistant.ps1
#
# Stops both processes (uvicorn + cloudflared) in a controlled way.
# Useful during development, or before pulling code updates that require
# a restart.
#
# Scoped specifically to THIS project and THIS tunnel (2026-09-17 fix) - the
# original version matched any "*venv\Scripts\python.exe" and any process
# literally named "cloudflared", which silently killed OTHER apps on this
# box too (other apps each running their own venv and their own
# cloudflared tunnel) every single time this script ran, since
# Get-Process's simple path/name match cannot tell one venv or one tunnel
# from another. Confirmed live: running this script briefly took down
# unrelated apps as a side effect - not something to rely on. Get-CimInstance Win32_Process (unlike Get-Process) exposes CommandLine,
# so this can match on this project's actual folder name and this bot's own
# tunnel name (assistant-tunnel, see start_assistant.ps1) instead.

$ProjectRoot = Split-Path -Parent $PSScriptRoot

Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -eq "python.exe" -and $_.CommandLine -like "*$ProjectRoot\venv\Scripts\python.exe*") -or
    ($_.Name -like "cloudflared*" -and $_.CommandLine -like "*assistant-tunnel*")
} | ForEach-Object {
    Write-Host "Stopping process: $($_.Name) (PID $($_.ProcessId))"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}

Write-Host "If you registered the Task Scheduler task, you can also run:  Stop-ScheduledTask -TaskName 'PersonalAssistantWhatsApp'"
