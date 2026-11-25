# Voice command accuracy

The release gate uses expected intent plus execution and state readback. Misrecognized commands can safely result in clarification or refusal. Such outcomes count as misses for eligible commands.

| Evidence | Result |
| --- | --- |
| Baseline typed corpus, five language groups | 66/66 |
| Baseline WAV corpus, five language groups and non-speech | 21/35 |
| Baseline explicit typed intent and verified result | 53/53 each |
| Baseline explicit WAV intent and verified result | 18/32 each |
| Baseline simulator commands and inventory checks | 42/42 |
| Targeted English misclassification | Reproduced with recognition results at the turn boundary. Verified deterministic reroute for a strong clock query only. |
| Noisy upload cohort | 13/30 command outcomes and 1/1 safe rejection for the fan-only condition. Zero failed cases reported execution. |
| First real English microphone attempt | Incorrect Kannada reply. Playback was audible. |
| Final backend suite | 209/209 |
| Saved Hindi fixture repeat | 5/5, CPU recognition with Kannada reply |
| Post-fix desktop activation | Talk entered Listening; Stop returned Ready. Human command recognition remains open. |

The retry is specific to short manual capture. It is expected to leave independent WAV upload results unchanged. The known previous intermittent Hindi fixture is a separate repeatability check, not an accuracy estimate for Hindi. The user-reported speech was never used as training or recorded fixture data. [Baseline metrics](VOICE_ACCURACY_REPORT.md) · [Noise conditions](NOISE_ROBUSTNESS_REPORT.md) · [Multilingual acceptance](MULTILINGUAL_ACCEPTANCE_REPORT.md)
