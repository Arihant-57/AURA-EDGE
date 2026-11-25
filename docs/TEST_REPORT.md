# AURA EDGE — Phase 0 Test & Verification Report

**Date:** 2026-11-25
**Host Hardware & Toolchain (Observed):**
- **OS:** Microsoft Windows 11 Home Single Language (`10.0.26300`, 64-bit)
- **CPU:** 13th Gen Intel(R) Core(TM) i7-13650HX (14 cores / 20 logical processors)
- **RAM:** 15.78 GB
- **GPU:** NVIDIA GeForce RTX 3050 6GB Laptop GPU (`6144` MiB VRAM via `nvidia-smi`, Driver `621.24`)
- **Toolchains:** Python `3.13.2` (plus `3.12`), `uv`, Node.js `v26.13.1`, npm `11.20.0`, Git `2.55.0.windows.5`, Docker Engine `29.8.0`, Ollama `0.40.16` (stopped during checks)

## 1. Backend Lint & Automated Tests (`ruff` + `pytest`)

Commands executed from `C:\Users\ariha\Desktop\AURA-EDGE`:

```powershell
$env:PYTHONPATH = "C:\Users\ariha\Desktop\AURA-EDGE\backend\src"
$env:AURA_EDGE_REQUIRE_LIVE_DB = "1"
.\.venv\Scripts\ruff.exe check .\backend
.\.venv\Scripts\pytest.exe -v .\backend\tests
```

Observed results:
- `ruff check`: **All checks passed!** (0 errors)
- `pytest`: **14 passed, 0 skipped, 0 failed** in `1.10s` (1 deprecation warning from FastAPI `TestClient` regarding `httpx2`):
  - `test_config_and_db_guard.py`: 5 passed (strict_offline default, SQLite rejection, non-loopback strict bind host rejection, `lan_only` non-loopback bind host permission, CSV env list parsing)
  - `test_db_live_integration.py`: 1 passed (real PostgreSQL + pgvector write/read/L2-distance verification)
  - `test_health_api.py`: 3 passed (`/api/v1/health`, `/api/v1/policy`, `/api/v1/policy/check`, `/api/v1/readiness` 200 ready and 503 unavailable states)
  - `test_network_policy.py`: 5 passed (strict_offline loopback-only, sync & async `PolicyEnforcingTransport` zero-I/O enforcement, `lan_only` RFC1918 & metadata blocking, `online_opt_in` host allowlist)

## 2. PostgreSQL + pgvector Migration & Restart Persistence Smoke Test

Commands executed against local Docker container `aura-edge-postgres` (`pgvector/pgvector:pg16`, bound to `127.0.0.1:5432`):

```powershell
docker compose up -d postgres
.\.venv\Scripts\alembic.exe -c backend\alembic.ini upgrade head
.\.venv\Scripts\python.exe backend\scripts\db_smoke_test.py --phase write --token phase0-restart-check
docker compose restart postgres
.\.venv\Scripts\python.exe backend\scripts\db_smoke_test.py --phase verify --token phase0-restart-check
.\.venv\Scripts\alembic.exe -c backend\alembic.ini downgrade base
.\.venv\Scripts\alembic.exe -c backend\alembic.ini upgrade head
```

Observed results:
- `upgrade head`: applied `0001_phase0_foundation` (`CREATE EXTENSION IF NOT EXISTS vector`, `system_settings`, `connectivity_audit_events`, `phase0_vector_smoke`).
- `--phase write`: committed `SystemSetting` (`phase0.smoke.phase0-restart-check`) and 3-D vector (`smoke-phase0-restart-check`) with `pgvector_version: "0.8.7"`.
- `docker compose restart postgres`: container restarted cleanly and returned `healthy` after 6s.
- `--phase verify`: verified persisted JSON payload, `l2_distance: 0.0`, and cleaned up smoke records (`status: "verified"`).
- `downgrade base` + `upgrade head`: cleanly reversed and re-applied `0001_phase0_foundation`.

## 3. Live FastAPI HTTP Smoke Test

Started `uvicorn aura_edge.main:app --host 127.0.0.1 --port 8000` with live `aura-edge-postgres` running:
- `GET http://127.0.0.1:8000/api/v1/health` -> HTTP `200`, `status: "ok"`, `connectivity_mode: "strict_offline"`, `bind_host: "127.0.0.1"`.
- `GET http://127.0.0.1:8000/api/v1/readiness` -> HTTP `200`, `ready: true`, `postgres_version: "PostgreSQL 16.16 (Debian 16.16-1.pgdg12+1)..."`, `pgvector_installed: true`, `pgvector_version: "0.8.7"`, `alembic_revision: "0001_phase0_foundation"`, GPU `NVIDIA GeForce RTX 3050 6GB Laptop GPU` (`6144` MiB VRAM), Ollama `reachable: false` (server stopped).
- `POST http://127.0.0.1:8000/api/v1/policy/check` with `{"url":"https://example.com"}` -> `allowed: false` (`strict_offline permits loopback targets only; non-loopback host denied.`).
- `POST http://127.0.0.1:8000/api/v1/policy/check` with `{"url":"http://127.0.0.1:11434/api/tags"}` -> `allowed: true`.

## 4. Frontend Typecheck, Tests & Offline Asset Verification

Commands executed inside `C:\Users\ariha\Desktop\AURA-EDGE\frontend`:

```powershell
npx tsc --noEmit
npm test -- --run
npm run build
```

Observed results:
- `npx tsc --noEmit`: exit code `0` (0 errors).
- `npm test -- --run` (Vitest `v3.2.7`): **Test Files 1 passed (1), Tests 3 passed (3)** (`src/App.test.tsx`: ready metrics, HTTP 503 DB failure honesty, network unreachable alert).
- `npm run build`: built `dist/index.html` (`0.48 kB`), `dist/assets/index-D_m0w1Kw.css` (`15.95 kB`), `dist/assets/index-BRSYkTM-.js` (`154.83 kB`).
- `dist/index.html` contains only local `/assets/...` bundle references (zero external CSS/JS/font links).

## 5. Original AURA V4 Untouched Check

- `git -C C:\Users\ariha\local-ai-lab\phase4-agent status` confirmed unchanged relative to session start (zero edits inside `C:\Users\ariha\local-ai-lab\phase4-agent`).

---

## 6. Phase 1 Verification Summary (`feat/backend-intelligence`)

### 6.1 Lint & Automated Test Suite (`ruff` + `pytest`)

Executed from `C:\Users\ariha\Desktop\AURA-EDGE` with `AURA_EDGE_REQUIRE_LIVE_DB=1` against live PostgreSQL `16.16` (`alembic` head `0002_phase1_voice_timers`):

- `ruff check .\backend`: **All checks passed!** (`0` errors)
- `pytest -v .\backend\tests`: **`128 passed, 0 failed, 0 skipped` in `25.77s`**:
  - Phase 0 regressions (`14` passed): `test_config_and_db_guard.py` (`5`), `test_db_live_integration.py` (`1`, updated for `0002_phase1_voice_timers`), `test_health_api.py` (`3`), `test_network_policy.py` (`5`).
  - Phase 1 suites (`114` passed):
    - `test_phase1_multilingual_router.py` (`99`): 96 EN/HI/KN utterances (`100%` intent & language match), Unicode preservation, compound commands, clarification, refusal.
    - `test_phase1_audio_and_vad.py` (`4`): Silero VAD speech/silence/noise, ring buffer capture, missing-mic error, self-trigger mute gating.
    - `test_phase1_tts_and_playback.py` (`3`): EN/HI/KN offline synthesis, LRU cache hits, sentence splitting & barge-in cancellation.
    - `test_phase1_stt_and_compute.py` (`3`): CPU vs CUDA Whisper `small` transcription, `ModelNotFoundError`, `ComputeManager` CPU/GPU/fallback reporting.
    - `test_phase1_state_machine.py` (`2`): 10-state FSM transitions, stale-turn guard, completed-action preservation on cancel.
    - `test_phase1_timers_live_db.py` (`1`): real PostgreSQL timer create/list/pause/resume/restart recovery + atomic duplicate-notification prevention.
    - `test_phase1_api_and_ws.py` (`2`): Phase 1 REST routes + `/api/v1/ws/live` WebSocket state/timer events.

### 6.2 Live Alembic Migration, Uvicorn & Hardware Checks

- **Alembic Reversibility:** Verified `upgrade head` (`0002_phase1_voice_timers`) -> `downgrade 0001_phase0_foundation` -> `upgrade head` cleanly against `aura-edge-postgres`.
- **Live Hardware Capture:** `AudioCapture.start_ptt_capture(open_hardware_stream=True)` captured `1536` samples (`0.096s`) from host `Microphone Array (Realtek(R) Audio)` and stopped cleanly (`reason=manual_stop`).
- **Live Uvicorn HTTP Endpoints (`127.0.0.1:8000`):** Verified `/api/v1/health`, `/api/v1/readiness` (`ready=True db=0002_phase1_voice_timers ollama=True`), `/api/v1/voice/status` (`state=IDLE stt_ready=True vad_ready=True mic=True spk=True`), `/api/v1/intent/process` across EN (`GetTime`), HI (`SetTimer`), KN (`Unsupported`), `/api/v1/timers` cleanup, and `/api/v1/voice/synthesize` returning HTTP `200` RIFF WAV (`47868` bytes via `sherpa_onnx` / `vits-mms-kan` in `233.03 ms`).
- **Strict Offline Network Isolation Check:** Confirmed `HF_HUB_OFFLINE=1` & `TRANSFORMERS_OFFLINE=1`, `0` external TCP/UDP sockets opened by Python during offline Kannada routing & TTS synthesis, and zero-I/O rejection of `huggingface.co`, `api.openai.com`, and `169.254.169.254` via `build_async_http_client(enforcer)`.
- **Benchmark & Multilingual Reports:** Full raw numbers documented in [VOICE_MODEL_EVALUATION.md](file:///C:/Users/ariha/Desktop/AURA-EDGE/docs/VOICE_MODEL_EVALUATION.md), [VOICE_BENCHMARKS.md](file:///C:/Users/ariha/Desktop/AURA-EDGE/docs/VOICE_BENCHMARKS.md), and [MULTILINGUAL_TEST_REPORT.md](file:///C:/Users/ariha/Desktop/AURA-EDGE/docs/MULTILINGUAL_TEST_REPORT.md).
