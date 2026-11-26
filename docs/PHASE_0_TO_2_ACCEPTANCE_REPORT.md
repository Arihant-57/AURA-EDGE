# Phase 0–2 Acceptance Report

Date: 25 November 2026

Phase 0 foundation, Phase 1 multilingual voice/timers and Phase 1.5 local-Qwen
conversation were verified and committed at `17ca024`. Live Qwen and human
microphone results are documented in the Phase 1.5 handover.

Phase 2 focused tests pass (`3 passed`), covering PostgreSQL restart persistence,
WebSocket events, validation, activities, routines, sensors, estimated energy and
EN/HI/KN voice/manual parity.

Final gate: **141 passed in 103.03 seconds** with live PostgreSQL enforcement.
Alembic cleanly downgraded from `0003_phase2_smart_home` to `0002` and upgraded
back to `0003`. Ruff and `git diff --check` passed.
