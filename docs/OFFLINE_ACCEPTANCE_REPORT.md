# Offline acceptance

The Windows desktop loaded its packaged frontend. The service reported `strict_offline`, its PostgreSQL/pgvector migration resolved locally, and Ollama and all active speech engines used loopback or local model files. The tests set Hugging Face and Transformers offline for model loading. The network-policy tests exercise outbound blocking and loopback access. These findings verify application policy and local execution with the host network available.

The physical isolation run is pending. After saving any work that needs connectivity, turn off Wi-Fi and disconnect Ethernet on the laptop, or run the procedure from an already disconnected session. Keep PostgreSQL and Ollama running locally. Launch with `./scripts/start-aura-desktop.ps1`. Confirm readiness, then use the actual microphone for time/date in English and Hindi or Kannada, a short timer, one explicitly named simulated device, and an unfamiliar request. Confirm the replies were audible and that the displayed device state and timer agree. Restore connectivity and record the outcome separately. The interactive runner covers five read-only phrases and saves outcomes and timings in `.runtime/human-microphone-check.json`.

No adapter settings were changed during automated testing.
