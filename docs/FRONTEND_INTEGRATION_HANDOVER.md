# Frontend Integration Handover (`feat/backend-intelligence` -> `feat/frontend-experience`)

**Date:** 2026-11-25  
**Backend Branch:** `feat/backend-intelligence`  
**Contract Reference:** [`docs/API_CONTRACT.md`](./API_CONTRACT.md)

**Zero Frontend Edits Guarantee:** **No files in `frontend/` or `main` were modified on `feat/backend-intelligence`.**

---

## 1. Compatibility & Breaking-Change Check

- **Phase 0 Endpoints Unchanged:** `/api/v1/health`, `/api/v1/readiness`, `/api/v1/policy`, and `/api/v1/policy/check` remain 100% backward compatible.
- **10-State Uppercase `AssistantState`:** All REST & WS events emit uppercase FSM states:
  `IDLE | LISTENING | TRANSCRIBING | UNDERSTANDING | CLARIFYING | EXECUTING | VERIFYING | SPEAKING | CANCELLED | ERROR`.
- **Decoupled Reply Language vs. STT Recognition Mode:**
  - `PUT /api/v1/voice/language` accepts optional `recognition_mode?: "multilingual" | "strict"` and returns `{ session_language, resolved_language, recognition_mode, stt_language }`.
  - Default `recognition_mode: "multilingual"` detects English/Hindi/Kannada independently from `session_language` (preferred reply language).
- **Authoritative `response_completed` Contract:**
  - `response_completed` is emitted **once** per turn **after** audio playback finishes (and before `conversation_history_updated`).
  - Render `transcript_ready` and `response_text_delta` provisionally, then finalize from `response_completed`.
  - Ignore/do not render an assistant chat bubble when `payload.silent === true` (R4 silence/noise gate rejection).
  - Treat `outcome === "refusal"` (`status === "refused"`) as an intentional capability boundary and `outcome === "runtime_failure"` (`status === "failed"`) as a retryable runtime/model error.
  - Check `action_executed` (`true` only for verified tool/device/timer actions; `false` for conversation/greeting/clarification/refusal).

---

## 2. Live Endpoints Available for Integration

| Feature | Method & Endpoint | Notes for Frontend |
|---|---|---|
| Voice & Hardware Status | `GET /api/v1/voice/status` | Poll on load for `mic_detected`, `speaker_detected`, `stt_ready`, `stt_recognition_mode`, `stt_language`, `stt_kannada_specialist`, `vad_ready`, `tts_summary`, `compute`, `session_language`, `active_state`. |
| Push-to-Talk Start | `POST /api/v1/voice/mic/start` | Optional `{"language": "auto"\|"en"\|"hi"\|"kn"}`. Returns `503` if no mic is connected. |
| Push-to-Talk Stop | `POST /api/v1/voice/mic/stop` | Stops capture, runs STT -> Router/LLM -> Tool -> optional TTS (`{"speak": true}`). Returns `ProcessTurnOutput`. |
| Upload WAV Audio | `POST /api/v1/voice/transcribe` | `multipart/form-data` (`file`, `language`, `recognition_mode`, `speak`, `allow_llm_fallback`). |
| Text Command | `POST /api/v1/intent/process` | `{"text", "language": null\|"auto"\|"en"\|"hi"\|"kn", "source": "text_input", "speak": false}`. Omitting `language` honors `session_language`. |
| Offline TTS Audio | `POST /api/v1/voice/synthesize` | Returns `audio/wav` stream (`en`, `hi`, `kn`) + cache headers (`X-Aura-TTS-Cached`). |
| Cancel Active Turn | `POST /api/v1/voice/cancel` | Immediately aborts mic/playback and transitions FSM to `CANCELLED -> IDLE`. |
| Continuous Mode & Mute | `POST /api/v1/voice/conversation/{start,stop}`, `PUT /api/v1/voice/mic/mute` | Half-duplex VAD continuous listening with acoustic cooldown & sticky user mute. |
| Session History & Prewarm | `GET/DELETE /api/v1/conversation/history`, `POST /api/v1/voice/prewarm` | Inspect/clear bounded session turns (`max 12`) and warm EN/HI/KN TTS + Ollama. |
| Language & Mode Switch | `PUT /api/v1/voice/language` | `{"language": "auto"\|"en"\|"hi"\|"kn", "recognition_mode"?: "multilingual"\|"strict"}`. |
| Compute Mode Switch | `PUT /api/v1/voice/compute-mode` | `{"compute_mode": "auto"\|"cpu"\|"gpu"}` — returns honest `effective_mode` and `fallback_reason`. |
| Persistent Timers | `GET/POST /api/v1/timers`, `POST /api/v1/timers/{id}/{pause,resume}`, `DELETE /api/v1/timers/{id}` | Authoritative PostgreSQL state; survives backend restarts. |
| Smart-Home Simulator | `GET /api/v1/smart-home/{rooms,devices,activities,history,energy,routines}`, `POST /api/v1/smart-home/devices/{id}/command` | Use `/rooms` for navigation, filter `/devices` by room, render controls from `capabilities`, and preserve simulated/estimated labels. |
| Real-Time Events | `WS /api/v1/ws/live` | Initial snapshot includes `recognition_mode`; emits `assistant_state_change`, `audio_level`, `transcript_ready`, `response_text_delta`, `response_completed`, `conversation_mode_change`, `mic_mute_change`, `conversation_history_updated`, `timer_*`, `action_verified`, `device_*`, `activity_completed`, `simulated_sensor_changed`, `routine_completed`, `clarification_required`, `error`. |

---

## 3. Recommended TypeScript Types for `frontend/src/api/types.ts`

```typescript
export type LanguageCode = "en" | "hi" | "kn";
export type RequestedLanguage = "auto" | LanguageCode;
export type RecognitionMode = "multilingual" | "strict";
export type TurnOutcome = "success" | "clarification" | "refusal" | "cancelled" | "runtime_failure";

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

export interface StageTimestamps {
  speech_end: number | null;
  stt_start: number | null;
  stt_completion: number | null;
  routing_completion: number | null;
  tool_completion: number | null;
  llm_first_token: number | null;
  first_valid_speech_chunk: number | null;
  tts_completion: number | null;
  first_non_silent_output_callback: number | null;
  playback_completion: number | null;
}

export interface TurnTimings {
  stt_ms: number | null;
  router_ms: number | null;
  llm_ttft_ms: number | null;
  llm_ms: number | null;
  tool_ms: number | null;
  tts_ms: number | null;
  audio_startup_ms: number | null;
  playback_ms: number | null;
  ttfa_ms: number | null;
  time_to_first_text_ms: number | null;
  time_to_first_synthesized_audio_ms: number | null;
  time_to_first_audio_output_ms: number | null;
  playback_duration_ms: number | null;
  total_turn_duration_ms: number | null;
  latency_ms: number | null;
}

export interface TimerView {
  id: string;
  label: string | null;
  duration_seconds: number;
  remaining_seconds: number;
  status: "active" | "paused" | "completed" | "cancelled" | "missed";
  language: LanguageCode;
  fire_at_utc: string | null;
  created_at_utc: string;
  updated_at_utc: string;
  completed_at_utc: string | null;
}

export interface ProcessTurnOutput {
  turn_id: string;
  intent: string;
  route_category: string;
  confidence: number;
  intent_source: string;
  detected_language: LanguageCode;
  preferred_reply_language: RequestedLanguage;
  recognition_mode: RecognitionMode;
  language_probability: number | null;
  transcript_confidence: number | null;
  stt_model: string | null;
  slots: Record<string, unknown>;
  response_text: string;
  spoken_text: string;
  response_language: LanguageCode;
  action_executed: boolean;
  outcome: TurnOutcome;
  status: "verified" | "failed" | "clarification_required" | "refused" | "cancelled" | "ignored_silence";
  state: AssistantState;
  data: Record<string, unknown>;
  latency_ms: number;
  stt_ms: number | null;
  router_ms: number | null;
  llm_ttft_ms: number | null;
  llm_ms: number | null;
  tool_ms: number | null;
  tts_ms: number | null;
  audio_startup_ms: number | null;
  playback_ms: number | null;
  ttfa_ms: number | null;
  tts_cache_hit: boolean;
  stage_timestamps: StageTimestamps;
  time_to_first_text_ms: number | null;
  time_to_first_synthesized_audio_ms: number | null;
  time_to_first_audio_output_ms: number | null;
  playback_duration_ms: number | null;
  total_turn_duration_ms: number | null;
  error: string | null;
  transcript: string | null;
}

export interface ResponseCompletedPayload {
  turn_id: string;
  outcome: TurnOutcome;
  status: string;
  action_executed: boolean;
  intent: string;
  route_category: string;
  language: LanguageCode;
  detected_language: LanguageCode;
  preferred_reply_language: RequestedLanguage;
  response_text: string;
  spoken_text: string;
  silent: boolean;
  timings: TurnTimings;
  stage_timestamps: StageTimestamps;
  llm_ttft_ms?: number | null;
  tts_ms?: number | null;
  audio_startup_ms?: number | null;
  playback_ms?: number | null;
  ttfa_ms?: number | null;
  rejection_reason?: string;
  error?: string;
}

export interface LanguageSelectRequest {
  language: RequestedLanguage;
  recognition_mode?: RecognitionMode | null;
}

export interface LanguageSelectResponse {
  session_language: RequestedLanguage;
  resolved_language: LanguageCode;
  recognition_mode: RecognitionMode;
  stt_language: RequestedLanguage;
}
```

---

## 4. How the Frontend Developer (`feat/frontend-experience`) Pulls This Contract

```powershell
git fetch origin feat/backend-intelligence
git checkout origin/feat/backend-intelligence -- docs/API_CONTRACT.md docs/FRONTEND_INTEGRATION_HANDOVER.md
```
