# AURA EDGE — Multilingual Test, STT/TTS Evaluation & Failure-Mode Report

## Stabilization update

The latest disposable-database replay passed 37/37 English, 10/10 Hindi, 9/9 Kannada, 5/5 Hinglish and 5/5 Kanglish typed cases. Recorded acceptance was 13/13 for the English group (including two silence/noise probes), 5/9 Hindi, 2/8 Kannada, 1/3 Hinglish and 0/2 Kanglish. English, Hindi and Kannada saved timer recordings all passed. The saved Hindi sample that had failed intermittently also passed five CPU repeats with a Kannada reply preference. These are synthetic fixtures. Background noise, accents and free-form mixed speech require human checking. See the [latest voice accuracy report](VOICE_ACCURACY_REPORT.md) for the current measurements.

**Date:** 2026-11-25  
**Branch:** `feat/backend-intelligence`  
**Benchmark Artifact:** `models/eval_audio/phase1_5_stabilization_benchmarks.json` (`backend/scripts/benchmark_stabilization.py`)

---

## 1. Speech Recognition Decoupling (`R2`)

`WhisperTranscriber` and `VoiceOrchestrator` now separate five distinct concepts that were previously coupled:
1. **Preferred reply language** (`VoiceOrchestrator.session_language`: `"auto" | "en" | "hi" | "kn"`).
2. **Recognition mode** (`stt_recognition_mode`: `"multilingual"` default, or explicit `"strict"`).
3. **Detected spoken language** (`TranscriptionResult.detected_language`: `"en" | "hi" | "kn"`).
4. **Recognition model used** (`TranscriptionResult.model_size`: `"small"`, `"base"`, or `"indic-conformer-kn"`).
5. **Real confidence & quality metrics** (`language_probability: float | None`, `transcript_confidence: float | None`, `avg_logprob`, `no_speech_prob`, `compression_ratio`, `voiced_ms`, `snr_db`, `quality_ok`, `rejection_reason`).

**Behavior verified in automated tests (`test_phase1_stt_and_compute.py`, `test_voice_orchestrator_outcomes.py`):**
- With Kannada selected as preferred reply language (`PUT /api/v1/voice/language {"language": "kn"}`), English speech (`phase1a_tts_en.wav`) is detected as `"en"` (`prob=0.9844`), routed in English to `SetTimer`, executed, and answered in Kannada.
- Hindi speech (`phase1a_tts_hi.wav`) under `"kn"` preference is detected as `"hi"` (`prob=0.9902`), normalized (`पाज मिन्त -> पांच मिनट`, `तामर -> टाइमर`), executed, and answered in Kannada.
- Fabricated `language_probability=1.0` was eliminated (`strict` CTC returns `language_probability=None`; `multilingual` returns Whisper's real probability).

---

## 2. STT Model Evaluation & WER / CER (`R3`)

> **Critical Dataset Caveat:** **Zero real human microphone recordings exist** in `AURA-EDGE`, `AURA-EDGE-RESEARCH`, or `phase4-agent`. All WER/CER values below were measured on **synthetic TTS waveforms** (saved `phase1a_tts_{en,hi,kn}.wav` and synthesized clean / 15 dB Gaussian-noise phrases). **Regional Karnataka Kannada accuracy (northern Karnataka expressions, colloquial pronunciation, natural human prosody) cannot be claimed from synthetic TTS alone and requires human microphone acceptance testing.**

### 2.1 Measured WER / CER on Identical Synthetic Audio (`faster-whisper-small` CUDA `int8_float16` vs `indic-conformer-kn` CPU `int8` vs Decoupled Selective Fallback)

| Sample (`source_kind`) | Ref Lang | `faster-whisper-small` (Raw Multilingual) | `indic-conformer-kn` (Direct CTC) | Decoupled Selective Fallback (`preferred_reply_language="kn"`) |
|---|---|---|---|---|
| `phase1a_tts_en.wav` (`synthetic_tts_saved`) | `en` | WER `0.0833`, CER `0.0870` (`en`, `812.73 ms` cold) | WER `1.0000`, CER `1.0000` (`89.28 ms`) | **`small`** (`en`, `prob=0.9844`), WER **`0.0833`**, CER **`0.0870`** (`282.65 ms`) |
| `synth_en_time` (`synthetic_tts`) | `en` | WER `0.0000`, CER `0.0000` (`en`, `234.59 ms`) | WER `1.2000`, CER `1.1875` (`37.22 ms`) | **`small`** (`en`, `prob=0.9922`), WER **`0.0000`**, CER **`0.0000`** (`236.81 ms`) |
| `synth_en_noisy` (`synthetic_tts_noisy_15db`) | `en` | WER `0.0000`, CER `0.0000` (`en`, `234.43 ms`) | WER `1.2000`, CER `1.1579` (`46.03 ms`) | **`small`** (`en`, `prob=0.9707`), WER **`0.0000`**, CER **`0.0000`** (`236.21 ms`) |
| `phase1a_tts_hi.wav` (`synthetic_tts_saved`) | `hi` | WER `0.5833`, CER `0.2500` (`hi`, `506.53 ms`) | WER `1.0000`, CER `1.0750` (`123.18 ms`) | **`small`** (`hi`, `prob=0.9902`), WER **`0.5833`**, CER **`0.2500`** (`517.45 ms`) |
| `synth_hi_date` (`synthetic_tts`) | `hi` | WER `0.2500`, CER `0.0769` (`hi`, `284.65 ms`) | WER `1.0000`, CER `1.1538` (`41.90 ms`) | **`small`** (`hi`, `prob=0.9492`), WER **`0.2500`**, CER **`0.0769`** (`282.97 ms`) |
| `phase1a_tts_kn.wav` (`synthetic_tts_saved`) | `kn` | WER `1.0000`, CER `1.0238` (`kn`, `2547.66 ms`) | WER `0.2000`, CER `0.0714` (`155.56 ms`) | **`indic-conformer-kn`** (`kn`, `prob=0.8433`), WER **`0.2000`**, CER **`0.0714`** (`2601.99 ms`) |
| `synth_kn_time` (`synthetic_tts`) | `kn` | WER `1.2500`, CER `1.1875` (`kn`, `2260.23 ms`) | WER `0.0000`, CER `0.0000` (`77.02 ms`) | **`indic-conformer-kn`** (`kn`, `prob=0.8384`), WER **`0.0000`**, CER **`0.0000`** (`1369.73 ms`) |
| `synth_kn_room` (`synthetic_tts`) | `kn` | WER `1.0000`, CER `1.0000` (`te`, `1981.83 ms`) | WER `1.0000`, CER `0.1500` (`55.42 ms`) | **`indic-conformer-kn`** (`kn`, `prob=0.3660`), WER **`1.0000`**, CER **`0.1500`** (`1819.92 ms`) |
| `synth_kn_noisy` (`synthetic_tts_noisy_15db`) | `kn` | WER `1.0000`, CER `1.1176` (`kn`, `2389.48 ms`) | WER `0.0000`, CER `0.0000` (`59.64 ms`) | **`indic-conformer-kn`** (`kn`, `prob=0.8584`), WER **`0.0000`**, CER **`0.0000`** (`2470.30 ms`) |

**Key Findings:**
- `faster-whisper-small` handles EN and HI well (`0–0.25` CER) and detects Kannada (`0.84–0.86`, or ambiguous `te` `0.366 < 0.85` on short loanword-heavy `"ಬೆಡ್‌ರೂಮ್ ಲೈಟ್ ಆನ್ ಮಾಡು."`), but its raw Kannada text is unusable (`CER >= 1.0`).
- Selective fallback delegates only `kn` or ambiguous Dravidian (`ml`/`te`/`ta` `< 0.85`) utterances to `indic-conformer-kn`, achieving `0.0–0.15` Kannada CER without running both models on EN/HI or breaking EN/HI detection.
- On `phase1a_tts_hi.wav`, Whisper's phonetic Devanagari errors (`"पाज मिन्त कर तामर लगाओ..."`) are deterministically repaired in `normalizer._normalize_hindi_asr_variants()` so compound `SetTimer + GetTime` still executes accurately.

---

## 3. Noise & False Speech Rejection Gate (`R4`)

- **Empirical Fan Noise Probe (`rng.normal(0, 0.02, 32000)`):** Direct `indic-conformer-kn` hallucinated `'ಿದ್ದಾರೆ'` (`60.62 ms`), whereas the decoupled R4 gate rejected it with `quality_ok=False`, `rejection_reason="noise"`, `is_empty=True`, `text=""` (`366.29 ms`).
- **Automated Gate Coverage (`test_speech_recognition_gate.py`, `test_voice_orchestrator_outcomes.py`):**
  - Pure silence (`peak < 1e-4` / `rms < 3e-4`) and steady noise (`flat_noise`, `no_speech_prob > 0.78`, `snr_db < 2.0`, Whisper hallucination phrases) -> `VoiceOrchestrator` returns silently to `IDLE` with `status="ignored_silence"`, `silent=True` in `response_completed`, zero Qwen calls, zero TTS.
  - Garbled/low-logprob/repetitive/wrong-script speech -> `rejection_reason="unclear_speech"` or `"invalid_script"` -> `VoiceOrchestrator` transitions to `CLARIFYING`, speaks one short `empty_speech` prompt, zero Qwen/tool execution.
  - Legitimate short commands (`"stop"`, `"yes"`, `"no"`, `"ಹೌದು"`, `"ಇಲ್ಲ"`, `"ನಿಲ್ಲಿಸು"`, `"हाँ"`, `"नहीं"`, `"रुको"`) bypass false length rejection and execute normally.

---

## 4. Multilingual Intent Routing & Smart-Home Parity (`R5`)

- **Fixture Suite (`multilingual_utterances.json`):** **`96 / 96` (`100.0%`)** across `en` (`32`), `hi` (`32`), `kn` (`32`), covering `GetTime`, `GetDate`, `SetTimer`, `ListTimers`, `GetTimerRemaining`, `PauseTimer`, `ResumeTimer`, `CancelTimer`, `CancelAllTimers`, `Help`, `StopAssistant`, compound actions, ambiguity clarification, and unsupported refusal.
- **Colloquial, Hinglish, Kanglish & Smart-Home Extensions Verified:**
  - Clock phrasings: `"What's the time?"`, `"Today's date?"`, `"Date today?"`, `"ಇವತ್ತಿನ ಡೇಟ್ ಏನು?"`, `"ಇವತ್ತು ತಾರೀಖು ಎಷ್ಟು?"`, `"ಈಗ ಎಷ್ಟು ಗಂಟೆ?"`, `"आज तारीख क्या है?"`, `"अभी कितने बजे हैं?"`, `"aaj ki date kya hai"`, `"ivattu tareekhu eshtu"`.
  - Mixed-script & Romanized smart-home: `"ಪಠಣ ಕೋಣೆಯ ಲೈಟ್ ಆನ್ ಮಾಡು"` (fixed Malayalam `ഠ` -> Kannada `ಠ`), `"ಬೆಡ್‌ರೂಮ್ light on ಮಾಡು"`, `"bedroom ki light on karo"`, `"bedroom fan off maadu"`, `"Start the robot vacuum"` (`ac` boundary fix prevents false match inside `vacuum`), localized `DeviceStatus` (no raw Python dict), climate sensor queries, and `morning`/`night`/`study`/`away` routines.

---

## 5. Kannada TTS Evaluation & Limitations (`R6`)

- **Active Kannada Voice:** `vits-mms-kan` (`facebook/mms-tts-kan`, 16 kHz, CC-BY-NC-4.0, CPU). One consistent voice per language is maintained (`en_US-lessac-medium`, `hi_IN-rohan-medium`, `vits-mms-kan`).
- **Pronunciation & Normal vs Streaming Parity:** `normalize_kannada_for_tts()` preserves and transliterates English device names (`Light Bedroom Main`), timer labels, `"AURA EDGE"` (`ಆರಾ ಎಡ್ಜ್`), context-aware `AM`/`PM` (`ಬೆಳಗ್ಗೆ`/`ಮಧ್ಯಾಹ್ನ`/`ಸಂಜೆ`/`ರಾತ್ರಿ`), decimals, dates, `%`, and `°C`. Verified in `test_speech_generation_and_streaming.py` that normal `speak_text` and streaming `speak_sentence_stream` produce sample-identical PCM (`np.array_equal`) and strip `\u200c`/`\u200d` before `vits-mms-kan` token lookup.
- **Evaluated Local `kn_piper` Model Limitation (`models/tts/kn_piper/kn_IN-syspin_male-medium.onnx`):** Inspected via `onnxruntime` and `sherpa_onnx.OfflineTts`: the ONNX file has an empty `custom_metadata_map: {}` (missing required `sample_rate`, `model_type`, `has_espeak`, `n_speakers` Piper metadata) and lacks Kannada `espeak-ng-data`, causing C++ abort at `offline-tts-vits-model.cc:Init:169`. Per project constraints, no unverified large models were downloaded; `vits-mms-kan` remains active.
- **Inherent `vits-mms-kan` Limitations:** Trained on native Kannada script (`tokens.txt` has 75 entries, no English IPA), so transliterated novel English acronyms/loanwords remain phonetic approximations requiring human listening verification.
