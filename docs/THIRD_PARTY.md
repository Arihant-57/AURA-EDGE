# Third-Party Software, Models & Provenance Inventory

## 1. Phase 0 Verified Python Runtime & Dev Packages (`.venv`, Python 3.13.2)

| Package | Version | License Metadata | Role |
|---|---|---|---|
| `fastapi` | `0.165.0` | MIT | Web API framework |
| `uvicorn` | `0.54.0` | BSD-3-Clause | ASGI HTTP server |
| `pydantic` | `2.14.4` | MIT | Typed data validation |
| `pydantic-settings` | `2.16.3` | MIT | Environment configuration |
| `sqlalchemy` | `2.1.9` | MIT | ORM and database abstraction |
| `alembic` | `1.20.1` | MIT | Reversible database migrations |
| `psycopg` / `psycopg-binary` | `3.3.9` | LGPL-3.0-only | PostgreSQL 16 driver |
| `pgvector` | `0.5.1` | MIT | SQLAlchemy `Vector` type integration |
| `httpx` | `0.28.1` | BSD-3-Clause | HTTP client wrapped by `NetworkPolicyEnforcer` |
| `psutil` | `7.2.2` | BSD-3-Clause | Host CPU/RAM telemetry |
| `nvidia-ml-py` | `13.615.71` | BSD | NVIDIA NVML GPU/VRAM telemetry |
| `pytest` | `9.2.0` | MIT | Test runner (dev) |
| `pytest-asyncio` | `1.5.0` | Apache-2.0 | Async test support (dev) |
| `ruff` | `0.17.6` | MIT | Linter (dev) |

## 2. Phase 0 Verified Frontend Packages (`frontend/package.json`)

| Package | Installed Version | Role |
|---|---|---|
| `react` / `react-dom` | `18.3.1` | UI runtime |
| `tailwindcss` / `@tailwindcss/vite` | `4.3.5` | Local utility CSS compilation (no runtime CDN) |
| `vite` / `@vitejs/plugin-react` | `6.4.6` / `4.7.0` | Local dev server and bundler |
| `typescript` | `5.9.3` | Static type checking |
| `vitest` / `@testing-library/react` / `@testing-library/jest-dom` / `jsdom` | `3.2.7` / `16.3.3` / `6.9.1` / `26.1.0` | UI unit/integration tests (dev) |

## 3. Infrastructure & Local Runtimes

- **Docker Engine / Desktop**: `29.8.0` (Desktop CLI v0.4.4, Compose v5.5.1) — local orchestration for PostgreSQL + pgvector container (`pgvector/pgvector:pg16`, PostgreSQL License).
- **Ollama**: `0.40.16` installed at `C:\Users\ariha\AppData\Local\Programs\Ollama\ollama.exe` (MIT; local model manifests present on disk: `qwen3.5:4b`, `qwen3.5:9b`, `qwen2.5-coder:7b`, `nemotron-3-nano:4b`, `nomic-embed-text:latest`). Not started or invoked in Phase 0.

## 4. Candidate Phase 1+ Upstream Sources (Not Yet Imported or Verified for Competition Rules)

| Candidate | Proposed Phase | License / Provenance Check Required Before Use |
|---|---|---|
| `SYSTRAN/faster-whisper` (`base` / `small` multilingual) | Phase 1 | MIT; verify model weights pre-download permission and CPU vs CUDA latency |
| `AI4Bharat/indic-parler-tts` / `OHF-Voice/piper1-gpl` | Phase 1 | Review Apache-2.0 / GPL-3.0 obligations (note GPL implications if bundling Piper GPL binaries) and Kannada/Hindi CPU latency |
| `OHF-Voice/hassil` / `OHF-Voice/intents` | Phase 1–2 | Review Apache-2.0 / CC-BY-4.0 / license compatibility for pattern matching |
| `eclipse-mosquitto` + `eclipse-paho/paho.mqtt.python` | Phase 2 | EPL-2.0 / EDL-1.0 for local LAN MQTT broker & client |
| `agronholm/apscheduler` | Phase 3 | MIT for persistent job scheduling |
| Amazon MASSIVE multilingual dataset | Phase 1/7 | CC-BY-4.0; evaluation/pattern reference only |
