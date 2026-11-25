# AURA EDGE voice engine stabilization

This pass builds on local stabilization checkpoint `626a887` and the approved desktop checkpoint `c32ffc0`. The protected backend stays at `856cc6f`. Synapse 2.0 problem 13 requires one-laptop offline speech input, varied phrasing, a simulated home, time/date and timers, spoken replies in English plus Hindi or Kannada, and safe refusal.

| Observed or reproduced failure | Cause | Correction |
| --- | --- | --- |
| The user spoke English and heard a Kannada reply. Stage metadata showed completed playback with detected language Kannada and intent Conversation. | An uncertain Kannada transcription could go straight into conversation. The physical waveform was intentionally not saved, so the specific acoustic cause is unknown. | A short manual capture with uncertain Kannada recognition and no deterministic route gets one English retry. Only a strong, single clock or date query can replace the initial recognition. A selected reply language remains independent. |
| Inline reasoning text entered provisional answers and was eligible for speech | The Ollama client skipped the separate thinking field but forwarded inline tags when present in content. | The Ollama client filters split or unfinished inline thinking sections before they reach the UI or speech. |
| Generated audio fragments could be separated by token-generation waits or split at a mid-sentence word boundary | The speaker consumed each text fragment while Ollama was still producing the next one. | Text still streams. Speech starts after the complete answer has been validated. English and Hindi use one speech input. Kannada retains sentence-level pauses because its installed MMS tokenizer has no punctuation tokens. |
| Automatic compute was displayed as confirmed GPU placement | The display previously used the requested Ollama policy as a runtime observation. | Settings identifies automatic placement. Local model VRAM is verified independently. |

The guarded retry and output changes use the existing Faster-Whisper, Sherpa ONNX, Ollama, WebSocket, and typed tool-registry paths. Runtime settings, models and database schema use the existing versions. The current environment reports Faster-Whisper 1.2.1, CTranslate2 4.9.1, Sherpa ONNX 1.13.8, ONNX Runtime 1.32.0, sounddevice 0.5.6 and Hassil 3.13.0. The database remains at `0005_phase4_personal_assistant`.

The original CLI pointed at the isolated revised backend resolved the typed English clock query to a verified, local `GetTime` result. The desktop physical test established the original recognition failure. The two inputs are different and the CLI's text result alone does not establish microphone parity. Native capture and software-injected PCM cover complementary parts of the pipeline.

The full backend regression passed 209 tests. The focused speech retest passed six cases, and the matched latency replay passed 36/36 outputs with callback completion. Ruff, frontend type checking, seven frontend tests and the production build passed. The Windows smoke passed microphone frames, capture cancel and mute, all eight dashboard sections, a disposable reminder, verified device synchronization and Stop during speech. The production dashboard Talk control entered Listening. User-confirmed recognition and smoothness remain open.

Start the application from the integration root with `./scripts/start-aura-desktop.ps1`. Press Talk, speak, then select the check mark to send. Stop cancels processing or playback. Run the optional five-language read-only acceptance runner with `& "$env:USERPROFILE/Desktop/AURA-EDGE/.venv/Scripts/python.exe" -m qa.live_acceptance`. Its terminal shows the transcript and response. The saved report contains only outcome, language, model and timing.

- [Model comparison and provenance](ASR_MODEL_COMPARISON.md)
- [Noise robustness](NOISE_ROBUSTNESS_REPORT.md)
- [Multilingual acceptance](MULTILINGUAL_ACCEPTANCE_REPORT.md)
- [Matched CPU/GPU benchmark](CPU_GPU_BENCHMARK_REPORT.md)
- [Command accuracy](VOICE_COMMAND_ACCURACY_REPORT.md)
