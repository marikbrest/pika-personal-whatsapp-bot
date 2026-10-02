# check_status.ps1
#
# Quick health check for the bot: are uvicorn and cloudflared running,
# are the logs recent, and does the public URL actually respond?
#
# Meant to be run by double-clicking check_status.bat from the desktop
# (not through Task Scheduler), so it always opens a normal, visible
# console window in the logged-on user's own session.

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogsDir = Join-Path $ProjectRoot "logs"

Write-Host "=============================================="
Write-Host "  Personal Assistant - Status Check"
Write-Host "=============================================="
Write-Host ""

# 1. Processes
$pythonProcs = Get-Process -Name "python" -ErrorAction SilentlyContinue
$tunnelProcs = Get-Process -Name "cloudflared" -ErrorAction SilentlyContinue

if ($pythonProcs) {
    Write-Host "[OK] uvicorn (python) is running - $($pythonProcs.Count) process(es)" -ForegroundColor Green
} else {
    Write-Host "[MISSING] uvicorn (python) is NOT running" -ForegroundColor Red
}

if ($tunnelProcs) {
    Write-Host "[OK] cloudflared tunnel is running" -ForegroundColor Green
} else {
    Write-Host "[MISSING] cloudflared tunnel is NOT running" -ForegroundColor Red
}

Write-Host ""

# 2. Log freshness
$uvicornLog = Join-Path $LogsDir "uvicorn.log"
$tunnelLog  = Join-Path $LogsDir "tunnel.log"

foreach ($log in @($uvicornLog, $tunnelLog)) {
    if (Test-Path $log) {
        $lastWrite = (Get-Item $log).LastWriteTime
        $age = (Get-Date) - $lastWrite
        Write-Host "Log: $log"
        Write-Host "  Last updated: $lastWrite ($([int]$age.TotalMinutes) minutes ago)"
    } else {
        Write-Host "Log not found: $log" -ForegroundColor Yellow
    }
}

Write-Host ""

# 3. Live check of the public URL
Write-Host "Checking https://assistant.your-domain.example/ ..."
try {
    $response = Invoke-WebRequest -Uri "https://assistant.your-domain.example/" -TimeoutSec 10 -UseBasicParsing
    if ($response.StatusCode -eq 200) {
        Write-Host "[OK] Public URL responded: $($response.StatusCode) $($response.Content)" -ForegroundColor Green
    } else {
        Write-Host "[WARNING] Public URL responded with status $($response.StatusCode)" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[FAILED] Could not reach the public URL: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "=============================================="
Read-Host "Press Enter to close this window"
