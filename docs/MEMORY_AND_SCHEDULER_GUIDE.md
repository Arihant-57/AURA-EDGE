# AURA EDGE — Memory, Scheduler, Custom Routines & Notifications Guide

## 1. Personal Memory Architecture

AURA EDGE enforces a strict separation between **short-term session memory** and **long-term durable memory**:

1. **Session Memory (`ConversationSession`)**: Bounded in-memory sliding window (`max_turns=12`, 30-minute idle timeout) used purely for conversational follow-ups (*"explain that again"*, *"make it shorter"*, *"repeat in Kannada"*).
2. **Long-Term Personal Memory (`MemoryService` + `memory_items`)**: Stores explicitly saved preferences, structured appliance preferences (`appliance:{device_id}:{property}`), profile facts, and durable instructions in PostgreSQL 16 + `pgvector`.
3. **Optional Conversation Log (`conversation_history_items`)**: Strictly separated from `memory_items` and disabled by default (`persist_conversation_history=False`).

### Three-Tier Retrieval Hierarchy
1. **Tier 1 — Exact Structured Lookup**: Exact `canonical_key` / structured JSONB preference matching (`appliance:ac.bedroom:temperature`, `profile:name`). Never uses vector similarity for exact numbers, device states, timestamps, or schedules.
2. **Tier 2 — Local Keyword / Unicode FTS**: PostgreSQL `to_tsvector('simple', original_text)` GIN index + Unicode token overlap across Latin, Devanagari (`\u0900-\u097F`), and Kannada (`\u0C80-\u0CFF`).
3. **Tier 3 — Local `pgvector` Semantic Search**: Uses locally cached `nomic-embed-text:latest` (`dim=768`, CPU-only via `options: {"num_gpu": 0}`) via `127.0.0.1:11434/api/embed`, with exact cosine distance (`<=>`). Falls back seamlessly to Tier 2 if Ollama is stopped or unavailable, and never fabricates zero/random embeddings.

### Privacy & Injection Safeguards
- **Toggleable & Deletable**: Setting `memory_enabled=false` blocks both retrieval and saving; users can update, delete, or clear all records via voice or REST.
- **Safety Filter (`MemorySafetyError`)**: Rejects prompt-injection attempts (*"ignore previous instructions"*, *"override system prompt"*) and secrets/credentials (`password`, `API key`, `OTP`, `CVV`, `PIN`).
- **Untrusted Context Wrapper**: Any retrieved memory passed into conversational Qwen prompts is bounded (`<=450` chars, `<=3` items) and wrapped in `<untrusted_user_memory>...</untrusted_user_memory>`.

---

## 2. Persistent PostgreSQL Job Scheduler

Countdown timers (`TimerManager` / `timers`) continue handling real-time second-level countdowns untouched. Phase 3 adds `SchedulerService` (`scheduled_jobs` + `job_executions`) for calendar/clock/recurring tasks using explicit `Asia/Kolkata` timezone awareness (`zoneinfo.ZoneInfo` + `tzdata`).

### Supported Job Types & Recurrence
- **Job Types**: `reminder`, `device_action`, `routine`.
- **Recurrence**: `once`, `daily` (`hour`, `minute`), `weekly` (`weekdays` `[0..6]`, `hour`, `minute`).

### Execution & Crash Recovery Guarantees
- **Single-Winner Concurrent Claiming**: Workers claim due jobs inside an atomic PostgreSQL transaction using `SELECT ... FOR UPDATE SKIP LOCKED` paired with `INSERT INTO job_executions ... ON CONFLICT (job_id, scheduled_occurrence_utc) DO NOTHING` (`uq_job_executions_job_occurrence`).
- **Job & Execution Lifecycle States**:
  - `scheduled` -> `due` -> `running` -> `completed` | `partial` | `failed` | `missed` | `cancelled` | `paused`.
- **Crash-Interrupted Executions**: On startup, any `job_executions` stuck in `running` from an unclean crash are marked `failed` (`interrupted_by_restart_uncertain_state`) with a high-priority notification, and **never blindly re-executed** for potentially non-idempotent actions.
- **Explicit Missed-Job Policies**:
  - `notify_missed` (default for reminders): Marks occurrence `missed`, posts a high-priority `reminder_missed` notification citing the original local due time, and advances recurring reminders to their next future occurrence.
  - `skip` (default for appliance actions & recurring routines): Records `missed`, skips stale overdue appliance commands rather than running them hours late, and computes the next single valid future occurrence (bounded catch-up — never replays dozens of missed runs).

---

## 3. Custom & Event-Triggered Routines

Built-in routines (`morning`, `night`, `study`, `away`) carry `is_builtin=True` and cannot be accidentally deleted (only edited or disabled). Custom routines support:
- **Triggers**: `manual`, `time`, or approved `simulator_event` (`sensor_threshold`, `activity_completed`, `device_state`).
- **Conditions**: Live PostgreSQL state checks (`device_state`, `room_state`, `sensor_threshold` with `eq`, `ne`, `gt`, `gte`, `lt`, `lte`).
- **Pre-Validation & Honest Partial Reporting**: Every step is validated against `SmartHomeDevice.capabilities`; runtime execution records exact per-step status (`verified` / `failed`), returning `status="partial"` when earlier steps succeed and later steps fail.
- **Loop Prevention**: Nested routine actions are rejected at definition time, and `source="event_routine"` calls never re-trigger simulator events (`_depth <= 1` + per-routine cooldown).

---

## 4. Proactive Notifications & Voice Examples

Notifications persist to PostgreSQL **before** broadcasting `notification_created` over `/api/v1/ws/live` (`dedup_key` prevents duplicate alerts). Audio alerts run on a bounded queue serialized with `VoiceOrchestrator._turn_lock`, honoring `spoken_enabled`, quiet hours (`Asia/Kolkata`), and explicit user microphone mute.

### Multilingual Voice Command Examples
- **Memory Save**: *"Remember I prefer the bedroom AC at 24 degrees."* / *"याद रखो मुझे बेडरूम का एसी 24 डिग्री पर पसंद है"* / *"ನೆನಪಿಡು ಬೆಡ್‌ರೂಮ್ ಏಸಿ 23 ಡಿಗ್ರಿ ಇರಲಿ"*
- **Memory Query & Update**: *"What temperature do I prefer?"* / *"Change my preferred temperature to 25"* / *"ನನ್ನ ಇಷ್ಟದ ಏಸಿ ತಾಪಮಾನ ಎಷ್ಟು?"*
- **Personalized Control**: *"Set my bedroom AC to my preferred temperature."*
- **Scheduled Appliance / Routine**: *"Turn off the bedroom light in 10 minutes."* / *"Open the curtains every morning at 7."* / *"Run the night routine at 10 PM."*
- **Reminders**: *"Remind me to submit my report tomorrow at 5 PM."* / *"10 मिनट में मुझे पानी पीने की याद दिलाओ"*
