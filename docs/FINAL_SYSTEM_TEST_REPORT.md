# AURA EDGE system verification

Windows 11, November 26, 2026. Tests used the integration checkout on the i7-13650HX laptop with 16 GB RAM and an RTX 3050 Laptop GPU. Tool and appliance tests used disposable PostgreSQL databases. The real desktop service was restarted while idle. Its dashboard returned HTTP 200 and readiness reported a connected database in strict-offline mode.

## Build and startup

The release code is local commit `fa0e4ba` on `codex/aura-stabilization`. The local dashboard and simulator are in `b20bdca`. The original checkout has main at `a0e3adb` and feat/backend-intelligence at `856cc6f`. From PowerShell on the prepared laptop run

```powershell
Set-Location 'C:\Users\ariha\local-ai-lab\phase4-agent\AURA-EDGE'
./scripts/start-aura-desktop.ps1
```

Open the [local browser dashboard](http://127.0.0.1:8000/app/) or expand the pet. The [desktop guide](DESKTOP_USER_GUIDE.md) includes environment setup and shutdown. The [judge guide](JUDGE_DEMO_GUIDE.md) lists the live walkthrough.

## Verified behavior

| Check | Result | Evidence |
| --- | --- | --- |
| Backend regression suite | 236/236 passed in 596 seconds with required PostgreSQL and installed local models | [Test run summary](../qa/results/final-regression.json) |
| Frontend | Production build passed and all seven tests passed, including a shared-state simulator switch | Frontend test suite and [simulator guide](SIMULATOR_GUIDE.md) |
| Windows companion | Native smoke traversed nine pages, captured 13 mic frames, exercised Stop and mute, and received backend notifications and state updates | [Mini island](../qa/screenshots/mini-island.png) |
| Simulator | 42/42 inventory, device and observation checks across 19 devices in five rooms | [State checks](../qa/results/final-devices.json) and [browser screenshot](../qa/screenshots/simulator.png) |
| Tool execution | 14/14 malformed timers, guarded device requests, compound and context checks | [Expected and actual readbacks](../qa/results/final-product.json) |
| Automatic language and routing | 66/66 typed and 21/35 audio checks passed. All 11 voiced English cases returned English. Two additional English-group checks covered silence and noise. All 14 missed cases reported no action | [Complete release corpus](../qa/results/final-acceptance.json) |
| Strict Hindi speech | First run 8/8, fresh release run 6/8. The two failures concerned the bedroom and the requested temperature | [Latest transcripts and readbacks](../qa/results/final-product.json) |
| Local factual questions | 8/8 rubric checks across conversation, arithmetic, weather limitations and capabilities | [Questions and replies](../qa/results/final-factual.json) |
| Controlled acoustic interference | 8/15 speech cases passed, plus one safe fan-only rejection. No failed case executed an action | [Per-condition results](../qa/results/final-noise.json) |
| Playback and latency | 36/36 trials reached a non-silent callback and completed. In Auto, warm median first audio took 385 ms for the recorded device request and 10.4 s for the typed general question | [Measurements](CPU_GPU_BENCHMARK_REPORT.md) |

Earlier issues included an English question answered in Kannada on the physical microphone, an attempt that never reached Listening, unverified model-generated confirmations, and a compound request that created only its reminder. On the release, response language and claims are validated before presentation. Device and reminder results have separate readbacks. A live idle-status check returned automatic input and reply selection, English as the resolved language, an unmuted microphone and a detected speaker. That check cannot identify what the earlier microphone attempt captured. [Voice results](VOICE_ACCURACY_REPORT.md) · [Routing fixes](AGENTIC_INTELLIGENCE_REPORT.md)

## Open acceptance

The physical English reply, audible continuity, live multilingual recognition and disconnected cold restart still require an operator. Strict-offline execution was exercised with networking available to the host. The automatic speech corpus includes missed requests, especially mixed speech. The number of sampled expressions and the pattern-based claim guard limit conclusions about false confirmations. Record physical results in the [testing TODO](TESTING_TODO.md). [Problem statement assessment](PROBLEM_STATEMENT_COMPLIANCE.md) · [Known limitations](KNOWN_LIMITATIONS.md)
