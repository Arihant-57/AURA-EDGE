# Conversation Test Report

Date: 25 November 2026

## Automated result

The Phase 1.5 conversation suite passed all 6 tests. The complete backend suite
passed all 137 tests with live-database enforcement.

Covered behavior:

- English, Hindi and Kannada greetings, acknowledgments and farewells.
- Deterministic language switching and cross-language reply requests.
- General conversation routing without a redundant classifier call.
- Bounded, atomically committed session history and history deletion.
- WebSocket transcript/response streaming and completion events.
- Sentence buffering, TTS caching and separated playback metrics.
- Continuous conversation start/stop and microphone mute/unmute.
- Local-Qwen outage reporting as `failed` with no orphaned history turn.
- Registered tool routing and deterministic refusal of restricted actions.

The streaming conversation tests use a controlled fake token stream. They prove
orchestration and API behavior, not live model answer quality or performance.

## Live and human acceptance result

The live local-Qwen harness passed with `qwen3.5:4b`: 6 multilingual speech/tool
turns, 6 natural conversation turns, 8 follow-up/language-switch turns, 4 safety
refusals, and WebSocket token/transcript/completion events. Qwen prewarm completed
in 263.29 ms in this run; this is a single machine observation, not a benchmark.

Human microphone testing used an AB13X USB Audio input with CUDA STT. Continuous
mode automatically resumed listening and stopped on farewell without an observed
audio feedback loop. Short noisy multilingual phrases remain less reliable than
clear speech, so push-to-talk remains the recommended fallback for the demo.
