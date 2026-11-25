# Stabilization verification

The runners target a disposable backend on loopback. Use a separate PostgreSQL/pgvector database and point it at the existing local model directory. Use `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`. Never point these write-mode runners at the personal database. The recorded PTT harness additionally checks for the dedicated disposable PostgreSQL port 55434.

```powershell
$py = "$env:USERPROFILE/Desktop/AURA-EDGE/.venv/Scripts/python.exe"
$models = "$env:USERPROFILE/Desktop/AURA-EDGE/models"
& $py -m qa.run_acceptance --port 8017 --kind accuracy --models $models --output .runtime/stabilization-accuracy.json
& $py -m qa.run_acceptance --port 8017 --kind devices --output .runtime/stabilization-devices.json
& $py -m qa.run_acceptance --port 8017 --kind hindi-repeat --models $models --output .runtime/stabilization-hindi-repeat.json
& $py -m qa.run_acceptance --port 8017 --kind benchmark --output .runtime/stabilization-benchmark.json
& $py -m qa.recorded_ptt --study-fan --output .runtime/stabilization-recorded-ptt-fan.json
& $py -m qa.live_acceptance
```

The last command is interactive and uses the running desktop backend by default. It exercises five language groups with read-only time and date requests. Use `--samples 1` for only English. It shows transcripts during the check and saves only outcomes, language/model data and timings. Run the local playback benchmarks and interactive check one at a time.

For follow-up experiments, run `-m qa.noise_acceptance --port 8017 --output .runtime/voice-followup-noise.json` or `-m qa.compare_asr --port 8017 --models $models --output .runtime/voice-followup-asr.json`. The optional `--hindi-dir` points at the separately verified ONNX model and its token file.

The sanitized evidence is in [results/stabilization.json](results/stabilization.json) and [results/voice-engine.json](results/voice-engine.json). Full local runner outputs, regression JUnit XML and smoke logs are retained under the ignored `.runtime` directory. The tracked corpus contains synthetic or public phrases. Human checks retain outcomes only.
