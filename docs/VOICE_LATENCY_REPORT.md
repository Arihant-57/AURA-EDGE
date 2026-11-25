# Voice Latency Report

Date: 25 November 2026

## Instrumentation status

`ProcessTurnOutput` exposes independent measurements for `stt_ms`, `router_ms`,
`llm_ttft_ms`, `llm_ms`, `tool_ms`, `tts_ms`, `playback_ms`, `ttfa_ms`, and total
`latency_ms`. TTS synthesis no longer includes audio playback duration, and a
cache-hit flag is reported separately.

The automated suite verifies field separation, non-negative synthesis timing,
zero playback time when speaker playback is disabled, first-audio readiness, and
TTS cache hits. It does not provide representative hardware latency numbers.

## Pending live measurements

The live harness completed against local Qwen and preserved its JSON report.
Human microphone acceptance recorded real GPU STT and speaker playback. An
English greeting measured STT 231.83 ms, TTS synthesis 209.93 ms, first audio
45.48 ms, playback 6731.09 ms, and total turn 6777.03 ms. A live Qwen turn showed
LLM TTFT 427.48 ms and generation 1546.41 ms. These are individual observations,
not aggregate benchmarks.

Playback duration dominates long answers. Continuous listening resumed after
responses and no speaker self-trigger was observed. Push-to-talk should be used
if room acoustics or noisy multilingual capture make continuous mode unstable.
