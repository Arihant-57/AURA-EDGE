# Frontend Integration Handover (`feat/backend-intelligence` -> `feat/frontend-experience`)

## Phase 1.5 integration note

The Assistant UI can now use continuous conversation start/stop, mic mute,
session history inspection/clear, and prewarm endpoints documented in
`API_CONTRACT.md`. Render `transcript_ready`, append `response_text_delta`, and
replace the provisional text with `response_completed`. Treat `refused` as an
intentional safety/capability boundary and `failed` as a retryable runtime error;
do not display a model outage as a successful assistant refusal.

The backend emits actual state and latency fields. Do not synthesize success,
streaming, or timing values in production integration.

**Date:** 2026-11-25  
**Backend Branch:** `feat/backend-intelligence`  
**Contract Reference:** [`docs/API_CONTRACT.md`](./API_CONTRACT.md)

This note notifies the frontend developer (`feat/frontend-experience`) of all newly live Phase 1 backend REST and WebSocket interfaces. **No files in `frontend/` were modified by the backend branch.**

---

## 1. Compatibility & Breaking-Change Check

- **Phase 0 Endpoints Unchanged:** `/api/v1/health`, `/api/v1/readiness`, `/api/v1/policy`, and `/api/v1/policy/check` remain 100% backward compatible.
- **Important Type Alignment Notice (`AssistantState`):**
  - `docs/API_CONTRACT.md` previously sketched 5 lowercase states (`"idle" | "listening" | "processing" | "speaking" | "error"`).
  - Phase 1 implements the full **10-state FSM** emitted in uppercase over `/api/v1/voice/status` and `/api/v1/ws/live`:
    `IDLE | LISTENING | TRANSCRIBING | UNDERSTANDING | CLARIFYING | EXECUTING | VERIFYING | SPEAKING | CANCELLED | ERROR`.

## 2. Live Endpoints Available for Integration

| Feature | Method & Endpoint | Notes for Frontend |
|---|---|---|
| Voice & Hardware Status | `GET /api/v1/voice/status` | Poll on load for `mic_detected`, `speaker_detected`, `stt_ready`, `stt_kannada_specialist`, `vad_ready`, `tts_summary`, `compute`, `session_language`, `active_state`. |
| Push-to-Talk Start | `POST /api/v1/voice/mic/start` | Optional `{"language": "auto"\|"en"\|"hi"\|"kn"}`. Returns `503` if no mic is connected. |
| Push-to-Talk Stop | `POST /api/v1/voice/mic/stop` | Stops capture, runs STT -> Router/LLM -> Tool -> optional TTS (`{"speak": true}`). Returns `ProcessTurnOutput`. |
| Upload WAV Audio | `POST /api/v1/voice/transcribe` | `multipart/form-data` (`file`, `language`, `speak`, `allow_llm_fallback`). Useful for browser MediaRecorder WAV testing. |
| Text Command | `POST /api/v1/intent/process` | Same backend pipeline as voice (`{"text", "language", "source": "text_input", "speak": false}`). |
| Offline TTS Audio | `POST /api/v1/voice/synthesize` | Returns `audio/wav` stream (`en`, `hi`, `kn`) + cache headers (`X-Aura-TTS-Cached`). |
| Cancel Active Turn | `POST /api/v1/voice/cancel` | Immediately aborts mic/playback and transitions FSM to `CANCELLED -> IDLE`. |
| Language Switch | `PUT /api/v1/voice/language` | `{"language": "auto"\|"en"\|"hi"\|"kn"}`. |
| Compute Mode Switch | `PUT /api/v1/voice/compute-mode` | `{"compute_mode": "auto"\|"cpu"\|"gpu"}` — returns honest `effective_mode` and `fallback_reason`. |
| Persistent Timers | `GET/POST /api/v1/timers`, `POST /api/v1/timers/{id}/{pause,resume}`, `DELETE /api/v1/timers/{id}` | Authoritative PostgreSQL state; survives backend restarts. |
| Real-Time Events | `WS /api/v1/ws/live` | Emits `assistant_state_change`, `audio_level`, `timer_updated`, `timer_completed`, `timer_missed`, `action_verified`, `clarification_required`, `error`. |

## 3. Recommended TypeScript Types for `frontend/src/api/types.ts`

```typescript
export type LanguageCode = "en" | "hi" | "kn";
export type RequestedLanguage = "auto" | LanguageCode;
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
  confidence: number;
  intent_source: "hassil_fast" | "llm_tool" | "clarification" | "refusal" | "cancellation" | "error";
  detected_language: LanguageCode;
  slots: Record<string, unknown>;
  response_text: string;
  response_language: LanguageCode;
  action_executed: boolean;
  status: "verified" | "failed" | "clarification_required" | "refused" | "cancelled";
  state: AssistantState;
  data: Record<string, unknown>;
  latency_ms: number;
  stt_ms: number | null;
  router_ms: number | null;
  llm_ms: number | null;
  tool_ms: number | null;
  tts_ms: number | null;
  ttfa_ms: number | null;
  error: string | null;
  transcript: string | null;
}
```

---

## 4. How the Frontend Developer (`feat/frontend-experience`) Pulls This Contract

The updated API contract is published on GitHub on `origin/feat/backend-intelligence` without modifying `frontend/` or `main`:

```powershell
git fetch origin feat/backend-intelligence
git checkout origin/feat/backend-intelligence -- docs/API_CONTRACT.md docs/FRONTEND_INTEGRATION_HANDOVER.md
```

Or review directly in [`docs/API_CONTRACT.md`](./API_CONTRACT.md) on branch `feat/backend-intelligence`.
