param(
    [string]$BackendRoot,
    [string]$DesktopPython,
    [ValidateRange(1024, 65535)]
    [int]$BackendPort = 8000
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $DesktopPython) {
    $DesktopPython = Join-Path $RepoRoot ".runtime\desktop-venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $DesktopPython)) {
    throw "Desktop Python environment is missing. Follow docs/DESKTOP_USER_GUIDE.md for one-time setup."
}
if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot "frontend\dist\index.html"))) {
    throw "Dashboard build is missing. Run npm run build from the frontend directory."
}

function Read-LocalJson([string]$Url) {
    $json = & curl.exe --noproxy "*" --silent --fail --max-time 4 $Url
    if ($LASTEXITCODE -eq 0) { ($json -join "`n") | ConvertFrom-Json }
}

$Base = "http://127.0.0.1:$BackendPort"
$DesktopArgs = @((Join-Path $RepoRoot "desktop\main.py"), "--backend-port", "$BackendPort")
$Schema = Read-LocalJson "$Base/openapi.json"
if ($Schema) {
    $Health = Read-LocalJson "$Base/api/v1/health"
    if ($Health.service -ne "aura-edge" -or $Health.connectivity_mode -ne "strict_offline") {
        throw "Port $BackendPort must serve AURA EDGE in strict offline mode."
    }
    $PlannerRoutes = @("/api/v1/briefing", "/api/v1/tasks", "/api/v1/calendar/events", "/api/v1/reminders")
    $Missing = $PlannerRoutes | Where-Object { -not $Schema.paths.PSObject.Properties[$_] }
    if ($Missing) {
        Write-Warning "The running backend is missing $($Missing -join ', '). Restart it from the approved Phase 4 checkout for the full desktop experience."
    }
    # Readiness also returns JSON with HTTP 503 when PostgreSQL is unavailable.
    $ReadinessJson = & curl.exe --noproxy "*" --silent --max-time 5 "$Base/api/v1/readiness"
    $Ready = if ($LASTEXITCODE -eq 0) { ($ReadinessJson -join "`n") | ConvertFrom-Json }
    if (-not $Ready.database.connected) { Write-Warning "PostgreSQL is unavailable. Check the existing AURA EDGE database service." }
    Write-Host "Reusing AURA EDGE on $Base."
} else {
    if (-not $BackendRoot) {
        $BackendRoot = $RepoRoot
        if (-not (Test-Path -LiteralPath (Join-Path $BackendRoot ".venv\Scripts\python.exe"))) {
            $BackendRoot = Join-Path $env:USERPROFILE "Desktop\AURA-EDGE"
        }
    }
    $BackendRoot = (Resolve-Path -LiteralPath $BackendRoot).Path
    $BackendPython = Join-Path $BackendRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $BackendPython)) {
        throw "The existing backend Python environment was not found. Pass -BackendRoot with the Phase 4 checkout."
    }
    if ((Get-Command docker -ErrorAction SilentlyContinue) -and -not $env:AURA_EDGE_DATABASE_URL) {
        $PgHealth = docker inspect -f "{{.State.Health.Status}}" aura-edge-postgres 2>$null
        if ($PgHealth -ne "healthy") { Write-Warning "The existing AURA PostgreSQL container is not healthy. Check PostgreSQL before using planner or device features." }
    }
    $DesktopArgs += @("--backend-root", $BackendRoot, "--backend-python", $BackendPython)
    Write-Host "Starting the desktop with its own backend process from $BackendRoot."
}

$Ollama = Read-LocalJson "http://127.0.0.1:11434/api/tags"
if (-not $Ollama) { Write-Warning "Local Ollama is unavailable. Start your existing Ollama service for conversational requests." }
elseif ($Ollama.models.name -notcontains "qwen3.5:4b") { Write-Warning "The default model qwen3.5:4b is missing from local Ollama." }

& $DesktopPython @DesktopArgs
exit $LASTEXITCODE
