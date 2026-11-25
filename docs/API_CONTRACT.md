# AURA EDGE — API Contract & Interface Specification

## Phase 1.5 conversation additions

- `POST /api/v1/voice/conversation/start` starts continuous hands-free capture.
- `POST /api/v1/voice/conversation/stop` stops the continuous session.
- `PUT /api/v1/voice/mic/mute` accepts `{"muted": true|false}`.
- `GET /api/v1/conversation/history` returns bounded session messages.
- `DELETE /api/v1/conversation/history` clears the current session history.
- `POST /api/v1/voice/prewarm` warms common TTS phrases and optionally local Ollama.

`ProcessTurnOutput.route_category` is one of `tool`, `conversation`, `greeting`,
`language_switch`, `clarification`, or `refusal`. Status `refused` means a policy
or capability refusal; `failed` means a supported operation could not complete
(for example, local Ollama is unavailable).

Additional WebSocket events include `transcript_ready`, `response_text_delta`,
`response_completed`, `conversation_history_updated`, `audio_level`, and
conversation lifecycle events. Clients must render deltas as provisional and
use `response_completed` as the authoritative final response.

> **Current Version:** Phase 0 (Baseline) + Phase 1 (Multilingual Voice, Persistent Timers, 10-State WebSocket FSM)  
> **Base URL:** `http://127.0.0.1:8000/api/v1`  
> **Vite UI Proxy:** `/api/v1` (proxied to `http://127.0.0.1:8000` via `vite.config.ts`)  
> **Security & Connectivity:** Bound strictly to loopback (`127.0.0.1`). `strict_offline` blocks all outbound non-loopback requests.

---

## 1. Standard Conventions

- All JSON requests and responses use `application/json; charset=utf-8`.
- Audio synthesis (`POST /api/v1/voice/synthesize`) returns binary `audio/wav` (`16-bit` mono PCM).
- Audio upload transcription (`POST /api/v1/voice/transcribe`) accepts `multipart/form-data` with a `.wav` `file`.
- Standard FastAPI error payloads return `{"detail": "..."}` (HTTP `400`, `404`, `422`, `503`).

---

## 2. Phase 0 Endpoints (Preserved 100% Compatible)

- `GET /api/v1/health` -> HTTP `200`: `status`, `service`, `version`, `phase`, `connectivity_mode`, `compute_mode`, `bind_host`, `timestamp_utc`.
- `GET /api/v1/readiness` -> HTTP `200` (ready) or `503` (DB/pgvector/Alembic not ready): `ready`, `connectivity`, `database`, `hardware`, `timestamp_utc`.
- `GET /api/v1/policy` -> Active connectivity mode and allowlist summary.
- `POST /api/v1/policy/check` -> Evaluates `{"url": "..."}` against `NetworkPolicyEnforcer` without outbound I/O.

---

## 3. Phase 1 Voice, Multilingual Intent & Compute Endpoints

### 3.1 Voice Pipeline Status
- **Method & Path:** `GET /api/v1/voice/status`
- **Response (HTTP 200):**
  ```json
  {
    "active_state": "IDLE",
    "active_turn_id": null,
    "stt_engine": "faster-whisper",
    "stt_model": "small",
    "stt_kannada_specialist": "indic-conformer-kn",
    "stt_ready": true,
    "stt_device": "cuda",
    "stt_compute_type": "int8_float16",
    "vad_ready": true,
    "tts_engine": "sherpa_onnx",
    "tts_summary": {
      "primary_backend": "sherpa_onnx",
      "voices": {
        "en": {"backend": "sherpa_onnx", "model": "vits-piper-en_US-lessac-medium", "voice": "en_US-lessac-medium"},
        "hi": {"backend": "sherpa_onnx", "model": "vits-piper-hi_IN-rohan-medium", "voice": "hi_IN-rohan-medium"},
        "kn": {"backend": "sherpa_onnx", "model": "vits-mms-kan", "voice": "facebook/mms-tts-kan"}
      },
      "cache_entries": 0,
      "cache_max_entries": 64,
      "cache_hits": 0,
      "cache_misses": 0
    },
    "supported_languages": ["en", "hi", "kn"],
    "session_language": "auto",
    "resolved_language": "en",
    "compute": {
      "requested_mode": "auto",
      "effective_mode": "gpu",
      "stt_device": "cuda",
      "stt_compute_type": "int8_float16",
      "llm_device": "gpu",
      "llm_num_gpu": null,
      "vad_device": "cpu",
      "tts_device": "cpu",
      "cuda_dll_dirs_registered": ["..."],
      "fallback_reason": null
    },
    "mic_detected": true,
    "speaker_detected": true,
    "audio_devices": [],
    "audio_route_mode": "normal"
  }
  ```

### 3.2 Push-To-Talk Microphone Capture
- **Start Mic:** `POST /api/v1/voice/mic/start` (alias `POST /api/v1/voice/ptt/start`)
  - Optional Body: `{"language": "auto" | "en" | "hi" | "kn"}`
  - Response `200`: `{"started": true, "turn_id": "abc123...", "state": "LISTENING", "language": "auto"}`
  - Response `503`: When no usable host microphone is present.
- **Stop Mic & Process:** `POST /api/v1/voice/mic/stop` (alias `POST /api/v1/voice/ptt/stop`)
  - Optional Body: `{"speak": true}`
  - Response `200`: `ProcessTurnOutput` (same schema as `POST /api/v1/intent/process`).

### 3.3 Audio Upload Transcription & Execution
- **Method & Path:** `POST /api/v1/voice/transcribe` (`multipart/form-data`)
- **Fields:** `file` (`.wav`), `language` (`"auto" | "en" | "hi" | "kn"`), `speak` (`bool = false`), `allow_llm_fallback` (`bool = true`)
- **Response (`200`):** `ProcessTurnOutput` (`503` if offline STT model weights are missing).

### 3.4 Process User Command (Text / Intent)
- **Method & Path:** `POST /api/v1/intent/process`
- **Request Body:**
  ```json
  {
    "text": "पांच मिनट का टाइमर लगाओ",
    "language": "auto",
    "source": "text_input",
    "speak": false,
    "allow_llm_fallback": true
  }
  ```
- **Response (`200` — `ProcessTurnOutput`):**
  ```json
  {
    "turn_id": "9f1a2b3c4d5e",
    "intent": "SetTimer",
    "confidence": 0.98,
    "intent_source": "hassil_fast",
    "detected_language": "hi",
    "slots": {"duration_seconds": 300},
    "response_text": "5 मिनट का टाइमर सेट कर दिया गया है।",
    "response_language": "hi",
    "action_executed": true,
    "status": "verified",
    "state": "IDLE",
    "data": {
      "timer": {
        "id": "...",
        "label": null,
        "duration_seconds": 300,
        "remaining_seconds": 300,
        "status": "active",
        "language": "hi"
      }
    },
    "latency_ms": 18.4,
    "stt_ms": null,
    "router_ms": 1.2,
    "llm_ms": null,
    "tool_ms": 16.5,
    "tts_ms": null,
    "ttfa_ms": null,
    "error": null,
    "transcript": "पांच मिनट का टाइमर लगाओ"
  }
  ```

### 3.5 Offline Speech Synthesis
- **Method & Path:** `POST /api/v1/voice/synthesize`
- **Request Body:** `{"text": "ಸಮಯ ಮುಗಿದಿದೆ", "language": "kn", "speed": 1.0}` (`speed` in `0.5..2.0`)
- **Response (`200`):** Binary `audio/wav` stream with headers `X-Aura-TTS-Backend`, `X-Aura-TTS-Model`, `X-Aura-TTS-Cached`, `X-Aura-TTS-Latency-Ms`.

### 3.6 Turn Cancellation, Language & Compute Selection
- `POST /api/v1/voice/cancel` — Body `{"turn_id": null}` -> `{"cancelled": bool, "turn_id": str|null, "completed_actions": [], "completed_actions_preserved": true}`
- `PUT /api/v1/voice/language` — Body `{"language": "auto"|"en"|"hi"|"kn"}` -> `{"session_language": ..., "resolved_language": ...}`
- `PUT /api/v1/voice/compute-mode` — Body `{"compute_mode": "auto"|"cpu"|"gpu"}` -> `ComputePlacement` object reporting requested mode, effective mode, device placements, and `fallback_reason`.

---

## 4. Phase 1 Persistent Timer Endpoints (`/api/v1/timers`)

- `GET /api/v1/timers?include_closed=false` -> `TimerView[]`
- `POST /api/v1/timers` — Body `{"duration_seconds": 300, "label": "tea", "language": "en"}` -> `201 Created` (`{"timer": TimerView, "message": "...", "status": "verified"}`)
- `GET /api/v1/timers/{timer_id}` -> `TimerView`
- `POST /api/v1/timers/{timer_id}/pause?language=en` -> `TimerActionResponse`
- `POST /api/v1/timers/{timer_id}/resume?language=en` -> `TimerActionResponse`
- `DELETE /api/v1/timers/{timer_id}?language=en` -> `TimerActionResponse`

---

## 5. Real-Time WebSocket Interface (`WS /api/v1/ws/live`)

All server frames share `{"type": string, "payload": object, "timestamp_utc": string}`.

| Event `type` | `payload` Schema |
|---|---|
| `assistant_state_change` | `{"state": AssistantState, "turn_id": string\|null, "message": string\|null, "language": "en"\|"hi"\|"kn"}` |
| `audio_level` | `{"rms_db": number, "peak": number}` |
| `timer_updated` | `{"timer": TimerView}` |
| `timer_completed` | `{"timer": TimerView}` |
| `timer_missed` | `{"timer": TimerView}` |
| `action_verified` | `{"turn_id": string, "action": string, "status": string, "success": bool, "data": object}` |
| `clarification_required` | `{"turn_id": string, "language": "en"\|"hi"\|"kn", "message": string}` |
| `error` | `{"code"?: string, "turn_id"?: string, "message": string}` |

### Canonical TypeScript States (`AssistantState`)
```typescript
export type AssistantState =
  | "IDLE"
  | "LISTENING"
  | "TRANSCRIBING"
  | "UNDERSTANDING"
  | "CLARIFYING"
  | "EXECUTING"
  | "VERIFYING"
  | "SPEAKING"
  | "CANCELLED"
  | "ERROR";
```

---

## 6. Reserved Phase 2 Contracts (Not Yet Implemented)
- `GET /api/v1/devices` and `PATCH /api/v1/devices/{device_id}` remain Phase 2 contracts and are **not** active in Phase 1.
