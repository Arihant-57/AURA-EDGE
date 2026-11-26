# Synapse 2.0 problem statement 13

The supplied `Synapse_2.0_Problem_Statements.pdf` lists “A Voice Assistant That Works Completely Offline” on page 6. It specifies one laptop, no internet, spoken input, varied phrasing, pretend home control, current time and date, timers, spoken feedback in English plus Hindi or Kannada, and safe refusal of requests the assistant does not understand.

| Requirement | Evaluation | Result |
| --- | --- | --- |
| Run on one laptop without external services | Local PostgreSQL, Ollama, speech models and dashboard under strict-offline policy | Automated local execution verified. Physical network disconnection is pending. |
| Recognize spoken requests | Uploaded synthetic WAVs, saved multilingual fixtures and injected PCM at the native capture path | Automated speech path verified. Corpus accuracy is partial. Human microphone acceptance is pending. |
| Interpret varied phrasing | 66 typed phrases covering five language groups | 66 passed, including variants, scoped follow-ups and refusals. |
| Control pretend devices | API commands, WebSocket updates and state readback; voice command replay | Verified against the simulated home. |
| Provide correct time and date and set timers | Local clock comparison, duration readback and existing live database timer tests | Automated verification passed. |
| Speak replies in the supported languages | English, Hindi and Kannada speech fixtures and existing speech-output tests | Automated playback path and model support verified. Human audibility and comprehension are pending. |
| Refuse unclear or unsupported requests | Negative typed cases, silence, noise and failed transcriptions | Verified observed safe outcomes. The temporary-power routing regression now clarifies without mutating the device. |

Overall acceptance remains open until a user completes the physical microphone, audibility and disconnected checks. Recorded substitutions in the synthetic corpus also keep broad multilingual voice reliability open.

The updated build also provides a browser dashboard, a simulator in the nine-page dashboard and an optional strict Hindi recognizer. English Auto and strict Hindi are distinct demo paths. The dedicated Hindi synthesis-to-tool test passed 8/8 on its first run and 6/8 on a fresh synthesis. Two later transcripts lost the intended room or temperature. Live volunteer speech and network-disconnected restart remain open. [Testing TODO](TESTING_TODO.md)

## Independent audit on November 26, 2026

Page 6 of the supplied competition PDF specifies the single-laptop, disconnected workflow. The independent matrix lists the implementation, test input, expected behavior, observed result, evidence, status and limitation for each official requirement. Regional and mixed speech still miss intended commands. The operator deferred physical listening and network-disconnected acceptance until the end. [Official requirements matrix](FINAL_VERIFICATION_REPORT.md#official-challenge-traceability)
