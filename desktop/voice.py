"""One event connection and voice control path for all native surfaces."""

import json
import math
from collections import deque

from PySide6.QtCore import Property, QByteArray, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWebSockets import QWebSocket


class VoiceConnection(QObject):
    liveEvent = Signal(str)
    connection = Signal(bool)
    changed = Signal()
    notice = Signal(str, str)
    failure = Signal(str)

    def __init__(self, port, parent=None):
        super().__init__(parent)
        self.http_url = f"http://127.0.0.1:{port}/api/v1"
        self.ws_url = QUrl(f"ws://127.0.0.1:{port}/api/v1/ws/live")
        self.socket = QWebSocket(parent=self)
        self.socket.connected.connect(self.on_connected)
        self.socket.disconnected.connect(self.on_disconnected)
        self.socket.errorOccurred.connect(self.on_disconnected)
        self.socket.textMessageReceived.connect(self.receive)
        self.network = QNetworkAccessManager(self)
        self.retry = QTimer(self, singleShot=True)
        self.retry.timeout.connect(lambda: self.socket.open(self.ws_url))
        self.badge = QTimer(self, singleShot=True)
        self.badge.timeout.connect(self.clear_badge)
        self.delay = 500
        self.online = False
        self.closing = False
        self.state = "IDLE"
        self.muted = False
        self.available = False
        self.continuous = False
        self.capture_ready = False
        self.transient = ""
        self.pending = 0
        self.levels = [0.0] * 16
        self.latest = {}
        self.notice_ids = deque(maxlen=128)

    def request(self, path, method="GET", body=None, callback=None, report=True):
        request = QNetworkRequest(QUrl(self.http_url + path))
        request.setTransferTimeout(300_000 if path == "/voice/mic/stop" else 8_000)
        if body is not None:
            request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        reply = self.network.sendCustomRequest(
            request, method.encode(), QByteArray(json.dumps(body).encode() if body is not None else b"")
        )

        def finished():
            try:
                result = json.loads(bytes(reply.readAll()) or b"{}")
            except ValueError:
                result = {}
            ok = reply.error() == QNetworkReply.NetworkError.NoError
            if not ok and report and not self.closing:
                self.failure.emit(str(result.get("detail") or reply.errorString()))
            if callback is not None:
                callback(result if ok else None)
            reply.deleteLater()

        reply.finished.connect(finished)

    def start(self):
        self.retry.start(0)

    def on_connected(self):
        self.delay = 500
        self.online = True
        self.changed.emit()
        self.connection.emit(True)
        self.request("/voice/status", callback=self.status, report=False)

    def on_disconnected(self, *_args):
        if self.closing:
            return
        if self.online:
            self.online = False
            self.latest.clear()
            self.capture_ready = False
            self.levels = [0.0] * len(self.levels)
            self.changed.emit()
            self.connection.emit(False)
        if not self.retry.isActive():
            self.retry.start(self.delay)
            self.delay = min(8000, self.delay * 2)

    def status(self, result):
        if result is None or not self.online:
            return
        self.available = result.get("mic_detected", False)
        self.muted = result.get("mic_muted", False)
        self.continuous = result.get("conversation_active", False)
        self.state = result.get("active_state", "IDLE")
        self.receive(json.dumps({"type": "voice_status", "payload": result, "timestamp_utc": ""}))

    def clear_badge(self):
        self.transient = ""
        self.changed.emit()

    def receive(self, raw):
        try:
            event = json.loads(raw)
            kind, payload = event["type"], event["payload"]
            if not isinstance(payload, dict):
                return
        except (ValueError, TypeError, KeyError):
            return
        if kind == "assistant_state_change":
            self.state = payload.get("state", "IDLE")
            self.capture_ready = False
            self.latest.pop("ptt_capture_ready", None)
            if self.state not in ("IDLE", "CANCELLED", "ERROR"):
                self.transient = ""
            if self.state != "LISTENING":
                self.levels = [0.0] * len(self.levels)
        elif kind == "mic_mute_change":
            self.muted = bool(payload.get("muted"))
            if self.muted:
                self.levels = [0.0] * len(self.levels)
        elif kind == "conversation_mode_change":
            self.continuous = bool(payload.get("active"))
        elif kind == "ptt_capture_ready":
            self.capture_ready = True
            self.latest[kind] = event
            self.levels = [0.0] * len(self.levels)
        elif kind == "audio_level":
            if self.muted or self.state != "LISTENING":
                return
            db = payload.get("rms_db")
            if not isinstance(db, (float, int)) or not math.isfinite(db):
                return
            self.levels = self.levels[1:] + [max(0.0, min(1.0, (db + 54.0) / 42.0))]
        elif kind == "response_completed" and payload.get("action_executed") and payload.get("status") == "verified":
            self.transient = "SUCCESS"
            self.badge.start(1400)
        elif kind == "response_completed" and payload.get("status") == "ignored_silence":
            self.transient = "NO_SPEECH"
            self.badge.start(4000)
        elif kind == "error" and payload.get("code") in ("no_speech_timeout", "mic_unavailable"):
            self.transient = "NO_SPEECH" if payload["code"] == "no_speech_timeout" else "MIC_ERROR"
            self.badge.start(4000)
        elif kind == "notification_created" and payload.get("visual_enabled"):
            notice = payload.get("notification", {})
            notice_id = notice.get("id")
            if notice_id not in self.notice_ids:
                self.notice_ids.append(notice_id)
                self.notice.emit(str(notice.get("title") or "AURA EDGE"), str(notice.get("message") or ""))
                if self.state == "IDLE":
                    self.transient = "REMINDER"
                    self.badge.start(2200)
        if kind in ("assistant_state_change", "mic_mute_change", "conversation_mode_change", "voice_status"):
            self.latest[kind] = event
        self.changed.emit()
        self.liveEvent.emit(raw)

    @Slot(result=str)
    def snapshot(self):
        return json.dumps({"connected": self.online, "events": list(self.latest.values())})

    @Property(str, notify=changed)
    def mood(self):
        if not self.online:
            return "OFFLINE"
        if self.muted and self.state == "IDLE":
            return "MUTED"
        if self.state in ("UNDERSTANDING", "TRANSCRIBING"):
            return "THINKING"
        return (self.transient or self.state) if self.state == "IDLE" else self.state

    @Slot(str)
    def control(self, action):
        if not self.online or self.closing:
            return
        if self.pending and action not in ("stop", "continuous-stop"):
            return
        if action == "talk":
            if not self.available or self.muted or self.continuous or self.state not in ("IDLE", "LISTENING"):
                return
            if self.state == "IDLE":
                path, method, body = "/voice/mic/start", "POST", None
            else:
                path, method, body = "/voice/mic/stop", "POST", {"speak": True}
        elif action == "mute":
            path, method, body = "/voice/mic/mute", "PUT", {"muted": not self.muted}
        elif action == "stop":
            path = "/voice/conversation/stop" if self.continuous else "/voice/cancel"
            method, body = "POST", None
        elif action == "continuous-start":
            if self.state != "IDLE" or not self.available or self.muted or self.continuous:
                return
            path, method, body = "/voice/conversation/start", "POST", {"mode": "continuous", "speak": True}
        elif action == "continuous-stop":
            path, method, body = "/voice/conversation/stop", "POST", None
        else:
            return
        self.pending += 1
        self.changed.emit()

        def done(result):
            self.pending -= 1
            self.changed.emit()
            if result is not None and path == "/voice/mic/start" and not result.get("started"):
                self.failure.emit(str(result.get("reason") or "Microphone did not start."))
            if not self.closing:
                self.request("/voice/status", callback=self.status, report=False)

        self.request(path, method, body, callback=done)

    def stop(self, done):
        self.closing = True
        self.retry.stop()
        # This backend route cancels playback and capture even outside continuous mode.
        self.request("/voice/conversation/stop", "POST", callback=lambda _result: done(), report=False)
