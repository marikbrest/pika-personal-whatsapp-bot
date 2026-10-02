# Daily database backup - thin Windows wrapper around scripts\backup_db.py
# (SQLite online backup, retention, optional rclone offsite copy: see that file).

$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectDir "venv\Scripts\python.exe"

& $python (Join-Path $PSScriptRoot "backup_db.py") @args
if ($LASTEXITCODE -ne 0) {
    Write-Host "BACKUP FAILED" -ForegroundColor Red
    exit 1
}
