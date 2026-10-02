# start_assistant.ps1
#
# Starts the two processes required for the bot (uvicorn + cloudflared tunnel)
# and monitors them in the background — if either one dies, it's restarted
# automatically.
#
# Meant to run via Task Scheduler on every Windows logon (see README.md,
# "Auto-start on boot" section, for registration instructions).
#
# Logs are saved under logs\ inside the project folder, so issues can be
# diagnosed even without watching the console window in real time.
#
# Written with splatting (hashtables) instead of backtick line continuations,
# to avoid subtle PowerShell parsing bugs (hidden trailing spaces after `).

$ErrorActionPreference = "Stop"

$Host.UI.RawUI.WindowTitle = "Personal Assistant - WhatsApp Bot Monitor"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogsDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

$UvicornOut = Join-Path $LogsDir "uvicorn.log"
$TunnelOut  = Join-Path $LogsDir "tunnel.log"

$PythonExe = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$CloudflaredExe = "cloudflared"  # installed globally via winget, on PATH

Write-Host "=============================================="
Write-Host "  Personal Assistant - WhatsApp Bot"
Write-Host "  This window monitors uvicorn and the Cloudflare Tunnel."
Write-Host "  If this window is closed, the bot is not running."
Write-Host "=============================================="
Write-Host ""

function Move-ExistingLog {
    param([string]$Path)
    # Start-Process' RedirectStandardOutput TRUNCATES its target, so without this
    # every restart destroys the previous run's log entirely - which is exactly
    # the history you need when diagnosing why it restarted. Archive instead.
    # Never allowed to block startup: a locked or unwritable log is not a reason
    # for the bot to stay down, so every failure here is swallowed with a warning.
    foreach ($p in @($Path, "$Path.err")) {
        try {
            if ((Test-Path $p) -and ((Get-Item $p).Length -gt 0)) {
                $stamp = (Get-Item $p).LastWriteTime.ToString("yyyy-MM-dd_HHmmss")
                Move-Item -Path $p -Destination "$p.$stamp" -Force
            }
        } catch {
            Write-Host "Could not rotate $p : $_" -ForegroundColor Yellow
        }
    }
    try {
        $dir = Split-Path -Parent $Path
        $base = Split-Path -Leaf $Path
        Get-ChildItem $dir -Filter "$base*" |
            Where-Object { $_.Name -match '\.\d{4}-\d{2}-\d{2}_\d{6}$' } |
            Sort-Object Name -Descending |
            Select-Object -Skip 14 |
            Remove-Item -Force
    } catch {
        Write-Host "Could not prune old logs: $_" -ForegroundColor Yellow
    }
}

function Start-UvicornProcess {
    Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - Starting uvicorn..."
    Move-ExistingLog $UvicornOut
    $params = @{
        FilePath               = $PythonExe
        # 127.0.0.1, not 0.0.0.0: cloudflared's ingress config proxies to
        # http://localhost:8000 from this same machine, so binding to loopback
        # loses nothing. Binding to 0.0.0.0 made every route - including
        # /admin/* - directly reachable from any device on the LAN, bypassing
        # Cloudflare Access entirely: the admin auth check only inspects the
        # Host header and the Cf-Access-Authenticated-User-Email header, both
        # attacker-settable on a direct connection, since neither is backed by
        # a Cloudflare-signed token (see admin_handler._authorized). Confirmed
        # exploitable 2026-09-13 with two forged headers against the live
        # instance. Loopback binding closes the network path entirely; the
        # header checks stay in place as defense in depth for the intended
        # (tunnel-fronted) deployment shape.
        ArgumentList           = @("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", "8000")
        WorkingDirectory       = $ProjectRoot
        RedirectStandardOutput = $UvicornOut
        RedirectStandardError  = "$UvicornOut.err"
        WindowStyle            = "Hidden"
        PassThru               = $true
    }
    return Start-Process @params
}

function Start-TunnelProcess {
    Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - Starting cloudflared tunnel..."
    Move-ExistingLog $TunnelOut
    $params = @{
        FilePath               = $CloudflaredExe
        ArgumentList           = @("tunnel", "run", "assistant-tunnel")
        WorkingDirectory       = $ProjectRoot
        RedirectStandardOutput = $TunnelOut
        RedirectStandardError  = "$TunnelOut.err"
        WindowStyle            = "Hidden"
        PassThru               = $true
    }
    return Start-Process @params
}

$uvicornProc = Start-UvicornProcess
Start-Sleep -Seconds 5   # give uvicorn time to come up before the tunnel tries to reach it
$tunnelProc = Start-TunnelProcess

Write-Host ""
Write-Host "Both processes are up. Monitoring every 30 seconds."
Write-Host "Bot URL: https://assistant.your-domain.example/"
Write-Host "Ctrl+C stops this monitor window (the background processes keep running)."
Write-Host ""

# Monitoring loop: every 30 seconds, check both processes are still alive,
# and restart whichever one isn't.
while ($true) {
    Start-Sleep -Seconds 30

    if ($uvicornProc.HasExited) {
        Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - WARNING: uvicorn stopped (exit code $($uvicornProc.ExitCode)), restarting..."
        $uvicornProc = Start-UvicornProcess
        Start-Sleep -Seconds 5
    }

    if ($tunnelProc.HasExited) {
        Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - WARNING: cloudflared stopped (exit code $($tunnelProc.ExitCode)), restarting..."
        $tunnelProc = Start-TunnelProcess
    }

    if (-not $uvicornProc.HasExited -and -not $tunnelProc.HasExited) {
        Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - OK: bot is running (uvicorn + tunnel healthy)"
    }
}
