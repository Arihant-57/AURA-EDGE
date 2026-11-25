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
./scripts/start-aura-desktop.ps1
```

The launcher checks local Ollama and PostgreSQL readiness and checks the four Phase 4 planner routes in OpenAPI. It reuses a running AURA EDGE backend when available. When starting its own backend, it loads source from this checkout and reuses the chosen backend environment and its local models. Use `-BackendRoot` if your existing backend environment is outside the default location.

- Drag the robot to move it. A single click opens the mini island, a double click opens the dashboard, and a right click opens the companion menu.
- Select the **microphone** to start listening. Speak your command, then select the **check mark** to send the recording. When AURA detects the end of speech, it shows **Ready to send**. The square **Stop** icon cancels the active turn. A lime **muted microphone** icon shows that capture is muted.
- Click outside to return to the pet. Pin the dashboard to keep it open. If the pet is hidden, open it from the Windows notification area.
- Room rows expose power where supported. Expand a row for settings such as brightness, fan speed, or AC temperature and mode. The bell opens persisted notifications. Tasks, calendar, reminders and household records are stored through the local backend.
- Select **Shutdown AURA** in the dashboard or **Exit AURA** in the tray and confirm. The desktop closes voice activity and stops its own backend process if it launched one. If the backend is stopped, scheduled actions resume under their existing missed-job policies when it comes back online.

For a short human microphone and audibility check, run this from the checkout with the local backend running.

```powershell
& "$env:USERPROFILE/Desktop/AURA-EDGE/.venv/Scripts/python.exe" -m qa.live_acceptance
```
