# Independent benchmark

Measured on backend commit `5cf8a58`, all 36 matched English trials passed their response, non-silent audio callback and playback completion checks. Each requested mode ran one first-after-placement trial and three warm trials for each of three workloads. The clock and general-knowledge question used typed input. The simulated bedroom light command used the same synthesized English waveform in all modes. The measurements were collected on the target i7-13650HX and RTX 3050 laptop while the audit was also running regression tests. These are loaded-host figures, not isolated laboratory timings.

| Mode | Workload | First output median / p95 (ms) | Total median (ms) | STT median (ms) | LLM first token median (ms) |
| --- | --- | ---: | ---: | ---: | ---: |
| CPU | Clock | 241 / 269 | 4,328 | — | — |
| CPU | General question | 12,821 / 15,734 | 21,331 | — | 7,507 |
| CPU | Recorded device | 4,454 / 5,833 | 7,030 | 4,309 | — |
| GPU | Clock | 565 / 927 | 4,145 | — | — |
| GPU | General question | 11,921 / 23,990 | 20,428 | — | 7,490 |
| GPU | Recorded device | 512 / 522 | 3,129 | 334 | — |
| Auto | Clock | 100 / 274 | 4,243 | — | — |
| Auto | General question | 11,040 / 11,397 | 19,574 | — | 5,997 |
| Auto | Recorded device | 334 / 411 | 2,940 | 247 | — |

First output ends at the first non-silent host callback. Total includes playback. The nearest-rank p95 for three warm repetitions equals the maximum and is a weak tail estimate. Per-turn routing, tool and synthesis times are in the JSON. In Auto the warm median device tool time was 18 ms. Several repeated replies used cached synthesis. Uploaded audio has no measured physical end-of-speech timestamp. New process startup on the prepared laptop took 13.742 s to its first successful database readiness poll, including polling and HTTP overhead. True cold boot and cold language-model load were not isolated. [Raw benchmark](../qa/results/audit-benchmark.json)

### Placement and overhead

Whisper used CPU in CPU mode and CUDA in GPU/Auto. The fixed-language Indic models, VAD, TTS and database work use CPU. The compute endpoint describes requested Ollama placement. During the GPU sampling window both Qwen and the embedding model reported zero resident VRAM. The system uses hybrid execution. The post-batch CPU, RAM and system VRAM snapshots were 79.3% / 13.14 GiB / 1,343 MiB for CPU, 82.3% / 13.58 GiB / 1,610 MiB for GPU, and 68.0% / 14.29 GiB / 1,610 MiB for Auto. These are host totals. [Placement and NVIDIA samples](../qa/results/audit-placement-samples.json)

An idle sample of the desktop process tree measured 0.061% of machine CPU and 109.6 MiB of summed working sets over 16.6 s. Peak sampled per-process GPU engine utilization was 0.00066%. Working sets can share pages. Eight GPU observations do not establish the effect of animation during inference or a long idle session. [Pet measurements](../qa/results/audit-desktop-idle.json)

### Previously reported first-audio delay

The historical Auto comparison increased from 4.796 s to 6.666 s for a validated conversational reply. Waiting for a complete, checked response before speech can add delay. The current warm Auto conversational median was 11.040 s, compared with 0.334 s for a verified spoken device request. The first model token itself arrived at a median 5.997 s for the general question. Host load, response length, model residency and cached synthesis differ between trials. This audit cannot attribute the historical 1.87 s change to any one cause. The operational and conversational paths should be presented separately. [Earlier measurements](CPU_GPU_BENCHMARK_REPORT.md)
