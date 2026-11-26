param(
    [string]$BackendRoot = "$env:USERPROFILE/Desktop/AURA-EDGE",
    [switch]$CheckSetup
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $BackendRoot ".venv/Scripts/python.exe"
$port = 8035
$dbPort = 55436
$id = [guid]::NewGuid().ToString("N")
$container = "aura-microphone-$id"
$marker = Join-Path $root ".runtime/microphone-stop-$id"
$result = Join-Path $root ".runtime/human-acceptance-$id.json"
$server = $null
$started = $false
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
    $server = Start-Process $python -ArgumentList @("-m", "qa.loopback_rehearsal", "`"$marker`"", "$port") -WorkingDirectory $root -WindowStyle Hidden -PassThru
    $ready = $null
    for ($i = 0; $i -lt 75; $i++) {
        if ($server.HasExited) { throw "The rehearsal backend exited." }
        try { $ready = Invoke-RestMethod "http://127.0.0.1:$port/api/v1/readiness" -TimeoutSec 3 } catch {}
        if ($ready.database.connected) { break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready.database.connected) { throw "The rehearsal backend did not become ready." }
    if ($CheckSetup) { Write-Output "Rehearsal backend and disposable PostgreSQL ready." }
    else {
        Write-Output "The rehearsal dashboard is http://127.0.0.1:$port/app/. Use this instance during the session."
        & $python -m qa.live_acceptance --port $port --samples 16 --rehearsal --include-text --output $result
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
