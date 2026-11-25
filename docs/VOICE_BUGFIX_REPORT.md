# AURA EDGE — Real-World Voice & Conversational Bugfix Report

**Branch:** `feat/backend-intelligence` (building on `d56a0ac`)  
**Scope:** Real-world microphone, playback, language fidelity, CLI, and concurrency fixes across Phase 0–2 without altering `frontend/`, `main`, AURA V4, or starting Phase 3.

---

## 1. Root Causes Found & Fixed

| Area | Root Cause | Implemented Fix | File(s) |
|---|---|---|---|
| **1. Audio Playback Quality** | Blocking chunk writes (`stream.write`) caused buffer underruns between sentences, rate mismatches (`16000` Hz Kannada vs `22050` Hz EN/HI vs `48000` Hz Windows WASAPI), clipping/DC clicks, and concurrent audio tasks (`speak_text` + timer notifications) could contend for playback. | Implemented single-owner exclusive `AudioPlayback` (`_owner_lock` + monotonic `_playback_gen` guard), callback-driven `_BoundedPcmBuffer` (8s circular buffer outputting clean zeros during brief sentence gaps), `prepare_pcm_for_playback()` (DC removal, silence trimming, peak limiting `<= 0.95`, 5 ms/8 ms fades, `resample_poly` fallback to host default `48000`/`44100` Hz), 2-sentence prefetch, and `55 ms` inter-sentence pause. Speed clamped safely to `0.5..2.0`. | [`playback.py`](../backend/src/aura_edge/speech/playback.py), [`sherpa_onnx_backend.py`](../backend/src/aura_edge/speech/tts_backends/sherpa_onnx_backend.py), [`tts_router.py`](../backend/src/aura_edge/speech/tts_router.py) |
| **2. Microphone Feedback (Echo Loop)** | `AudioRouteMode.MONITOR_ONLY` only softened callback routing and restored `NORMAL` immediately on `IDLE`, letting lingering speaker reverb re-trigger Silero VAD in hands-free mode. | Switched to strict **half-duplex** gating: entering speech calls `AudioCapture.suspend_for_playback()`, which closes the active hardware `InputStream`, drops incoming frames, drains `_frame_queue`, clears `_preroll`/`_recorded_frames`, and resets Silero VAD. `resume_after_playback()` runs only after physical output drain plus configurable `audio_post_playback_cooldown_sec` (`0.65s`, `AURA_EDGE_AUDIO_POST_PLAYBACK_COOLDOWN_SEC`), while preserving explicit user mute. | [`audio_capture.py`](../backend/src/aura_edge/speech/audio_capture.py), [`voice_orchestrator.py`](../backend/src/aura_edge/application/voice_orchestrator.py), [`config.py`](../backend/src/aura_edge/config.py) |
| **3. EN/HI/KN Language Enforcement & Hallucinations** | (a) `normalizer._replace_number_words()` replaced `"do"` -> `"2"` unconditionally whenever `language != "en"`, corrupting English sentences like `"What languages do you support?"` after `/lang kn`. (b) `IntentProcessRequest.language` defaulted to `"auto"`, overriding `/voice/language`. (c) `stt.py` could propagate unsupported Whisper language codes. (d) LLMs could answer in English while tagged `"kn"` or claim Tagalog support. | Restricted `_ROMAN_INDIC_NUMBER_WORDS` to always require a trailing time unit; defaulted `IntentProcessRequest.language = None` (honoring `session_language`); locked `stt.py` to Unicode script + `{"en", "hi", "kn"}`; added `validate_response_language()` rejecting `<35%` Kannada/Devanagari script or unsupported-language claims before Kannada/Hindi TTS speaks. | [`normalizer.py`](../backend/src/aura_edge/intelligence/normalizer.py), [`stt.py`](../backend/src/aura_edge/speech/stt.py), [`routes_voice.py`](../backend/src/aura_edge/api/routes_voice.py), [`conversation.py`](../backend/src/aura_edge/intelligence/conversation.py) |
| **4. Outdated Capability Prompt** | `_CONVERSATION_SYSTEM_PROMPT` line 28 still claimed AURA could not control smart-home lights after Phase 2 was merged. | Added `ToolRegistry.describe_capabilities(language)` dynamically summarizing `DateTimeTool`, `TimerManager`, and active `SmartHomeService` rooms/devices/routines, injected into `ConversationSession.build_llm_messages()`, while keeping strict tool-verification honesty. | [`conversation.py`](../backend/src/aura_edge/intelligence/conversation.py), [`tool_registry.py`](../backend/src/aura_edge/execution/tool_registry.py), [`planner.py`](../backend/src/aura_edge/intelligence/planner.py) |
| **5. Short Natural Deterministic Replies** | Greetings, acknowledgments, and identity/language questions gave verbose boilerplate or hit Ollama. | Shortened `"greeting"`, `"acknowledgment"`, `"farewell"` templates; added deterministic `Identity` and `SupportedLanguages` intents in `fast_router.py` / `schemas.py`; kept `keep_alive="15m"` and `num_predict=120` (`220` only for detailed explanations). | [`response_catalog.py`](../backend/src/aura_edge/intelligence/response_catalog.py), [`fast_router.py`](../backend/src/aura_edge/intelligence/fast_router.py), [`ollama_client.py`](../backend/src/aura_edge/intelligence/ollama_client.py) |
| **6. CLI Validation & Deduplication** | Bare `/`, bare `/lang`, unknown `/foo`, and punctuation (`...`) fell through to `/intent/process` and Ollama; WebSocket + HTTP both printed turns and duplicate `AURA>` prompts. | Added `parse_cli_command()` rejecting invalid/incomplete slash commands and punctuation-only input locally (Unicode-safe for Devanagari/Kannada); added `TurnOutputDeduplicator` deduplicating WS/HTTP output by `turn_id` and suppressing WS prompts while HTTP turns are in flight. | [`voice_cli.py`](../backend/scripts/voice_cli.py) |
| **7. Concurrency & Turn Serialization** | Concurrent text inputs, continuous VAD turns, and timer completion chimes could race over FSM state and playback. | Added `_turn_lock` in `VoiceOrchestrator`, `_preempt_idle_listening()` before text/PTT/upload turns, serialized `_speak_timer_notification()`, and checked cancellation before opening audio output. | [`voice_orchestrator.py`](../backend/src/aura_edge/application/voice_orchestrator.py) |
| **8. Separated Latency Telemetry** | Needed explicit distinction between synthesis readiness and actual audible output start/drain. | Added `audio_startup_ms` (`PlaybackMetrics`, `ProcessTurnOutput`, `response_completed` WS event) cleanly separated from `stt_ms`, `llm_ttft_ms`, `llm_ms`, `tts_ms`, `first_audio_ready_ms`/`ttfa_ms`, and actual `playback_ms`. | [`playback.py`](../backend/src/aura_edge/speech/playback.py), [`voice_orchestrator.py`](../backend/src/aura_edge/application/voice_orchestrator.py) |

---

## 2. Installed Local Ollama Model Evaluation (No Downloads)

Evaluated already-installed local Ollama models (`127.0.0.1:11434`, `think=false`, `num_predict=100`, `num_ctx=4096`) on short EN/HI/KN knowledge prompts (`"Why is the sky blue in simple words?"`, `"आकाश नीला क्यों दिखता है?"`, `"ಆಕಾಶ ಏಕೆ ನೀಲಿಯಾಗಿ ಕಾಣುತ್ತದೆ?"`):

| Model | Lang | TTFT (ms) | Total (ms) | Speed (tok/s) | Script Valid | Honest Observed Quality |
|---|---|---:|---:|---:|---|---|
| `qwen3.5:4b` (default) | `en` | `4564.46` | `8566.39` | `8.51` | Yes | Accurate & concise ("Rayleigh scattering"). |
| `qwen3.5:4b` (default) | `hi` | `4433.39` | `14575.96` | `7.30` | Yes | Fluent grammar, but mistranslated Rayleigh as `"रूट-फ्रैक्टल"` and inserted Latin parens `(Rayleigh scattering)`. |
| `qwen3.5:4b` (default) | `kn` | `5079.10` | `19241.86` | `7.06` | Yes | Weak semantics: hallucinated `"ರೇಲಿ ಸ್ಪರ್ಧ"` and `"ಮಣ್ಣಿನಲ್ಲಿ"` ("in soil"). Requires human verification; keep responses brief. |
| `nemotron-3-nano:4b` | `en` | `725.78` | `1052.09` | `42.87` | Yes | Very fast & accurate in English. |
| `nemotron-3-nano:4b` | `hi` | `564.06` | `762.79` | `50.38` | Yes | Broken Hindi (`"आकाश हल्का है।"` — "the sky is light"). |
| `nemotron-3-nano:4b` | `kn` | `542.10` | `1033.70` | `46.76` | Yes | Broken truncated Kannada (`"ಆಕಾಶ ಅಲ್ಲ, ನೀಲಿಯಾಗಿ ಕಾಣುತ್."`). |
| `qwen3.5:9b` (installed) | `en` | `1028.79` | `4337.08` | `10.28` | Yes | Accurate & well-paced. |
| `qwen3.5:9b` (installed) | `hi` | `961.68` | `5669.87` | `10.41` | Yes | Pure Devanagari, accurate & natural (`"वायुमंडल के छोटे कण..."`). |
| `qwen3.5:9b` (installed) | `kn` | `1119.97` | `6925.50` | `9.99` | Yes | Pure Kannada script, accurate & concise explanation (minor suffix slip `"ಬೆಳಕಿನನ್ನು"` vs `"ಬೆಳಕನ್ನು"`). |

**Takeaway:** Default stays `qwen3.5:4b` to respect the 6 GB RTX 3050 VRAM budget alongside `faster-whisper-small`, with `validate_response_language()` guarding Hindi/Kannada output. For higher-quality local Hindi and Kannada dialogue (with slower/tighter VRAM headroom), already-installed `qwen3.5:9b` can be selected via `AURA_EDGE_OLLAMA_MODEL=qwen3.5:9b`. Live human Kannada voice evaluation remains required before claiming production-grade Kannada fluency.

---

## 3. Automated Verification Summary

- `ruff check .\backend`: **All checks passed!** (`0` errors)
- Focused bugfix + Phase 1.5/2 check (`test_voice_bugfixes.py`, `test_phase1_5_conversation.py`, `test_phase1_tts_and_playback.py`, `test_phase1_multilingual_router.py`, `test_phase2_smart_home.py`): **`117 passed, 0 failed, 0 skipped`**
- Full regression suite against live PostgreSQL (`AURA_EDGE_REQUIRE_LIVE_DB=1 .\.venv\Scripts\pytest.exe -v .\backend\tests`): **`146 passed, 0 failed, 0 skipped`** in `127.53s`.

---

## 4. Human Microphone Acceptance Test (5-Minute Verification)

Automated tests ran with physical speaker output disabled (`play_to_speaker=False` / mocked streams) to isolate concurrency and timing cleanly. Before marking acoustic voice quality final on your hardware, run:

```powershell
cd C:\Users\ariha\Desktop\AURA-EDGE
.\scripts\start-aura-voice.ps1 -ConversationMode continuous
```

Check each item interactively in a quiet room:
1. **Prompt & CLI Hygiene:** Type `/`, `/lang`, `/unknown`, and `...` at `AURA>` — verify local usage hints appear cleanly and AURA does **not** speak or hit Ollama. Verify only one `AURA>` prompt renders per turn.
2. **Short Greetings & Identity:** Say `"Hello"` -> verify brief reply (`"Hello! How can I help?"`), clean smooth audio, zero clicks/stuttering. Ask `"What is your name?"` -> brief identity reply. Ask `"What languages do you support?"` -> confirms ONLY English, Hindi, and Kannada.
3. **Half-Duplex Anti-Feedback:** After AURA speaks a long answer (e.g. `"Why is the sky blue?"`), stay silent for 3 seconds — verify AURA does **not** transcribe its own speaker echo into a second turn.
4. **Explicit Language & Smart-Home / Timers:** Run `/lang kn`, ask `"ಈಗ ಎಷ್ಟು ಗಂಟೆ ಆಗಿದೆ?"` and `"ಬೆಡ್‌ರೂಮ್ ಫ್ಯಾನ್ ಆನ್ ಮಾಡು"` (or `"Turn on the bedroom fan"`), then set a `5 second timer` while talking — confirm smooth Kannada/English voice switches with zero overlapping playback.
5. **Clean Shutdown:** Press `/quit` and run `.\scripts\stop-dev.ps1 -WithDb`.
