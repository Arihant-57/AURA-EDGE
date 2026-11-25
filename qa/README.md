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

The last command is interactive and uses the running desktop backend by default. It exercises only time and date and stores pass/fail and timings. It does not retain transcripts or recordings. Run the local playback benchmarks and interactive check one at a time.

The sanitized run evidence is in [results/stabilization.json](results/stabilization.json). Full local runner outputs, regression JUnit XML and smoke logs are retained under the ignored `.runtime` directory. The tracked corpus and evidence contain only synthetic or public test phrases.
