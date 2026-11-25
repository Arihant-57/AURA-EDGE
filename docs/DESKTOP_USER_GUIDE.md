# AURA EDGE Desktop user guide

## Setup

Use the existing Phase 4 checkout, Python environment, PostgreSQL data and model directory. Run these one-time commands from the Phase 5 checkout.

```powershell
python -m venv .runtime/desktop-venv
& ./.runtime/desktop-venv/Scripts/python.exe -m pip install -r desktop/requirements.txt
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
```

The dependency setup can use the package registries. The desktop application runs from local files and loopback services after installation.

## Launch and operate

```powershell
./scripts/start-aura-desktop.ps1 -BackendRoot "$env:USERPROFILE/Desktop/AURA-EDGE"
```

The launcher checks local Ollama and PostgreSQL readiness. It reuses a running AURA EDGE backend when available. A warning about Phase 4 endpoints means the process listening on the configured port needs to be restarted by its owner using the approved backend checkout.

- Drag the robot to move it. A single click opens the mini island, a double click opens the dashboard, and a right click opens the companion menu.
- Select **Talk**, speak your command, then select **Send**. **Stop** cancels capture, processing or playback. **Mute** controls backend microphone capture.
- Click outside to return to the pet. Pin the dashboard to keep it open. If the pet is hidden, open it from the Windows notification area.
- Room controls reflect simulated devices. The bell opens persisted notifications. Tasks, calendar, reminders and household records are stored through the local backend.
- Select **Shutdown AURA** in the dashboard or **Exit AURA** in the tray and confirm. The desktop closes voice activity and stops its own backend process if it launched one. If the backend is stopped, scheduled actions resume under their existing missed-job policies when it comes back online.
