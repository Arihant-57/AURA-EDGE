# AURA EDGE system stabilization

The stabilization branch starts from the approved Phase 5 desktop checkpoint `c32ffc0`. The protected Phase 4 backend branch remains at `856cc6f2bf43a1ed2aa3283bf4178951421030d7`. Testing uses a disposable PostgreSQL/pgvector instance and the preinstalled local speech and Ollama models.

| Reproduced issue | Root cause | Correction and retest |
| --- | --- | --- |
| Capture could remain in Listening after an input timeout or device-open error | Manual capture ran in a background task with no recovery at the turn boundary | Capture classifies completion and errors, releases input, emits feedback, and returns to Idle. Both regressions went from failing to passing. |
| Muting during capture left a manual turn active | Mute changed the global flag without cancelling its active manual capture | Mute cancels active PTT and releases the microphone. Continuous mode retains its existing lifecycle. The regression went from failing to passing. |
| Completed captures and quiet input looked idle or appeared to do nothing | Both clients lacked a capture-ready event and the dashboard discarded silent results | The clients show Ready to send and distinguish silence from failure. Assistant tests cover both. |
| Temporary power requests switched a device immediately | The matcher ignored duration scope and a follow-up could fall through to generic chat | Delayed commands retain timing and verified device context. Unsupported temporary changes clarify before mutation. Focused routing tests and persisted readback passed. |
| Common timer, dimmer and local-language phrasing fell through | Narrow number, unit, percentage and lexical matching | Extended bounded forms for the affected commands. Focused routing checks and the typed replay passed. |
| A managed desktop could load older code | The launcher took source and runtime configuration from the same checkout | The launcher loads source from its own checkout and uses the existing Python runtime, configuration and model directory. |
| Performance implied confirmed Ollama GPU execution | The label displayed dispatch policy as measured placement | Settings describes CPU requests and automatic placement. The before/after frontend regression passed after the wording change, and local Ollama resident VRAM was checked independently. |

The approved visual design is preserved. Stabilization changes to the backend are limited to the voice orchestrator, audio capture, conversation context, deterministic router and text normalizer, plus new targeted tests. Models, dependency locks, migrations and database schemas retain their existing versions.

The regular local backend on port 8000 was restarted from the stabilization checkout. Readiness verified the existing PostgreSQL migration, strict-offline policy, Whisper Small, Sherpa ONNX, planner routes and an Idle assistant with the local microphone and speaker detected.

- [Problem statement and gates](PROBLEM_STATEMENT_COMPLIANCE.md)
- [Feature verification](FEATURE_VERIFICATION_MATRIX.md)
- [Speech replay and errors](VOICE_ACCURACY_REPORT.md)
- [Latency and placement](CPU_GPU_BENCHMARK_REPORT.md)
- [Regression checks](REGRESSION_TEST_REPORT.md)
- [Offline acceptance](OFFLINE_ACCEPTANCE_REPORT.md)
- [Remaining limitations](KNOWN_LIMITATIONS.md)
