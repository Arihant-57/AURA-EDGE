# AURA EDGE — Phase 0 Engineering Handover & Context for ChatGPT

> **Document Purpose:** Complete technical summary and context packet for continuing AURA EDGE development in ChatGPT. Contains verified environment findings, Phase 0 architecture, code structure, audit of the pre-existing AURA V4 codebase, test verification evidence, and the exact scope boundary for Phase 1.

---

## 1. Project Background & Challenge Specifications

- **Project Name:** AURA EDGE
- **Challenge:** Synapse 2.0, Problem Statement 13 — *"A Voice Assistant That Works Completely Offline"*
- **Location on Host:** `C:\Users\ariha\Desktop\AURA-EDGE` (Git branch: `main`, Commit: `3a60c73`)
- **Core Vision:** An offline-first, multilingual personal AI assistant for Indian homes and workspaces capable of operating completely without internet access on a single Windows laptop.
- **Language Requirements:** Spoken interaction in **English (EN)**, **Hindi (HI)**, and **Kannada (KN)**, responding in the spoken language of the user with varied phrasing recognition, time/date, timers, smart-home device control, and graceful refusal of unsupported commands.
- **Current Authorization Status:** **Phase 0 (Foundation) is complete and verified.** Phase 1 (Multilingual Voice Pipeline) is the immediate next step.

---

## 2. Host Environment & Hardware Inventory (Verified Locally)

The following metrics were directly measured and verified on the host machine (no simulated or assumed specs):

| Component | Measured Host Value | Notes |
|---|---|---|
| **Operating System** | Windows 11 Home Single Language (Build `10.0.26300`, 64-bit) | Native host execution |
| **CPU** | 13th Gen Intel(R) Core(TM) i7-13650HX | 14 physical cores, 20 logical threads |
| **System RAM** | 15.78 GB Total | ~7.5–15 GB used depending on running daemons |
| **GPU / VRAM** | NVIDIA GeForce RTX 3050 6GB Laptop GPU | 6144 MiB VRAM via `nvidia-smi` / NVML; Driver `621.24` |
| **Python** | Python `3.13.2` (primary in `.venv`) & Python `3.12` installed | `uv`, `pip` available |
| **Node.js & npm** | Node.js `v26.13.1`, npm `11.20.0` | Located at `C:\Program Files\nodejs\` |
| **Docker Engine** | Docker Desktop 29.8.0 (Compose v5.5.1) | Used to host local PostgreSQL 16 + pgvector |
| **Local Ollama** | Ollama `0.40.16` installed at `~\AppData\Local\Programs\Ollama\` | Client & CLI available; loopback port `11434` |
| **Models On Disk** | `qwen3.5:4b`, `qwen3.5:9b`, `qwen2.5-coder:7b`, `nemotron-3-nano:4b`, `nomic-embed-text:latest` | `qwen3.5:2b` is **not yet pulled** |
| **Hugging Face Cache** | `models--Systran--faster-whisper-base.en`, `models--sentence-transformers--all-MiniLM-L6-v2` | Multilingual Whisper (`base`/`small`) **not yet cached** |

---

## 3. Pre-existing Codebase Audit: AURA V4 (`phase4-agent`)

The user had an existing experimental project at `C:\Users\ariha\local-ai-lab\phase4-agent` (`AURA V4`). 
**Critical Safety Rule Enforced:** The existing repository was kept **100% untouched** (commit `0a1635c`). No `.env`, personal memories (`aura_memory_and_storage/`), or source files were copied into `AURA-EDGE`.

### Reuse Audit Findings & Clean-Room Strategy:
1. **Microphone & Speech (`src/aura/core/mic_listener.py`):**
   - *AURA V4 State:* Hardcoded for English (`base.en`), int8 on CPU, with English-only bias prompts (`Arihant`, `Jarvis`).
   - *AURA EDGE Strategy:* Build a fresh multilingual speech module in Phase 1 supporting `faster-whisper` (`base` / `small` multilingual) with language detection or manual EN/HI/KN selection.
2. **TTS Engine (`src/aura/core/voice.py`):**
   - *AURA V4 State:* Relies on cloud Microsoft `edge-tts` over the internet with fallback to Windows SAPI (which has poor or absent Indic neural voices).
   - *AURA EDGE Strategy:* Cloud `edge-tts` is strictly blocked in offline mode. Phase 1 must evaluate lightweight local neural TTS (e.g. `AI4Bharat/indic-parler-tts`, `piper`, or optimized offline speech engines).
3. **Intent Routing (`src/aura/core/fast_router.py`):**
   - *AURA V4 State:* Mixed deterministic regex routing with direct external Spotify HTTP API requests and YouTube/Google URL generation.
   - *AURA EDGE Strategy:* Build a typed intent router (supporting Hassil / multilingual regex / local embeddings) that resolves to pure local actions without touching external URLs.
4. **Storage & Memory (`src/aura/core/personal_memory.py`, `workspace_connector.py`):**
   - *AURA V4 State:* Unstructured `personal_facts.jsonl` and local `.json` files; IMAP/SMTP for Gmail.
   - *AURA EDGE Strategy:* Authoritative PostgreSQL 16 + `pgvector` with Alembic migrations for all persistent state.
5. **Hardware Telemetry (`src/aura/core/telemetry.py`):**
   - Cleanly re-implemented in AURA EDGE as `aura_edge/infrastructure/hardware.py` using `psutil`, `pynvml`, and policy-enforced Ollama probing.

---

## 4. Phase 0 Architecture & What Was Built in `AURA-EDGE`

The repository at `C:\Users\ariha\Desktop\AURA-EDGE` is organized as a clean **layered modular monolith**:

```
C:\Users\ariha\Desktop\AURA-EDGE\
├── .env.example                     # Documents all environment configurations (strict_offline default)
├── .gitignore                       # Clean hygiene (excludes .env, .venv, node_modules, logs, models)
├── docker-compose.yml               # Local PostgreSQL 16 + pgvector (127.0.0.1:5432)
├── README.md                        # Quickstart, setup instructions, and architecture summary
├── scripts\
│   ├── start-dev.ps1                # Start FastAPI (port 8000) & Vite (port 5173), optional -WithDb
│   └── stop-dev.ps1                 # Stop local processes & optional DB container
├── docs\
│   ├── ARCHITECTURE.md              # Modular monolith boundaries & connectivity contract
│   ├── DECISIONS.md                 # Architecture Decision Records & AURA V4 reuse audit
│   ├── PROGRESS.md                  # Phase 0–7 status log
│   ├── TEST_REPORT.md               # Verbatim test execution commands & outputs
│   ├── THIRD_PARTY.md               # Package licenses & candidate models inventory
│   └── spec\                        # Read-only copies of master context & prompt
├── backend\
│   ├── pyproject.toml               # Package configuration (FastAPI, SQLAlchemy, Alembic, pgvector, httpx)
│   ├── requirements.txt & dev.txt   # Pinned dependencies
│   ├── alembic.ini & alembic\       # Reversible DB migrations (0001_phase0_foundation.py)
│   ├── scripts\
│   │   └── db_smoke_test.py         # Write -> Restart -> Verify pgvector smoke test
│   ├── src\aura_edge\
│   │   ├── config.py                # Settings with strict_offline default, loopback & PG guards
│   │   ├── infrastructure\
│   │   │   ├── network_policy.py    # Fail-closed NetworkPolicyEnforcer + httpx transports
│   │   │   └── hardware.py          # Real CPU, RAM, NVML GPU, and loopback Ollama telemetry
│   │   ├── data\
│   │   │   ├── database.py          # Engine factory + live DatabaseReadiness probe
│   │   │   └── models.py            # SystemSetting, ConnectivityAuditEvent, Phase0VectorSmoke
│   │   ├── api\
│   │   │   └── routes_health.py     # /health, /readiness (200/503), /policy, /policy/check
│   │   ├── speech\                  # [Boundary reserved for Phase 1]
│   │   ├── intelligence\            # [Boundary reserved for Phase 1/3]
│   │   ├── application\             # [Boundary reserved for Phase 2/4]
│   │   ├── execution\               # [Boundary reserved for Phase 1+]
│   │   └── main.py                  # FastAPI application entrypoint with CORS & policy wiring
│   └── tests\
│       ├── test_config_and_db_guard.py
│       ├── test_network_policy.py
│       ├── test_health_api.py
│       └── test_db_live_integration.py
└── frontend\
    ├── package.json                 # React 18, Vite 6, Tailwind CSS v4, TypeScript 5.9, Vitest
    ├── vite.config.ts               # Proxy /api -> 127.0.0.1:8000, strict port 5173
    ├── index.html & src\index.css   # Zero external CDNs / zero Google fonts (system font stack)
    ├── src\App.tsx                  # Live console rendering health, DB, hardware, and policy dry-run
    ├── src\api\client.ts            # Typed client for backend health/readiness endpoints
    └── src\App.test.tsx             # Component tests (ready state, HTTP 503 DB failure, unreachable)
```

---

## 5. Key Technical Implementations

### A. Centralized Outbound Network Policy (`strict_offline`)
- Implemented in `aura_edge/infrastructure/network_policy.py`.
- **Modes:** `strict_offline` (default), `lan_only`, `online_opt_in`.
- In `strict_offline`, **only loopback targets** (`127.0.0.1`, `localhost`, `::1`) are permitted. All public internet, LAN IPs, and link-local cloud metadata (`169.254.169.254`) are rejected fail-closed.
- Evaluates URLs syntactically without triggering external DNS resolution.
- Includes `PolicyEnforcingTransport` and `AsyncPolicyEnforcingTransport` wrapping `httpx.Client`, ensuring any outbound HTTP attempt to a blocked target raises `NetworkPolicyViolationError` **before** opening a network socket.

### B. PostgreSQL 16 + pgvector Persistence
- Configured via `docker-compose.yml` (`pgvector/pgvector:pg16` on `127.0.0.1:5432`).
- SQLite is strictly blocked by Pydantic validator in `Settings` and `create_db_engine`.
- Initial Alembic migration `0001_phase0_foundation` activates `CREATE EXTENSION IF NOT EXISTS vector;` and creates `system_settings`, `connectivity_audit_events`, and `phase0_vector_smoke`.
- **Verified Smoke Test:** `backend/scripts/db_smoke_test.py` wrote a test setting and a 3D vector `[0.1, 0.2, 0.7]`, the PostgreSQL container was restarted, and a fresh process read back the records via pgvector L2 distance `<->` (`distance == 0.0`), verifying durability across restarts.

### C. Backend Health & Diagnostics API
- `GET /api/v1/health` -> HTTP 200: returns service name, version, connectivity mode, bind host.
- `GET /api/v1/readiness` -> Returns full diagnostic breakdown (connectivity summary, PostgreSQL status, pgvector version, Alembic revision, CPU/RAM/GPU telemetry, Ollama reachability). Returns HTTP `200` when ready, or honest HTTP `503` if PostgreSQL is down.
- `GET /api/v1/policy` & `POST /api/v1/policy/check` -> Dry-run endpoint to evaluate whether any URL is permitted without emitting outbound network packets.

### D. Zero-CDN Local Frontend Shell
- Built with React 18, Vite 6, Tailwind CSS v4, and TypeScript.
- Renders live backend status, PostgreSQL + pgvector readiness, measured CPU/RAM/GPU stats, and an interactive policy evaluation widget.
- Contains zero external fonts or CDN dependencies; verified by scanning `dist/` bundle.

---

## 6. Test Suite & Verification Results (All Passing)

1. **Python Linter (`ruff`):**
   - Command: `ruff check .\backend`
   - Result: **0 errors** (`All checks passed!`).
2. **Backend Automated Tests (`pytest`):**
   - Command: `$env:AURA_EDGE_REQUIRE_LIVE_DB="1"; pytest -v .\backend\tests`
   - Result: **14 passed, 0 failed** in `1.10s`.
   - Covered: Strict loopback enforcement, zero-I/O `httpx` blocking, LAN-only CIDR rules, online opt-in allowlisting, SQLite URL rejection, non-loopback bind rejection, readiness 200/503 behavior, and live PostgreSQL + pgvector L2 distance queries.
3. **Database Durability Smoke Test:**
   - Command: `python db_smoke_test.py --phase write` -> `docker compose restart postgres` -> `python db_smoke_test.py --phase verify`
   - Result: **Verified (`l2_distance: 0.0`, `pgvector_version: "0.8.7"`)**.
4. **Live HTTP Server Smoke Test:**
   - Started Uvicorn on `127.0.0.1:8000`.
   - `/api/v1/health` -> HTTP 200.
   - `/api/v1/readiness` -> HTTP 200 (`ready: true`, PostgreSQL 16.16, pgvector 0.8.7, RTX 3050 6GB GPU detected).
   - `/api/v1/policy/check` -> `https://example.com` = `DENIED`; `http://127.0.0.1:11434` = `ALLOWED`.
5. **Frontend Tests & Build:**
   - `npx tsc --noEmit` -> 0 errors.
   - `npm test -- --run` (Vitest) -> **3 passed, 0 failed** (Ready state, 503 DB failure, Network error).
   - `npm run build` -> Clean production bundle in `frontend/dist/` with zero remote URLs.

---

## 7. Open Questions & Competition Blockers to Note

1. **Missing Problem Statements PDF:** `Synapse_2.0_Problem_Statements.pdf` was not present on disk; specifications were derived from `AURA_EDGE_MASTER_CONTEXT.md`.
2. **Competition Rules on Prior Code & Models:** Need confirmation from hackathon organizers regarding whether pre-event code from AURA V4 can be imported or if a clean-room implementation is mandatory, and whether pre-downloading model weights is permitted before the timer begins.
3. **Offline Model Cache:** Multilingual `faster-whisper` (`base` or `small`), local Hindi/Kannada TTS voices, and `qwen3.5:2b` are not yet cached locally.

---

## 8. Exact Next Step: Prompt for Phase 1 (Multilingual Voice)

When you are ready to proceed with ChatGPT, here is the suggested prompt:

```text
We have completed Phase 0 of AURA EDGE in C:\Users\ariha\Desktop\AURA-EDGE.
The backend (FastAPI, strict offline policy, PostgreSQL 16 + pgvector via Docker, hardware telemetry) and frontend shell (Vite, React, Tailwind v4) are fully tested and passing (14/14 pytest, 3/3 vitest, db smoke verified).

Now, let's design and implement Phase 1: Multilingual Voice Pipeline (English, Hindi, Kannada).
Requirements from Problem Statement 13:
1. Microphone-to-STT pipeline supporting English, Hindi, and Kannada using faster-whisper (base/small multilingual).
2. Spoken reply generation in the user's detected/chosen language using a lightweight local offline TTS (benchmark CPU latency on Windows).
3. System date/time tool and persistent real countdown timers (create, inspect, cancel).
4. Deterministic intent router for simple commands with graceful refusal of unknown/misunderstood requests.
5. CPU vs GPU compute placement options (testing RTX 3050 6GB vs CPU-only mode).
6. Strict offline guarantee: absolutely no cloud TTS (like edge-tts) or external APIs.

Please review our Phase 0 architecture and provide the step-by-step implementation plan for Phase 1.
```
