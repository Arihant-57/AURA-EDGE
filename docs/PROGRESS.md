# AURA EDGE — Project Progress Log

## Phase 5 — Windows desktop experience

The Phase 5 branch adds the native companion, embedded React dashboard and integration verification. See the [handover](PHASE_5_HANDOVER.md) and [test report](PHASE_5_TEST_REPORT.md).

## Part A & Phase 4 — Shared Multilingual Grounding & Personal Secretary (`feat/backend-intelligence`)

**Status:** Core Automated Verification **COMPLETE** (`ruff check .\backend` 0 errors; **`191 passed, 0 failed, 0 skipped`** in `426.72s` against live PostgreSQL `0005_phase4_personal_assistant`). `frontend/` and `main` remain untouched.

### Verified Part A & Phase 4 Deliverables
- [x] **Part A — Code-Switched Multilingual Intelligence & Grounding**: Expanded EN/HI/KN/Hinglish/Kanglish normalization (`normalizer.py`), preserved clean mixed-script KN+EN Whisper transcripts (`stt.py`), prioritized Kannada script counts over stray Devanagari, added SOV memory deletion precedence and code-switched delay/reminder parsing (`fast_router.py`), added verified-entity anaphora + post-delete memory scrubbing + `validate_no_unverified_action_claim()` (`conversation.py`, `voice_orchestrator.py`), and enforced operational grounding + boolean `power` coercion (`planner.py`).
- [x] **Database Migration `0005_phase4_personal_assistant`**: Added `tasks`, `calendar_events`, `grocery_items`, `shopping_list_items`, `local_recipes`, `expense_imports`, `expense_transactions` (`BIGINT amount_paise`), and `parcels`; verified reversible `upgrade -> downgrade 0004 -> upgrade` round-trip on disposable `aura_edge_mig_test`.
- [x] **Phase 4 Personal Secretary Modules (`application/personal/*`, `api/routes_personal.py`, `ToolRegistry`)**: `TaskService`, `CalendarService` (conflict detection + daily/weekly recurrence + `SchedulerService` reminders), `BriefingService` (strictly grounded PostgreSQL daily summaries), `GroceryService` (inventory + shopping list + 8 seeded offline recipes), `ExpenseService` (exact integer paise SQL `SUM()` + SHA-256 CSV dedup), `ParcelService` (honest offline provenance + delivery reminder), REST routes, WS events (`task_updated`, `calendar_updated`, `grocery_updated`, `expense_updated`, `parcel_updated`), and EN/HI/KN voice intents.
- [ ] **Deferred Optional Scope**: Local document `pgvector` RAG & allowlisted desktop/file launcher omitted before competition freeze to prioritize reliability of core personal secretary & voice grounding.

---

## Phase 3 — Persistent Memory, Scheduling, Routines & Proactive Notifications (`feat/backend-intelligence`)

**Status:** Automated & empirical verification **COMPLETE** (`ruff check .\backend` 0 errors; **`183 passed, 0 failed, 0 skipped`** in `411.41s` against live PostgreSQL `0004_phase3_memory_events`). `frontend/` and `main` remain untouched.

### Verified Phase 3 Deliverables
- [x] **Remaining Voice Defects (3/3 Fixed)**: Preserved Kannada virama `\u0CCD` in `normalizer.py` (`"ಟೈಮ್ ಎಷ್ಟು"` -> `GetTime`); guaranteed single `response_completed` on mid-STT & `StaleTurnError` cancellations; serialized `cancel_turn()` `_turn_lock` cleanup and single `resume_after_playback()`.
- [x] **Database Migration `0004_phase3_memory_events`**: Added `memory_settings`, `memory_items` (`Vector(768)` + partial unique canonical index + `'simple'` GIN FTS index), opt-in `conversation_history_items`, `scheduled_jobs`, `job_executions` (`UNIQUE(job_id, scheduled_occurrence_utc)`), `notifications` (`dedup_key`), `notification_preferences`, and extended `smart_home_routines`; verified reversible upgrade/downgrade round-trip on disposable DB.
- [x] **Personal Memory & Local CPU Embeddings**: `LocalEmbedder` (`nomic-embed-text:latest`, `dim=768`, `num_gpu=0`) + `MemoryService` with prompt-injection & credential filter, structured appliance preference resolution/updates, runtime `memory_enabled` toggle, untrusted `<untrusted_user_memory>` LLM context, and 3-tier exact/keyword/`pgvector` retrieval.
- [x] **Persistent Scheduler & Extended Routines**: `SchedulerService` (`SELECT ... FOR UPDATE SKIP LOCKED`, `Asia/Kolkata` once/daily/weekly recurrence, crash-interrupted & missed-job policies) + `SmartHomeService` custom/conditional/`simulator_event` routines, partial failure reporting, and recursion guards.
- [x] **Proactive Notifications, AI Tools & REST/WS APIs**: `NotificationService` (DB-before-WS, quiet hours, bounded audio queue shared with timers & simulator completions), EN/HI/KN fast routing + Qwen planner + `ToolRegistry` execution, REST routes (`/api/v1/memory`, `/reminders`, `/schedules`, `/notifications`, `/smart-home/routines`), and real 10-step E2E scenario + benchmarks (`backend/scripts/benchmark_phase3.py`).

---

## Voice Pipeline Stabilization Milestone (`feat/backend-intelligence`, R1–R9)

**Status:** Automated & empirical verification **COMPLETE** (`ruff check .\backend` 0 errors; **`170 passed, 0 failed, 0 skipped`** in `281.65s` against live PostgreSQL). `frontend/` and `main` remain untouched.  
**Next Gate:** **Human Microphone Acceptance Test** (3–5 minutes in [`VOICE_BUGFIX_REPORT.md`](./VOICE_BUGFIX_REPORT.md)) required before authorizing **Phase 3**.

### Verified Stabilization Deliverables
- [x] **M1 — Speech Recognition (`R2`, `R3`, `R4`)**: Decoupled `session_language` (preferred reply language) from `stt_language="auto"` and `stt_recognition_mode="multilingual"|"strict"`; eliminated fabricated `language_probability=1.0`; added selective `indic-conformer-kn` fallback + CTC script validator + quiet-mic AGC + multi-frame Silero VAD onset (`onset_frames=2`) + bounded noise floor + fixed `_preroll` ring buffer + stream reuse across `no_speech_timeout` + R4 silence/noise/unclear-speech quality gate preserving short commands.
- [x] **M2 — Speech Generation & Audio Stability (`R6`, `R8`)**: Built shared `normalize_kannada_for_tts()` transliterating English device names, timer labels, `AURA EDGE`, `AM`/`PM`, decimals, dates, `%`, `°C` (no more Latin stripping); stripped ZWNJ/ZWJ before `vits-mms-kan` token lookup; refined Kannada templates in `response_catalog.py`; rewrote `_BoundedPcmBuffer` as lock-free SPSC ring buffer capturing `first_non_silent_mono` inside callback; added concurrent 2-sentence prefetch and barge-in fade-out; documented `kn_piper` empty ONNX metadata limitation.
- [x] **M3 — Intelligence, Tools, Orchestrator, API & CLI (`R5`, `R7`, `R8`)**: Expanded EN/HI/KN/Hinglish/Kanglish clock, smart-home status, climate, and routine routing; fixed Kannada `"ಪಠಣ"` typo and Indic boundary regexes; added `DeviceControl` planner validation and localized `SmartHomeService` status replies; unified single post-playback `response_completed` WS broadcast with `outcome` and `action_executed`; fixed `start_ptt()`/`cancel_turn()` `_turn_lock` serialization and Ollama mid-stream `cancel_event` racing; validated every streamed sentence & tail chunk before TTS queuing; instrumented 10 monotonic `stage_timestamps` and derived latencies.
- [x] **M4 — Verification, Benchmarks & Docs (`R1`, `R3`, `R7`, `R8`, `R9`)**: Executed CodeRabbit baseline & post-edit CLI checks + full local diff review; measured real WER/CER across `faster-whisper-small`, `indic-conformer-kn`, and decoupled fallback on synthetic WAVs; measured sequential Ollama (`qwen3.5:4b`, `qwen3.5:9b`, `nemotron-3-nano:4b`) GPU/RAM/VRAM placement and throughput; measured cold & warm `VoiceOrchestrator` monotonic latencies; updated all 7 `docs/*.md` files.

---

## Phase 0 — Foundation (Authorized)

- [x] Standalone repo (`AURA-EDGE`, `AURA V4` untouched), `.gitignore`, layered modular monolith (`config.py`, `infrastructure/`, `data/`, `api/`), strict offline `PolicyEnforcingTransport`, PostgreSQL 16 + pgvector (`0001_phase0_foundation`), minimal Vite + React 18 UI shell (`frontend/`).

## Phase 1 — Offline Multilingual Voice Intelligence (`feat/backend-intelligence`)

- [x] Offline model preparation (`faster-whisper-base/small`, `indic-conformer-kn`, `silero_vad.onnx`, `sherpa-onnx` EN/HI/KN TTS, Ollama `qwen3.5:4b`), `AudioCapture` + `SileroVadDetector`, Hassil EN/HI/KN router (`96/96`), PostgreSQL persistent timers (`0002_phase1_voice_timers`), 10-state FSM, `ComputeManager`, `voice_cli.py`, and turnkey launcher `scripts/start-aura-voice.ps1`.

## Phase 1.5 & Phase 2 — Conversational Streaming & Persistent Smart-Home Simulator

- [x] Bounded 12-turn `ConversationSession`, streaming `OllamaClient` (`keep_alive="15m"`), `SentenceStreamBuffer`, continuous VAD mode, and `WS /api/v1/ws/live` streaming deltas.
- [x] Persistent Phase 2 smart-home simulator (`5` rooms, `19` seeded devices, `4` routines `morning`/`night`/`study`/`away`, capability validation, audit history, estimated energy metrics, and shared EN/HI/KN voice dispatcher).

---

## Next Gate & Deferred Phases

- **Live Microphone Verification:** Complete the physical microphone acceptance check in [`docs/VOICE_BUGFIX_REPORT.md`](./VOICE_BUGFIX_REPORT.md) + confirm Synapse 2.0 competition reuse/model-prep rules before final submission.
- **Phase 4+ (Deferred — Not Started):** Tasks/calendar/briefings, groceries/recipes, expenses/parcels, local PDF RAG, desktop/file launcher tools, and React dashboard implementation on `feat/frontend-experience`.
