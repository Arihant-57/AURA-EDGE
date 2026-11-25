# AURA EDGE — Phase 1A Voice & Local LLM Model Evaluation Report

**Date:** 2026-11-25  
**Branch:** `feat/backend-intelligence`  
**Evaluation Artifacts:**
- `models/eval_audio/phase1a_report.json` (initial Phase 1A feasibility probe before `num_threads=4` and anti-loop decoding tuning)
- `models/eval_audio/phase1_benchmarks.json` (post-tuning 3-trial reproducible CPU vs GPU benchmark)
- Generated WAV samples: `models/eval_audio/phase1a_tts_{en,hi,kn}.wav`

---

## 1. Corrections to Research Baseline (Verified at Runtime)

Prior research benchmarks (`00_MASTER_REVERSE_ENGINEERING_REPORT.md` / `whisper_probe.py`) tested only `base.en` on 3 seconds of zero-valued audio. Phase 1A replaced that with real synthesized English, Hindi, and Kannada speech samples running with `HF_HUB_OFFLINE=1` and `local_files_only=True`:

1. **Windows CUDA DLL Loading:** Installing `faster-whisper` / `ctranslate2` without keeping DLL search handles fails `cuda` inference on Windows if `cublas64_12.dll` / `cudnn*.dll` are not on `PATH`. `register_windows_cuda_dlls()` in `backend/src/aura_edge/infrastructure/compute.py` probes `%LOCALAPPDATA%` / `site-packages/torch/lib` and keeps `os.add_dll_directory(...)` handles alive in module-level `_DLL_DIR_HANDLES`.
2. **Sherpa-ONNX Wheel Pin (`1.13.8` vs `2.0.3`):** `sherpa-onnx 2.0.3` removed the built-in Piper/espeak-ng phonemizer (`phoneme_codepoints is empty, no frontend available`). Phase 1 pins `sherpa-onnx>=1.12.0,<2.0` (`1.13.8`), which initializes Piper VITS models (`en`, `hi`) offline with zero external dependencies.
3. **Kannada Piper vs MMS-TTS:** Community `micahchoo/piper-kn` failed runtime init (`'sample_rate' does not exist in the metadata`). `facebook/mms-tts-kan` exported via `Symmetris-labs/tts-mms` (`kan/model.onnx`, `kan/tokens.txt`) initializes reliably and produces clean non-silent `16000 Hz` Kannada speech.

---

## 2. Offline Text-to-Speech (TTS) Feasibility Gate

All TTS synthesis executes strictly on CPU (`HF_HUB_OFFLINE=1`, no `edge_tts`, no cloud APIs):

| Language | Engine / Backend | Model / Voice | Sample Rate | RMS Amplitude | Phase 1A (`num_threads=2`) Cold / Warm | Post-Tuning (`num_threads=4`) Cold / Warm | Process RSS Delta | License Verification |
|---|---|---|---|---|---|---|---|---|
| `en` | `sherpa_onnx` (Piper VITS) | `vits-piper-en_US-lessac-medium` | `22050 Hz` | `0.10523` (`3.057s`) | `813.14 ms` / `143.35 ms` | `263.52 ms` / `202.96 ms` | `51.4 -> 246.6 MB` | MIT (Rhasspy/Piper) |
| `en` (fallback) | `windows_sapi` (Win32 OneCore/SAPI5) | Built-in OS voice | `22050 Hz` | `0.10337` (`4.034s`) | `471.86 ms` (single run) | Reserved fallback | Built-in OS | Windows OS built-in |
| `hi` | `sherpa_onnx` (Piper VITS) | `vits-piper-hi_IN-rohan-medium` | `22050 Hz` | `0.16507` (`3.923s`) | `1617.22 ms` / `556.11 ms` | `368.70 ms` / `373.46 ms` | `301.6 -> 480.7 MB` | MIT (Rhasspy/Piper) |
| `kn` | `sherpa_onnx` (MMS-TTS VITS) | `vits-mms-kan` (`facebook/mms-tts-kan`) | `16000 Hz` | `0.17452` (`5.541s`) | `4880.70 ms` / `3844.47 ms` | `1415.53 ms` / `1801.67 ms` (CPU bench); HTTP short phrase `233.03 ms` | `480.9 -> 708.8 MB` | `CC-BY-NC-4.0` (Meta MMS) |

> [!NOTE]
> `facebook/mms-tts-kan` carries `CC-BY-NC-4.0`, which matches non-commercial academic/competition evaluation. When mixing ASCII parentheses inside Kannada responses (e.g., `(India Standard Time)`), MMS-TTS logs skipped unknown Latin codepoints and speaks the surrounding Kannada characters clearly; for live production responses, pure Kannada phrasing is recommended.

**Why Kokoro & Indic Parler-TTS were not selected for Phase 1 default:** `sherpa-onnx` already covers English (`en_US-lessac-medium`) and Hindi (`hi_IN-rohan-medium`) at `<380 ms` warm latency using ~`200 MB` RSS per model, leaving GPU VRAM free for Faster-Whisper + Qwen 3.5 4B without extra PyTorch TTS overhead or Hugging Face gated access dependencies.

---

## 3. Multilingual Speech-to-Text (Faster-Whisper `base` vs `small`)

Tested offline against real WAV speech (`phase1a_tts_{en,hi,kn}.wav`) using `local_files_only=True`:

### Phase 1A Comparison (`phase1a_report.json`, baseline decoding)

| Model | Mode / Compute Type | EN Auto-Detect & Latency | HI Auto-Detect & Latency | KN Auto-Detect & Latency | Observed Recognition Notes |
|---|---|---|---|---|---|
| `faster-whisper-base` | CPU (`int8`) | `en` (`0.9925`), `1659.88 ms` (`772.67 ms` locked) | `hi` (`0.9938`), `1520.93 ms` | `kn` (`0.8622`), `1732.59 ms` | English verbatim accurate; HI & KN frequently emitted Arabic/Urdu tokens (`5 minutes کا timer Lagao...`). |
| `faster-whisper-base` | GPU CUDA (`int8_float16`) | `en` (`0.9917`), `1149.41 ms` (`105.12 ms` locked) | `hi` (`0.9907`), `237.03 ms` | `kn` (`0.8838`), `360.99 ms` | Same script substitution issue on HI/KN. Rejected as default. |
| `faster-whisper-small` | CPU (`int8`) | `en` (`0.9869`), `4801.55 ms` (`2476.43 ms` locked) | `hi` (`0.9875`), `4295.22 ms` | `kn` (`0.7528`), `36641.63 ms` (looped) | Accurate English + Devanagari Hindi (`पाज मिनद का ताईमर...`); default Whisper decoded Kannada into token repetition loops before anti-loop flags. |
| `faster-whisper-small` | GPU CUDA (`int8_float16`) | `en` (`0.9897`), `311.36 ms` (`212.32 ms` locked) | `hi` (`0.9873`), `473.94 ms` | `kn` (`0.7856`), `8411.76 ms` (looped) | Accurate English + Devanagari Hindi (`पाज मिनद का ताईमर लगाओ और अभी क्या समय हुए है बताओ`). |

### Post-Tuning Selection (`faster-whisper-small` with `repetition_penalty=1.2, no_repeat_ngram_size=3, vad_filter=True, condition_on_previous_text=False`)

From `models/eval_audio/phase1_benchmarks.json` (3 warm trials):
- **English (`small`, warm median):** CPU `2483.19 ms` (`int8`) -> GPU `259.59 ms` (`int8_float16`), verbatim exact transcription.
- **Hindi (`small`, warm median):** CPU `3560.80 ms` (`int8`) -> GPU `687.72 ms` (`int8_float16`), readable Devanagari transcription routed cleanly by Unicode-safe normalizer + router.
- **Kannada (`small`, warm median on `vits-mms-kan` synthetic audio):** CPU `20401.98 ms` -> GPU `3680.22 ms`. Anti-repetition decoding eliminated infinite loops, **but word-level accuracy on synthetic `vits-mms-kan` audio remains noisy** (`"ಕಾರ್ತುಗಳಿಲ್ನಿ ಬಂದಿ..."`), whereas direct Unicode Kannada text input (`32/32` utterances) routes with `100%` deterministic accuracy. For noisy/low-accuracy Kannada STT transcripts, UI/manual language selection + hybrid clarification fallback prevents false action execution.

**Missing Model Error Handling Verified:** Instantiating/transcribing with a nonexistent `models_dir` raised `ModelNotFoundError` cleanly with setup instructions (`prepare_models.py`) rather than attempting network download.

---

## 4. Local LLM Feasibility Gate (`qwen3.5:4b` via Loopback Ollama)

Evaluated on `127.0.0.1:11434` through `NetworkPolicyEnforcer` (`num_ctx=4096`, JSON structured output):

| Metric | GPU Mode (`num_gpu=null`, RTX 3050 6GB) | CPU Mode (`num_gpu=0`) |
|---|---|---|
| Phase 1A (`phase1a_report.json`) Cold / Warm Latency | `6746.73 ms` / `861.19 ms` | `10398.66 ms` / `3062.36 ms` |
| Phase 1A TTFT / Throughput | `280.66 ms` / `41.43 tok/s` | `538.94 ms` / `9.52 tok/s` |
| Phase 1 Benchmark (`phase1_benchmarks.json`) Cold / Warm | `1903.30 ms` / `1992.20 ms` | `8613.20 ms` / `2115.45 ms` |
| Phase 1 Benchmark TTFT / Throughput | `660.83 ms` / `10.54 tok/s` | `648.81 ms` / `9.57 tok/s` |
| Schema Compliance | Valid JSON (`SetTimer`, `duration_seconds=300`) | Valid JSON (`SetTimer`, `duration_seconds=300`) |

*(Variation in GPU `warm_ms` / `tok/s` between `phase1a_report.json` and `phase1_benchmarks.json` reflects Windows shared-VRAM contention while CUDA Whisper `small` was resident alongside Ollama on the 6 GB RTX 3050, with system RAM at `96.4%`–`97.3%`. `qwen3.5:9b` remains disabled by default to prevent VRAM eviction/spilling.)*
