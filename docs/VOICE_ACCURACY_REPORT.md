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

## Product engineering replay

The final automatic replay used the same 66 text and 35 audio expectations. The speech runner synthesizes fresh WAVs for 30 cases, which can differ across runs. Intermediate runs scored 22/35 and 21/35. The fixed-temperature decoder scored 21/35, including both non-speech probes. This does not demonstrate an overall Auto-mode gain. The original joined Kannada clock transcript now routes correctly in a regression test. A live replay also returned the right clock answer, though its regenerated waveform produced a different word boundary.

The optional local Hindi model applies only when the operator chooses Hindi with strict recognition. In that mode eight fresh synthetic commands passed, including time, date, both bedroom light polarities, an AC temperature and two timer durations. English input with Kannada replies continues through multilingual recognition when that setting is selected. The compatible INT8 weights are already staged in the ignored local model directory on this laptop. The runtime never downloads them. The measured Hindi strict result is separate from the five-language Auto result.

### Original fourteen misses

| Case | Failure in the original recognized text | Disposition |
| --- | --- | --- |
| 43 | Hindi minute, timer and start verb distorted | Strict Hindi timer readback passed |
| 44 | Hindi room, light and on wording distorted | Strict Hindi verified the light on |
| 45 | Light substituted with a different Hindi word | Clarified in baseline. Strict Hindi verified off |
| 47 | Room distorted and AC joined to its numeric temperature | Strict Hindi verified 24 degrees |
| 48 | Kannada number question joined to the clock noun | Reproduced original transcript and corrected its word boundary |
| 50 | Kannada day noun lost | Variable across fresh recordings. Auto remains open |
| 52 | Kannada timer verb changed | Unsupported. No timer inferred |
| 53 | Kannada timer noun and verb fused | Unsupported |
| 54 | Kannada room, light and on tokens changed | Unsupported |
| 55 | Kannada off command lost polarity | Unsafe to reconstruct. Clarify or refuse |
| 57 | Hindi-English bedroom and light both distorted | Clarify or refuse |
| 59 | Mixed Hindi reminder, number and time unit distorted | No reminder inferred |
| 62 | Kannada-English action rendered as an unrelated word | Unsupported |
| 64 | Mixed Kannada duration and reminder distorted | Unsupported |

The earliest failing stage evidenced for these cases was transcription. Source audio was synthesized, so attribution to the recognizer alone would require an independently recorded reference. The expanded specialist comparison uses matched waveforms per engine and checks slots and intent together. On its fixed-language test, Hindi CTC matched 6/11 against Whisper 4/11, and Kannada CTC matched 3/9 against Whisper 0/9. These sets include mixed-language inputs. The ambiguous mixed-language case requires clarification. [Auto replay](../qa/results/final-decode.json) · [Specialist comparison](../qa/results/final-specialists.json) · [Strict Hindi results](../qa/results/final-product.json)

### Final Auto results by language

| Input | Passed | Mean WER | Mean CER |
| --- | ---: | ---: | ---: |
| English | 13/13 | 9.0% | 9.3% |
| Hindi | 4/9 | 51.2% | 29.8% |
| Kannada | 3/8 | 49.4% | 22.4% |
| Hinglish | 1/3 | 60.8% | 57.2% |
| Kannada-English | 0/2 | 75.0% | 51.9% |

English totals include two non-speech rejection probes. Error rates exclude those probes. Hindi lost one passing case versus baseline and Kannada gained one. Raw cases retain all substitutions and expected slots. A speaker should choose strict Hindi for the Hindi-only demo on this installation and switch back to multilingual Auto for mixed speech.

A second strict Hindi run on the release build regenerated the audio and passed 6/8. One bedroom-light command was transcribed with an unresolved room and one temperature command contained an unrecognized number spelling. Neither verified the requested change. The earlier 8/8 result did not reproduce on this set of fresh waveforms. [Release readbacks](../qa/results/final-product.json)

The complete release replay reproduced 66/66 typed and 21/35 Auto audio checks, with the same per-language totals as above. All 11 voiced English samples selected English for the response. All 14 missed Auto samples reported no execution. [Release corpus](../qa/results/final-acceptance.json)
