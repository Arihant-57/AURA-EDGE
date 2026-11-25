# AURA EDGE — Project Progress Log

## Phase 2 implemented

- Five persistent rooms and 19 simulated devices.
- Validated controls, availability, activities, estimated energy and history.
- Morning, Night, Study and Away routines.
- Simulator availability, sensor injection and forced completion controls.
- Shared REST and EN/HI/KN voice dispatcher with ambiguity clarification.

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
- **Phase 2**: Stateful smart-home simulator, device registry, UI controls, routines, optional LAN ESP32 MQTT adapter.
- **Phase 3**: PostgreSQL preferences/history, local RAG, persistent scheduler, notification bus.
- **Phase 4**: Personal assistant tools (tasks, calendar, briefings, groceries, recipes, expenses, cached parcels, allowlisted desktop/files).
- **Phase 5**: Full dashboard, Dynamic Island, live WebSocket updates, optional phone LAN UI.
- **Phase 6**: Optional opt-in read-only external sync & service navigation.
- **Phase 7**: Full regression, security/privacy audit, offline/LAN demonstration, CPU vs GPU benchmarks.

---

## Phase 1 — Offline Multilingual Voice Intelligence (Implemented on `feat/backend-intelligence`)

**Status:** Verified, pushed to `origin/feat/backend-intelligence` (**`131 passed, 0 failed, 0 skipped`** in `37.22s` across Phase 0 + Phase 1 suites with live PostgreSQL). Frontend (`frontend/`) and `main` remain untouched.

### Verified Deliverables
- [x] **Phase 1A Model Preparation & Feasibility Gate**: Offline model downloader (`backend/scripts/prepare_models.py`) + verification harness (`verify_phase1a_models.py`) validated `faster-whisper-base` & `small` (CPU `int8` & Windows CUDA `int8_float16` via retained `torch\lib` DLL handles), `indic-conformer-kn` (`NiranjanYadavIN/indicconformer-sherpa-onnx` CPU INT8 specialist), `silero_vad.onnx`, `sherpa-onnx 1.13.8` Piper (`en_US-lessac-medium`, `hi_IN-rohan-medium`), MMS-TTS (`vits-mms-kan` `num_threads=8`), Windows SAPI5 fallback, missing-model failure, and Ollama `qwen3.5:4b`.
- [x] **Phase 1B Audio & VAD Engine**: Implemented `AudioCapture` (`sounddevice` 16 kHz mono, 3s pre-roll ring buffer, zero-inference callback, device selection, self-trigger mute gating) and CPU `SileroVadDetector` (adaptive noise floor, initial no-speech timeout, trailing silence). Live Realtek mic capture verified both standalone (`1536` samples) and through `POST /api/v1/voice/ptt/{start,stop}`.
- [x] **Phase 1C Multilingual Intelligence Pipeline**: Built Unicode-preserving normalizer (Devanagari `\u0900-\u097F`, Kannada `\u0C80-\u0CFF`, context-guarded Romanized Indic numbers, ASR phonetic cleanup), Tier-0 Hassil + keyword router + compound validator (`96/96` utterances, `0.41 ms` median), policy-enforced `OllamaClient` (`qwen3.5:4b`), strict typed schema guard, clarification, and refusal.
- [x] **Phase 1D PostgreSQL-Backed Local Tools**: Implemented `DateTimeTool` (clean HI/KN spoken time strings without ASCII timezone suffixes) and PostgreSQL-backed `TimerManager` (`0002_phase1_voice_timers` Alembic migration with reversible downgrade verified) supporting named timers, multiple timers, pause/resume/cancel/cancel-all, restart recovery, and atomic `notified_at_utc IS NULL` duplicate-event prevention.
- [x] **Phase 1E Offline TTS & Playback**: Implemented `TTSRouter` (language-specific Piper/MMS-TTS + LRU WAV cache + Kannada Latin-char sanitization + Indic/Latin sentence splitting) and `PlaybackController` (thread-safe `sounddevice.OutputStream`, sentence prefetching, 64-sample fade-out barge-in).
- [x] **Phase 1F 10-State Assistant FSM**: Implemented `IDLE..ERROR` `AssistantStateMachine` with `turn_id` tracking, stale-event guards, and honest cancellation (`completed_actions_preserved`).
- [x] **Phase 1G CPU/GPU Placement Management**: Implemented `ComputeManager` (`cpu`, `gpu`, `auto`) with effective device reporting, explicit `fallback_reason`, shared-VRAM protection (`num_gpu=0` in CPU mode, VAD/TTS/IndicConformer pinned to CPU, `qwen3.5:9b` disabled by default).
- [x] **Turnkey Launcher, Conversations & Offline Audit**: Built `scripts/start-aura-voice.ps1` + `backend/scripts/voice_cli.py`; verified `15/15` EN/HI/KN multi-turn voice conversations (`verify_voice_conversations.py`), process-level `0`-external-socket offline audit (`verify_offline.py`), 3-trial CPU vs GPU benchmarks (`phase1_benchmarks.json`), and all handover/API docs.

### Known Limitations (Phase 1)
- Physical host Wi-Fi/network interface was intentionally kept active during the automated session so agent execution was not interrupted; user can run `.\scripts\start-aura-voice.ps1` under Airplane Mode post-session to physically double-check offline operation.
- Natural in-room microphone testing with live speakers in EN/HI/KN is enabled via `.\scripts\start-aura-voice.ps1` and should be spot-checked interactively by the user.
- Smart-home device simulator and external connectors remain deferred to Phase 2+.

---

## Phase 1.5, Phase 2 & Real-World Voice Bug Fix (`feat/backend-intelligence`)

**Status:** Verified with live PostgreSQL (**`146 passed, 0 failed, 0 skipped`** in `127.53s`; `ruff check .\backend` clean). `frontend/` and `main` remain untouched.

### Verified Deliverables
- [x] **Phase 1.5 Natural Conversational Streaming & Session Memory**: Bounded 12-turn `ConversationSession`, streaming `OllamaClient` (`keep_alive="15m"`), sentence-level `SentenceStreamBuffer`, live `transcript_ready`/`response_text_delta`/`response_completed` WS events, and continuous/PTT controls.
- [x] **Phase 2 Persistent Smart-Home Simulator (`d56a0ac`)**: 19 PostgreSQL-backed virtual home devices across 5 rooms, 4 routines (`morning`, `night`, `study`, `away`), capability validation, state audit history, estimated energy metrics, and multilingual voice control (`DeviceControl`).
- [x] **Real-World Voice, Playback, Language, CLI & Concurrency Bug Fix**:
  - Exclusive `AudioPlayback` owner lock + generation token + callback-driven `_BoundedPcmBuffer` + DC/anti-clipping/fade/device-rate resampling (`prepare_pcm_for_playback`) + separated `audio_startup_ms`.
  - Strict half-duplex `suspend_for_playback()` / `resume_after_playback()` + configurable `audio_post_playback_cooldown_sec` (`0.65s`) preventing speaker-to-mic feedback loops.
  - Explicit EN/HI/KN language enforcement across `routes_voice.py`, `normalizer.py` (fixed Romanized `"do"` -> `"2"` corruption in `hi`/`kn` modes), `stt.py` (no unsupported `'tl'` leakage), `validate_response_language()`, and deterministic `Identity` / `SupportedLanguages` templates.
  - Dynamic `ToolRegistry.describe_capabilities()` replacing the obsolete smart-home disclaimer in `conversation.py`.
  - Local slash-command & punctuation validation + `TurnOutputDeduplicator` in `voice_cli.py`, and `_turn_lock` serialization in `VoiceOrchestrator`.
  - Live Ollama EN/HI/KN comparison across `qwen3.5:4b`, `nemotron-3-nano:4b`, and `qwen3.5:9b` documented in [VOICE_BUGFIX_REPORT.md](file:///C:/Users/ariha/Desktop/AURA-EDGE/docs/VOICE_BUGFIX_REPORT.md) alongside a Human Microphone Acceptance Test checklist.
