param(
    [string]$BackendRoot = "$env:USERPROFILE/Desktop/AURA-EDGE",
    [switch]$CheckSetup,
    [switch]$RestartCheck,
    [switch]$KannadaCheck
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $BackendRoot ".venv/Scripts/python.exe"
$port = if ($RestartCheck) { 8036 } else { 8035 }
$dbPort = if ($RestartCheck) { 55438 } else { 55436 }
$id = [guid]::NewGuid().ToString("N")
$container = "aura-microphone-$id"
$marker = Join-Path $root ".runtime/microphone-stop-$id"
$result = Join-Path $root ".runtime/human-acceptance-$id.json"
$server = $null
$started = $false
if (($CheckSetup -and $RestartCheck) -or ($KannadaCheck -and ($CheckSetup -or $RestartCheck))) { throw "Choose one verification mode." }

function Start-RehearsalBackend([string]$stopMarker) {
    $serverArgs = @("-m", "qa.loopback_rehearsal", "`"$stopMarker`"", "$port")
    if (-not $RestartCheck -and -not $CheckSetup) { $serverArgs += "--interactive-audio" }
    Start-Process $python -ArgumentList $serverArgs -WorkingDirectory $root -WindowStyle Hidden -PassThru
}

function Wait-RehearsalBackend($process) {
    for ($i = 0; $i -lt 75; $i++) {
        if ($process.HasExited) { throw "The rehearsal backend exited." }
        try {
            $ready = Invoke-RestMethod "http://127.0.0.1:$port/api/v1/readiness" -TimeoutSec 3
            if ($ready.database.connected) { return }
        } catch {}
        Start-Sleep -Seconds 1
    }
    throw "The rehearsal backend did not become ready."
}
$previous = @{}
foreach ($name in @("AURA_EDGE_DATABASE_URL", "AURA_EDGE_MODELS_DIR", "AURA_EDGE_HINDI_SPECIALIST_DIR", "PYTHONPATH", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "PYTHONIOENCODING")) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name)
}
Push-Location $root
try {
    if (-not (Test-Path -LiteralPath $python)) { throw "Set -BackendRoot to the installed AURA backend environment." }
    if (Get-NetTCPConnection -State Listen -LocalPort $port,$dbPort -ErrorAction SilentlyContinue) { throw "The rehearsal ports are already in use." }
    New-Item -ItemType Directory -Force -Path .runtime | Out-Null
    $secret = [guid]::NewGuid().ToString("N")
    docker run --pull never --rm -d --name $container -e POSTGRES_DB=audit_human -e POSTGRES_USER=audit -e "POSTGRES_PASSWORD=$secret" -p "127.0.0.1:${dbPort}:5432" pgvector/pgvector:pg16 | Out-Null
    if ($LASTEXITCODE) { throw "The locally cached PostgreSQL image could not start." }
    $started = $true
    for ($i = 0; $i -lt 45; $i++) {
        docker exec $container pg_isready -h 127.0.0.1 -U audit -d audit_human *> $null
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 1
    }
    $env:AURA_EDGE_DATABASE_URL = "postgresql+psycopg://audit:${secret}@127.0.0.1:${dbPort}/audit_human"
    $env:AURA_EDGE_MODELS_DIR = Join-Path $BackendRoot "models"
    foreach ($location in @($env:AURA_EDGE_HINDI_SPECIALIST_DIR, (Join-Path $root "models/stt/indic-conformer-hi"), (Join-Path $BackendRoot "models/stt/indic-conformer-hi"))) {
        if ($location -and (Test-Path -LiteralPath (Join-Path $location "model.int8.onnx"))) {
            $env:AURA_EDGE_HINDI_SPECIALIST_DIR = $location
            break
        }
    }
    $env:PYTHONPATH = Join-Path $root "backend/src"
    $env:HF_HUB_OFFLINE = "1"
    $env:TRANSFORMERS_OFFLINE = "1"
    $env:PYTHONIOENCODING = "utf-8"
    & $python -m alembic -c backend/alembic.ini upgrade head
    if ($LASTEXITCODE) { throw "The rehearsal database migration failed." }
    $server = Start-RehearsalBackend $marker
    Wait-RehearsalBackend $server
    if ($CheckSetup) { Write-Output "Rehearsal backend and disposable PostgreSQL ready." }
    elseif ($RestartCheck) {
        $report = Join-Path $root ".runtime/offline-restart-$id.json"
        & $python -m qa.restart_rehearsal prepare --port $port --output $report
        if ($LASTEXITCODE) { throw "Could not prepare offline restart checks." }
        New-Item -ItemType File -Path $marker | Out-Null
        if (-not $server.WaitForExit(30000)) { throw "The rehearsal backend did not stop within 30 seconds." }
        if ($null -ne $server.ExitCode -and $server.ExitCode -ne 0) {
            throw "The rehearsal backend exited with status $($server.ExitCode)."
        }
        $marker += "-restart"
        $server = Start-RehearsalBackend $marker
        Wait-RehearsalBackend $server
        & $python -m qa.restart_rehearsal verify --port $port --output $report
        if ($LASTEXITCODE) { throw "Offline restart checks failed. Results: $report" }
        Write-Output "Restart checks passed. Results: $report"
    }
    else {
        Write-Output "The rehearsal dashboard is http://127.0.0.1:$port/app/. Use this instance during the session."
        $checks = if ($KannadaCheck) { @("--kannada-check") } else { @("--samples", "16", "--rehearsal") }
        & $python -m qa.live_acceptance --port $port --include-text --output $result @checks
        if ($LASTEXITCODE) { throw "Session interrupted. Completed checks are saved in $result." }
    }
} finally {
    if ($server -and -not $server.HasExited) {
        New-Item -ItemType File -Path $marker -Force | Out-Null
        $server.WaitForExit(30000) | Out-Null
    }
    if ($started) { docker stop $container | Out-Null }
    foreach ($name in $previous.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name]) }
    Pop-Location
}
