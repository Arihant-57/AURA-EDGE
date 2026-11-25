param(
    [switch]$WithDb
)

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $RepoRoot ".runtime"

foreach ($name in @("frontend", "backend")) {
    $pidFile = Join-Path $RuntimeDir "$name.pid"
    if (Test-Path $pidFile) {
        $procId = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($procId -match "^\d+$") {
            Write-Host "Stopping $name process tree (PID $procId)..."
            taskkill /PID $procId /T /F 2>$null | Out-Null
        }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }
}

if ($WithDb) {
    Write-Host "Stopping local PostgreSQL container..."
    Push-Location $RepoRoot
    try {
        docker compose stop postgres
    } finally {
        Pop-Location
    }
}

Write-Host "AURA EDGE Phase 0 local processes stopped."
