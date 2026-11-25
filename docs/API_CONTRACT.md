# AURA EDGE — API Contract & Interface Specification

> **Current Version:** Phase 0 + Phase 1 + Phase 1.5 + Phase 2 + Phase 3 (Memory, Scheduler, Routines, Notifications)  
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

## 3. Phase 1 / 1.5 Voice, Multilingual Intent & Compute Endpoints

### 3.1 Voice Pipeline Status (`GET /api/v1/voice/status`)
Returns active FSM state, STT/VAD/TTS status, compute placement, hardware devices, and session/recognition settings:
```json
{
  "active_state": "IDLE",
  "active_turn_id": null,
  "stt_engine": "faster-whisper",
  "stt_model": "small",
  "stt_kannada_specialist": "indic-conformer-kn",
  "stt_recognition_mode": "multilingual",
  "stt_language": "auto",
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
  "audio_route_mode": "normal",
  "mic_muted": false,
  "conversation_active": false,
  "conversation_mode": "idle",
  "history_length": 0
}
```

### 3.2 Language & Recognition Mode Selection (`PUT /api/v1/voice/language`)
- **Request Body:** `{"language": "auto"|"en"|"hi"|"kn", "recognition_mode": "multilingual"|"strict"|null}`
- **Response (`200`):**
  ```json
  {
    "session_language": "kn",
    "resolved_language": "kn",
    "recognition_mode": "multilingual",
    "stt_language": "auto"
  }
  ```
- **Contract Note:** `session_language` controls the **preferred reply language** (`"auto"` replies in the detected spoken language). In `"multilingual"` mode (default), STT detects spoken language independently across EN/HI/KN so English or Hindi speech still executes and replies in Kannada when `session_language="kn"`. `"strict"` mode explicitly constrains single-language specialist decoding when `session_language != "auto"`.

### 3.3 Push-To-Talk & Audio Upload Transcription
- `POST /api/v1/voice/mic/start` (alias `/voice/ptt/start`) — Optional `{"language": "auto"|"en"|"hi"|"kn"}` -> `{"started": true, "turn_id": "...", "state": "LISTENING", "language": "..."}` (`503` if no mic).
- `POST /api/v1/voice/mic/stop` (alias `/voice/ptt/stop`) — Optional `{"speak": true}` -> `ProcessTurnOutput`.
- `POST /api/v1/voice/transcribe` (`multipart/form-data`) — Fields: `file` (`.wav`), `language` (`"auto"|"en"|"hi"|"kn"`), `recognition_mode` (`"multilingual"|"strict"|null`), `speak` (`bool = false`), `allow_llm_fallback` (`bool = true`) -> `ProcessTurnOutput` (`503` if offline STT weights missing).

### 3.4 Process User Command (`POST /api/v1/intent/process`)
- **Request Body:**
  ```json
  {
    "text": "पांच मिनट का टाइमर लगाओ",
    "language": null,
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
    "route_category": "tool",
    "confidence": 0.98,
    "intent_source": "hassil_fast",
    "detected_language": "hi",
    "preferred_reply_language": "auto",
    "recognition_mode": "multilingual",
    "language_probability": 0.9902,
    "transcript_confidence": 0.5347,
    "stt_model": "small",
    "slots": {"duration_seconds": 300},
    "response_text": "5 मिनट का टाइमर सेट कर दिया गया है।",
    "spoken_text": "5 मिनट का टाइमर सेट कर दिया गया है।",
    "response_language": "hi",
    "action_executed": true,
    "outcome": "success",
    "status": "verified",
    "state": "IDLE",
    "data": {"timer": {"id": "...", "duration_seconds": 300, "status": "active", "language": "hi"}},
    "latency_ms": 18.4,
    "stt_ms": null,
    "router_ms": 1.2,
    "llm_ttft_ms": null,
    "llm_ms": null,
    "tool_ms": 16.5,
    "tts_ms": null,
    "audio_startup_ms": null,
    "playback_ms": null,
    "ttfa_ms": null,
    "tts_cache_hit": false,
    "stage_timestamps": {
      "speech_end": null,
      "stt_start": null,
      "stt_completion": null,
      "routing_completion": 1024.51,
      "tool_completion": 1041.01,
      "llm_first_token": null,
      "first_valid_speech_chunk": null,
      "tts_completion": null,
      "first_non_silent_output_callback": null,
      "playback_completion": null
    },
    "time_to_first_text_ms": 17.7,
    "time_to_first_synthesized_audio_ms": null,
    "time_to_first_audio_output_ms": null,
    "playback_duration_ms": null,
    "total_turn_duration_ms": 18.4,
    "error": null,
    "transcript": "पांच मिनट का टाइमर लगाओ"
  }
  ```
- **Outcome & Action Semantics:**
  - `outcome` is strictly one of `"success" | "clarification" | "refusal" | "cancelled" | "runtime_failure"`.
  - `route_category` is one of `"tool" | "conversation" | "greeting" | "language_switch" | "clarification" | "refusal" | "silence" | "cancellation" | "error"`.
  - `action_executed` is `true` **only** when a verified tool (`DateTimeTool`, `TimerManager`, `SmartHomeService`) succeeded (or a completed action was preserved on cancellation); greetings, conversations, clarifications, refusals, and silent/noise turns always return `action_executed: false`.
  - `spoken_text` strictly equals the validated text actually queued to TTS (`""` if muted/`speak=false`/silence).
  - `language_probability` and `transcript_confidence` are `float | null` (never fabricated `1.0` for CTC or unverified turns).
  - `stage_timestamps` values are monotonic `time.perf_counter()` seconds (`float | null`).

### 3.5 Offline Speech Synthesis, Cancel & Compute Mode
- `POST /api/v1/voice/synthesize` — Body `{"text": "ಸಮಯ ಮುಗಿದಿದೆ", "language": "kn", "speed": 1.0}` (`speed` in `0.5..2.0`) -> `audio/wav` + headers `X-Aura-TTS-Backend`, `X-Aura-TTS-Model`, `X-Aura-TTS-Cached`, `X-Aura-TTS-Latency-Ms`.
- `POST /api/v1/voice/cancel` — Body `{"turn_id": null}` -> `{"cancelled": bool, "turn_id": str|null, "completed_actions": [], "completed_actions_preserved": true}`.
- `PUT /api/v1/voice/compute-mode` — Body `{"compute_mode": "auto"|"cpu"|"gpu"}` -> `ComputePlacement`.

### 3.6 Continuous Conversation & History Endpoints (Phase 1.5+)
- `POST /api/v1/voice/conversation/start` — Body `{"language": "auto"|"en"|"hi"|"kn"|null, "speak": true, "mode": "continuous"|"ptt", "silence_timeout_sec": 60.0}`.
- `POST /api/v1/voice/conversation/stop` -> Stops continuous listening, playback, and active turn cleanly.
- `PUT /api/v1/voice/mic/mute` — Body `{"muted": bool}`.
- `GET /api/v1/conversation/history` -> `{"count": int, "messages": ConversationMessage[]}`.
- `DELETE /api/v1/conversation/history` -> `{"cleared": int}`.
- `POST /api/v1/voice/prewarm` — Body `{"prewarm_ollama": true}`.

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
| `assistant_state_change` | `{"state": AssistantState, "turn_id": string\|null, "message"?: string\|null, "language": "auto"\|"en"\|"hi"\|"kn", "recognition_mode"?: "multilingual"\|"strict"}` *(initial connect snapshot includes `recognition_mode`)* |
| `audio_level` | `{"rms_db": number, "peak": number}` |
| `transcript_ready` | `{"turn_id": string, "transcript": string, "language": "en"\|"hi"\|"kn", "preferred_reply_language": "auto"\|"en"\|"hi"\|"kn", "recognition_mode": "multilingual"\|"strict", "language_probability": number\|null, "transcript_confidence": number\|null, "model_size": string\|null, "stt_ms": number}` |
| `response_text_delta` | `{"turn_id": string, "token": string, "language": "en"\|"hi"\|"kn"}` *(provisional tokens; final `response_completed` is authoritative)* |
| `response_completed` | `{"turn_id": string, "outcome": TurnOutcome, "status": string, "action_executed": bool, "intent": string, "route_category": string, "language": "en"\|"hi"\|"kn", "detected_language": "en"\|"hi"\|"kn", "preferred_reply_language": "auto"\|"en"\|"hi"\|"kn", "response_text": string, "spoken_text": string, "silent": bool, "timings": TurnTimings, "stage_timestamps": StageTimestamps, "llm_ttft_ms"?: number\|null, "tts_ms"?: number\|null, "audio_startup_ms"?: number\|null, "playback_ms"?: number\|null, "ttfa_ms"?: number\|null, "rejection_reason"?: string, "error"?: string}` |
| `conversation_mode_change` | `{"active": bool, "mode": "continuous"\|"ptt"\|"idle", "language"?: string, "reason"?: string}` |
| `mic_mute_change` | `{"muted": bool, "route_mode": string}` |
| `conversation_history_updated` | `{"count": number, "turn_id"?: string}` |
| `timer_updated` | `{"timer": TimerView}` |
| `timer_completed` | `{"timer": TimerView}` |
| `timer_missed` | `{"timer": TimerView}` |
| `action_verified` | `{"turn_id": string, "action": string, "status": string, "success": bool, "data": object}` |
| `device_state_changed` | `{"device": SmartHomeDeviceView, "context_id": string, "source": "voice"\|"ui"\|"routine"\|"sim"\|"schedule"\|"event_routine"}` |
| `device_availability_changed` | `{"device_id": string, "available": bool}` |
| `activity_completed` | `{"device": SmartHomeDeviceView}` |
| `simulated_sensor_changed` | `{"device": SmartHomeDeviceView, "is_simulated": true}` |
| `routine_completed` | `{"routine_id": string, "context_id": string, "status": "verified"\|"partial"\|"failed", "results": object[]}` |
| `routine_updated` | `{"action": "created"\|"updated"\|"deleted", "routine"?: RoutineView, "routine_id"?: string}` |
| `memory_updated` | `{"action": "created"\|"updated"\|"settings_updated", "item"?: MemoryItemView, "settings"?: MemorySettings}` |
| `memory_deleted` | `{"id"?: string, "profile_id"?: string, "cleared_all": bool, "deleted_count"?: int}` |
| `reminder_created` | `{"reminder": ScheduledJobView}` |
| `reminder_due` | `{"reminder": ScheduledJobView, "execution": JobExecutionView}` |
| `schedule_updated` | `{"action": "created"\|"updated"\|"paused"\|"resumed"\|"cancelled"\|"executed"\|"missed_recovered", "schedule": ScheduledJobView}` |
| `schedule_executed` | `{"schedule": ScheduledJobView, "execution": JobExecutionView}` |
| `schedule_failed` | `{"schedule": ScheduledJobView, "execution"?: JobExecutionView, "error": string}` |
| `notification_created` | `{"notification": NotificationView, "unread_count": int, "visual_enabled": bool}` |
| `notification_updated` | `{"notification"?: NotificationView, "action"?: "read_all", "updated_count"?: int, "unread_count": int}` |
| `clarification_required` | `{"turn_id": string, "language": "en"\|"hi"\|"kn", "message": string, "reason"?: string}` |
| `error` | `{"code"?: string, "turn_id"?: string, "message": string}` |

### Ordering Guarantees for `response_completed`
1. **Single Terminal Emission:** Every valid turn (tools, greetings, language switches, conversations, clarifications, refusals, cancellations, and silent R4 rejections) emits `response_completed` **once** (runtime failures emit `error`).
2. **Post-Playback Timing:** `response_completed` is broadcast **after** `_maybe_speak()` / `speak_sentence_stream()` finishes and **before** `conversation_history_updated`, ensuring `timings`, `stage_timestamps`, `spoken_text`, and `response_text` are complete and synchronized.
3. **Silent Ignored Turns:** When the R4 gate rejects pure silence or background noise (`status="ignored_silence"`), `response_completed` carries `"silent": true`, `"response_text": ""`, `"spoken_text": ""`, and `"action_executed": false` so UI clients do not render empty assistant bubbles.

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

## 6. Phase 2 & 3 Persistent Smart-Home Simulator Endpoints (`/api/v1/smart-home`)

- `GET /api/v1/smart-home/rooms` -> Room list & device counts (`living_room`, `bedroom`, `kitchen`, `study`, `utility_room`).
- `GET /api/v1/smart-home/devices?room=...` -> All persistent `SmartHomeDevice` records (`19` seeded devices).
- `GET /api/v1/smart-home/devices/{device_id}` -> Single device (`404` if missing).
- `POST /api/v1/smart-home/devices/{device_id}/command` — Body `{"action": str, "value": any, "source": "ui"|"voice"|"routine"|"simulator"|"schedule"|"event_routine", "context_id": null}` (`200` verified, `422` capability rejection, `409` unavailable).
- `GET /api/v1/smart-home/activities` -> `{"running": DeviceView[], "scheduled": ScheduledJobView[], "completed": DeviceView[]}`.
- `GET /api/v1/smart-home/history?device_id=...&limit=100` -> State change audit history.
- `GET /api/v1/smart-home/energy` -> Estimated real-time simulated energy summary.
- `GET /api/v1/smart-home/routines` -> Lists built-in (`morning`, `night`, `study`, `away`) + custom routines with `triggers`, `conditions`, `actions`.
- `POST /api/v1/smart-home/routines` (`201`) — Create custom routine (`name`, `actions`, optional `id`, `description`, `aliases`, `enabled`, `triggers`, `conditions`). Pre-validates every device capability (`422` if invalid/cyclic).
- `GET /api/v1/smart-home/routines/{routine_id}` -> Single routine (`404` if missing).
- `PATCH /api/v1/smart-home/routines/{routine_id}` -> Edit name, aliases, enabled, triggers, conditions, or actions.
- `DELETE /api/v1/smart-home/routines/{routine_id}` -> Delete custom routine (`409` for built-in routines).
- `POST /api/v1/smart-home/routines/{routine_id}/run?check_conditions=true` -> Returns `{"routine_id", "context_id", "status": "verified"|"partial"|"failed"|"skipped_condition", "results": [...]}`.
- `PUT /api/v1/smart-home/simulator/devices/{device_id}/availability` — Toggle device online/offline availability (emits persistent notification).
- `POST /api/v1/smart-home/simulator/devices/{device_id}/complete` — Fast-forward running appliance cycle to completed (emits persistent notification).
- `POST /api/v1/smart-home/simulator/devices/{device_id}/sensor` — Inject temperature/humidity readings (`422` if not writable; evaluates `sensor_threshold` routines).

---

## 7. Phase 3 Persistent Personal Memory Endpoints (`/api/v1/memory`)

- `GET /api/v1/memory?category=...&enabled_only=true&limit=100` -> `{"memory_enabled": bool, "count": int, "items": MemoryItemView[]}`.
- `POST /api/v1/memory` (`201`) — Body `{"original_text", "category"?, "canonical_key"?, "structured_value"?, "language"?: "en"|"hi"|"kn", "provenance"?: "api", "enabled"?: true, "expires_at_utc"?: null}` -> `{"status": "verified", "created": bool, "item": MemoryItemView}` (`409` if disabled in settings, `422` if safety/capability validation fails).
- `GET /api/v1/memory/search?q=...&category=...&limit=8&use_semantic=true` -> `{"memory_enabled": bool, "retrieval_method": "exact"|"keyword"|"semantic"|"hybrid_keyword_semantic"|"disabled"|"none", "items": MemoryItemView[]}`.
- `GET /api/v1/memory/settings` -> `MemorySettings`.
- `PUT /api/v1/memory/settings` — Body `{"memory_enabled"?, "auto_save_preferences"?, "persist_conversation_history"?, "retention_days"?, "clear_retention_days"?, "semantic_search_enabled"?}`.
- `GET /api/v1/memory/{memory_id}`, `PATCH /api/v1/memory/{memory_id}`, `DELETE /api/v1/memory/{memory_id}`, `DELETE /api/v1/memory` (clear all).

---

## 8. Phase 3 Reminders & Persistent Scheduler Endpoints (`/api/v1`)

### Reminders
- `GET /api/v1/reminders?include_closed=false&limit=100` -> `ScheduledJobView[]`.
- `POST /api/v1/reminders` (`201`) — Body `{"message", "title"?, "run_at_utc", "timezone"?: "Asia/Kolkata", "recurrence"?, "language"?: "en", "missed_policy"?: "notify_missed"}` -> `{"status": "verified", "reminder": ScheduledJobView}`.
- `PATCH /api/v1/reminders/{job_id}` & `DELETE /api/v1/reminders/{job_id}`.

### Schedules
- `GET /api/v1/schedules?job_type=...&include_closed=false&limit=100` -> `ScheduledJobView[]`.
- `POST /api/v1/schedules` (`201`) — Body `{"job_type": "device_action"|"routine"|"reminder", "run_at_utc", "timezone"?, "recurrence"?, "missed_policy"?, "device_id"?, "action"?, "value"?, "routine_id"?, "message"?, "title"?}` -> `{"status": "verified", "schedule": ScheduledJobView}`.
- `GET /api/v1/schedules/{job_id}`, `PATCH /api/v1/schedules/{job_id}`, `DELETE /api/v1/schedules/{job_id}`.
- `POST /api/v1/schedules/{job_id}/pause` & `POST /api/v1/schedules/{job_id}/resume`.
- `GET /api/v1/schedules/{job_id}/executions?limit=50` -> `JobExecutionView[]`.

---

## 9. Phase 3 Proactive Notifications Endpoints (`/api/v1/notifications`)

- `GET /api/v1/notifications?unread_only=false&notification_type=...&include_dismissed=false&limit=50` -> `{"unread_count": int, "count": int, "items": NotificationView[]}`.
- `GET /api/v1/notifications/unread-count` -> `{"unread_count": int}`.
- `POST /api/v1/notifications/{notification_id}/read` -> `{"status": "verified", "notification": NotificationView, "unread_count": int}`.
- `POST /api/v1/notifications/read-all` -> `{"status": "verified", "updated_count": int, "unread_count": int}`.
- `POST /api/v1/notifications/{notification_id}/dismiss` -> `{"status": "verified", "notification": NotificationView, "unread_count": int}`.
- `GET /api/v1/notifications/preferences` & `PUT /api/v1/notifications/preferences` — `{"visual_enabled", "spoken_enabled", "quiet_hours_enabled", "quiet_hours_start", "quiet_hours_end", "timezone_name", "updated_at_utc"}`.

---

## 10. Phase 4 Personal Secretary Endpoints (`/api/v1`)

### Tasks
- `GET /api/v1/tasks?status=...&category=...` -> `{"items": TaskView[], "count": int}`.
- `POST /api/v1/tasks` (`201`) — Body `{"title", "description"?, "priority"?: "low"|"normal"|"high"|"urgent", "category"?, "due_at_utc"?, "create_reminder"?: true, "language"?: "en"|"hi"|"kn"}` -> `{"task": TaskView}`.
- `PATCH /api/v1/tasks/{task_id}`, `POST /api/v1/tasks/{task_id}/complete`, `DELETE /api/v1/tasks/{task_id}`.

### Offline Calendar & Daily Briefing
- `GET /api/v1/calendar/events?range_kind=today|tomorrow|upcoming` -> `{"items": CalendarEventView[], "count": int}`.
- `POST /api/v1/calendar/events` (`201`) — Body `{"title", "start_at_utc", "end_at_utc"?, "description"?, "location"?, "all_day"?, "timezone_name"?, "recurrence"?, "reminder_minutes_before"?, "language"?}` -> `{"event": CalendarEventView, "conflicts": CalendarEventView[]}`.
- `PATCH /api/v1/calendar/events/{event_id}`, `DELETE /api/v1/calendar/events/{event_id}`.
- `GET /api/v1/briefing?language=en|hi|kn` -> Grounded live PostgreSQL snapshot (`events_today`, `pending_tasks`, `upcoming_reminders`, `scheduled_device_actions`, `unread_notifications`, `low_stock_items`, `parcels_due`, `counts`, `summary_text`).

### Groceries, Inventory & Offline Recipes
- `GET/POST /api/v1/groceries/list`, `PATCH/DELETE /api/v1/groceries/list/{item_id}`.
- `GET/POST /api/v1/groceries/inventory`, `DELETE /api/v1/groceries/inventory/{item_id}`.
- `GET /api/v1/groceries/missing` -> Low-stock/empty inventory items.
- `GET /api/v1/recipes/suggest?q=...` -> Seeded offline recipes (`8` defaults) with `available_ingredients`, `missing_ingredients`, `can_make_now`, `match_ratio`.

### Expenses (Exact Integer Paise) & Cached Parcels
- `GET/POST /api/v1/expenses`, `DELETE /api/v1/expenses/{expense_id}`.
- `GET /api/v1/expenses/summary?period=day|month|all` -> SQL `SUM(amount_paise)` total & category breakdown (`total_paise`, `total_inr`, `by_category`).
- `POST /api/v1/expenses/import-csv` — Body `{"filename", "csv_content"}` with SHA-256 file + row deduplication.
- `GET/POST /api/v1/parcels`, `PATCH/DELETE /api/v1/parcels/{parcel_id}` — Locally cached/manual records with `source`, `is_sample`, `last_updated_at_utc`, and `offline_provenance`.
- **Additional `WS /api/v1/ws/live` events:** `task_updated`, `calendar_updated`, `grocery_updated`, `expense_updated`, `parcel_updated`.
