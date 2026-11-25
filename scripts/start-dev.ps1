param(
    [switch]$WithDb,
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $RepoRoot ".runtime"
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$FrontendDir = Join-Path $RepoRoot "frontend"
$NpmCmd = "C:\Program Files\nodejs\npm.cmd"

if (-not (Test-Path $PythonExe)) {
    throw "Virtual environment not found at $PythonExe. Run setup instructions in README.md first."
}

if ($WithDb) {
    Write-Host "Starting local PostgreSQL + pgvector container..."
    Push-Location $RepoRoot
    try {
        docker compose up -d postgres
        & $PythonExe -m alembic -c (Join-Path $RepoRoot "backend\alembic.ini") upgrade head
    } finally {
        Pop-Location
    }
}

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null

$env:PYTHONPATH = Join-Path $RepoRoot "backend\src"
$BackendArgs = @("-m", "uvicorn", "aura_edge.main:app", "--host", "127.0.0.1", "--port", "8000")

if ($Foreground) {
    Write-Host "Starting AURA EDGE backend on http://127.0.0.1:8000 (foreground)..."
    & $PythonExe @BackendArgs
    exit $LASTEXITCODE
}

$BackendProc = Start-Process -FilePath $PythonExe -ArgumentList $BackendArgs -WorkingDirectory $RepoRoot -PassThru -WindowStyle Hidden
$BackendProc.Id | Set-Content (Join-Path $RuntimeDir "backend.pid")
Write-Host "Started backend PID $($BackendProc.Id) at http://127.0.0.1:8000"

if (Test-Path (Join-Path $FrontendDir "node_modules")) {
    $FrontendProc = Start-Process -FilePath $NpmCmd -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "5173") -WorkingDirectory $FrontendDir -PassThru -WindowStyle Hidden
    $FrontendProc.Id | Set-Content (Join-Path $RuntimeDir "frontend.pid")
    Write-Host "Started frontend PID $($FrontendProc.Id) at http://127.0.0.1:5173"
} else {
    Write-Warning "frontend/node_modules not found; run 'npm install' inside frontend/ before starting UI."
}
