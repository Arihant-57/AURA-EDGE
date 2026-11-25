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
| **Audio & Speech Input** | `speech/audio_capture.py`, `speech/vad.py`, `speech/stt.py` | `sounddevice` 16 kHz capture, 3s pre-roll ring buffer, zero-inference callback, Silero VAD v5 ONNX on CPU, adaptive noise floor, self-trigger mute gating, offline `WhisperTranscriber` (`local_files_only=True`, domain prompts) + `indic-conformer-kn` Kannada specialist (`170–671 ms`). |
| **Voice Synthesis & Playback** | `speech/tts_router.py`, `speech/playback.py`, `speech/tts_backends/{base,sherpa_onnx_backend,onecore_backend}.py` | Language-aware `TTSRouter` (Piper EN, Piper HI, MMS-TTS KN `num_threads=8` w/ ASCII guard + SAPI5 fallback), `OrderedDict` LRU cache keyed by `(language, backend, model, voice, speed, normalized_text)`, `।॥.!?` sentence splitter, thread-safe `PlaybackController` with prefetch & fade-out cancellation. |
| **Multilingual Intelligence** | `intelligence/{schemas,response_catalog,normalizer,fast_router,ollama_client,planner}.py`, `intelligence/intents/{en,hi,kn}/phase1.yaml` | Unicode-preserving Devanagari (`\u0900-\u097F`) & Kannada (`\u0C80-\u0CFF`) normalization, deterministic Hassil + keyword routing for 10 intents + compound splitting, loopback policy-wrapped `OllamaClient` (`qwen3.5:4b`), strict typed validation & refusal. |
| **Local Tools & 10-State FSM** | `execution/state_machine.py`, `execution/tool_registry.py`, `execution/tools/{datetime_tool,timer_tool}.py` | `IDLE..ERROR` FSM with `turn_id` + stale-event protection, localized EN/HI/KN `DateTimeTool`, PostgreSQL-persisted `TimerManager`, `ToolRegistry` reserving Phase 2 device hook. |
| **Orchestrator & API** | `application/voice_orchestrator.py`, `api/{routes_voice,routes_timers,ws_events}.py`, `main.py` | Full turn orchestration, REST endpoints (`/voice/*`, `/intent/process`, `/timers*`), and live WebSocket event hub (`/api/v1/ws/live`). |
| **Launch & CLI Tools** | `scripts/start-aura-voice.ps1`, `scripts/start-dev.ps1`, `scripts/stop-dev.ps1`, `backend/scripts/voice_cli.py` | Turnkey launcher starting Docker/PostgreSQL, Alembic `0002`, local Ollama, offline backend (`HF_HUB_OFFLINE=1`), and interactive Push-to-Talk microphone CLI. |
| **Model & Eval Scripts** | `backend/scripts/prepare_models.py`, `verify_phase1a_models.py`, `verify_voice_conversations.py`, `verify_offline.py`, `benchmark_phase1.py` | Separate offline model prep (`indic-conformer-kn`, `faster-whisper`, `silero_vad`, `sherpa-onnx` TTS), conversation checks, process-socket offline verification, and CPU/GPU benchmarking. |

---

## 2. How to Test With Your Microphone (Single Command)

From `C:\Users\ariha\Desktop\AURA-EDGE` in PowerShell:

```powershell
.\scripts\start-aura-voice.ps1
```

Optional flags: `-Language en|hi|kn|auto`, `-ComputeMode auto|cpu|gpu`, `-NoSpeak`, `-NoCli`.

Inside the CLI:
- Press **`ENTER`** -> speak into your microphone -> press **`ENTER`** again to stop and process your command.
- Or type any EN/HI/KN command directly, or use `/lang kn`, `/mode cpu`, `/timers`, `/cancel`, `/status`, `/quit`.
- To verify **physical airplane-mode / Wi-Fi disconnect**: disable Wi-Fi (or toggle Airplane Mode) after closing external tools, then run `.\scripts\start-aura-voice.ps1` and test voice commands or run `.venv\Scripts\python.exe backend\scripts\verify_offline.py --require-live-backend`. *(During automated agent verification, the host NIC was intentionally kept connected with approval policy intact; process-socket auditing confirmed `0` external connections.)*

---

## 3. Known Limitations & Phase 2 Readiness

- **Live Human Speech vs Synthetic Evaluation:** `indic-conformer-kn` + `_normalize_kannada_asr_variants` achieved `5/5` on EN/HI/KN synthesized conversation turns (`170–420 ms` KN STT), and host Realtek hardware mic capture was verified via live PTT; natural human accent variation in real rooms should still be spot-checked interactively via `.\scripts\start-aura-voice.ps1`.
- **No Smart-Home Simulator Yet:** Phase 2 smart-home devices, routines, and MQTT adapters were intentionally excluded per strict Phase 1 scope; `ToolRegistry` provides typed extension hooks ready for Phase 2.
- **Frontend Coordination:** See [docs/FRONTEND_INTEGRATION_HANDOVER.md](file:///C:/Users/ariha/Desktop/AURA-EDGE/docs/FRONTEND_INTEGRATION_HANDOVER.md) and [docs/API_CONTRACT.md](file:///C:/Users/ariha/Desktop/AURA-EDGE/docs/API_CONTRACT.md) for all implemented endpoints, WebSocket payloads, and git pull instructions for `feat/frontend-experience`.
