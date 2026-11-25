# AURA EDGE — Real-World Voice & Conversational Stabilization Report

**Branch:** `feat/backend-intelligence` (building on `d429f44` / `d56a0ac`)  
**Scope:** Root-cause voice pipeline stabilization across Phases 0, 1, 1.5, and 2 (`R1–R8`) without altering `frontend/`, `main`, AURA V4, or starting Phase 3.

---

## 1. Root Causes Found & Fixed (`R1–R8`)

| Req | Area | Root Cause | Implemented Fix | File(s) |
|---|---|---|---|---|
| **R1** | **Independent Review & Concurrency** | `start_ptt()` and `cancel_turn()` bypassed `_turn_lock`; background tasks (`_timer_audio_tasks`, `_conversation_task`, `SmartHomeService._simulation_task`) lacked exception logging; `process_text` and `_continuous_conversation_loop` could leave FSM stuck on unexpected errors; empty/cancelled turns hardcoded `confidence=1.0`. | Serialized `start_ptt()` and `cancel_turn()` with `_turn_lock` (bounded 1.0s wait in `cancel_turn`); added `_log_bg_task_exception` callbacks and loop `try/except` guards; set `confidence=0.0` on empty/clarify/cancelled turns; preserved `_vram_lock`, `WhisperTranscriber._lock`, and `_owner_lock`. | [`voice_orchestrator.py`](../backend/src/aura_edge/application/voice_orchestrator.py), [`smart_home.py`](../backend/src/aura_edge/application/smart_home.py) |
| **R2** | **Decoupled STT & Reply Language** | `stt.py` (lines 168–183) bypassed Whisper whenever `language="kn"`, invoked `indic-conformer-kn` directly, and fabricated `language_probability=1.0`—turning English speech and noise into random Kannada syllables. | Separated `session_language` (preferred reply language) from `stt_language="auto"` and `stt_recognition_mode="multilingual"` (with optional `"strict"` mode). Default `"multilingual"` mode always runs Whisper multilingual detection first and selectively delegates to `indic-conformer-kn` using Whisper's real `language_probability` (or `None` in strict CTC mode—never `1.0`). | [`stt.py`](../backend/src/aura_edge/speech/stt.py), [`voice_orchestrator.py`](../backend/src/aura_edge/application/voice_orchestrator.py), [`routes_voice.py`](../backend/src/aura_edge/api/routes_voice.py), [`config.py`](../backend/src/aura_edge/config.py) |
| **R3** | **KN/HI STT & Audio Input** | `_preroll` ring buffer was never populated; continuous loop reopened `sd.InputStream` every 8s timeout; single-frame VAD pops locked `_noise_floor_rms` high; quiet mics lacked bounded AGC; `indic-conformer-kn` could emit non-Kannada Indic tokens from its 5,633-token vocab. | Populated `_preroll` during idle/pre-speech and kept ~448 ms pre-speech context on `speech_start`; reused active `InputStream` across `no_speech_timeout`; added `onset_frames=2` (64 ms), bounded noise-floor adaptation, and continuation hysteresis in `SileroVadDetector`; added `CaptureMetrics` (`last_capture_metrics`); added quiet-mic AGC (capped 6x), Phase 2 domain prompts, `_validate_kn_ctc_output()`, and Hindi/Kannada ASR variant normalization (`पाज -> पांच`, `तामर -> टाइमर`, `ಗಂತೆ -> ಗಂಟೆ`). | [`vad.py`](../backend/src/aura_edge/speech/vad.py), [`audio_capture.py`](../backend/src/aura_edge/speech/audio_capture.py), [`stt.py`](../backend/src/aura_edge/speech/stt.py), [`normalizer.py`](../backend/src/aura_edge/intelligence/normalizer.py) |
| **R4** | **Noise & False Speech Rejection** | `stt.py` only checked `peak < 1e-4` and passed `vad_filter=False`; noise hallucinations (`"Thanks for watching"`, `"ಿದ್ದಾರೆ"`) fell through to Qwen LLM or triggered 20s introductions / clarification loops. | Added `_estimate_audio_stats`, `_extract_segment_metrics` (`avg_logprob`, `no_speech_prob`, `compression_ratio`), and `_evaluate_quality_gate` returning `rejection_reason` (`"silence"`, `"noise"`, `"unclear_speech"`, `"invalid_script"`) while preserving legitimate short commands (`stop`, `yes`, `no`, `ಹೌದು`, `ಇಲ್ಲ`, `ನಿಲ್ಲಿಸು`, `हाँ`, `नहीं`, `रुको`). `VoiceOrchestrator` silently returns to `IDLE` on `silence`/`noise` and speaks one short clarification on `unclear_speech`/`invalid_script`. | [`stt.py`](../backend/src/aura_edge/speech/stt.py), [`voice_orchestrator.py`](../backend/src/aura_edge/application/voice_orchestrator.py) |
| **R5** | **Routing, Tools & Terminal Outcomes** | Missing colloquial/mixed EN/HI/KN date, time, smart-home, and routine patterns; `fast_router.py` had a Malayalam `ഠ` typo in `"study"` (`ಪಠಣ`) and `\b` boundary failures on Indic suffixes; `planner.py` lacked `DeviceControl` schema; deterministic tools never emitted `response_completed`; `CLARIFYING` FSM transitions and `voice_cli.py` handlers were incomplete. | Added EN/HI/KN/Hinglish/Kanglish clock, smart-home status, climate, and routine patterns + fixed `ಪಠಣ` & Indic boundary regexes; added `DeviceControl` validation in `planner.py`; localized `SmartHomeService` device status replies; ensured every turn emits `response_completed` ONCE after `_maybe_speak()` with explicit `outcome` (`success`, `clarification`, `refusal`, `cancelled`, `runtime_failure`) and accurate `action_executed`; updated `voice_cli.py` WS worker. | [`fast_router.py`](../backend/src/aura_edge/intelligence/fast_router.py), [`intents/*/phase1.yaml`](../backend/src/aura_edge/intelligence/intents/en/phase1.yaml), [`planner.py`](../backend/src/aura_edge/intelligence/planner.py), [`tool_registry.py`](../backend/src/aura_edge/execution/tool_registry.py), [`smart_home.py`](../backend/src/aura_edge/application/smart_home.py), [`voice_orchestrator.py`](../backend/src/aura_edge/application/voice_orchestrator.py), [`voice_cli.py`](../backend/scripts/voice_cli.py) |
| **R6** | **Natural Kannada TTS & Pronunciation** | `tts_router.py` and `sherpa_onnx_backend.py` stripped all Latin characters, erasing English device names (`Light Bedroom Main`), timer labels, `AURA EDGE`, `AM`/`PM`, and decimal points (`3.5`); `vits-mms-kan` emitted ZWNJ/ZWJ unknown-char warnings; Kannada templates were literal/anglicized. | Implemented shared `normalize_kannada_for_tts()` transliterating device names, `AURA EDGE` (`ಆರಾ ಎಡ್ಜ್`), context-aware `AM`/`PM`, decimals, dates, `%`, `°C`, and unknown English words; stripped ZWNJ/ZWJ immediately before `vits-mms-kan` token lookup; refined Kannada templates in `response_catalog.py`; prewarmed `empty_speech` and `stopped`; preserved one fixed voice per language (`vits-mms-kan`, `en_US-lessac-medium`, `hi_IN-rohan-medium`). | [`tts_backends/base.py`](../backend/src/aura_edge/speech/tts_backends/base.py), [`sherpa_onnx_backend.py`](../backend/src/aura_edge/speech/tts_backends/sherpa_onnx_backend.py), [`tts_router.py`](../backend/src/aura_edge/speech/tts_router.py), [`response_catalog.py`](../backend/src/aura_edge/intelligence/response_catalog.py) |
| **R7** | **Model Selection & Placement** | Needed empirical VRAM/RAM/throughput verification across installed Ollama models and mid-stream cancellation responsiveness. | Evaluated `qwen3.5:4b`, `qwen3.5:9b`, and `nemotron-3-nano:4b` sequentially (see [`VOICE_LATENCY_REPORT.md`](./VOICE_LATENCY_REPORT.md)); confirmed `qwen3.5:4b` (100% GPU, `40.48–41.55 tok/s`, 4944 MiB VRAM) as optimal default; kept `keep_alive="15m"` configurable and added race-against-`cancel_event` inside `OllamaClient.stream_chat`. | [`ollama_client.py`](../backend/src/aura_edge/intelligence/ollama_client.py), [`config.py`](../backend/src/aura_edge/config.py) |
| **R8** | **Streaming, Audio Stability & Latency** | Streaming language guard only checked sentence #1 in HI/KN and skipped EN + tail; `_BoundedPcmBuffer` acquired `threading.Condition` inside PortAudio callback; final sentence received a 55 ms trailing silence pad; `stop()` skipped `apply_fade_out()`; monotonic `stage_timestamps` were missing. | Validated every streamed sentence and tail chunk via `validate_response_language()` before TTS queuing and enforced `spoken_text == response_text` parity; rewrote `_BoundedPcmBuffer` as lock-free SPSC ring buffer capturing `first_non_silent_mono` inside callback; added concurrent 2-sentence prefetch `_feeder` and barge-in fade-out; fixed premature mic resume on `CANCELLED`/`ERROR`; added all 10 `stage_timestamps` and derived latency fields. | [`playback.py`](../backend/src/aura_edge/speech/playback.py), [`tts_router.py`](../backend/src/aura_edge/speech/tts_router.py), [`voice_orchestrator.py`](../backend/src/aura_edge/application/voice_orchestrator.py) |

---

## 2. Installed Ollama Comparison Summary (Sequential Measurement)

Measured sequentially via local Ollama (`127.0.0.1:11434`, `think=false`, `num_predict=100`, `num_ctx=4096`) on RTX 3050 6 GB Laptop GPU + 16 GB RAM:

| Model | Placement (`ollama ps`) | VRAM Active / Total | Warm Speed (tok/s) | Warm TTFT (EN/HI/KN) | Observed Quality |
|---|---|---:|---:|---:|---|
| **`qwen3.5:4b` (default)** | `100% GPU` | `4944 / 6144 MiB` | `40.48–41.55` | `257 / 296 / 296 ms` | Accurate EN/HI; valid Kannada script (`validate_response_language` passes), brief & fits 6 GB VRAM alongside Whisper. |
| `qwen3.5:9b` | `41%/59% CPU/GPU` | `5573 / 6144 MiB` | `9.85–9.95` | `374 / 695 / 687 ms` | Best Kannada prose, but ~4x slower (`first_valid_sentence` 7.69s in KN) and spills 41% to RAM. |
| `nemotron-3-nano:4b` | `100% GPU` | `3764 / 6144 MiB` | `47.62–50.54` | `253 / 344 / 313 ms` | Fast EN, but semantically broken in HI (`"है, आपका सवाल है।"`) and KN (echoes question). |

---

## 3. Automated Verification Summary

- `.\.venv\Scripts\ruff.exe check .\backend`: **All checks passed!** (`0` errors)
- Full Phase 0–2 regression suite against live PostgreSQL (`$env:AURA_EDGE_REQUIRE_LIVE_DB="1"; .\.venv\Scripts\pytest.exe -v .\backend\tests`): **`170 passed, 0 failed, 0 skipped` in `281.65s`**.

> **Important Honesty Note:** Automated tests ran with simulated/null speaker streams (zero physical speaker output) and synthetic TTS WAV fixtures (`0` real human microphone recordings exist in the workspace). Acoustic echo suppression, TTS pronunciation naturalness, and regional Karnataka Kannada STT accuracy **cannot** be claimed complete until verified in the human microphone acceptance test below.

---

## 4. Human Microphone Acceptance Test (3–5 Minute Verification)

### Startup & Diagnostic Log Capture

Open PowerShell in `C:\Users\ariha\Desktop\AURA-EDGE` and capture both console transcript and backend diagnostics:

```powershell
cd C:\Users\ariha\Desktop\AURA-EDGE
New-Item -ItemType Directory -Force -Path .runtime\logs | Out-Null
Start-Transcript -Path .runtime\logs\human_acceptance_cli.log -Append
.\scripts\start-aura-voice.ps1 -ConversationMode continuous
```

*(Backend logs are also written under `.runtime\`; check `GET http://127.0.0.1:8000/api/v1/voice/status` or inspect `AURA>` `[LATENCY]` lines.)*

### Required 8 Acceptance Scenarios

| # | Scenario | Action / Spoken Utterance | Expected Result |
|---:|---|---|---|
| 1 | **English question while Kannada is selected** | Type `/lang kn` at `AURA>`, then speak `"What is today's date?"` | Whisper detects `en` (NOT forced to Kannada syllables), reads real host clock, replies & speaks today's date in Kannada. |
| 2 | **Natural Kannada conversation** | With `kn` active, speak `"ಆಕಾಶ ಏಕೆ ನೀಲಿಯಾಗಿ ಕಾಣುತ್ತದೆ?"` (or a regional/colloquial Kannada greeting) | Valid Kannada script reply, smooth consistent `vits-mms-kan` voice, zero Latin truncation. |
| 3 | **Hindi conversation** | Speak `"नमस्ते"` then `"आकाश नीला क्यों दिखता है?"` (or switch `/lang hi`) | Devanagari reply, consistent `hi_IN-rohan-medium` voice, smooth streaming playback. |
| 4 | **Current date and time** | Speak `"What's the time?"`, `"Today's date?"`, `"ಈಗ ಎಷ್ಟು ಗಂಟೆ?"`, `"आज तारीख क्या है?"` | Instant deterministic host-clock answer (`action_executed=True`) in the expected reply language. |
| 5 | **Timer creation** | Speak `"Set a 10 second timer"` | Timer created in PostgreSQL, spoken confirmation plays, completion alert fires after 10s without duplicate lines. |
| 6 | **Bedroom light control** | Speak `"Turn on the bedroom light"` (or `"ಬೆಡ್‌ರೂಮ್ ಲೈಟ್ ಆನ್ ಮಾಡು"`) | `light.bedroom_main` turns on (`action_executed=True`), localized confirmation spoken cleanly (`"ಬೆಡ್‌ರೂಮ್ ಲೈಟ್..."` transliterated if in Kannada). |
| 7 | **Silence after assistant response** | After a long spoken reply finishes, remain silent for `3–5 seconds` | Half-duplex suspension + `0.65s` cooldown + R4 noise gate ignore room reverberation; zero self-trigger or spurious Qwen turn. |
| 8 | **Voice smoothness & pronunciation** | Listen across EN/HI/KN for `"AURA EDGE"`, numbers, dates, `AM`/`PM`, device names | No buffer clicks/pops, no mid-reply voice changes, no dropped English device names in Kannada speech. |

### Push-to-Talk Fallback & Shutdown

If room reverberation, speaker bleed, or noisy background makes hands-free continuous VAD unreliable, run in Push-to-Talk mode instead:

```powershell
.\scripts\start-aura-voice.ps1 -ConversationMode ptt
```

When finished:

```powershell
# Type /quit at AURA> prompt, then:
.\scripts\stop-dev.ps1 -WithDb
Stop-Transcript
```
