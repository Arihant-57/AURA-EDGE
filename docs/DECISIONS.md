# AURA EDGE — Phase 0 Engineering Decisions, Reuse Audit & Open Questions

## Persistent simulator before hardware adapters

Phase 2 uses PostgreSQL-authoritative simulated devices and one validated
dispatcher. Stable IDs and context history were adopted from the research;
volatile topology and publish-only fake success were rejected. Energy is labeled
estimated and simulated. MQTT was deferred because it is not required offline.

## 1. Key Phase 0 Decisions

| ID | Decision | Rationale |
|---|---|---|
| D-01 | Create a clean standalone repository at `C:\Users\ariha\Desktop\AURA-EDGE` and keep `C:\Users\ariha\local-ai-lab\phase4-agent` (AURA V4) 100% untouched. | Prevents regressions in AURA V4, prevents leaking `.env` or personal memory (`aura_memory_and_storage/`), and isolates competition prototype governance until reuse rules are confirmed. |
| D-02 | Enforce PostgreSQL + `pgvector` (`pgvector/pgvector:pg16` bound to `127.0.0.1:5432`) with strict rejection of SQLite URLs in `Settings` and `create_db_engine`. | Honors specification Section 3 & Phase 0 gate 4 ("no SQLite substitution without approval"). |
| D-03 | Enforce connectivity policies centrally in `NetworkPolicyEnforcer` + `PolicyEnforcingTransport` / `AsyncPolicyEnforcingTransport` without external DNS lookups. | Prevents accidental cloud fallbacks, avoids DNS-based leaks during strict offline mode, and keeps `strict_offline` as default. |
| D-04 | Use Vite + React 18 + TypeScript + Tailwind CSS v4 with local system fonts only (`index.html` / `index.css`). | Eliminates remote font/CDN requests during offline operation while matching the target dashboard stack. |

## 2. AURA V4 (`C:\Users\ariha\local-ai-lab\phase4-agent`) Reuse Audit

Inspection performed read-only on 2026-11-25. **No source code, credentials, `.env`, or personal data files were copied into `AURA-EDGE`.**

| AURA V4 Module / Artifact | Observed Findings (Read-Only Inspection) | Reuse Assessment & License / Offline Risk | Clean-Room Alternative in AURA EDGE |
|---|---|---|---|
| Repo license & origin (`README.md`, `git remote`) | MIT badge in `README.md`; `origin` points to `https://github.com/Arihant-57/AURA-V4.git`; local uncommitted state left untouched. | Competition rules on prior personal code reuse are **not yet confirmed** (must verify against official Synapse 2.0 rules before importing any code). | Implement fresh, modular domain-specific modules in `AURA-EDGE` unless organizer approval is documented. |
| `src/aura/core/mic_listener.py` | Uses `faster-whisper` (`base.en` default, `int8` CPU) + English prompt/hotwords (`_WHISPER_INITIAL_PROMPT`, `"Arihant"`, `"Jarvis"`). | English-only default (`base.en`) and user-specific prompts break EN/HI/KN multilingual targets. | Phase 1 fresh speech adapter using multilingual `faster-whisper` (`base` / `small`) with configurable language & compute placement. |
| `src/aura/core/voice.py` | Uses `edge_tts` (cloud Microsoft Edge TTS) with `4.0s` timeout and Windows SAPI (`System.Speech`) offline fallback. | `edge_tts` requires internet and violates `strict_offline`; SAPI lacks reliable Kannada/Hindi neural voices. | Phase 1 local multilingual TTS benchmark (`indic-parler-tts`, Piper voices, or verified offline fallback) strictly wrapped by `NetworkPolicyEnforcer`. |
| `src/aura/core/fast_router.py` | Regex/deterministic command routing + direct Spotify HTTP calls (`api.spotify.com`) and YouTube/Google URL builders. | Mixes deterministic local routing with hardcoded external URLs. | Phase 1–2 typed intent schema + Hassil / multilingual pattern matcher decoupled from network URLs. |
| `src/aura/core/providers.py`, `model_router.py` | Connects to loopback Ollama (`127.0.0.1:11434`), plus optional cloud providers (Groq, Gemini, OpenRouter, GitHub Models). | Cloud routes must remain strictly isolated behind `online_opt_in` + allowlist. | Fresh Phase 1–3 Ollama client routed through `build_http_client(enforcer)` so strict mode physically blocks non-loopback calls. |
| `src/aura/core/personal_memory.py`, `memory.py`, `workspace_connector.py` | Stores `personal_facts.jsonl`, `USER_PROFILE.md`, `calendar_events.json` on disk; IMAP/SMTP/Google Calendar REST calls. | File-based JSONL/JSON stores lack transactional integrity and relational constraints; IMAP/REST needs opt-in online isolation. | Authoritative PostgreSQL + `pgvector` schema via Alembic migrations; opt-in Phase 6 connectors isolated behind `online_opt_in`. |
| `src/aura/core/telemetry.py` | Non-blocking `psutil` + `pynvml` + `nvidia-smi` fallback + Ollama ping. | Good pattern reference; `AURA-EDGE` Phase 0 `hardware.py` implemented cleanly from scratch using `psutil` + `pynvml` + policy-wrapped Ollama check. | Implemented natively in `aura_edge/infrastructure/hardware.py`. |
| `src/aura/island.py`, `island_ui/Island.qml`, `server.py` | PySide6/QML desktop overlay (`232 KB` `server.py`, `238 KB` `overlay.py`). | Monolithic coupling to coding-agent loop; large files hard to audit. | Clean React/TS dashboard (`frontend/`) first; evaluate optional PySide6 island wrapper in Phase 5 if competition rules allow. |
| `src/aura/mcp/desktop_server.py`, `filesystem_server.py`, `jarvis_server.py` | Strict path sandboxing & SSRF guard; `jarvis_server.py` invokes DuckDuckGo web search. | SSRF guard pattern is useful; web search tools disallowed in `strict_offline`. | Phase 4 allowlisted local desktop/file tools respecting `NetworkPolicyEnforcer`. |

## 3. Unresolved Competition Rules & Blockers

1. **Missing Official PDF (`Synapse_2.0_Problem_Statements.pdf`)**: Searched `Downloads`, `Documents`, `Desktop`, and `local-ai-lab`; the file is not present on disk (only `Synapse_2_Permission_and_Expense_Request.docx` exists in `Downloads`). Requirements in `docs/spec/AURA_EDGE_MASTER_CONTEXT.md` (which summarizes Problem Statement 13) were used; exact wording in the PDF should be cross-checked once supplied.
2. **Competition Reuse / Pre-Work Policy**: Confirm with Synapse 2.0 organizers whether pre-downloaded model weights (Hugging Face / Ollama), pre-built UI/boilerplate, or prior repository code reuse are allowed before submission.
3. **Multilingual Models Not Yet Cached**: Only `faster-whisper-base.en` (English) and `all-MiniLM-L6-v2` are cached in `~/.cache/huggingface/hub`, and `qwen3.5:2b` is not pulled in `~/.ollama/models`. Phase 1 will require pulling/preparing multilingual STT/TTS weights and benchmarking `qwen3.5:4b` vs `2b` if desired.

---

## 4. Phase 1 Engineering Decisions (`feat/backend-intelligence`)

| ID | Decision | Rationale |
|---|---|---|
| D-05 | Pin `sherpa-onnx>=1.12.0,<2.0` (`1.13.8`) and synthesize Kannada offline with `vits-mms-kan` (`Symmetris-labs/tts-mms`, `CC-BY-NC-4.0`) using `num_threads=4`, alongside Piper `en_US-lessac-medium` & `hi_IN-rohan-medium` (`MIT`). | `sherpa-onnx 2.0.3` dropped the built-in Piper/espeak-ng frontend; `micahchoo/piper-kn` failed runtime metadata loading. `1.13.8` + `vits-mms-kan` synthesizes non-silent offline speech across all 3 languages (`233 ms`–`1.8 s` KN, `<380 ms` EN/HI warm). |
| D-06 | Select `faster-whisper-small` with `repetition_penalty=1.2`, `no_repeat_ngram_size=3`, `vad_filter=True`, and Windows `torch\lib` CUDA DLL handle caching (`_DLL_DIR_HANDLES`). | `base` mis-scripted Hindi/Kannada into Arabic tokens; `small` eliminates HI script substitution, and anti-loop decoding fixes Kannada token loops (`36.6s -> 13.7s` CPU / `8.4s -> 3.1s` GPU). Note that Kannada human voice evaluation remains open (see `VOICE_MODEL_EVALUATION.md`). |
| D-07 | Keep Silero VAD v5 ONNX (`silero_vad.onnx`) and `TTSRouter` pinned to CPU even when `compute_mode="gpu"`. | Preserves the `6144 MiB` RTX 3050 VRAM budget for `faster-whisper-small` (`int8_float16`) and `qwen3.5:4b` (`num_ctx=4096`), preventing out-of-VRAM context thrashing (`9B` disabled by default). |
| D-08 | Isolate Romanized Hindi `"do"` (`2`) behind Indic language/time-unit guards inside `intelligence/normalizer.py`. | Unconditional `_NUMBER_WORDS["do"] = 2` corrupted English utterances such as `"What can you do?"`. Separating `_ROMAN_INDIC_NUMBER_WORDS` preserves `100%` accuracy on both English and Romanized Hindi timers. |
| D-09 | Enforce atomic timer completion (`UPDATE timers ... WHERE status='active' AND notified_at_utc IS NULL`) in PostgreSQL `TimerRepository`. | Prevents duplicate completion announcements across background watchdog polls and container/app restarts. |
| D-10 | Route Kannada speech (`language="kn"` directly, or `language="auto"` on detected `"kn"`) to CPU INT8 `indic-conformer-kn` (`NiranjanYadavIN/indicconformer-sherpa-onnx`), remove ASCII `({tz_name})` from HI/KN spoken time strings, sanitize non-Kannada Latin chars before `vits-mms-kan`, and set `SherpaOnnxBackend` default `num_threads=8`. | Cuts Kannada STT latency from `20,401 ms` (CPU) / `3,680 ms` (GPU) to `170–671 ms` (`57–93 ms` isolated) while fixing Kannada script accuracy (`100%` pass on 5-turn KN spoken conversation) and eliminating all `Skip unknown character` TTS stderr warnings. |
