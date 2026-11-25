<#
.SYNOPSIS
    One-command launcher for AURA EDGE Phase 1 (PostgreSQL + Alembic + Offline Backend + Live Mic CLI).
.EXAMPLE
    .\scripts\start-aura-voice.ps1
    .\scripts\start-aura-voice.ps1 -Language kn -ComputeMode gpu
    .\scripts\start-aura-voice.ps1 -NoCli
#>
param(
    [ValidateSet("auto", "en", "hi", "kn")]
    [string]$Language = "auto",

    [ValidateSet("auto", "cpu", "gpu")]
    [string]$ComputeMode = "auto",

    [switch]$NoCli,
    [switch]$NoSpeak,
    [switch]$SkipOllama,
    [switch]$KeepRunningOnExit
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $RepoRoot ".runtime"
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$CliScript = Join-Path $RepoRoot "backend\scripts\voice_cli.py"

if (-not (Test-Path $PythonExe)) {
    throw "Virtual environment not found at $PythonExe. Run setup instructions in README.md first."
}

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null

# 1. Ensure Docker Engine is running for PostgreSQL
$dockerReady = $false
try {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { $dockerReady = $true }
} catch {}

if (-not $dockerReady) {
    $dockerExeCandidates = @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe",
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    )
    $dockerExe = $dockerExeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $dockerExe) {
        throw "Docker Engine is not running and Docker Desktop executable was not found."
    }
    Write-Host "[1/5] Starting Docker Desktop..."
    Start-Process -FilePath $dockerExe | Out-Null
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 2
        try {
            docker info *> $null
            if ($LASTEXITCODE -eq 0) { $dockerReady = $true; break }
        } catch {}
    }
    if (-not $dockerReady) {
        throw "Docker Engine did not become ready within 90 seconds."
    }
}

# 2. Start PostgreSQL + pgvector container and wait until healthy
Write-Host "[2/5] Starting PostgreSQL (aura-edge-postgres) and waiting for health..."
Push-Location $RepoRoot
try {
    docker compose up -d postgres | Out-Null
    $pgHealthy = $false
    for ($i = 0; $i -lt 30; $i++) {
        $health = docker inspect -f "{{.State.Health.Status}}" aura-edge-postgres 2>$null
        if ($health -eq "healthy") { $pgHealthy = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $pgHealthy) {
        throw "PostgreSQL container aura-edge-postgres did not reach healthy state."
    }

    Write-Host "[3/5] Applying Alembic migrations (upgrade head)..."
    & $PythonExe -m alembic -c (Join-Path $RepoRoot "backend\alembic.ini") upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Alembic upgrade head failed."
    }
} finally {
    Pop-Location
}

# 3. Optional local Ollama loopback check
$StartedOllama = $null
if (-not $SkipOllama) {
    $ollamaUp = $false
    try {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2
        $ollamaUp = $true
    } catch {}

    if (-not $ollamaUp -and (Get-Command ollama -ErrorAction SilentlyContinue)) {
        Write-Host "[4/5] Starting local Ollama server on 127.0.0.1:11434..."
        $StartedOllama = Start-Process -FilePath "ollama" -ArgumentList @("serve") -PassThru -WindowStyle Hidden
        for ($i = 0; $i -lt 15; $i++) {
            Start-Sleep -Seconds 1
            try {
                $null = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2
                $ollamaUp = $true
                break
            } catch {}
        }
    } else {
        Write-Host "[4/5] Local Ollama reachable=$ollamaUp."
    }
}

# 4. Enforce strict offline model guards and start Uvicorn backend on loopback
$env:PYTHONPATH = Join-Path $RepoRoot "backend\src"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
$env:AURA_EDGE_CONNECTIVITY_MODE = "strict_offline"
$env:AURA_EDGE_COMPUTE_MODE = $ComputeMode
$env:AURA_EDGE_DEFAULT_LANGUAGE = $Language

# Stop previous tracked backend PID if still running
$BackendPidFile = Join-Path $RuntimeDir "backend.pid"
if (Test-Path $BackendPidFile) {
    $oldPid = Get-Content $BackendPidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($oldPid -match "^\d+$" -and (Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue)) {
        Stop-Process -Id ([int]$oldPid) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $BackendPidFile -Force -ErrorAction SilentlyContinue
}

Write-Host "[5/5] Starting AURA EDGE backend (compute=$ComputeMode, language=$Language, strict_offline)..."
$BackendLog = Join-Path $RuntimeDir "backend.log"
$BackendErr = Join-Path $RuntimeDir "backend.err.log"
$BackendProc = Start-Process -FilePath $PythonExe `
    -ArgumentList @("-m", "uvicorn", "aura_edge.main:app", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $BackendLog `
    -RedirectStandardError $BackendErr `
    -PassThru -WindowStyle Hidden
$BackendProc.Id | Set-Content $BackendPidFile

$readyOk = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/readiness" -TimeoutSec 3
        if ($r.ready -eq $true) {
            $readyOk = $true
            break
        }
    } catch {}
}

if (-not $readyOk) {
    if ($BackendProc -and -not $BackendProc.HasExited) {
        taskkill /PID $BackendProc.Id /T /F 2>$null | Out-Null
    }
    throw "AURA EDGE backend failed readiness check on http://127.0.0.1:8000/api/v1/readiness. See $BackendErr"
}

Write-Host "AURA EDGE ready at http://127.0.0.1:8000 (PID $($BackendProc.Id))."

if ($NoCli) {
    Write-Host "Running in -NoCli mode. Stop cleanly with .\scripts\stop-dev.ps1 -WithDb"
    exit 0
}

try {
    $cliArgs = @($CliScript, "--url", "http://127.0.0.1:8000")
    if ($NoSpeak) { $cliArgs += "--no-speak" }
    & $PythonExe @cliArgs
} finally {
    if (-not $KeepRunningOnExit) {
        Write-Host "Stopping AURA EDGE backend..."
        if ($BackendProc -and -not $BackendProc.HasExited) {
            taskkill /PID $BackendProc.Id /T /F 2>$null | Out-Null
        }
        Remove-Item $BackendPidFile -Force -ErrorAction SilentlyContinue
        if ($StartedOllama -and -not $StartedOllama.HasExited) {
            Stop-Process -Id $StartedOllama.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
