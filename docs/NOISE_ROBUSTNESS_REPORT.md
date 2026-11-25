# Noise robustness

PortAudio reports a USB audio input with one channel, a USB stereo output, a 44.1 kHz default device rate and explicit support for the application's 16 kHz mono INT16 capture request. The capture callback reports driver flags and uses a bounded queue that can discard its oldest frame when full. The existing 512-sample frames are 32 ms at 16 kHz. Silero starts with a 0.45 configured threshold, includes pre-roll, estimates a noise floor, and ends after the configured 1.2 seconds of trailing silence. The capture and recognition tests cover quiet speech, clipping-related normalization and silence rejection. No real sample was recorded to disk. Physical clip amplitude, packet loss, room response and pre-roll timing for the user's failed command are unavailable.

The matched noise probe used five offline synthesized commands, one each for English, Hindi, Kannada, Hinglish and Kanglish. Each clip was resampled once to mono 16 kHz and reused for all conditions. Noise models were synthetic hum plus broadband fan-like noise and short keyboard-like transients. The reported word error rates are means across the five clips and include failed recognition. Noise-only is separate.

| Condition | Verified outcomes | Mean WER |
| --- | ---: | ---: |
| Clean, after common resampling | 2/5 | 48.7% |
| Quiet, 24 dB attenuation | 2/5 | 52.7% |
| Fan at 20 dB SNR | 2/5 | 46.2% |
| Fan at 10 dB SNR | 2/5 | 59.3% |
| Fan at 5 dB SNR | 2/5 | 67.0% |
| Keyboard at 10 dB SNR | 3/5 | 46.2% |
| Fan alone | 1/1 safe rejection | — |

No failed noisy trial verified a device action. The kitchen and bedroom simulation state was isolated from personal data. Three clean utterances in this small cohort already failed, so noisy-case differences cannot be attributed solely to background sound. The WAV endpoint exercises recognition and the quality gate. Physical capture/VAD and uploaded audio use different paths. [Voice command accuracy](VOICE_COMMAND_ACCURACY_REPORT.md)

The application keeps the measured Silero/AGC settings. RNNoise and DeepFilterNet were assessed from their project requirements. No compatible existing Windows noise-suppression runtime was found in this project, so no denoiser A/B measurement or performance claim is made. Nearby speech, actual competition fan noise and room reverberation remain for live acceptance. [RNNoise](https://github.com/xiph/rnnoise) · [DeepFilterNet](https://github.com/Rikorose/DeepFilterNet)
