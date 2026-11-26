"""Request graceful shutdown of the desktop launched from this checkout."""
import time
from pathlib import Path
from uuid import UUID, uuid4


def install_shutdown_request(runtime, app, controller):
    from PySide6.QtCore import QFileSystemWatcher

    token = uuid4().hex
    instance = runtime / "desktop-instance"
    request = runtime / f"desktop-exit-{token}"
    instance.write_text(token, encoding="ascii")
    watcher = QFileSystemWatcher([str(runtime)], controller)

    def check(*_):
        if request.exists():
            controller.shutdown()

    def cleanup():
        if instance.exists() and instance.read_text(encoding="ascii") == token:
            instance.unlink()
        request.unlink(missing_ok=True)

    watcher.directoryChanged.connect(check)
    app.aboutToQuit.connect(cleanup)
    check()


def request_shutdown(runtime):
    instance = runtime / "desktop-instance"
    if not instance.is_file():
        raise SystemExit("No script-enabled desktop in this checkout. Use Exit AURA in the tray for an older instance.")
    token = UUID(instance.read_text(encoding="ascii")).hex
    request = runtime / f"desktop-exit-{token}"
    request.touch()
    end = time.monotonic() + 30
    while instance.exists() and time.monotonic() < end:
        if instance.read_text(encoding="ascii") != token:
            break
        time.sleep(.15)
    if instance.exists() and instance.read_text(encoding="ascii") == token:
        raise SystemExit("The desktop did not acknowledge shutdown within 30 seconds.")
    print("AURA desktop closed.")


if __name__ == "__main__":
    request_shutdown(Path(__file__).resolve().parents[1] / ".runtime")
