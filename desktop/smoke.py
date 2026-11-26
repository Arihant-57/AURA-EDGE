"""Exercise the actual Qt WebEngine UI against a local AURA backend."""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from urllib.request import Request, urlopen

from companion import CAPSULE_WIDTH
from local_server import start_server
from main import ROOT, Desktop
from PySide6.QtCore import QPoint, QProcess, QRect, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget


def wait_for(condition, description, timeout=12):
    start = monotonic()
    while monotonic() - start < timeout:
        if condition():
            return
        QTest.qWait(60)
    raise AssertionError(description)


def launch_visual_backdrop():
    """Use a separate app with sample data as the composited Windows background."""
    script = """
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem
app = QApplication([])
sheet = QTableWidget(40, 15)
sheet.setWindowTitle("Sample worksheet")
sheet.setAlternatingRowColors(True)
sheet.setStyleSheet("QTableWidget { background: #eee5ce; alternate-background-color: #c8b798; color: #2c251a; gridline-color: #977e5f; } QHeaderView::section { background: #887657; color: #fff5df; padding: 4px; }")
sheet.setHorizontalHeaderLabels([chr(65+i) for i in range(sheet.columnCount())])
for row in range(sheet.rowCount()):
    for col in range(sheet.columnCount()):
        if col == 0:
            value = ("Sample worksheet" if row == 0 else f"Item {row:02d}")
        else:
            value = f"{100 + 11*row + 37*col:,}" if row % 2 == 0 else ""
        sheet.setItem(row, col, QTableWidgetItem(value))
sheet.showMaximized()
QTimer.singleShot(300, lambda: print("ready", flush=True))
app.exec()
"""
    process = QProcess()
    process.start(sys.executable, ["-c", script])
    if not process.waitForReadyRead(8000):
        process.kill()
        process.waitForFinished(3000)
        raise RuntimeError("Sample worksheet did not appear")
    return process


def run(port, read_only, capture_dir=None, exercise_mic=False):
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
    if capture_dir:
        capture_dir.mkdir(parents=True, exist_ok=True)
    backdrop = launch_visual_backdrop() if capture_dir else None
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(runtime / "smoke-settings"))
    server = start_server(ROOT / "frontend" / "dist", port)
    desktop = Desktop(SimpleNamespace(backend_port=port, backend_python=None, backend_root=None), server)
    desktop.preference("pinned", "false")
    outside = QWidget()
    pet = desktop.companion
    previous_mute = None
    audio_samples = []

    def observe_audio(raw):
        event = json.loads(raw)
        if event["type"] == "audio_level":
            level = event["payload"].get("rms_db")
            if isinstance(level, (int, float)):
                audio_samples.append(level)

    desktop.voice_model.liveEvent.connect(observe_audio)

    def capture(name, window, full=False):
        if not capture_dir:
            return
        QTest.qWait(220)
        screen = window.screen()
        bounds = screen.availableGeometry()
        region = bounds if full else window.geometry().adjusted(-18, -16, 18, 16).intersected(bounds)
        region.translate(-screen.geometry().topLeft())
        image = screen.grabWindow(0, *region.getRect())
        assert not image.isNull() and image.save(str(capture_dir / name)), name

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

    def verify_pages():
        sections = {
            "Home": (),
            "Automation": ("Activities",),
            "Rooms": (),
            "Tasks": ("Calendar", "Reminders"),
            "Routines": (),
            "Assistant": (),
            "Household": ("Inventory", "Recipes", "Expenses", "Deliveries"),
            "Settings": ("Desktop appearance", "Voice & language", "Performance", "Personal memory", "Notifications", "Privacy"),
        }
        def settled():
            return javascript("""Boolean(document.querySelector('main .card')) &&
                ![...document.querySelectorAll('main .empty')].some(node => node.textContent === 'Loading…')""")

        for section, tabs in sections.items():
            click(section, "document.querySelector('nav')")
            for tab in ("", *tabs):
                if tab:
                    click(tab, "document.querySelector('main')")
                name = f"{section} / {tab}" if tab else section
                wait_for(settled, f"{name} did not load")
                errors = javascript("[...document.querySelectorAll('main .inline-error')].map(node => node.textContent).join(' | ')")
                assert not errors, f"{name}: {errors}"
        print("PASS live data across all eight dashboard pages and their tabs", flush=True)

    try:
        wait_for(lambda: desktop.voice_model.online and desktop.voice_model.available, "Native voice snapshot unavailable")
        assert pet.isVisible() and desktop.mode == "PET_ONLY"
        assert pet.grab().toImage().pixelColor(0, 0).alpha() == 0, "Pet corner is not transparent"
        if capture_dir:
            desktop.place_free(QRect(130, 104, pet.width(), pet.height()))
        capture("01-pet.png", pet)
        start = pet.pos()
        QTest.mousePress(pet, Qt.MouseButton.LeftButton, pos=QPoint(43, 40))
        QTest.mouseMove(pet, QPoint(10, 10), 80)
        QTest.mouseRelease(pet, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
        assert desktop.mode == "PET_ONLY" and pet.pos() != start
        persisted = pet.pos()
        assert desktop.position()[0].topLeft() == persisted
        QTest.mouseClick(pet, Qt.MouseButton.LeftButton, pos=QPoint(43, 40))
        wait_for(lambda: desktop.mode == "MINI_ISLAND", "Single-click did not open the mini island")
        wait_for(lambda: pet.width() == CAPSULE_WIDTH and pet.reveal > .99, "Mini island expansion did not finish")
        assert pet.talk.isVisible()
        pill = pet.grab().toImage()
        assert 175 < pill.pixelColor(pill.width() // 2, int(pill.height() * .81)).alpha() < 255, "The capsule glass failed to render"
        capture("02-mini-island.png", pet)
        if not read_only or exercise_mic:
            voice_status = backend("/voice/status")
            assert voice_status["active_state"] == "IDLE" and not voice_status["conversation_active"]
            previous_mute = voice_status["mic_muted"]
            if previous_mute:
                QTest.mouseClick(pet.mute, Qt.MouseButton.LeftButton)
                wait_for(lambda: not desktop.voice_model.muted, "Could not temporarily enable microphone")
            QTest.mouseClick(pet.talk, Qt.MouseButton.LeftButton)
            wait_for(lambda: desktop.voice_model.state == "LISTENING", "Talk did not activate capture")
            wait_for(lambda: len(audio_samples) >= 3, "Listening did not receive live audio levels")
            capture("03-listening.png", pet)
            QTest.mouseClick(pet.stop, Qt.MouseButton.LeftButton)
            wait_for(lambda: desktop.voice_model.state == "IDLE", "Stop did not cancel capture")
            QTest.mouseClick(pet.mute, Qt.MouseButton.LeftButton)
            wait_for(lambda: desktop.voice_model.muted, "Mute did not reach backend")
            assert backend("/voice/status")["mic_muted"] is True
            assert pet.mute.isChecked()
            capture("04-muted.png", pet)
            QTest.mouseClick(pet.mute, Qt.MouseButton.LeftButton)
            wait_for(lambda: not desktop.voice_model.muted, "Unmute did not reach backend")
            print(f"PASS Talk, live audio samples ({len(audio_samples)}), Stop, mute and unmute", flush=True)
        desktop.show_mode("PET_ONLY")
        QTest.mouseDClick(pet, Qt.MouseButton.LeftButton, pos=QPoint(43, 40))
        wait_for(lambda: desktop.mode == "EXPANDED_DASHBOARD", "Double-click did not open dashboard")
        wait_for(lambda: javascript("Boolean(window.auraDesktop)"), "Desktop bridge did not connect")
        wait_for(lambda: text("Estimated Energy") and text("Smart TV"), "Dashboard data did not render")
        wait_for(lambda: javascript("document.querySelector('.room-scene img')?.naturalWidth > 0"), "Local room image missing")
        QTest.qWait(220)
        dashboard = desktop.dashboard
        shot = dashboard.grab()
        pixels = shot.toImage()
        bounds = dashboard.screen().availableGeometry()
        assert abs(dashboard.width() / bounds.width() - .93) < .01
        assert abs(dashboard.height() / bounds.height() - .88) < .01
        assert pixels.pixelColor(0, 0).alpha() == 0, "Dashboard corner is opaque"
        alpha = pixels.pixelColor(pixels.width() // 2, pixels.height() - round(5 * shot.devicePixelRatio())).alpha()
        assert 175 < alpha < 225, f"Dashboard surface is not composed as glass: alpha {alpha}"
        if capture_dir:
            assert shot.save(str(capture_dir / "05-dashboard.png"))
        capture("07-dashboard-over-worksheet.png", dashboard, full=True)
        assert not pet.isVisible()
        print(f"PASS transparent pet, painted capsule, position persistence, single/double-click, live Home and dashboard glass ({alpha}/255 alpha)", flush=True)

        if capture_dir:
            click("Rooms", "document.querySelector('nav')")
            click("Bedroom")
            click("Show Bedroom Light controls")
            click("Show Bedroom AC controls")
            wait_for(lambda: javascript("Boolean(document.querySelector('[aria-label=\"Bedroom Light brightness\"]'))"), "Brightness control did not expand")
            wait_for(lambda: javascript("document.querySelector('.room-scene img')?.naturalWidth > 0"), "Bedroom photograph did not load")
            QTest.qWait(240)
            assert dashboard.grab().save(str(capture_dir / "06-bedroom-controls.png"))
            click("Home", "document.querySelector('nav')")
            verify_pages()
            desktop.preference("pinned", "true")
            outside.show()
            outside.activateWindow()
            wait_for(outside.isActiveWindow, "Could not focus an outside window")
            assert desktop.mode == "EXPANDED_DASHBOARD", "Pin did not keep the dashboard open"
            desktop.preference("pinned", "false")
            desktop.show_mode("EXPANDED_DASHBOARD")
            wait_for(dashboard.isActiveWindow, "Could not refocus the dashboard")
            outside.activateWindow()
            wait_for(lambda: desktop.mode == "PET_ONLY", "Outside click did not return to the pet")
            desktop.show_mode("MINI_ISLAND")
            wait_for(lambda: pet.reveal > .99 and pet.isActiveWindow(), "Mini island did not reopen")
            outside.activateWindow()
            wait_for(lambda: desktop.mode == "PET_ONLY" and pet.reveal == 0, "Mini island did not collapse")
            assert pet.pos() == persisted
            print("PASS pin, dashboard outside collapse and animated capsule collapse", flush=True)

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
            click("Show Bedroom Light controls")
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
            if previous_mute is not None:
                if desktop.voice_model.state == "LISTENING":
                    backend("/voice/cancel", "POST")
                if backend("/voice/status")["mic_muted"] != previous_mute:
                    backend("/voice/mic/mute", "PUT", {"muted": previous_mute})
            desktop.voice_model.closing = True
            desktop.finish_shutdown()
        if backdrop is not None:
            backdrop.terminate()
            if not backdrop.waitForFinished(3000):
                backdrop.kill()
                backdrop.waitForFinished(3000)
        app.processEvents()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--read-only", action="store_true", help="Skip persistent backend writes.")
    parser.add_argument("--exercise-mic", action="store_true", help="Exercise live microphone, mute, and Stop without editing records.")
    parser.add_argument("--capture-dir", type=Path, help="Save real Windows screenshots over a sample worksheet.")
    args = parser.parse_args()
    if args.port == 8000 and not args.read_only:
        parser.error("Use a disposable backend for write-mode smoke checks.")
    run(args.port, args.read_only, args.capture_dir, args.exercise_mic)
