# AURA EDGE

**Offline-first, multilingual personal AI assistant prototype (Phase 0 Foundation).**

> **Current Scope (Phase 0 only):** Repository structure, strict-offline connectivity policy enforcement, local PostgreSQL + `pgvector` schema & Alembic migrations, host hardware telemetry, health/readiness endpoints, and a minimal local React/TypeScript/Tailwind status shell. Voice (STT/TTS), smart home control, memory retrieval, personal assistant workflows, and online adapters belong to Phases 1–6 and are **not implemented yet**.

## Prerequisites (Verified Target Windows 11 Environment)

- Windows 11 (x64)
- Python 3.11+ (verified on Python `3.13.2`)
- Node.js + npm (verified on Node `v26.13.1`, npm `11.20.0`)
- Docker Desktop (for local PostgreSQL 16 + `pgvector` container bound to `127.0.0.1:5432`)
- Git

## Quickstart (Phase 0)

### 1. Start local PostgreSQL + pgvector
Start Docker Desktop (`docker desktop start` or open Docker Desktop), then run:

```powershell
cd C:\Users\ariha\Desktop\AURA-EDGE
copy .env.example .env
docker compose up -d postgres
```

### 2. Create Python environment and apply initial migration

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .\backend -r .\backend\requirements-dev.txt
.\.venv\Scripts\alembic.exe -c backend\alembic.ini upgrade head
```

### 3. Build or run the local frontend shell

```powershell
cd frontend
npm install
npm run build
```

### 4. Start & stop the local Phase 0 application

```powershell
# Start FastAPI (127.0.0.1:8000) and Vite UI (127.0.0.1:5173)
.\scripts\start-dev.ps1

# Or also start/migrate PostgreSQL first:
.\scripts\start-dev.ps1 -WithDb

# Stop local backend & frontend processes (add -WithDb to stop PostgreSQL container too):
.\scripts\stop-dev.ps1
```

Open `http://127.0.0.1:5173` in a local browser.

## Verification Commands

```powershell
# Backend lint + full test suite (with live PostgreSQL required)
$env:AURA_EDGE_REQUIRE_LIVE_DB = "1"
.\.venv\Scripts\ruff.exe check .\backend
.\.venv\Scripts\pytest.exe -v .\backend\tests

# PostgreSQL + pgvector restart persistence smoke test
.\.venv\Scripts\python.exe backend\scripts\db_smoke_test.py --phase write --token manual-smoke
docker compose restart postgres
.\.venv\Scripts\python.exe backend\scripts\db_smoke_test.py --phase verify --token manual-smoke

# Frontend typecheck, unit tests, and offline production build
cd frontend
npx tsc --noEmit
npm test -- --run
npm run build
```

## Connectivity Policy Defaults

`AURA_EDGE_CONNECTIVITY_MODE` defaults to `strict_offline`:
- Outbound HTTP/WS traffic via `NetworkPolicyEnforcer` and `PolicyEnforcingTransport` allows **loopback targets only** (`127.0.0.1`, `::1`, `localhost`).
- External domains, public IPs, RFC1918 LAN IPs, link-local metadata (`169.254.0.0/16`), and non-loopback `bind_host` configurations are denied fail-closed.
- `lan_only` and `online_opt_in` modes require explicit configuration.
