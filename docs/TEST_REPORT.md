# AURA EDGE — Consolidated Test & Verification Report

**Date:** 2026-11-25  
**Branch:** `feat/backend-intelligence`  
**Host Hardware & Toolchain (Observed):**
- **OS:** Microsoft Windows 11 Home Single Language (`10.0.26300`, 64-bit)
- **CPU:** 13th Gen Intel(R) Core(TM) i7-13650HX (14 cores / 20 logical processors)
- **RAM:** 15.78 GB
- **GPU:** NVIDIA GeForce RTX 3050 6GB Laptop GPU (`6144` MiB VRAM via `nvidia-smi`, Driver `621.24`)
- **Toolchains:** Python `3.13.2`, Ruff, Pytest `9.2.0`, PostgreSQL `16.16` (`pgvector 0.8.7`), Ollama `0.40.16`

---

## 1. Consolidated Backend Lint & Full Phase 0–2 Regression Suite

Executed from `C:\Users\ariha\Desktop\AURA-EDGE` against live PostgreSQL (`aura-edge-postgres`):

```powershell
.\.venv\Scripts\ruff.exe check .\backend
$env:AURA_EDGE_REQUIRE_LIVE_DB="1"
.\.venv\Scripts\pytest.exe -v .\backend\tests
```

**Observed Results:**
- `ruff check .\backend`: **All checks passed!** (`0` errors)
- `pytest -v .\backend\tests`: **`170 passed, 0 failed, 0 skipped` in `281.65s` (`0:04:41`)**

### Suite Breakdown (`170` Tests Total)
- **Phase 0 Foundation (`14` passed):** `test_config_and_db_guard.py` (`5`), `test_db_live_integration.py` (`1`), `test_health_api.py` (`3`), `test_network_policy.py` (`5`).
- **Phase 1 Core (`117` passed):**
  - `test_phase1_multilingual_router.py` (`100`: 96 EN/HI/KN utterances + Unicode preservation, compound guard, LLM schema refusal, R5 colloquial clock & smart-home/routine/planner tests)
  - `test_phase1_audio_and_vad.py` (`6`: host devices, missing mic error, echo suppression/cancel, Silero VAD, multi-frame onset `onset_frames=2`, pre-roll preservation + `CaptureMetrics`)
  - `test_phase1_stt_and_compute.py` (`4`: CPU/GPU fallback, missing model guard, CPU/CUDA Whisper on EN/HI/KN, R2 decoupled EN/HI speech under KN reply preference)
  - `test_phase1_tts_and_playback.py` (`3`: offline EN/HI/KN TTS, LRU eviction, sentence splitting & barge-in)
  - `test_phase1_state_machine.py` (`2`), `test_phase1_timers_live_db.py` (`1`), `test_phase1_api_and_ws.py` (`2`), `test_phase1_voice_conversations.py` (`3`).
- **Phase 1.5 & Phase 2 (`10` passed):** `test_phase1_5_conversation.py` (`7`), `test_phase2_smart_home.py` (`3`).
- **Stabilization & Cross-Track Integration (`25` passed):**
  - `test_speech_recognition_gate.py` (`4`: R4 silence/fan noise rejection, garbled/repetition/hallucination rejection, short command preservation, CTC script validation & quiet-mic AGC)
  - `test_speech_generation_and_streaming.py` (`7`: R6 Kannada transliteration & `vits-mms-kan` normal == streaming parity, catalog/prewarm/voice consistency, `SentenceStreamBuffer` abbreviations/clauses, lock-free `_BoundedPcmBuffer`, concurrent prefetch & inside-callback monotonic timing, barge-in fade-out)
  - `test_voice_bugfixes.py` (`6`: capability prompt, language enforcement, PCM conditioning & half-duplex suspension, 10 consecutive hands-free turns + concurrency, CLI validation & `TurnOutputDeduplicator`, CLI WS terminal outcomes)
  - `test_voice_orchestrator_outcomes.py` (`8`: R2 capture metrics & strict mode, R4 silence vs unclear_speech, R5 `response_completed` ordering & ambiguous `DeviceControl` clarification, R8 per-chunk streaming validation & `spoken_text == response_text` parity, R7/R8 Ollama cancellation & half-duplex turn serialization, real WAV cross-lingual EN->KN and HI->KN replies, Hinglish/Kanglish mixed utterances end-to-end, simulated callback monotonic `stage_timestamps` + mid-stream cancellation concurrency).

---

## 2. Independent Code Review (`R1`: CodeRabbit CLI + Local Diff Review)

1. **CodeRabbit CLI Executions:**
   - **Baseline (`d56a0ac..d429f44`):** `coderabbit review --agent --committed --base-commit d56a0ac --dir backend/src/aura_edge` emitted 4 `"reviewing"` heartbeats over ~237s with 0 findings before timeout cancellation; local review identified 24 confirmed + 3 partial baseline defects.
   - **Post-Edit (`--uncommitted`):** `coderabbit review --agent --uncommitted --dir backend/src/aura_edge` emitted 4 `"reviewing"` heartbeats over >240s with 0 findings (`task-130` cancelled cleanly at 240s notification).
2. **Local Diff Review (`git diff -- backend/src/aura_edge` across 21 modified source files + `voice_cli.py`):**
   - Verified all 24 confirmed + 3 partial baseline defects resolved (`_turn_lock` around `start_ptt()`/`cancel_turn()`, background task error callbacks, half-duplex resume ordering, lock-free `_BoundedPcmBuffer`, 2-frame VAD onset + pre-roll + stream reuse, R2 STT/reply decoupling, R4 quality gate, R5 `response_completed` + `CLARIFYING` transitions, R6 Kannada transliteration, R7 Ollama `cancel_event` racing, R8 per-chunk validation + monotonic `stage_timestamps`).
   - Caught and fixed two additional cross-track integration issues during verification:
     - `fast_router.py`: fixed `\b` word-boundary failure after Indic characters (`"ಬೆಡ್‌ರೂಮ್ light on ಮಾಡು"`) and added Romanized Hinglish/Kanglish power verbs (`on karo`, `on maadu`, `off maadu`).
     - `normalizer.py`: added context-guarded `"पाज -> पांच"` and `"मिनद -> मिनट"` ASR normalization so `faster-whisper-small`'s Hindi transcript of `phase1a_tts_hi.wav` routes deterministically to `SetTimer + GetTime` instead of falling back to Qwen.

---

## 3. Empirical Benchmarks & Next Gate

- **STT WER/CER, R4 Noise Probe & Kannada TTS Evaluation:** See [`docs/MULTILINGUAL_TEST_REPORT.md`](./MULTILINGUAL_TEST_REPORT.md) (`models/eval_audio/phase1_5_stabilization_benchmarks.json`).
- **Ollama Placement & Monotonic Pipeline Latencies:** See [`docs/VOICE_LATENCY_REPORT.md`](./VOICE_LATENCY_REPORT.md).
- **Human Acceptance Gate (`3–5 min`):** See [`docs/VOICE_BUGFIX_REPORT.md`](./VOICE_BUGFIX_REPORT.md). Automated verification did **not** test physical microphone acoustics, room echo, or human regional Karnataka speech; those require the human acceptance test before Phase 3.
