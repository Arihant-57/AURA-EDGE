# Voice acceptance

**Automated voice coverage is partial. Human acceptance is pending.** At production code commit `b04450e`, 66/66 typed cases passed and 27/35 Auto audio cases passed. Before the optional Hindi second pass, audit runs yielded 21/35 to 24/35. Thirty cases regenerate synthetic speech at run time. Three use saved synthetic WAVs and two are non-speech controls. Differences between runs cannot establish an overall accuracy gain. [Release turn records](../qa/results/audit-shipping-accuracy.json)

| Input group | Auto audio passed |
| --- | ---: |
| English spoken phrases | 11/11 |
| Silence and noise rejection | 2/2 |
| Hindi | 9/9 |
| Kannada | 4/8 |
| Hindi-English | 1/3 |
| Kannada-English | 0/2 |

### Failure investigation

The eight missed Auto cases all returned clarification or refusal and reported no executed action. Their original text, actual transcript, word and character errors, selected intent, reply, language and checks are retained in the turn records. Running the corresponding text through the same backend succeeded. Earlier Hindi misses lost timer, room, light or temperature words. The remaining Kannada misses lost timer or light/power words. One off command was transcribed with the opposite polarity and an unrecognized light name. The system requested clarification. Both mixed-language groups had cross-script substitutions. One Kannada-English light command was classified as Hindi and received a Hindi refusal. The turn record labels silence with a fallback language even though no words were returned or spoken. Mapping an unrecognized or missing device, value or polarity to a plausible command can operate the wrong appliance. These substitutions remain unresolved. The fixed-language comparison used existing Whisper and IndicConformer engines on matching waveforms. [Model comparison](ASR_MODEL_COMPARISON.md) · [Per-case transcripts](../qa/results/audit-shipping-accuracy.json)

The Hindi specialist initially missed a bedroom AC request after splitting the spoken AC abbreviation and misspelling the number twenty-four. The corrected parser recognizes split syllables only when followed by a numeric temperature and the Hindi word for degrees. Its negated form still clarifies. The latest strict Hindi corpus passed 8/8 with persisted readbacks. A native-clock workaround does not establish robust identification across real accents. [Strict Hindi and control results](../qa/results/audit-shipping-actions.json)

A Hindi test upload changed the language used by a subsequent English browser query. An independent WAV regression also showed a strict English upload replacing a saved Kannada/multilingual preference. Upload-specific settings now apply to one turn. The saved-language and recognition-mode checks pass. Uploaded audio previously inherited the preceding microphone turn's noise statistics and endpoint. A regression reproduced the stale 420 ms voiced-duration value. The corrected upload path uses its own audio and leaves the physical endpoint unmeasured. These defects explain a reproducible cross-turn failure; the exact cause of the user's original physical attempt remains unconfirmed.

The native test received ten level updates during physical capture and stopped successfully. Two independent synthetic PCM streams injected before VAD exercised listening, transcription, routing, execution, verification, playback and return to Idle. Both appliance changes matched persistent readback. These checks do not measure a human speaker's clipping, first and last words, pronunciation or audible continuity. [Physical device smoke](../qa/results/audit-native.txt) · [Fan PCM](../qa/results/audit-ptt-fan.json) · [Light PCM](../qa/results/audit-ptt-light.json)

### One human session

From the integration checkout run `./scripts/test-aura-microphone.ps1`. Sixteen language, conversation, clock and refusal checks plus eight simulator, context, timer and negation checks use isolated data. The runner logs the public prompt, transcript, reply, slots, intent, detected and reply languages, elapsed time, callback and operator judgments locally under the ignored runtime directory. It performs device, timer or clock readback where specified. It saves completed turns incrementally. No raw audio file is retained.

At the end, also press Stop during a long spoken answer and verify its audible ending. Mute, try Talk, check that no words are captured and unmute. Record those observations along with physical network disconnection in the [testing checklist](TESTING_TODO.md).

### Matched Hindi comparison

On eight identical Hindi command waveforms, the prior Auto recognizer passed 3/8 and the new Auto path passed 8/8. Strict Hindi passed 8/8. Each mode left device and timer records unchanged on all six negated or informational controls. The fallback runs a second local decode only for clean, high-confidence Hindi script when the original interpretation is unresolved. Existing interpretations, ambiguous refusals and mixed-script input keep their original route. A second hypothesis cannot replace conflicting recognized numbers or turn a marked question into a new mutation. This result covers the measured phrases. The 1/3 Hinglish result is an ambiguous-input rejection. Both actual Hinglish action examples missed. [Matched A/B records](../qa/results/audit-hindi-ab.json) · [Miss ledger](../qa/results/audit-shipping-misses.json)

### Noise and recognition limits

The shipping build passed 23/31 fan, keyboard, clean and quiet-input trials and 10/16 reflection, nearby-speech and speaker-bleed trials. None of the failed trials reported executing an action. In the independent fixed-language decoder comparison, the Hindi specialist matched intent parameters on 8/11 waveforms and Whisper on 3/11. The Kannada specialist matched 4/9 and Whisper 0/9. Neither local decoder recovered every requested action. [Noise trials](../qa/results/audit-shipping-noise.json) · [Interference trials](../qa/results/audit-shipping-interference.json) · [Decoder transcripts](../qa/results/audit-model-comparison.json)
