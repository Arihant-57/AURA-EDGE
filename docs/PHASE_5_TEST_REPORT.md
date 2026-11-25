# AURA EDGE Phase 5 test report

## Frontend and Windows desktop

- `npm.cmd run build`, `npm.cmd run typecheck`, and `npm.cmd test -- --run` passed. The four Vitest cases cover shared room selection, a verified boolean device command, invalid command values and API rejection detail, missing planner routes, and timezone/recurrence conversion.
- Python compilation, desktop Ruff and PowerShell launcher parsing passed.
- Native Qt smoke verified transparent pet rendering, dragging and storage of its position, single-click mini island, double-click embedded dashboard, the bridge, local photography and live Home data. The dashboard capture has a transparent corner at 1.25× Windows scaling.
- Against the exact Phase 4 backend at `127.0.0.1:8015`, capture entered listening, Stop cancelled capture, and mute/unmute were verified by REST. A real typed intent changed the bedroom light and the open dashboard updated over WebSocket. The dashboard slider changed brightness and REST confirmed the stored value. The planner created a real reminder, the scheduler delivered it, and the bell displayed the persisted notification. Outside activation returned to the pet, the dashboard reopened, and desktop shutdown left the reused service and its records available.
- The dashboard Stop button was exercised after the live backend entered `SPEAKING` for a daily briefing. The backend returned to `IDLE`.
- A new desktop instance launched and shut down its own backend. After restart, REST returned the saved 68% bedroom-light brightness and three completed smoke reminders. The pet restored its saved position.
- One three-second dashboard interaction sample covered three desktop processes. The working sets summed to 378.0 MiB (shared pages may be counted twice). CPU averaged 21.87% of one logical core. The Windows GPU engine counter sum was 0% in the sampled snapshot. This was neither an idle benchmark nor a peak VRAM measurement.

## Protected backend regression check

All tests used a disposable PostgreSQL container with separate verification databases. The complete suite returned **190 passed, 1 failed**. The failing test was `test_cross_lingual_real_wav_en_and_hi_speech_to_kannada_reply`: the Hindi fixture returned clarification where the test expected success. The same test passed when rerun alone directly against the untouched original Phase 4 checkout. The complete suite was not all green.

The existing service on port 8000 was inspected read-only. Its OpenAPI omitted Phase 4 personal endpoints even though readiness reported the Phase 4 migration. The clean baseline server on port 8015 exposed the Phase 4 planner endpoints.

## Backend integrity evidence

Baseline and original `feat/backend-intelligence` both resolved to `856cc6f2bf43a1ed2aa3283bf4178951421030d7`. This check exited 0 with empty output:

```powershell
git diff --exit-code 856cc6f2bf43a1ed2aa3283bf4178951421030d7 -- backend docker-compose.yml .env.example scripts/start-dev.ps1 scripts/stop-dev.ps1 scripts/start-aura-voice.ps1
```

The checks used the existing model directory and backend environment. The original database and services were preserved.

## Manual acceptance remaining

A human speaking the bedroom-light command through the physical microphone, desktop movement across multiple physical monitors, and end-to-end microphone acceptance in each of the three languages remain manual checks. Wake word, spoken Stop and measured speaker output are described in [backend gaps](PHASE_5_BACKEND_GAPS.md).

## UI refinement on Windows

- The mini island now paints its own charcoal capsule, border and shadow. One 235 ms transition updates the native window bounds, capsule shape, pet placement and control opacity. The custom microphone, send, mute, stop and expand controls retain native keyboard focus and accessible names.
- The floating dashboard measures 93% of the available screen width and 88% of its height. The sampled surface alpha was 211/255 (83%) with transparent corners. The Windows Acrylic probe drew a pale rectangular backdrop outside the capsule and obscured the WebEngine view, so the app uses per-pixel translucency. [Windows System Backdrop](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwm_systembackdrop_type) · [Qt WebEngine page backgrounds](https://doc.qt.io/qt-6/qwebenginepage.html#backgroundColor-prop)
- The idle process on port 8000 exited on a targeted console Ctrl+C. The replacement uses the approved Phase 4 source and the same model and PostgreSQL directories. Briefing, tasks, calendar events and reminders returned HTTP 200. Readiness reported `0005_phase4_personal_assistant`, connected PostgreSQL and reachable Ollama.
- The native smoke run checked dragging, single and double clicks, microphone capture, real audio-level frames, Stop, mute and unmute, Home and bedroom rendering, all eight dashboard pages and their tabs, pinning and outside collapse. It used the existing database with persistent writes disabled. The production build, type check, four frontend tests and desktop Ruff passed.
- Seven actual screenshots are available in `.runtime/refinement-screenshots/`. The desktop composition capture uses a separate sample worksheet application. The Home and bedroom captures show the native Qt window. Listening uses measured input, and speaking shows backend status because the backend supplies no output meter.

To repeat the native captures against the local service:

```powershell
& ./.runtime/desktop-venv/Scripts/python.exe desktop/smoke.py --port 8000 --read-only --exercise-mic --capture-dir .runtime/refinement-screenshots
```
