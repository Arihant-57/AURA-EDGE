# AURA EDGE — System Architecture (Phase 0 Baseline)

## 1. Architectural Pattern

AURA EDGE uses a **layered modular monolith** combined with an **event-driven automation boundary** (implemented incrementally across phases). One deployable core FastAPI application houses strict domain boundaries, backed by local supporting processes (PostgreSQL + pgvector, Ollama, and later Mosquitto MQTT).

> Status note: Only **Phase 0 (Foundation)** is implemented today. Speech, intelligence routing/planning, smart home devices, personal tools, and WebSockets are defined as package boundaries only.

## 2. Phase 0 Module Layout (`backend/src/aura_edge/`)

| Layer | Module / Path | Responsibility (Phase 0 Status) |
|---|---|---|
| Configuration | `config.py` | Loads `AURA_EDGE_*` settings; defaults to `strict_offline`; enforces loopback bind host in strict mode; rejects non-PostgreSQL URLs (e.g. SQLite). |
| Infrastructure | `infrastructure/network_policy.py` | Centralized fail-closed `NetworkPolicyEnforcer` + `PolicyEnforcingTransport` / `AsyncPolicyEnforcingTransport` for `httpx`. Evaluates `strict_offline`, `lan_only`, and `online_opt_in` without external DNS lookups. |
| Infrastructure | `infrastructure/hardware.py` | Measured host CPU/RAM (`psutil`), NVIDIA GPU/VRAM (`pynvml`), and loopback Ollama (`GET /api/tags`) via `NetworkPolicyEnforcer`. |
| Data | `data/models.py`, `data/database.py` | SQLAlchemy 2.0 ORM (`system_settings`, `connectivity_audit_events`, `phase0_vector_smoke`), PostgreSQL engine guard, and live `DatabaseReadiness` probe (`pgvector` + Alembic revision check). |
| API | `api/routes_health.py`, `main.py` | `GET /api/v1/health`, `GET /api/v1/readiness` (200 ready / 503 honest unavailability), `GET /api/v1/policy`, `POST /api/v1/policy/check`. |
| Presentation | `frontend/` | Local Vite + React 18 + TypeScript + Tailwind CSS v4 shell; zero external fonts or CDNs; renders real backend, DB, policy, and hardware diagnostics. |
| Reserved Domain Boundaries | `speech/`, `intelligence/`, `application/`, `execution/` | Package boundaries reserved for Phases 1–4. No stubbed/fake runtime handlers. |

## 3. Connectivity Contract

| Mode | Default? | Permitted Targets | Denied Targets |
|---|---|---|---|
| `strict_offline` | **Yes** | Loopback only (`127.0.0.1`, `::1`, `localhost`) | All public domains/IPs, RFC1918 LAN IPs, link-local metadata (`169.254.0.0/16`), non-loopback server bind hosts |
| `lan_only` | No | Loopback + configured private RFC1918 CIDRs (`AURA_EDGE_LAN_ALLOWED_CIDRS`) | Public internet domains/IPs, link-local metadata, multicast/unspecified IPs |
| `online_opt_in` | No | Loopback + configured LAN + exact hostnames in `AURA_EDGE_ONLINE_ALLOWED_HOSTS` | Unlisted external hosts, link-local metadata, multicast/unspecified IPs |

## 4. Planned Implementation Order (Phases 0–7)

- **Phase 0 (Current)**: Standalone repository, config & strict offline policy, PostgreSQL + pgvector + Alembic foundation, real health/readiness API, minimal local UI shell, baseline verification.
- **Phase 1**: EN/HI/KN microphone-to-STT-to-router-to-TTS, CPU/GPU compute selection, system clock date/time, persistent real timers, safe refusal.
- **Phase 2**: Device registry, stateful smart-home simulator, UI device cards, room/group commands, basic routines, optional LAN ESP32 MQTT adapter.
- **Phase 3**: PostgreSQL preferences & conversation history, local multilingual retrieval (pgvector), APScheduler / persistent jobs, event/notification bus, restart recovery.
- **Phase 4**: Personal assistant tools (tasks, calendar, briefings, groceries, recipes, deduplicated expense import, cached parcels, allowlisted desktop/file ops).
- **Phase 5**: Full desktop-first dashboard, Dynamic Island integration/equivalent, WebSocket state stream, optional paired phone LAN interface.
- **Phase 6**: Opt-in read-only sync and official service handoff (no automated checkout or payment).
- **Phase 7**: Full regression, security/privacy audit, offline/LAN demonstration, CPU vs GPU benchmarks.
