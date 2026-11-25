"""Painted companion capsule and its native, keyboard-accessible controls."""

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QUrl,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

CAPSULE_WIDTH = 413
CAPSULE_HEIGHT = 72
VOICE_LABELS = {
    "OFFLINE": "Disconnected", "IDLE": "Tap to talk", "MUTED": "Mic muted",
    "LISTENING": "Listening", "THINKING": "Thinking", "EXECUTING": "Executing",
    "VERIFYING": "Verifying", "SPEAKING": "Speaking", "SUCCESS": "Action verified",
    "REMINDER": "New notification", "CLARIFYING": "Clarify command",
    "ERROR": "Assistant error", "CANCELLED": "Cancelled",
    "NO_SPEECH": "No speech heard", "MIC_ERROR": "Mic unavailable",
}


def clamp(rect, screen):
    available = screen.availableGeometry()
    return QRect(
        max(available.left(), min(rect.x(), available.right() - rect.width() + 1)),
        max(available.top(), min(rect.y(), available.bottom() - rect.height() + 1)),
        rect.width(), rect.height(),
    )


def interpolate(start, end, progress):
    return QRect(*(round(a + (b - a) * progress) for a, b in zip(start.getRect(), end.getRect())))


class IconControl(QAbstractButton):
    def __init__(self, icon, label, size, primary=False):
        super().__init__()
        self.icon = icon
        self.primary = primary
        self.setFixedSize(size, size)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.describe(label)

    def describe(self, label):
        if self.accessibleName() != label:
            self.setAccessibleName(label)
            self.setToolTip(label)

    def nextCheckState(self):
        # Selection is set from the backend microphone state.
        pass

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        selected = self.isChecked()
        if self.primary or selected:
            fill = "#e0ecaa" if self.underMouse() and self.isEnabled() else "#d7e58f"
            if not self.isEnabled():
                fill = "#625f47"
            ink = "#26291a" if self.isEnabled() else "#d2cdb7"
        else:
            fill = "#61594a" if self.underMouse() and self.isEnabled() else "#403a31"
            ink = "#f1efe8" if self.isEnabled() else "#918a7a"
        if self.isDown():
            fill = QColor(fill).darker(115)
        painter.setBrush(QColor(fill))
        border = QColor("#e9f2ba") if self.hasFocus() else QColor(237, 229, 210, 54)
        painter.setPen(QPen(border, 1))
        painter.drawEllipse(bounds)

        painter.translate((self.width() - 20) / 2, (self.height() - 20) / 2)
        pen = QPen(QColor(ink), 1.55, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.icon in ("mic", "mute"):
            painter.drawRoundedRect(QRectF(7, 2, 6, 10), 3, 3)
            painter.drawArc(QRectF(4.5, 3.8, 11, 11.5), 180 * 16, 180 * 16)
            painter.drawLine(10, 15, 10, 18)
            painter.drawLine(7, 18, 13, 18)
            if self.icon == "mute":
                painter.setPen(QPen(QColor(fill), 3.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawLine(3, 3, 17, 17)
                painter.setPen(pen)
                painter.drawLine(3, 3, 17, 17)
        elif self.icon == "stop":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(ink))
            painter.drawRoundedRect(QRectF(5, 5, 10, 10), 2, 2)
        elif self.icon == "send":
            painter.drawLine(4, 10, 8, 14)
            painter.drawLine(8, 14, 16, 6)
        else:
            painter.drawLine(6, 8, 10, 12)
            painter.drawLine(10, 12, 14, 8)


class Waveform(QWidget):
    def __init__(self, voice, parent):
        super().__init__(parent)
        self.voice = voice
        self.setFixedSize(60, 26)
        self.setAccessibleName("Microphone input level")

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        live = self.voice.online and self.voice.state == "LISTENING" and not self.voice.muted
        painter.setBrush(QColor("#d7e58f" if live else "#898569" if not self.voice.muted else "#746d60"))
        n = len(self.voice.levels)
        step = self.width() / n
        for index, level in enumerate(self.voice.levels):
            if live:
                height = 2.3 + level * 21
            elif self.voice.muted:
                height = 2.3
            else:
                height = (3, 5, 7, 4, 6, 9, 6, 4)[index % 8]
            bar = QRectF(index * step + 0.8, (self.height() - height) / 2, 2.1, height)
            painter.drawRoundedRect(bar, 1.05, 1.05)


class Companion(QWidget):
    def __init__(self, controller, pet_source):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("AURA EDGE Companion")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.press_point = None
        self.dragging = False
        self.suppress_click = False
        self.reveal = 0.0
        self.single_click = QTimer(self, singleShot=True)
        self.single_click.timeout.connect(lambda: controller.show_mode("MINI_ISLAND"))
        self.transition = QVariantAnimation(self)
        self.transition.setStartValue(0.0)
        self.transition.setEndValue(1.0)
        self.transition.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.transition.valueChanged.connect(self.apply_frame)

        self.pet = QQuickWidget(self)
        self.pet.setClearColor(Qt.GlobalColor.transparent)
        self.pet.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.pet.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.pet.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
        self.pet.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pet.setSource(QUrl.fromLocalFile(str(pet_source)))
        if self.pet.rootObject() is None:
            raise RuntimeError("Could not load AURA's QML pet")

        self.controls = QWidget(self)
        self.controls.setGeometry(72, 5, CAPSULE_WIDTH - 85, CAPSULE_HEIGHT - 12)
        self.controls_opacity = QGraphicsOpacityEffect(self.controls)
        self.controls.setGraphicsEffect(self.controls_opacity)
        layout = QHBoxLayout(self.controls)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label_stack = QVBoxLayout()
        label_stack.setContentsMargins(0, 0, 0, 0)
        label_stack.setSpacing(1)
        layout.addLayout(label_stack, 1)
        title = QLabel("AURA")
        title.setFont(QFont("Segoe UI Variable", 11, QFont.Weight.DemiBold))
        title.setStyleSheet("color: #f1efe8;")
        self.label = QLabel("Connecting…")
        self.label.setFont(QFont("Segoe UI Variable", 9))
        self.label.setMinimumWidth(100)
        label_stack.addStretch()
        label_stack.addWidget(title)
        label_stack.addWidget(self.label)
        label_stack.addStretch()
        self.wave = Waveform(controller.voice_model, self.controls)
        layout.addWidget(self.wave)
        layout.addSpacing(4)
        self.talk = IconControl("mic", "Start listening", 37, primary=True)
        self.mute = IconControl("mute", "Mute microphone", 31)
        self.mute.setCheckable(True)
        self.stop = IconControl("stop", "Stop assistant", 31)
        self.expand = IconControl("expand", "Open dashboard", 29)
        for button in (self.talk, self.mute, self.stop, self.expand):
            layout.addWidget(button)
        self.talk.clicked.connect(lambda: controller.voice_model.control("talk"))
        self.mute.clicked.connect(lambda: controller.voice_model.control("mute"))
        self.stop.clicked.connect(lambda: controller.voice_model.control("stop"))
        self.expand.clicked.connect(lambda: controller.show_mode("EXPANDED_DASHBOARD"))
        self.controls.hide()
        controller.voice_model.changed.connect(self.update_voice)
        self.update_voice()

    def present(self, target, mini, animate, reduced_motion):
        self.transition.stop()
        self.pet.rootObject().setProperty("reducedMotion", reduced_motion)
        pet_target = QRect(13, 7, 53, 53) if mini else QRect(QPoint(), target.size())
        self.frames = (self.geometry(), target, self.pet.geometry(), pet_target, self.reveal, float(mini))
        if animate:
            self.transition.setDuration(235)
            self.transition.setCurrentTime(0)
            self.apply_frame(0)
            self.transition.start()
        else:
            self.apply_frame(1)

    def apply_frame(self, progress):
        start, end, pet_start, pet_end, first, last = self.frames
        self.reveal = first + (last - first) * progress
        self.setGeometry(interpolate(start, end, progress))
        self.pet.setGeometry(interpolate(pet_start, pet_end, progress))
        self.controls_opacity.setOpacity(self.reveal)
        self.controls.setEnabled(self.reveal > 0.8)
        self.controls.setVisible(self.reveal > 0)
        self.update()

    def paintEvent(self, _event):
        if self.reveal <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self.reveal)
        surface = QRectF(self.rect()).adjusted(5, 3, -5, -7)
        radius = min(surface.width(), surface.height()) / 2 * (0.6 + self.reveal * 0.4)
        painter.setPen(Qt.PenStyle.NoPen)
        for offset, alpha in ((5, 15), (3, 24), (1, 44)):
            painter.setBrush(QColor(16, 12, 8, alpha))
            painter.drawRoundedRect(surface.adjusted(-offset, 1, offset, offset), radius + offset, radius + offset)
        fill = QLinearGradient(surface.topLeft(), surface.bottomLeft())
        fill.setColorAt(0, QColor(51, 46, 38, 225))
        fill.setColorAt(1, QColor(27, 24, 20, 228))
        painter.setBrush(fill)
        painter.setPen(QPen(QColor(213, 201, 174, 138), 1))
        painter.drawRoundedRect(surface, radius, radius)

    def update_voice(self):
        voice = self.controller.voice_model
        root = self.pet.rootObject()
        mood = voice.mood
        root.setProperty("mood", mood)
        root.setProperty("level", voice.levels[-1])
        if voice.capture_ready:
            text = "Ready to send"
        elif mood == "IDLE" and not voice.available:
            text = "No microphone"
        else:
            text = VOICE_LABELS.get(mood, mood.title())
        if text != self.label.text():
            self.label.setText(text)
            color = "#d7e58f" if mood in ("LISTENING", "SPEAKING", "SUCCESS") else "#d0c8b7"
            self.label.setStyleSheet(f"color: {color};")
        listening = voice.state == "LISTENING" and not voice.continuous
        self.talk.icon = "send" if listening else "mic"
        self.talk.describe("Send recording" if listening else "Start listening")
        self.talk.setEnabled(voice.online and voice.available and not voice.muted and not voice.continuous
                             and not voice.pending and voice.state in ("IDLE", "LISTENING"))
        self.talk.update()
        self.mute.describe("Unmute microphone" if voice.muted else "Mute microphone")
        self.mute.setChecked(voice.muted)
        self.mute.setEnabled(voice.online and not voice.pending)
        self.stop.setEnabled(voice.online and (voice.continuous or voice.state != "IDLE" or voice.pending > 0))
        if self.controls.isVisible():
            self.wave.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_point = event.globalPosition().toPoint()
            self.origin = self.pos()
            self.dragging = False
            self.suppress_click = False

    def mouseMoveEvent(self, event):
        if self.press_point is None or self.controller.mode != "PET_ONLY":
            return
        point = event.globalPosition().toPoint()
        if self.dragging or (point - self.press_point).manhattanLength() >= QApplication.startDragDistance():
            self.single_click.stop()
            self.transition.stop()
            self.dragging = True
            pos = self.origin + point - self.press_point
            screen = QApplication.screenAt(point) or self.screen()
            self.move(clamp(QRect(pos, self.size()), screen).topLeft())

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.dragging:
            self.controller.place_free(self.geometry())
        elif not self.suppress_click and self.press_point is not None and self.controller.mode == "PET_ONLY":
            self.single_click.start(QApplication.doubleClickInterval())
        self.press_point = None
        self.dragging = False

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.single_click.stop()
            self.suppress_click = True
            self.controller.show_mode("EXPANDED_DASHBOARD")

    def contextMenuEvent(self, event):
        self.single_click.stop()
        self.controller.open_menu(event.globalPos())

    def showEvent(self, event):
        self.pet.rootObject().setProperty("onDesktop", True)
        super().showEvent(event)

    def hideEvent(self, event):
        self.pet.rootObject().setProperty("onDesktop", False)
        super().hideEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.WindowDeactivate and self.controller.mode == "MINI_ISLAND":
            QTimer.singleShot(160, lambda: self.controller.collapse_if_outside(self, "MINI_ISLAND"))
        return super().event(event)
