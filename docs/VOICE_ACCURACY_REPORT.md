# Voice acceptance and accuracy

The replay used the regular local REST endpoints and the installed TTS and recognition models. A pass requires the expected route and its observable result, such as device readback, timer duration, or safe refusal.

| Language group | Typed | Synthetic WAV |
| --- | ---: | ---: |
| English, including silence and noise probes | 37/37 | 13/13 |
| Hindi | 10/10 | 5/9 |
| Kannada | 9/9 | 2/8 |
| Hinglish | 5/5 | 1/3 |
| Kanglish | 5/5 | 0/2 |
| Total | 66/66 | 21/35 |

The audio sample comprises 30 locally synthesized phrases, three saved synthetic fixtures and two rejection probes. All three saved multilingual timer fixtures passed. Twelve misses ended Unsupported and two asked for clarification. None of these fourteen outcomes was scored as a verified execution. Running the thirty fixtures from the same typed inputs isolates the remaining failures to the recorded speech path for this sample. TTS prosody, the recognition model and its language selection can all contribute. This run cannot isolate their individual effects.

| Language group | Voiced samples | Mean word error rate | Mean character error rate | Detected language matched corpus label |
| --- | ---: | ---: | ---: | ---: |
| English | 11 | 9.0% | 9.3% | 11/11 |
| Hindi | 9 | 46.2% | 23.1% | 9/9 |
| Kannada | 8 | 54.6% | 14.0% | 8/8 |
| Hinglish | 3 | 65.8% | 60.2% | 3/3 |
| Kanglish | 2 | 62.5% | 68.1% | 1/2 |

The transcription rates are per-utterance means, excluding the two rejection probes. Mixed utterances use their Indic language as the expected recognition label. The explicit intent and verified execution checks each passed 53/53 for text and 18/32 for audio. Device parameter and state checks passed 18/18 and 4/11. Timer duration checks passed 7/7 and 7/10. Safe negative/no-speech checks passed 9/9 and 3/3. Generated reply language validated for 66/66 typed replies and 33/33 voiced-sample replies. Those acceptance turns skipped playback. Separate playback benchmarking completed all 36 turns with a non-silent host callback.

In the independent microphone-path check, “switch off the study fan” passed through VAD, recognition, verified execution, persisted readback, playback callback and Idle. The same harness heard “bedroom” as “bid room” on the light command, clarified and kept the seeded device state. The separate WAV upload of that bedroom command passed.

The structured replay contains expected and actual intent, transcript, reply, per-case execution checks and timing. The previous first-pass score used different test assertions for compound results and safe refusal, so its totals are excluded from the delta. The before/after routing regressions reproduce concrete fixes with equivalent assertions.

Human microphone accuracy, accent coverage, and subjective intelligibility are pending. No human recordings are included.
