# Feature verification matrix

| Feature | Automated evidence | Physical or remaining work |
| --- | --- | --- |
| Push-to-talk, mute, Stop and VAD | Capture recovery tests, injected PCM and Windows desktop smoke | Speak into the actual microphone and confirm the result. |
| Time, date, timer, follow-up and safe clarification | Text and WAV replay; `test_stabilization_routing.py`, `test_phase1_timers_live_db.py` | Verify representative spoken variants. |
| Five rooms, nineteen devices and activities | REST capability enumeration, state readback and WebSocket events; `test_phase2_smart_home.py` | Devices are simulated. |
| Schedules, routines and restart policy | `test_phase3_scheduler_and_routines.py`, `test_phase3_e2e_demo.py`, live disposable desktop smoke | Existing recurring and missed-job settings apply. |
| Memory and verified conversational reference | `test_phase3_memory.py`; disposable save, retrieve, delete and compound follow-up replay | Physical speech quality remains language dependent. |
| Notifications, quiet hours and language preferences | `test_phase3_notifications.py`, `test_phase1_stt_and_compute.py`, saved Hindi repeat | Confirm that spoken replies are audible and understandable. |
| Tasks, calendar, reminders and briefings | `test_phase4_tasks_calendar_briefing.py` and disposable desktop smoke | Persist in local PostgreSQL. |
| Groceries, stock, recipes, expenses and deliveries | `test_phase4_groceries_expenses_parcels.py` and desktop smoke | Delivery records use saved local data. |
| Pet, mini island, tray and dashboard | Windows desktop smoke exercises real Qt windows, the dashboard bridge and state updates | Optional visual redesign is beyond this pass. |
| CPU, GPU and Auto | Same-machine placement and latency replay | See the [benchmark report](CPU_GPU_BENCHMARK_REPORT.md). |
| Wake word and spoken interruption | Inspected the installed model inventory and audio lifecycle | Reliable always-listening wake word and full-duplex voice interruption are unsupported in this configuration. Explicit controls remain available. |
