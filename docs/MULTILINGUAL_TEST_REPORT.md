# AURA EDGE — Multilingual Test & Failure-Mode Report

**Date:** 2026-11-25  
**Fixture Suite:** `backend/tests/fixtures/multilingual_utterances.json` (`96` utterances: `32` English, `32` Hindi, `32` Kannada)  
**Test Suite:** `backend/tests/test_phase1_multilingual_router.py` (`99` test cases) + Audio/VAD/STT/TTS/FSM/DB/API suites (`128` total tests passed, `0` failed, `0` skipped)

---

## 1. Utterance Coverage & Accuracy Summary

| Language | Tested Scripts & Modes | Utterances | Deterministic Accuracy | Verified Tool Action Success | False / Incorrect Action Rate |
|---|---|---|---|---|---|
| **English (`en`)** | Native script + natural phrasing + numeric words (`five`, `one minute and 30 seconds`) | `32` | `32 / 32` (`100.0%`) | `100.0%` | `0.0%` |
| **Hindi (`hi`)** | Devanagari (`\u0900-\u097F`) + Indic digits (`५`) + Romanized (`5 minute ka timer lagao`, `kitne baje hain`) | `32` | `32 / 32` (`100.0%`) | `100.0%` | `0.0%` |
| **Kannada (`kn`)** | Kannada script (`\u0C80-\u0CFF`) + Indic digits (`೫`) + Romanized (`5 nimisha timer idu`, `samaya estu`) | `32` | `32 / 32` (`100.0%`) | `100.0%` | `0.0%` |
| **Total** | Full Phase 1 multilingual matrix | `96` | `96 / 96` (`100.0%`) | `100.0%` | `0.0%` |

### Tested Intent & Edge Categories per Language
- Current time queries (`GetTime`)
- Current date queries (`GetDate`)
- Timer creation with seconds/minutes/hours and Hindi/Kannada/English number words (`SetTimer`)
- Named timers (e.g., `tea`, `pasta`, `चाय`, `ಟೀ` labels preserved via Unicode regexes)
- Compound multi-action commands (e.g., `"Set a timer for 5 minutes and tell me the current time"`, `"पांच मिनट का टाइमर लगाओ और समय बताओ"`, `"ಐದು ನಿಮಿಷದ ಟೈಮರ್ ಸೆಟ್ ಮಾಡು ಮತ್ತು ಸಮಯ ಹೇಳು"`)
- Timer inspection (`ListTimers`), remaining time (`RemainingTimer`), pause (`PauseTimer`), resume (`ResumeTimer`), cancel single (`CancelTimer`), and cancel all (`CancelAllTimers`)
- Ambiguous commands (`"Set a timer"`, `"Cancel timer"`, `"टाइमर लगाओ"`, `"ಟೈಮರ್ ಸೆಟ್ ಮಾಡು"`) -> routed to `Clarify` state with localized question
- Unsupported/out-of-scope requests (smart home light control, food ordering, weather lookup) -> routed to `Unsupported` refusal with `action_executed=False`
- Assistant capabilities (`Help`) and turn stop phrases (`StopAssistant`)

---

## 2. Recorded Speech & Live Microphone Verification

1. **Recorded Multilingual Speech Evaluation (`phase1a_tts_{en,hi,kn}.wav`):**
   - Verified offline end-to-end STT -> Router -> Tool -> TTS across both CPU (`int8`) and GPU CUDA (`int8_float16`) using `faster-whisper-small`.
   - **Limitation Observed on Kannada STT:** `faster-whisper-small` transcribed English verbatim and Hindi cleanly in Devanagari, whereas synthetic `vits-mms-kan` Kannada audio produced high WER in Whisper (`"ಇರೆಂತ್ಲಿ..."` / `"ಕಾರ್ತುಗಳಿಲ್ನಿ..."`). Native Kannada script input (`text` / edited transcript / Phase 2 user corrections) works at `100%` accuracy; real human Kannada microphone evaluation should be checked prior to live stage demo if spoken Kannada commands rely on Whisper auto-STT rather than text or fixed triggers.
2. **Live Microphone Hardware Verification (`AudioCapture`):**
   - Executed `AudioCapture.start_ptt_capture(open_hardware_stream=True)` against real Windows microphone hardware (`Microphone Array (Realtek(R) Audio)`, `16000 Hz`, `mono`).
   - Recorded `1536` non-blocking PCM samples (`0.096s`) inside the zero-inference ring-buffer callback and closed stream cleanly (`reason=manual_stop`).

---

## 3. Failure-Mode & Resilience Matrix (All Executed in Automated Tests / Probes)

| Failure Condition Tested | Observed Behaviour | Verified By |
|---|---|---|
| **Empty speech / whitespace** | Transitions via `Clarify` and returns localized prompt without calling LLM or tools | `test_phase1_multilingual_router.py` |
| **Noisy non-speech audio** | Adaptive noise-floor RMS + Silero VAD `initial_timeout_sec` rejects static noise frames | `test_phase1_audio_and_vad.py` |
| **Ambiguous timer cancel w/ multiple active timers** | Returns `needs_clarification=True` listing active timer labels; refuses to guess which timer to cancel | `test_phase1_timers_live_db.py` |
| **Missing microphone device** | `AudioCapture` raises clear error (`No microphone detected`) and transitions turn safely | `test_phase1_audio_and_vad.py` |
| **Missing STT model files** | Raises `ModelNotFoundError` naming expected path and `prepare_models.py`; never downloads offline | `test_phase1_stt_and_compute.py` + `phase1a_report.json` |
| **CUDA unavailable / GPU failure fallback** | `ComputeManager` falls back to CPU `int8` and records `effective_mode="cpu"` + explicit `fallback_reason` | `test_phase1_stt_and_compute.py` |
| **Ollama unavailable (`503` / refused)** | Planner marks LLM unavailable and refuses unrouted requests safely instead of crashing or fabricating actions | `test_phase1_multilingual_router.py` |
| **Database unavailable** | Readiness returns `503`, `timer_repo` propagates DB failure honestly without memory-only fakery | `test_health_api.py`, `test_db_live_integration.py` |
| **Internet unavailable (`strict_offline`)** | `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `NetworkPolicyEnforcer` raises `NetworkPolicyViolationError` with `0` external TCP/UDP sockets opened | Live Python probe + `test_network_policy.py` |
| **Barge-in / cancellation mid-turn** | `TurnContext` + stale-turn guard prevents old async events from overwriting new turns; completed timer rows are never falsely deleted (`completed_actions_preserved`) | `test_phase1_state_machine.py` + `test_phase1_api_and_ws.py` |
| **PostgreSQL restart while timer running** | Timers survive container restart, recover on `TimerManager.recover_pending_timers()`, and `notified_at_utc IS NULL` atomic update guarantees zero duplicate completion notifications | `test_phase1_timers_live_db.py` |
