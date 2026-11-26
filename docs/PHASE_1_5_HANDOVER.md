# Phase 1.5 Handover

## Verified state — 25 November 2026

Phase 1.5 is implemented in the uncommitted backend working tree on
`feat/backend-intelligence`. The local and remote branch heads both point to
`331e0bf995ec74253e289180fecad6736d3ec423`.

Implemented behavior includes deterministic multilingual greetings,
acknowledgments, farewells and language switching; local-Qwen conversation;
bounded in-memory session history; streamed response events and sentence TTS;
continuous VAD conversation controls; microphone mute; and separated latency
fields for STT, routing, LLM TTFT/generation, TTS, playback, TTFA and total turn
time.

Safety boundaries remain explicit: registered tools execute through the tool
registry, restricted or unsupported actions return `refused`, and an unavailable
local conversation model returns `failed`. Failed model turns are not committed
to conversation history.

## Automated verification

- `ruff check backend`: passed.
- `pytest -q backend/tests/test_phase1_5_conversation.py`: 6 passed in 28.99s.
- `AURA_EDGE_REQUIRE_LIVE_DB=1 pytest -q backend/tests`: 137 passed in 77.50s.
- PostgreSQL was required by the final suite; no database test was skipped.

## Live acceptance — 25 November 2026

The live harness passed against local `qwen3.5:4b` with PostgreSQL and the GPU
runtime. It verified two natural-conversation turns in each of English, Hindi and
Kannada; eight context/language-switch follow-ups; registered `GetTime` execution
in all three languages; four restricted-action refusals; response streaming;
history; mute/unmute; and conversation start/stop. The report is stored at
`models/eval_audio/phase1_5_report.json` as a local evaluation artifact.

Human USB-microphone testing confirmed continuous capture resumes after spoken
responses, `/lang` switching works, a spoken farewell stops the session, and no
speaker-output self-trigger was observed. Short/noisy Hindi and Kannada phrases
were sometimes mistranscribed, and console input can be awkward while continuous
capture is active. Push-to-talk is therefore the reliable demonstration fallback.

The transcript also exposed clock/timer requests falling through to conversation.
Focused regressions now keep common date variants deterministic and route a
misspelled `timmer` through tool classification instead of conversational success.
Ruff and the focused Phase 1.5 suite pass (`7 passed`).

Phase 2 may proceed without rewriting this voice stack; voice and manual device
commands must share one registered smart-home execution engine.
