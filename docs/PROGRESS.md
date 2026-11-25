# AURA EDGE — Project Progress Log

## Phase 0 — Foundation (Authorized)

**Status:** Verified and ready for Phase 0 gate review (subject to unresolved competition rules noted below).

### Verified Deliverables
- [x] **Standalone Repository**: Created `C:\Users\ariha\Desktop\AURA-EDGE` on branch `main`; `C:\Users\ariha\local-ai-lab\phase4-agent` (AURA V4) verified untouched.
- [x] **Git Hygiene**: `.gitignore` excludes `.env`, `.venv/`, `node_modules/`, `dist/`, `.runtime/`, logs, private memory/JSONL, and model caches; `.env.example` committed.
- [x] **Layered Modular Monolith Backend**: Implemented FastAPI backend (`config.py`, `infrastructure/network_policy.py`, `infrastructure/hardware.py`, `data/models.py`, `data/database.py`, `api/routes_health.py`, `main.py`) with reserved package boundaries (`speech/`, `intelligence/`, `application/`, `execution/`).
- [x] **Centralized Offline Policy**: `strict_offline` default blocks non-loopback `bind_host` and non-loopback HTTP/WS requests via `PolicyEnforcingTransport` / `AsyncPolicyEnforcingTransport` before any outbound network I/O; `lan_only` and `online_opt_in` tested via unit tests.
- [x] **PostgreSQL + pgvector & Alembic**: `docker-compose.yml` configures `pgvector/pgvector:pg16` bound to `127.0.0.1:5432`; SQLite rejected at config/engine boundary; initial migration `0001_phase0_foundation` applied, reversed (`downgrade base`), and re-applied; write -> `docker compose restart postgres` -> read/L2-distance verification passed (`pgvector 0.8.7`).
- [x] **Minimal Local UI Shell**: Vite + React 18 + TypeScript + Tailwind CSS v4 frontend (`frontend/`) renders real `/api/v1/health`, `/api/v1/readiness` (200 & 503 states), and `/api/v1/policy/check` diagnostics using zero remote fonts or CDNs.
- [x] **Baseline Environment & Documentation**: `README.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/THIRD_PARTY.md`, `docs/TEST_REPORT.md`, and `scripts/start-dev.ps1` / `stop-dev.ps1`.

### Known Limitations & Unresolved Rules (Phase 0)
- `Synapse_2.0_Problem_Statements.pdf` was not present in the local workspace or `Downloads`; specs were taken from `docs/spec/AURA_EDGE_MASTER_CONTEXT.md`.
- Organizer permission on code reuse, pre-downloaded models, and pre-built tooling remains to be confirmed before Phase 1+.
- Local PostgreSQL runs in a Docker container (`pgvector/pgvector:pg16`), so Docker Desktop must be started before running PostgreSQL-dependent commands.
- Ollama (`0.40.16`) was stopped during Phase 0 checks (reported honestly by `/api/v1/readiness` as unreachable), and `qwen3.5:2b` plus multilingual `faster-whisper` (`base`/`small`) and EN/HI/KN TTS models are not yet downloaded/benchmarked.

### Proposed Future Phases (Not Started — Awaiting User Authorization)
- **Phase 1**: EN/HI/KN voice pipeline (`faster-whisper` multilingual STT, local TTS, deterministic router, CPU/GPU placement, clock/date, timers, refusal).
- **Phase 2**: Stateful smart-home simulator, device registry, UI controls, routines, optional LAN ESP32 MQTT adapter.
- **Phase 3**: PostgreSQL preferences/history, local RAG, persistent scheduler, notification bus.
- **Phase 4**: Personal assistant tools (tasks, calendar, briefings, groceries, recipes, expenses, cached parcels, allowlisted desktop/files).
- **Phase 5**: Full dashboard, Dynamic Island, live WebSocket updates, optional phone LAN UI.
- **Phase 6**: Optional opt-in read-only external sync & service navigation.
- **Phase 7**: Full regression, security/privacy audit, offline/LAN demonstration, CPU vs GPU benchmarks.
