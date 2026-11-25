"""Exercise the actual Qt WebEngine UI against a local AURA backend."""

import argparse
import json
from datetime import datetime, timedelta, timezone
from time import monotonic
from types import SimpleNamespace
from urllib.request import Request, urlopen

from local_server import start_server
from main import ROOT, Desktop
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget


def wait_for(condition, description, timeout=12):
    start = monotonic()
    while monotonic() - start < timeout:
        if condition():
            return
        QTest.qWait(60)
    raise AssertionError(description)


def run(port, read_only):
    def backend(path, method="GET", body=None):
        request = Request(
            f"http://127.0.0.1:{port}/api/v1{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(runtime / "smoke-settings"))
    server = start_server(ROOT / "frontend" / "dist", port)
    desktop = Desktop(SimpleNamespace(backend_port=port, backend_python=None, backend_root=None), server)
    desktop.preference("pinned", "false")
    outside = QWidget()
    pet = desktop.companion

    def javascript(code):
        replies = []
        desktop.dashboard.page.runJavaScript(code, replies.append)
        wait_for(lambda: replies, "JavaScript callback did not return")
        return replies[0]

    def text(value):
        return javascript(f"document.body.innerText.includes({json.dumps(value)})")

    def click(label, scope="document"):
        code = f"""(() => {{
            const found = [...{scope}.querySelectorAll('button')].find(
                b => b.getAttribute('aria-label') === {json.dumps(label)} || b.textContent.trim() === {json.dumps(label)}
            );
            if (!found || found.disabled) return false;
            found.click();
            return true;
        }})()"""
        wait_for(lambda: javascript(code), f"Could not click {label}")

    try:
        wait_for(lambda: desktop.voice_model.online and desktop.voice_model.available, "Native voice snapshot unavailable")
        assert pet.isVisible() and desktop.mode == "PET_ONLY"
        assert pet.grab().toImage().pixelColor(0, 0).alpha() == 0, "Pet corner is not transparent"
        start = pet.pos()
        QTest.mousePress(pet, Qt.MouseButton.LeftButton, pos=QPoint(43, 40))
        QTest.mouseMove(pet, QPoint(10, 10), 80)
        QTest.mouseRelease(pet, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
        assert desktop.mode == "PET_ONLY" and pet.pos() != start
        persisted = pet.pos()
        assert desktop.position()[0].topLeft() == persisted
        QTest.mouseClick(pet, Qt.MouseButton.LeftButton, pos=QPoint(43, 40))
        wait_for(lambda: desktop.mode == "MINI_ISLAND", "Single-click did not open the mini island")
        wait_for(lambda: pet.width() == 413, "Mini island expansion did not finish")
        assert pet.talk.isVisible()
        if not read_only:
            QTest.mouseClick(pet.talk, Qt.MouseButton.LeftButton)
            wait_for(lambda: desktop.voice_model.state == "LISTENING", "Talk did not activate capture")
            QTest.mouseClick(pet.stop, Qt.MouseButton.LeftButton)
            wait_for(lambda: desktop.voice_model.state == "IDLE", "Stop did not cancel capture")
            QTest.mouseClick(pet.mute, Qt.MouseButton.LeftButton)
            wait_for(lambda: desktop.voice_model.muted, "Mute did not reach backend")
            assert backend("/voice/status")["mic_muted"] is True
            QTest.mouseClick(pet.mute, Qt.MouseButton.LeftButton)
            wait_for(lambda: not desktop.voice_model.muted, "Unmute did not reach backend")
        desktop.show_mode("PET_ONLY")
        QTest.mouseDClick(pet, Qt.MouseButton.LeftButton, pos=QPoint(43, 40))
        wait_for(lambda: desktop.mode == "EXPANDED_DASHBOARD", "Double-click did not open dashboard")
        wait_for(lambda: javascript("Boolean(window.auraDesktop)"), "Desktop bridge did not connect")
        wait_for(lambda: text("Estimated Energy") and text("Smart TV"), "Dashboard data did not render")
        wait_for(lambda: javascript("document.querySelector('.room-scene img')?.naturalWidth > 0"), "Local room image missing")
        desktop.dashboard.grab().save(str(runtime / "phase5-home-smoke.png"))
        assert not pet.isVisible()
        print("PASS pet transparency, drag, position persistence, single/double-click, dashboard bridge and live Home", flush=True)

        if not read_only:
            # The disposable verification profile uses Asia/Kolkata (UTC+05:30).
            began = datetime.now(timezone(timedelta(hours=5, minutes=30)))
            due = (began + timedelta(minutes=1)).replace(second=0, microsecond=0)
            if due - began < timedelta(seconds=12):
                due += timedelta(minutes=1)
            click("Tasks", "document.querySelector('nav')")
            click("Reminders")
            wait_for(lambda: text("New reminder"), "Reminders panel missing")
            message = f"Phase 5 desktop smoke {began.strftime('%H%M%S')}"
            javascript(f"""(() => {{
                document.querySelector('input[name=message]').value = {json.dumps(message)};
                document.querySelector('input[name=when]').value = {json.dumps(due.strftime('%Y-%m-%dT%H:%M'))};
                return true;
            }})()""")
            click("Save reminder")
            wait_for(lambda: text("Reminder saved."), "Reminder creation did not confirm")
            click("Rooms", "document.querySelector('nav')")
            click("Bedroom")
            route = "/smart-home/devices/light.bedroom_main"
            wait_for(lambda: javascript("Boolean(document.querySelector('[aria-label=\"Bedroom Light Power\"]'))"), "Bedroom controls missing")
            before_state = backend(route)["state"]
            before = before_state["power"]
            brightness = 77 if before_state["brightness"] != 77 else 68
            toggle = "false" if before else "true"
            result = backend("/intent/process", "POST", {"text": f"Turn {'off' if before else 'on'} the bedroom light.", "language": "en", "speak": False})
            assert result["status"] == "verified" and result["action_executed"], result
            wait_for(lambda: javascript(f"document.querySelector('[aria-label=\"Bedroom Light Power\"]').getAttribute('aria-checked') === '{toggle}'"), "Verified voice action did not update dashboard over WebSocket")
            javascript(f"""(() => {{
                const range = document.querySelector('[aria-label="Bedroom Light brightness"]');
                Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(range, {brightness});
                range.dispatchEvent(new Event('input', {{bubbles:true}}));
                return true;
            }})()""")
            javascript("""document.querySelector('[aria-label="Bedroom Light brightness"]').dispatchEvent(new KeyboardEvent('keyup', {bubbles:true, key:'ArrowRight'}))""")
            wait_for(lambda: backend(route)["state"]["brightness"] == brightness, "Manual brightness did not persist")

            outside.show()
            outside.activateWindow()
            wait_for(lambda: desktop.mode == "PET_ONLY" and pet.isVisible(), "Outside activation did not collapse the dashboard")
            wait_for(lambda: pet.pos() == persisted, "Pet position was not restored")
            assert pet.pos() == persisted
            desktop.show_mode("EXPANDED_DASHBOARD")
            desktop.preference("pinned", "true")
            click("Home", "document.querySelector('nav')")
            wait_for(lambda: text("AURA Highlights"), "Dashboard did not reopen")
            wait_for(lambda: any(item["title"] == message for item in backend("/notifications")["items"]), "Reminder notification did not arrive", timeout=90)
            notice_button = javascript("document.querySelector('.bell-wrap button').getAttribute('aria-label')")
            click(notice_button)
            wait_for(lambda: text(message), "Persistent notification missing from dashboard")
            print("PASS capture activation/cancel/mute, browser-to-backend reminder, verified action synchronization, brightness, outside collapse and notification", flush=True)
            click("Close", "document.querySelector('.notification-popover')")
            javascript("""(() => {
                const ask = document.querySelector('.ask-header');
                ask.querySelector('input').value = 'Give me my daily briefing.';
                ask.requestSubmit();
                return true;
            })()""")
            wait_for(lambda: desktop.voice_model.state == "SPEAKING", "Assistant did not enter actual speaking state", timeout=22)
            click("Stop assistant")
            wait_for(lambda: desktop.voice_model.state == "IDLE", "Stop did not return speech to idle")
            assert backend("/voice/status")["active_state"] == "IDLE"
            print("PASS dashboard Stop during backend speech", flush=True)
            desktop.shutdown()
            wait_for(lambda: desktop.voice_model.socket is None, "Desktop did not shut down", timeout=20)
            assert not pet.isVisible()
            assert backend(route)["state"]["brightness"] == brightness
            assert any(item["title"] == message for item in backend("/reminders?include_closed=true"))
            print("PASS desktop shutdown leaves reused backend and data available", flush=True)
        else:
            desktop.voice_model.closing = True
            desktop.finish_shutdown()
    finally:
        outside.close()
        if desktop.voice_model.socket is not None:
            desktop.voice_model.closing = True
            desktop.finish_shutdown()
        app.processEvents()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--read-only", action="store_true", help="Skip backend writes and capture.")
    args = parser.parse_args()
    if args.port == 8000 and not args.read_only:
        parser.error("Use a disposable backend for write-mode smoke checks.")
    run(args.port, args.read_only)
