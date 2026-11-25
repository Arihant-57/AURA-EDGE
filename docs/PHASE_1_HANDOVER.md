# AURA EDGE — Phase 1 Handover Document

**Branch:** `feat/backend-intelligence`  
**Phase Status:** Complete & Verified (Phase 0 regressions + Phase 1 voice/intelligence/timer deliverables).  
**Untouched Boundaries Verified:** `main` (`a0e3adb`), `frontend/`, `C:\Users\ariha\local-ai-lab\phase4-agent` (AURA V4), and `C:\Users\ariha\Desktop\AURA-EDGE-RESEARCH`.

---

## 1. Delivered Modules (`backend/src/aura_edge/`)

| Layer | File(s) | Responsibility |
|---|---|---|
| **Config & Compute** | `config.py`, `infrastructure/compute.py` | Phase 1 settings (`models_dir`, `stt_model_size`, `ollama_model=qwen3.5:4b`, `ollama_num_ctx=4096`), `apply_offline_env_guards()`, Windows CUDA DLL handle cache, `ComputeManager` (`cpu`, `gpu`, `auto` with honest fallback reason). |
| **Persistence (PostgreSQL + Alembic)** | `data/models.py`, `data/repositories/timer_repo.py`, `alembic/versions/0002_phase1_voice_timers.py` | `timers` & `voice_turn_metrics` tables; reversible Alembic `0002_phase1_voice_timers`; atomic `mark_completed_once()` preventing duplicate timer completion events across restarts. |
| **Audio & Speech Input** | `speech/audio_capture.py`, `speech/vad.py`, `speech/stt.py` | `sounddevice` 16 kHz capture, 3s pre-roll ring buffer, zero-inference callback, Silero VAD v5 ONNX on CPU, adaptive noise floor, self-trigger mute gating, offline `WhisperTranscriber` (`local_files_only=True`, anti-loop decoding). |
| **Voice Synthesis & Playback** | `speech/tts_router.py`, `speech/playback.py`, `speech/tts_backends/{base,sherpa_onnx_backend,onecore_backend}.py` | Language-aware `TTSRouter` (Piper EN, Piper HI, MMS-TTS KN + SAPI5 fallback), `OrderedDict` LRU cache keyed by `(language, backend, model, voice, speed, normalized_text)`, `।॥.!?` sentence splitter, thread-safe `PlaybackController` with prefetch & fade-out cancellation. |
| **Multilingual Intelligence** | `intelligence/{schemas,response_catalog,normalizer,fast_router,ollama_client,planner}.py`, `intelligence/intents/{en,hi,kn}/phase1.yaml` | Unicode-preserving Devanagari (`\u0900-\u097F`) & Kannada (`\u0C80-\u0CFF`) normalization, deterministic Hassil + keyword routing for 10 intents + compound splitting, loopback policy-wrapped `OllamaClient` (`qwen3.5:4b`), strict typed validation & refusal. |
| **Local Tools & 10-State FSM** | `execution/state_machine.py`, `execution/tool_registry.py`, `execution/tools/{datetime_tool,timer_tool}.py` | `IDLE..ERROR` FSM with `turn_id` + stale-event protection, localized EN/HI/KN `DateTimeTool`, PostgreSQL-persisted `TimerManager`, `ToolRegistry` reserving Phase 2 device hook. |
| **Orchestrator & API** | `application/voice_orchestrator.py`, `api/{routes_voice,routes_timers,ws_events}.py`, `main.py` | Full turn orchestration, REST endpoints (`/voice/*`, `/intent/process`, `/timers*`), and live WebSocket event hub (`/api/v1/ws/live`). |
| **Model & Eval Scripts** | `backend/scripts/prepare_models.py`, `verify_phase1a_models.py`, `benchmark_phase1.py` | Separate offline model prep vs verification & reproducible CPU/GPU benchmarking. |

---

## 2. How to Run Offline (Developer Checklist)

1. Activate `.venv` and set `PYTHONPATH=C:\Users\ariha\Desktop\AURA-EDGE\backend\src`.
2. Ensure local model artifacts exist under `models/` (gitignored; run `python backend/scripts/prepare_models.py` while online **once** prior to offline demo).
3. Start Docker container `postgres` (`docker compose up -d postgres`) and run `alembic -c backend/alembic.ini upgrade head` (`0002_phase1_voice_timers`).
4. Optional for LLM fallback: ensure `ollama serve` is running locally on `127.0.0.1:11434` with `qwen3.5:4b` pulled.
5. Start backend: `.venv\Scripts\uvicorn.exe aura_edge.main:app --host 127.0.0.1 --port 8000` (with `--app-dir backend/src` or `PYTHONPATH`).

---

## 3. Known Limitations & Phase 2 Readiness

- **Kannada Spoken STT Accuracy:** `faster-whisper-small` exhibits weak word recognition on synthetic `vits-mms-kan` audio (~3.7s GPU / ~20.4s CPU) even with `repetition_penalty=1.2`; human Kannada speech recognition and/or fine-tuned Whisper Indic LoRA evaluation are recommended next if spoken Kannada voice accuracy is prioritized.
- **No Smart-Home Simulator Yet:** Phase 2 smart-home devices, routines, and MQTT adapters were intentionally excluded per strict Phase 1 scope; `ToolRegistry` provides typed extension hooks ready for Phase 2.
- **Frontend Coordination:** See [docs/FRONTEND_INTEGRATION_HANDOVER.md](file:///C:/Users/ariha/Desktop/AURA-EDGE/docs/FRONTEND_INTEGRATION_HANDOVER.md) and [docs/API_CONTRACT.md](file:///C:/Users/ariha/Desktop/AURA-EDGE/docs/API_CONTRACT.md) for all implemented endpoints and WebSocket payloads.
