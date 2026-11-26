# Phase 5 frontend API coverage

The client prefixes the routes below with `/api/v1`. Request validation stays in FastAPI. The client displays transport failures, HTTP validation details, and backend outcome fields before refreshing the affected snapshots.

| Dashboard or companion control | REST request and response | Live invalidation |
| --- | --- | --- |
| Backend, database and model status | `GET /readiness` → readiness and effective hardware fields | Reconnect |
| Talk, finish recording, mute, stop | `GET /voice/status`, `POST /voice/mic/start`, `POST /voice/mic/stop {speak}`, `PUT /voice/mic/mute {muted}`, `POST /voice/cancel {turn_id}` → authoritative turn or voice state | `assistant_state_change`, `audio_level`, `mic_mute_change`, `response_completed` |
| Continuous conversation | `POST /voice/conversation/start {mode,speak}`, `POST /voice/conversation/stop` → conversation state | `conversation_mode_change` |
| Typed assistant and settings | `POST /intent/process {text,source,speak}`, `PUT /voice/language {language,recognition_mode}`, `PUT /voice/compute-mode {compute_mode}`, `GET, DELETE /conversation/history` | `transcript_ready`, `response_text_delta`, `response_completed`, `conversation_history_updated` |
| Rooms, devices, energy | `GET /smart-home/{rooms,devices,history,energy}`, `POST /smart-home/devices/{id}/command {action,value,source}` → verified device snapshot | `device_state_changed`, `device_availability_changed`, `simulated_sensor_changed` |
| Simulator | `PUT …/simulator/devices/{id}/availability {available}`, `POST …/complete`, `POST …/sensor {state}` → verified device snapshot | Device and activity events |
| Timers and appliance cycles | `GET /timers`, `GET /smart-home/activities`, timer pause/resume and `DELETE /timers/{id}` | `timer_updated`, `timer_completed`, `timer_missed`, `activity_completed` |
| Automation and reminders | `GET, POST /{schedules,reminders}`, `PATCH, DELETE /{schedules,reminders}/{id}`, schedule pause/resume and executions. Create with `run_at_utc`, timezone and recurrence. Job snapshots include status and next run. | `schedule_updated`, `schedule_executed`, `schedule_failed`, `reminder_created`, `reminder_due` |
| Routines | `GET, POST /smart-home/routines`, `PATCH, DELETE …/{id}`, `POST …/{id}/run` → per-step results | `routine_updated`, `routine_completed` |
| Planner and briefing | `GET /briefing`, `GET, POST /tasks`, `PATCH, DELETE /tasks/{id}`, `POST /tasks/{id}/complete`, `GET, POST /calendar/events`, `PATCH, DELETE /calendar/events/{id}`. Calendar writes include conflicts. | `task_updated`, `calendar_updated` |
| Groceries, inventory, recipes | `GET, POST /groceries/{list,inventory}`, `PATCH /groceries/list/{id}`, `DELETE /groceries/{list,inventory}/{id}`, `GET /recipes/suggest` → local records and ingredient matches | `grocery_updated` |
| Expenses, deliveries | `GET, POST /expenses`, `DELETE /expenses/{id}`, `GET /expenses/summary`, `POST /expenses/import-csv {filename,csv_content}`, `GET, POST /parcels`, `PATCH, DELETE /parcels/{id}` | `expense_updated`, `parcel_updated` |
| Memory and notifications | `GET /memory`, `GET /memory/search`, `PATCH, DELETE /memory/{id}`, `DELETE /memory`, `GET, PUT /memory/settings`, `GET /notifications`, `POST` read/read-all/dismiss, `GET, PUT /notifications/preferences` | `memory_updated`, `memory_deleted`, `notification_created`, `notification_updated` |

The service on port 8000 during the initial audit reported the Phase 4 migration but omitted Phase 4 personal endpoints in OpenAPI. Those contracts were taken from the approved baseline route source and require the matching process at runtime.
