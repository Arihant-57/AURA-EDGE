# AURA EDGE Phase 5 handover

Branch `feat/phase5-desktop-experience` is based on `856cc6f2bf43a1ed2aa3283bf4178951421030d7`.

The Windows desktop starts with a draggable QML pet. Its mini island exposes voice controls and measured microphone levels. The expanded Qt WebEngine dashboard covers Home, Rooms, Automation, Routines, Tasks & Planner, Assistant, Household and Settings. Device controls and planner records use the existing FastAPI service. The desktop and React view share the native live-event connection. Notifications remain in the backend and can also appear through the Windows tray.

- [Setup, launch and shutdown](DESKTOP_USER_GUIDE.md)
- [Desktop architecture](DESKTOP_ARCHITECTURE.md)
- [Endpoint coverage](FRONTEND_API_COVERAGE.md)
- [Design references and local assets](DESIGN_SYSTEM.md)
- [Backend capability gaps](PHASE_5_BACKEND_GAPS.md)
- [Test results and integrity evidence](PHASE_5_TEST_REPORT.md)

Dependencies are the existing locked React/Vite stack plus the pinned Qt Addons and WebEngine packages in a separate desktop environment. The existing voice Python environment, model directory and PostgreSQL data are reused. [Qt for Python](https://doc.qt.io/qtforpython-6/) · [Qt WebEngine wheel](https://pypi.org/project/PySide6-WebEngine/6.12.0.140/)

## Provenance

- The local AURA V4 companion, QML, microphone and telemetry code informed the read-only architecture review. The existing research inventory records AURA V4 `0a1635c` (MIT), Home Assistant Core `80f9e44` (Apache-2.0), and Andromeda `abd0193` (MIT). Phase 5 uses original implementations. [Existing inventory](THIRD_PARTY.md)
- Transparent window and pet interaction references were Kai (no project-wide reuse license), Petex (Apache-2.0), Agent Pet (MIT), and Windows Notch Overlay (Apache-2.0). [Kai](https://github.com/RicoMitra/Kai) · [Petex](https://github.com/iebb/petex) · [Agent Pet](https://github.com/xiangking/agent-pet) · [Windows Notch Overlay](https://github.com/Taxperia/windows-notch-overlay)
- Capability-driven controls and offline speech research used Mushroom (Apache-2.0), OpenWakeWord (Apache-2.0 code, CC BY-NC-SA 4.0 bundled models), and Sherpa-ONNX (Apache-2.0). Phase 5 adds no speech models or upstream assets. [Mushroom](https://github.com/piitaya/lovelace-mushroom) · [OpenWakeWord](https://github.com/dscripka/openWakeWord) · [Sherpa-ONNX](https://github.com/k2-fsa/sherpa-onnx)
