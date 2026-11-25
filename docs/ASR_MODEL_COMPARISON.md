# Offline ASR and speech technology comparison

Windows 11 host with an i7-13650HX, 16 GB nominal RAM and RTX 3050 6 GB. The project already has Whisper Base, Whisper Small and the Kannada CTC ONNX export. The active Small weight file is about 484 MB, Base about 145 MB, and the installed Kannada INT8 export about 198 MB. Full runtime and voice licensing details are in [THIRD_PARTY.md](THIRD_PARTY.md).

| Candidate | Offline fit, evidence and disposition |
| --- | --- |
| Faster-Whisper / CTranslate2 (MIT) | Supported on this Windows host. CPU INT8 and CUDA INT8-FP16 validated. English results are strong on the local fixture set. Hindi and especially code switching have substantial misses. Keep Small as the primary and use one bounded English retry when justified. [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) · [CTranslate2 quantization](https://opennmt.net/CTranslate2/quantization.html) |
| AI4Bharat IndicConformer per-language / installed Sherpa-compatible export | Offline Sherpa CPU integration is already running for Kannada INT8. Existing comparison showed materially better Kannada text on short saved examples than plain Whisper. The export card documents origin, license and other Indian languages. Availability of a Hindi checkpoint does not establish Hindi or code-switching gains. [AI4Bharat source](https://github.com/AI4Bharat/IndicConformerASR) · [Sherpa export](https://huggingface.co/NiranjanYadavIN/indicconformer-sherpa-onnx) |
| AI4Bharat multilingual IndicConformer | The gated MIT model card lists 600M parameters and 22 Indic languages with ONNX. Windows integration and RAM/latency for this conversion need measurement, and the card alone does not establish English code-switched behavior. Higher integration cost with up to 6 GB GPU memory shared. [Model card](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual) |
| trysem IndicConformer 120M ONNX | The CC-BY-4.0 card offers per-language ONNX exports including Hindi and Kannada, expects model-specific mel preprocessing, and documents model details. Its Windows integration, numeric intent safety and mixed-language benchmarks are unverified here. [Model card](https://huggingface.co/trysem/indicconformer-120m-onnx) |
| Vistaar / IndicWhisper and IndicSUPERB / Kathbath | General speech benchmarks, including Hindi and Kannada. Vistaar documents benchmark-specific WER variation. Kathbath has multilingual speaker coverage. Review each subset's terms and build a small timed command sample if adopting them. The scores cannot be transferred to this laptop or to command accuracy. [Vistaar](https://github.com/AI4Bharat/vistaar) · [IndicSUPERB](https://github.com/AI4Bharat/indicSUPERB) |
| Sherpa ONNX / Silero VAD | Existing on-device Windows CPU paths for CTC, VAD and TTS, with Apache-2.0 and MIT runtime repositories respectively. Signal detection is independent of the spoken language. [Sherpa ONNX](https://github.com/k2-fsa/sherpa-onnx) · [Silero VAD](https://github.com/snakers4/silero-vad) |
| RNNoise / DeepFilterNet | Local denoisers, not ASR. RNNoise's reference tool consumes 48 kHz mono PCM and the DeepFilterNet CLI expects 48 kHz WAV. Adding an unbenchmarked resampling/denoiser chain to the 16 kHz capture gate was deferred. Windows install/build and latency must be proven. [RNNoise](https://github.com/xiph/rnnoise) · [DeepFilterNet](https://github.com/Rikorose/DeepFilterNet) |
| openWakeWord / Hassil | openWakeWord code is Apache-2.0 and distributed pretrained weights are CC-BY-NC-SA 4.0. Existing Windows ONNX runtime makes a local keyword model feasible if one is trained and validated. Hassil (Apache-2.0) already routes offline multilingual command grammars without model RAM/VRAM. [openWakeWord](https://github.com/dscripka/openWakeWord) · [Hassil](https://github.com/home-assistant/hassil) |

## Matched CPU pilot

Each language used three identical resampled synthetic WAVs for both engines. Whisper was explicitly given the language to measure transcription independently of automatic language detection. Both CTC decoders used four CPU threads. Intent is the deterministic router result, before any database action.

| Language and engine | Intent match | Mean WER | Mean CER | Median decode |
| --- | ---: | ---: | ---: | ---: |
| Hindi, Whisper Small INT8 | 2/3 | 43.3% | 19.6% | 2,743 ms |
| Hindi, IndicConformer INT8 candidate | 3/3 | 0.0% | 0.0% | 84 ms |
| Kannada, Whisper Small INT8 | 0/3 | 125.0% | 134.3% | 12,022 ms |
| Kannada, installed IndicConformer INT8 | 1/3 | 36.1% | 7.6% | 140 ms |

The Hindi candidate weights and shared tokens were pinned to `dc0fe4b4f11c4970a76b2935a4191d7da3d128cc`. The 197,595,593-byte ONNX file's SHA-256 verified as `915c71e04dd7e5378a4057fdebb252b3a587188e4e99db6d7ce0909ad5ad05fa`. The shared token file was checked against its upstream Git object identity. The source card lists the AI4Bharat origin and Apache-2.0 export. The experiment ran CPU-only in the ignored QA workspace and never joined production routing. [Pinned export](https://huggingface.co/NiranjanYadavIN/indicconformer-sherpa-onnx/tree/dc0fe4b4f11c4970a76b2935a4191d7da3d128cc)

No model was replaced. Three examples per language leave real speech, numerical slot fidelity, false accepts, noise and code switching unmeasured for the Hindi candidate. The prior saved Hindi fixture passed 5/5 again on the current runtime. See the earlier matched Whisper versus Kannada CTC table in [MULTILINGUAL_TEST_REPORT.md](MULTILINGUAL_TEST_REPORT.md) and the latest [command-level results](VOICE_COMMAND_ACCURACY_REPORT.md).
