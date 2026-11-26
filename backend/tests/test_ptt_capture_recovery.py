"""Focused regression coverage for failed and timed-out microphone capture."""

import asyncio
from threading import Event

import pytest
from fastapi.testclient import TestClient

from aura_edge.application import voice_orchestrator
from aura_edge.config import Settings
from aura_edge.main import create_app
from aura_edge.speech.audio_capture import AudioDeviceUnavailableError


@pytest.mark.parametrize(("reason", "expected_code"), [
    ("open_failed", "mic_unavailable"),
    ("no_speech_timeout", "no_speech_timeout"),
])
def test_aborted_push_to_talk_returns_to_idle(monkeypatch, reason, expected_code):
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        orch = app.state.orchestrator
        ready = Event()
        events = []
        publish = orch.ws_hub.broadcast

        async def capture(**_kwargs):
            if reason == "open_failed":
                raise AudioDeviceUnavailableError("Input device stopped responding.")
            orch.audio_capture._last_capture_reason = reason

        async def record(kind, payload):
            events.append((kind, payload))
            await publish(kind, payload)
            if kind == "error":
                ready.set()

        monkeypatch.setattr(voice_orchestrator, "has_input_microphone", lambda _device: True)
        monkeypatch.setattr(orch.audio_capture, "start_ptt_capture", capture)
        monkeypatch.setattr(orch.ws_hub, "broadcast", record)
        assert client.post("/api/v1/voice/mic/start").json()["started"]
        assert ready.wait(2), "Capture ended without a user-facing event"
        assert client.get("/api/v1/voice/status").json()["active_state"] == "IDLE"
        assert [(payload["code"]) for kind, payload in events if kind == "error"] == [expected_code]


def test_mute_during_manual_recording_releases_current_turn(monkeypatch):
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        orch = app.state.orchestrator
        recording = Event()

        async def capture(**_kwargs):
            orch.audio_capture._recording = True
            recording.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(voice_orchestrator, "has_input_microphone", lambda _device: True)
        monkeypatch.setattr(orch.audio_capture, "start_ptt_capture", capture)
        assert client.post("/api/v1/voice/mic/start").json()["started"]
        assert recording.wait(2)
        assert client.put("/api/v1/voice/mic/mute", json={"muted": True}).json()["muted"]
        assert client.get("/api/v1/voice/status").json()["active_state"] == "IDLE"
        assert client.post("/api/v1/voice/mic/start").json()["started"] is False
        assert client.put("/api/v1/voice/mic/mute", json={"muted": False}).json()["muted"] is False
