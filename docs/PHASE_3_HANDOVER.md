# AURA EDGE — Phase 3 Handover (`feat/backend-intelligence`)

**Date:** 2026-11-25  
**Branch:** `feat/backend-intelligence`  
**Previous Verified Baseline:** `812098b8c29e7e2ef456d20a2a180fada6dd24f7`  
**Database Revision:** `0004_phase3_memory_events`  
**Scope Preserved:** `main`, `frontend/`, AURA V4 (`phase4-agent`), and `AURA-EDGE-RESEARCH` are untouched.

---

## 1. Delivered Phase 3 Features

1. **Remaining Voice Stabilization Corrections (3/3 Fixed & Tested):**
   - `intelligence/normalizer.py`: Preserved Kannada virama `\u0CCD` so `"ಟೈಮ್ ಎಷ್ಟು"` routes deterministically to `GetTime` instead of being normalized into `"ಟೈಮರ್್"`.
   - `application/voice_orchestrator.py`: Guaranteed single `response_completed` emission on mid-STT and `StaleTurnError` cancellations (`outcome="cancelled"`).
   - `application/voice_orchestrator.py`: Cleaned `cancel_turn()` bounded `_turn_lock` drain (`>= 2.0s`) and prevented double `resume_after_playback()` calls while keeping explicit user mute sticky.
2. **PostgreSQL + `pgvector` Personal Memory & Privacy Controls:**
   - Normalized `memory_items`, `memory_settings`, and separate opt-in `conversation_history_items` tables (`0004_phase3_memory_events`).
   - Structured appliance preference persistence (`canonical_key = "appliance:{device_id}:{property}"`) validated against real `SmartHomeDevice` capabilities and updated cleanly in place.
   - 3-tier retrieval hierarchy: Tier-1 Exact SQL -> Tier-2 Unicode FTS (`to_tsvector('simple', ...)`) + token overlap -> Tier-3 exact `pgvector` cosine search using locally cached Ollama `nomic-embed-text:latest` (`dim=768`, CPU-only `num_gpu=0`) with transparent keyword fallback.
   - Prompt-injection & secret filtering (`MemorySafetyError`), untrusted `<untrusted_user_memory>` LLM context delimiters, and runtime `memory_enabled` toggle.
3. **PostgreSQL Persistent Scheduler & Crash/Missed-Job Recovery:**
   - `scheduled_jobs` + `job_executions` tables supporting one-time reminders, daily recurring jobs, weekly recurring jobs, and scheduled routines/device actions in `Asia/Kolkata` (`Asia/Kolkata` default + configurable timezones).
   - Concurrent-safe claiming (`SELECT ... FOR UPDATE SKIP LOCKED` + `UNIQUE(job_id, scheduled_occurrence_utc)`).
   - Restart recovery marks interrupted `running` executions as `failed` (`interrupted_by_restart_uncertain_state`) without blindly re-running non-idempotent actions, emits missed-reminder notifications with original due time, and skips overdue stale appliance actions with bounded next-occurrence calculation.
4. **Extended Custom & Conditional Routines (`SmartHomeService`):**
   - Preserves built-in `morning`, `night`, `study`, `away` routines (`is_builtin=True`).
   - Full CRUD (`/api/v1/smart-home/routines`) with pre-validated device capabilities, `manual`/`time`/`simulator_event` triggers, `device_state`/`room_state`/`sensor_threshold` conditions, honest partial-execution reporting (`partial`), and cyclic recursion guards.
5. **Persistent Proactive Notifications & Audio Queue (`NotificationService`):**
   - DB-first notification persistence (`notifications` + `notification_preferences`) with `dedup_key` deduplication, REST catch-up for offline frontends, quiet hours (`Asia/Kolkata`), sticky user mute preservation, and bounded serialized audio playback shared across timers, reminders, simulator completions, device availability changes, and scheduled tasks.

---

## 2. Startup Commands

From `C:\Users\ariha\Desktop\AURA-EDGE`:

```powershell
# 1. Start PostgreSQL + Ollama via existing dev launcher or directly, then run migrations:
.\.venv\Scripts\alembic.exe -c backend/alembic.ini upgrade head

# 2. Launch AURA EDGE backend (strict offline loopback):
.\.venv\Scripts\uvicorn.exe aura_edge.main:app --app-dir backend/src --host 127.0.0.1 --port 8000
```

---

## 3. Known Limitations & Phase 4 Prerequisites

- Physical microphone/speaker acceptance testing in EN/HI/KN (acoustic echo suppression, regional Karnataka Kannada ASR accuracy, subjective `vits-mms-kan` voice quality) still awaits your live hardware test.
- Competition organizer policy on pre-cached models (`faster-whisper`, `sherpa-onnx`, `qwen3.5:4b`, `nomic-embed-text:latest`) and prior AURA V4 research reuse should be formally verified against official Synapse 2.0 rules before submission.
- **Deferred to Phase 4+:** Task boards, calendar events, grocery/inventory/recipe management, expense ledger imports, cached parcel tracking, local PDF/document RAG ingestion, desktop/file launcher tools, and React dashboard implementation on `feat/frontend-experience`.
