# AURA EDGE — Phase 3 Test & Performance Report

**Date:** 2026-11-25  
**Branch:** `feat/backend-intelligence`  
**Database Revision:** `0004_phase3_memory_events`  
**Hardware:** Intel Core i7-13650HX (14c/20t), 15.78 GB RAM, NVIDIA RTX 3050 6GB Laptop GPU

---

## 1. Alembic Migration Verification

1. **Primary Database Upgrade (`aura_edge`):**
   - `.\.venv\Scripts\alembic.exe -c backend/alembic.ini upgrade head` -> upgraded `0003_phase2_smart_home -> 0004_phase3_memory_events` cleanly, preserving all 19 devices, 4 built-in routines (`is_builtin = true`), and existing timer tables.
2. **Disposable Database Round-Trip (`aura_edge_mig_test`):**
   - Ran `upgrade head` (`0001 -> 0004`) -> `downgrade 0003_phase2_smart_home` -> `upgrade head` cleanly with zero errors, then dropped `aura_edge_mig_test`.

---

## 2. Targeted Phase 3 Automated Test Results (`13 passed, 0 failed, 0 skipped`)

Executed against live PostgreSQL + pgvector (`$env:AURA_EDGE_REQUIRE_LIVE_DB="1"`):

| Test File | Tests | Scope Verified | Result |
|---|---:|---|---|
| `backend/tests/test_phase3_voice_fixes.py` | 3 | Kannada `ಟೈಮ್ ಎಷ್ಟು` routing (`GetTime`), mid-STT cancellation single `response_completed`, `cancel_turn()` `_turn_lock` release & single mic resume with sticky mute | **3 passed** |
| `backend/tests/test_phase3_memory.py` | 4 | Memory CRUD, upsert duplicate prevention, restart persistence, `memory_enabled=false` block & no-memory clarification, EN/HI/KN Unicode + live CPU `nomic-embed-text` `pgvector` semantic search & fallback, prompt-injection/credential rejection, `<untrusted_user_memory>` delimiter | **4 passed** |
| `backend/tests/test_phase3_scheduler_and_routines.py` | 3 | Controllable-clock daily/weekly `Asia/Kolkata` jobs, pause/resume/cancel, 3-day offline missed-reminder vs overdue device `skip` + bounded recurring jump, `FOR UPDATE SKIP LOCKED` concurrent claiming (1 winner of 2 workers), crash recovery (`interrupted_by_restart_uncertain_state`), unavailable device failure, custom routine CRUD, conditions, `simulator_event` trigger, partial execution, recursion guard, built-in delete protection | **3 passed** |
| `backend/tests/test_phase3_notifications.py` | 2 | DB-before-WS persistence, offline REST catch-up, unread count, `dedup_key` deduplication, live WS events + washer completion notification, quiet hours (`Asia/Kolkata`), sticky user mute preservation, retention when speaker/TTS raises `OSError` | **2 passed** |
| `backend/tests/test_phase3_e2e_demo.py` | 1 | Full 10-step E2E workflow (save bedroom AC 24°C preference -> exact recall -> set AC to preferred -> schedule off in 1m -> inspect `/schedules` & `/activities` -> advance clock -> verify AC off & notification -> restart app -> verify survival) + Hindi & Kannada commands | **1 passed** |

---

## 3. Consolidated Regression Suite (`Phase 0 – Phase 3`)

- **Ruff Lint (`.\.venv\Scripts\ruff.exe check .\backend`):** `All checks passed!` (`0` errors)
- **Diff Hygiene (`git diff --check` & `git diff main..HEAD -- frontend`):** Clean (`0` frontend modifications, `main` unchanged at `a0e3adb`)
- **Full Pytest Suite (`.\.venv\Scripts\pytest.exe -v .\backend\tests`):** **`183 passed, 0 failed, 0 skipped` in `411.41s (0:06:51)`**
- **CodeRabbit CLI Review:** `coderabbit auth status` confirmed authenticated (`Arihant-57`); remote review (`coderabbit review --agent --uncommitted --dir backend/src/aura_edge`) timed out/disconnected with `TRPCWebSocketClosedError` after 3 heartbeats, so full local security, offline URL, shell/SQL, and diff verification was completed locally (`0` external URLs, `0` arbitrary shell/eval calls).

---

## 4. Measured Phase 3 Latencies (`backend/scripts/benchmark_phase3.py`)

Measured locally on CPU Ollama `nomic-embed-text:latest` (`dim=768`, `options: {"num_gpu": 0}`) + live PostgreSQL (`models/eval_audio/phase3_benchmarks.json`):

| Operation | Measured Latency (ms) | Notes |
|---|---:|---|
| Memory Save (with live CPU `nomic-embed-text` embed + DB upsert) | **337.35 ms** | Single trial cold/warm local CPU embed + commit |
| Exact Structured Preference Lookup (`10` trials median) | **15.70 ms** | Zero LLM or embedding overhead |
| Semantic `pgvector` Cosine Search (`5` warm trials median) | **256.32 ms** | Exact cosine search on `Vector(768)` via CPU embedder |
| Schedule Create (`POST /api/v1/schedules`) | **27.23 ms** | Includes DB capability pre-validation + commit |
| Scheduler Claim + Device Execution + Notification (`tick_once`) | **83.25 ms** | `SKIP LOCKED` claim + state mutation + audit + notification |
| Simple Voice/Text Tool (`GetTime`, `10` trials median) | **9.12 ms** | Existing Phase 1 performance completely preserved |
