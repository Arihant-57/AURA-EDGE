"""AURA EDGE's Windows pet, companion pill and embedded React dashboard."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from companion import CAPSULE_HEIGHT, CAPSULE_WIDTH, Companion, clamp
from local_server import start_server
from PySide6.QtCore import (
    QDir,
    QEvent,
    QFile,
    QIODevice,
    QLockFile,
    QObject,
    QPoint,
    QProcess,
    QProcessEnvironment,
    QRect,
    QSettings,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from voice import VoiceConnection

ROOT = Path(__file__).resolve().parent.parent
POSITIONS = {"free": "Free placement", "top-center": "Top center", "top-right": "Top right",
             "bottom-right": "Bottom right", "bottom-left": "Bottom left"}
SIZES = {"small": 68, "medium": 88, "large": 108}
STYLE = """
QMenu { color: #f1efe8; background: #302c25; border: 1px solid #61594c; padding: 5px; font: 10pt "Segoe UI"; }
QMenu::item { padding: 7px 27px 7px 20px; }
QMenu::item:selected { background: #545043; }
QMenu::separator { height: 1px; background: #524c42; margin: 4px; }
"""

class LocalOnly(QWebEngineUrlRequestInterceptor):
    def __init__(self, root_url, parent):
        super().__init__(parent)
        self.root_url = root_url

    def interceptRequest(self, info):
        if not info.requestUrl().toString().startswith(self.root_url):
            info.block(True)


class DashboardPage(QWebEnginePage):
    def __init__(self, controller, profile, view):
        super().__init__(profile, view)
        self.controller = controller

    def acceptNavigationRequest(self, url, _type, _main_frame):
        return url.toString() == self.controller.server.url

    def javaScriptConfirm(self, origin, message):
        self.controller.in_dialog = True
        try:
            return super().javaScriptConfirm(origin, message)
        finally:
            self.controller.in_dialog = False


class Dashboard(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("AURA EDGE")
        self.setWindowIcon(controller.icon)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view = QWebEngineView(self)
        self.view.setAutoFillBackground(False)
        self.view.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        profile = QWebEngineProfile(self)
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.interceptor = LocalOnly(controller.server.url, profile)
        profile.setUrlRequestInterceptor(self.interceptor)
        self.page = DashboardPage(controller, profile, self.view)
        self.page.setBackgroundColor(Qt.GlobalColor.transparent)
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        self.view.setPage(self.page)

        self.channel = QWebChannel(self.page)
        self.channel.registerObject("aura", controller)
        self.page.setWebChannel(self.channel)
        api = QFile(":/qtwebchannel/qwebchannel.js")
        if not api.open(QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError("Qt WebChannel JavaScript resource unavailable")
        script = QWebEngineScript()
        script.setName("AURA desktop bridge")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setSourceCode(bytes(api.readAll()).decode() + """
            window.auraNative = true;
            new QWebChannel(qt.webChannelTransport, channel => {
              window.auraDesktop = channel.objects.aura;
              window.dispatchEvent(new Event('aura-desktop-ready'));
            });
        """)
        self.page.scripts().insert(script)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.view.load(QUrl(controller.server.url))

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            QTimer.singleShot(170, lambda: self.controller.collapse_if_outside(self, "EXPANDED_DASHBOARD"))
        super().changeEvent(event)

    def closeEvent(self, event):
        if self.controller.mode != "SHUTTING_DOWN":
            event.ignore()
            self.controller.show_mode("PET_ONLY")
        else:
            super().closeEvent(event)


class Desktop(QObject):
    liveEvent = Signal(str)
    connection = Signal(bool)
    preferencesChanged = Signal(str)
    navigate = Signal(str)
    allowed_prefs: ClassVar[dict[str, tuple[str, ...]]] = {
        "size": tuple(SIZES), "position": tuple(POSITIONS),
        "onTop": ("true", "false"), "reducedMotion": ("true", "false"),
        "pinned": ("true", "false"), "translucent": ("true", "false"),
    }

    def __init__(self, args, server):
        super().__init__()
        self.server = server
        self.settings = QSettings("AURA EDGE", "Desktop")
        self.mode = "PET_ONLY"
        self.in_dialog = False
        self.menu = None
        self.next_page = ""
        self.dashboard = None
        self.child = None
        self.stop_marker = None
        self.voice_control = VoiceConnection(args.backend_port, self)
        self.voice_control.liveEvent.connect(self.liveEvent)
        self.voice_control.connection.connect(self.connection)
        self.voice_control.notice.connect(self.on_notice)
        self.voice_control.failure.connect(self.voice_error)
        self.companion = Companion(self, ROOT / "desktop" / "Pet.qml")
        self.icon = QIcon(self.logo())
        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip("AURA EDGE")
        self.tray.activated.connect(self.tray_clicked)
        self.tray_menu = QMenu()
        self.tray_menu.setStyleSheet(STYLE)
        self.tray_menu.aboutToShow.connect(lambda: self.populate_menu(self.tray_menu))
        self.tray.setContextMenu(self.tray_menu)
        self.tray.show()
        for screen in QApplication.screens():
            screen.availableGeometryChanged.connect(self.relocate)
        QApplication.instance().screenRemoved.connect(self.relocate)
        QApplication.instance().screenAdded.connect(self.screen_added)
        if args.backend_python and args.backend_root:
            self.start_backend(args)
        self.voice_control.start()
        self.show_mode("PET_ONLY")

    @property
    def voice_model(self):
        return self.voice_control

    def logo(self):
        logo = QPixmap(64, 64)
        logo.fill(Qt.GlobalColor.transparent)
        painter = QPainter(logo)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#292723"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(1, 1, 62, 62, 13, 13)
        painter.setBrush(QColor("#d7e58f"))
        from PySide6.QtGui import QPolygon
        painter.drawPolygon(QPolygon([QPoint(32, 9), QPoint(10, 52), QPoint(32, 41),
                                      QPoint(54, 52), QPoint(32, 9), QPoint(32, 24),
                                      QPoint(44, 43), QPoint(32, 37), QPoint(20, 43), QPoint(32, 24)]))
        return logo

    def start_backend(self, args):
        self.stop_marker = ROOT / ".runtime" / f"stop-{uuid4().hex}"
        self.stop_marker.parent.mkdir(exist_ok=True)
        self.child = QProcess(self)
        self.child.setWorkingDirectory(str(args.backend_root))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONPATH", str(ROOT / "backend" / "src"))
        env.insert("AURA_DESKTOP_MODELS_FALLBACK", str(args.backend_root / "models"))
        env.insert("HF_HUB_OFFLINE", "1")
        env.insert("TRANSFORMERS_OFFLINE", "1")
        self.child.setProcessEnvironment(env)
        self.child.setStandardOutputFile(str(ROOT / ".runtime" / "desktop-backend.log"))
        self.child.setStandardErrorFile(str(ROOT / ".runtime" / "desktop-backend-errors.log"))
        self.child.finished.connect(lambda *_: self.on_notice("AURA backend", "Local backend stopped. Check the desktop backend logs.") if self.mode != "SHUTTING_DOWN" else None)
        self.child.errorOccurred.connect(lambda _error: self.on_notice("AURA backend", self.child.errorString()) if self.mode != "SHUTTING_DOWN" else None)
        self.child.start(str(args.backend_python), [str(ROOT / "desktop" / "managed_backend.py"), str(self.stop_marker), str(args.backend_port)])

    def prefs(self):
        return {
            "size": self.settings.value("size", "medium"),
            "position": self.settings.value("position", "free"),
            "onTop": self.settings.value("onTop", True, bool),
            "reducedMotion": self.settings.value("reducedMotion", False, bool),
            "pinned": self.settings.value("pinned", False, bool),
            "translucent": self.settings.value("translucent", True, bool),
            "page": self.next_page,
        }

    @Slot(result=str)
    def preferences(self):
        return json.dumps(self.prefs())

    @Slot(result=str)
    def snapshot(self):
        return self.voice_control.snapshot()

    @Slot(str)
    def voice(self, action):
        self.voice_control.control(action)

    @Slot(str, str)
    def preference(self, name, value):
        if value not in self.allowed_prefs.get(name, ()):
            return
        self.settings.setValue(name, value == "true" if value in ("true", "false") else value)
        self.settings.sync()
        self.preferencesChanged.emit(self.preferences())
        if self.mode in ("PET_ONLY", "MINI_ISLAND"):
            self.relocate()

    @Slot(str)
    def windowAction(self, action):
        if action == "minimize":
            self.show_mode("PET_ONLY")
        elif action == "shutdown":
            self.confirm_shutdown()
        elif action == "pin":
            self.preference("pinned", "false" if self.prefs()["pinned"] else "true")
        elif action == "move" and self.dashboard is not None:
            self.dashboard.windowHandle().startSystemMove()

    def screen_added(self, screen):
        screen.availableGeometryChanged.connect(self.relocate)
        self.relocate()

    def position(self):
        pos = self.settings.value("petPoint", type=QPoint) if self.settings.contains("petPoint") else None
        screen = QApplication.screenAt(pos) if pos is not None else None
        if screen is None:
            screen = next((item for item in QApplication.screens() if item.name() == self.settings.value("screen")), QApplication.primaryScreen())
        bounds = screen.availableGeometry()
        prefs = self.prefs()
        size = SIZES.get(prefs["size"], 88)
        position = prefs["position"]
        if position == "free":
            if pos is None or QApplication.screenAt(pos) is None:
                pos = QPoint(bounds.right() - size - 16, bounds.bottom() - size - 16)
        else:
            x = bounds.center().x() - size // 2 if position == "top-center" else bounds.left() + 16 if position == "bottom-left" else bounds.right() - size - 16
            y = bounds.top() + 12 if position.startswith("top") else bounds.bottom() - size - 16
            pos = QPoint(x, y)
        return clamp(QRect(pos.x(), pos.y(), size, size), screen), screen

    def place_free(self, rect):
        screen = QApplication.screenAt(rect.center()) or QApplication.screenAt(rect.topLeft()) or QApplication.primaryScreen()
        rect = clamp(rect, screen)
        self.settings.setValue("petPoint", rect.topLeft())
        self.settings.setValue("screen", screen.name())
        self.preference("position", "free")

    def relocate(self, *_args):
        if self.mode in ("PET_ONLY", "MINI_ISLAND"):
            self.show_mode(self.mode, activate=False)

    def show_mode(self, mode, page="", activate=True):
        if self.mode == "SHUTTING_DOWN":
            return
        if mode == "HIDDEN" and not self.tray.isVisible():
            return
        was_mode = self.mode
        self.mode = mode
        companion, screen = self.position()
        if mode == "EXPANDED_DASHBOARD":
            self.companion.transition.stop()
            self.companion.hide()
            if page:
                self.next_page = page
            if self.dashboard is None:
                self.dashboard = Dashboard(self)
            if page:
                self.navigate.emit(page)
            if was_mode != mode:
                bounds = screen.availableGeometry()
                width, height = round(bounds.width() * 0.93), round(bounds.height() * 0.88)
                self.dashboard.setGeometry(QRect(bounds.center().x() - width // 2, bounds.center().y() - height // 2, width, height))
            self.dashboard.show()
            self.dashboard.raise_()
            self.dashboard.activateWindow()
            return
        if self.dashboard is not None:
            self.dashboard.hide()
        if mode == "HIDDEN":
            self.companion.transition.stop()
            self.companion.hide()
            return
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.prefs()["onTop"]:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        if self.companion.windowFlags() != flags:
            self.companion.setWindowFlags(flags)
            self.companion.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        mini = mode == "MINI_ISLAND"
        self.companion.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, not (mini and activate))
        target = QRect(companion.x() - 5, companion.center().y() - CAPSULE_HEIGHT // 2, CAPSULE_WIDTH, CAPSULE_HEIGHT) if mini else companion
        if mini and target.right() > screen.availableGeometry().right():
            target.moveRight(companion.right())
        reduced_motion = self.prefs()["reducedMotion"]
        transition = activate and was_mode in ("PET_ONLY", "MINI_ISLAND") and mode != was_mode and not reduced_motion
        self.companion.present(clamp(target, screen), mini, transition, reduced_motion)
        self.companion.show()
        self.companion.raise_()
        if mini and activate:
            self.companion.activateWindow()

    def collapse_if_outside(self, window, mode):
        if self.mode != mode or window.isActiveWindow() or self.in_dialog or self.menu is not None:
            return
        if QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None:
            QTimer.singleShot(160, lambda: self.collapse_if_outside(window, mode))
            return
        if mode != "EXPANDED_DASHBOARD" or not self.prefs()["pinned"]:
            self.show_mode("PET_ONLY")

    def on_notice(self, title, body):
        if self.tray.supportsMessages() and self.mode != "SHUTTING_DOWN":
            self.tray.showMessage(title, body, self.icon, 5500)

    def voice_error(self, message):
        self.on_notice("AURA voice", message)
        self.liveEvent.emit(json.dumps({"type": "desktop_voice_error", "payload": {"message": message}, "timestamp_utc": ""}))

    def open_menu(self, pos):
        self.menu = QMenu()
        self.menu.setStyleSheet(STYLE)
        self.populate_menu(self.menu)
        self.menu.exec(pos)
        self.menu = None
        if self.mode == "MINI_ISLAND":
            self.companion.activateWindow()

    def populate_menu(self, menu):
        menu.clear()
        prefs = self.prefs()

        def entry(label, callback, checked=None):
            action = QAction(label, menu)
            if checked is not None:
                action.setCheckable(True)
                action.setChecked(checked)
            action.triggered.connect(callback)
            menu.addAction(action)
            return action

        def talk():
            self.show_mode("MINI_ISLAND")
            self.voice_control.control("talk")

        entry("Talk to AURA", talk).setEnabled(self.voice_control.online and self.voice_control.available and not self.voice_control.muted)
        entry("Open mini island", lambda: self.show_mode("MINI_ISLAND"))
        entry("Open dashboard", lambda: self.show_mode("EXPANDED_DASHBOARD"))
        entry("Mute microphone", lambda: self.voice_control.control("mute"), self.voice_control.muted).setEnabled(self.voice_control.online)
        menu.addSeparator()
        entry("Always on top", lambda: self.preference("onTop", str(not prefs["onTop"]).lower()), prefs["onTop"])
        for key, label, options in (("position", "Position", POSITIONS), ("size", "Size", {size: size.title() for size in SIZES})):
            submenu = menu.addMenu(label)
            for value, name in options.items():
                action = submenu.addAction(name)
                action.setCheckable(True)
                action.setChecked(value == prefs[key])
                action.triggered.connect(lambda _checked, k=key, v=value: self.preference(k, v))
        entry("Reduce motion", lambda: self.preference("reducedMotion", str(not prefs["reducedMotion"]).lower()), prefs["reducedMotion"])
        entry("Settings", lambda: self.show_mode("EXPANDED_DASHBOARD", "Settings"))
        entry("Show pet" if self.mode == "HIDDEN" else "Hide pet", lambda: self.show_mode("PET_ONLY" if self.mode == "HIDDEN" else "HIDDEN")).setEnabled(self.tray.isVisible())
        menu.addSeparator()
        entry("Exit AURA", self.confirm_shutdown)

    def tray_clicked(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_mode("PET_ONLY" if self.mode == "HIDDEN" else "EXPANDED_DASHBOARD")

    def confirm_shutdown(self):
        if self.mode == "SHUTTING_DOWN" or self.in_dialog:
            return
        self.in_dialog = True
        parent = self.dashboard if self.mode == "EXPANDED_DASHBOARD" else self.companion if self.companion.isVisible() else None
        message = "Exit AURA and end its current voice session?"
        if self.child is not None:
            message += " Scheduled actions pause while the local backend is stopped."
        confirm = QMessageBox.question(parent, "Exit AURA EDGE", message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        self.in_dialog = False
        if confirm == QMessageBox.StandardButton.Yes:
            self.shutdown()

    def shutdown(self):
        if self.mode == "SHUTTING_DOWN":
            return
        self.mode = "SHUTTING_DOWN"
        self.companion.single_click.stop()
        self.companion.hide()
        if self.dashboard is not None:
            self.dashboard.hide()
        self.tray.hide()
        QTimer.singleShot(9_000, self.finish_shutdown)
        self.voice_control.stop(self.finish_shutdown)

    def finish_shutdown(self):
        if self.voice_control.socket is None:
            return
        self.voice_control.socket.close()
        self.voice_control.socket = None
        if self.child is not None and self.child.state() != QProcess.ProcessState.NotRunning:
            self.stop_marker.touch()
            if not self.child.waitForFinished(8_000):
                self.child.terminate()
                if not self.child.waitForFinished(2_000):
                    self.child.kill()
                    self.child.waitForFinished(2_000)
            self.stop_marker.unlink(missing_ok=True)
        if self.dashboard is not None:
            self.dashboard.view.setPage(QWebEnginePage(self.dashboard.view))
            self.dashboard.page.deleteLater()
            self.dashboard.close()
        self.server.shutdown()
        self.server.server_close()
        QApplication.instance().quit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--backend-root", type=Path)
    parser.add_argument("--backend-python", type=Path)
    args = parser.parse_args()
    if bool(args.backend_root) != bool(args.backend_python):
        parser.error("Provide both the backend checkout and its Python executable.")
    if args.backend_python and (not args.backend_python.is_file() or not (args.backend_root / "backend" / "src").is_dir()):
        parser.error("The existing backend checkout or Python executable could not be found.")
    build = ROOT / "frontend" / "dist"
    if not (build / "index.html").is_file():
        parser.error("Build the dashboard with npm run build in frontend first.")

    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{flags} --disable-background-networking".strip()
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    lock = QLockFile(str(Path(QDir.tempPath()) / "aura-edge-desktop.lock"))
    if not lock.tryLock(0):
        print("AURA EDGE Desktop is already running.", file=sys.stderr)
        return 0
    server = start_server(build, args.backend_port)
    controller = Desktop(args, server)
    app.aboutToQuit.connect(lock.unlock)
    result = app.exec()
    del controller
    return result


if __name__ == "__main__":
    sys.exit(main())
