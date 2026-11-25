# Third-Party Software, Models & Provenance Inventory

> **License Separation Policy:** Runtime library licenses (e.g., Apache-2.0 / MIT) are tracked separately from downloaded model weights, voice datasets, and evaluation corpora.

## 1. Verified Python Runtime & Dev Packages (`.venv`, Python 3.13.2)

| Package | Installed Version | License Metadata | Phase / Role |
|---|---|---|---|
| `fastapi` | `0.165.0` | MIT | Phase 0+ Web API framework |
| `uvicorn` | `0.54.0` | BSD-3-Clause | Phase 0+ ASGI HTTP server |
| `pydantic` | `2.14.4` | MIT | Typed validation |
| `pydantic-settings` | `2.16.3` | MIT | Environment configuration |
| `sqlalchemy` | `2.1.9` | MIT | ORM and DB abstraction |
| `alembic` | `1.20.1` | MIT | Reversible schema migrations |
| `psycopg` / `psycopg-binary` | `3.3.9` | LGPL-3.0-only | PostgreSQL 16 driver |
| `pgvector` | `0.5.1` | MIT | PostgreSQL `Vector` column support |
| `httpx` | `0.28.1` | BSD-3-Clause | HTTP client wrapped by `NetworkPolicyEnforcer` |
| `psutil` | `7.2.2` | BSD-3-Clause | Host CPU/RAM telemetry |
| `nvidia-ml-py` | `13.615.71` | BSD | NVIDIA NVML GPU/VRAM telemetry |
| `faster-whisper` | `1.2.1` | MIT | Offline STT wrapper |
| `ctranslate2` | `4.9.1` | MIT | CPU `int8` / CUDA `int8_float16` Transformer runtime |
| `sherpa-onnx` / `sherpa-onnx-core` | `1.13.8` | Apache-2.0 (Note: `1.13.8` bundles Piper/espeak-ng frontend; `2.0.3` stripped the frontend requiring external phonemization) | Offline CPU TTS ONNX runtime |
| `onnxruntime` | `1.32.0` | MIT | CPU Silero VAD ONNX inference |
| `sounddevice` | `0.5.6` | MIT | PortAudio mic capture & speaker playback |
| `numpy` | `2.5.5` | BSD-3-Clause / multi | Audio buffer math |
| `scipy` | `1.18.1` | BSD-3-Clause | Polyphase audio resampling |
| `hassil` | `3.13.0` | Apache-2.0 | Multilingual intent template grammar matcher |
| `PyYAML` | `6.0.4` | MIT | Intent YAML parser |
| `python-multipart` | `0.0.34` | Apache-2.0 | FastAPI WAV upload parsing |
| `huggingface-hub` | `2.4.0` | Apache-2.0 | Offline preparation script (`prepare_models.py`) only; disabled at runtime via `HF_HUB_OFFLINE=1` |
| `pytest` / `pytest-asyncio` / `ruff` | `9.2.0` / `1.5.0` / `0.17.6` | MIT / Apache-2.0 / MIT | Testing & linting (dev) |

## 2. Frontend Packages (`frontend/package.json`)

- `react` / `react-dom` `18.3.1`, `tailwindcss` / `@tailwindcss/vite` `4.3.5`, `vite` `6.4.6`, `typescript` `5.9.3`, `vitest` `3.2.7`, `@testing-library/react` `16.3.3` (unchanged in Phase 1).

## 3. Offline Model Weights & Voice Licenses (`models/`, gitignored)

| Model / Voice | Source | Weight / Dataset License | Usage Constraint / Note |
|---|---|---|---|
| `Systran/faster-whisper-base` & `small` | Hugging Face (`Systran`) | **MIT** | Pre-cached locally in `models/stt/`; `local_files_only=True` enforced at runtime. |
| `snakers4/silero-vad` (`silero_vad.onnx`) | GitHub (`snakers4`) | **MIT** | CPU-only VAD in `models/vad/silero_vad.onnx`. |
| `vits-piper-en_US-lessac-medium` | `k2-fsa/sherpa-onnx` release / `rhasspy/piper-voices` | Mixed (Piper MIT / Lessac Blizzard-2013 research-only base) | Review Blizzard-2013 non-commercial clause if redistributing commercially. |
| `vits-piper-hi_IN-rohan-medium` | `k2-fsa/sherpa-onnx` release / `rhasspy/piper-voices` | IIT Madras IndicTTS Hindi dataset license + Lessac base | Verify IITM academic/non-commercial terms before commercial release. |
| `vits-mms-kan` (`facebook/mms-tts-kan` ONNX) | `Symmetris-labs/tts-mms` (`facebook/mms-tts-kan`) | **`CC-BY-NC-4.0`** (Non-Commercial) | Clean CPU Kannada TTS for hackathon/research; **disallowed for commercial distribution**. |
| `qwen3.5:4b` (Ollama manifest) | Ollama local registry | Apache-2.0 (verify model card) | Local inference via `127.0.0.1:11434`. |
| `Windows.Media.SpeechSynthesis` / SAPI5 | Microsoft Windows 11 OS | Proprietary OS built-in | English emergency fallback only (`0` HI, `0` KN offline OS voices). |

## 4. Reference Repositories Inspected Outside Repository (`C:\Users\ariha\Desktop\AURA-EDGE-RESEARCH`)

Kept strictly outside `AURA-EDGE`; zero source code copied verbatim without attribution/review:
- `MahmoudAtieh/lyra` (`17c9787`, MIT) — reverse-engineered pattern ideas only.
- `home-assistant/core` (`80f9e44`, Apache-2.0) — timer restore & intent grammar pattern ideas only.
- `aleostudio/andromeda` (`abd0193`, MIT) — audio ring-buffer & prefetch architecture ideas only.
- `Arihant-57/AURA-V4` (`0a1635c`, MIT) — untouched; read-only audit only.
