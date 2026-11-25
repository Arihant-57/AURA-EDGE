# CPU, GPU and Auto benchmark

Measured on the existing Windows laptop with an i7-13650HX, 15.78 GB usable RAM and an RTX 3050 Laptop with 6 GB VRAM. Each requested mode ran the same deterministic time query, a conversational query about leaf color, and the same English WAV controlling a simulated device. Each workload had one first turn after changing placement and three warm replicates. The backend and Ollama services were already running. All 36 turns passed their outcome checks, produced a non-silent speaker callback and completed playback.

Times are milliseconds. Each item is first turn / warm median. Three warm observations per bucket are too few for a stable p95 estimate.

| Workload and metric | CPU | GPU | Auto |
| --- | ---: | ---: | ---: |
| Time query, router | 0.7 / 0.8 | 0.8 / 0.7 | 0.9 / 0.8 |
| Time query, first output | 166 / 69 | 158 / 85 | 67 / 73 |
| Time query, total | 4,061 / 3,808 | 4,055 / 3,978 | 4,213 / 4,052 |
| Conversation, LLM first token | 13,780 / 3,232 | 3,288 / 3,205 | 3,159 / 3,156 |
| Conversation, first output | 15,631 / 4,894 | 4,975 / 4,941 | 4,823 / 4,796 |
| Conversation, total | 23,233 / 12,495 | 12,572 / 12,545 | 12,421 / 12,404 |
| Recorded device command, STT | 2,292 / 2,312 | 247 / 252 | 241 / 238 |
| Recorded device command, tool | 13.9 / 14.8 | 15.3 / 13.3 | 17.7 / 13.3 |
| Recorded device command, first output | 2,426 / 2,402 | 1,210 / 344 | 327 / 330 |
| Recorded device command, total | 4,930 / 4,902 | 3,694 / 2,823 | 2,802 / 2,809 |

First-output time uses the first non-silent operating-system callback. Total duration includes playback. The raw results include TTS timings. A zero can represent reuse of cached audio. The first CPU conversation was slower than its warm runs, so its latency should not be attributed solely to the compute selector.

| Requested mode | STT | Ollama resident model VRAM on follow-up probe | Host CPU | Host RAM | System VRAM |
| --- | --- | ---: | ---: | ---: | ---: |
| CPU | CPU | 0 bytes | 23.7% | 13.31 GB | 1,168 MB |
| GPU | CUDA | 0 bytes | 20.3% | 13.26 GB | 1,495 MB |
| Auto | CUDA | 0 bytes | 18.0% | 13.60 GB | 1,495 MB |

The resource columns are host snapshots after each batch. VAD and speech synthesis use CPU in all three modes. The compute API's Ollama field describes scheduling policy. A separate `/api/ps` check after a successful conversational turn in each mode reported `qwen3.5:4b` resident with zero VRAM. CUDA materially sped up speech recognition in this replay. Ollama's GPU acceleration was unverified, and the Settings label now describes automatic placement accurately.

In a separate device-command probe, `nvidia-smi` sampled the host roughly every 220 ms during three successful replays per mode. Peak sampled GPU utilization was 4% across 62 samples in CPU, 69% across 12 in GPU, and 92% across 5 in Auto. These are short host-wide observations. The shorter GPU and Auto runs yield fewer samples and the maxima do not rank the modes.

Raw results include the per-turn timings, placement, resource snapshots and GPU probe. No model or compute configuration was changed. Process-level GPU utilization and true cold-boot timing remain unmeasured.
