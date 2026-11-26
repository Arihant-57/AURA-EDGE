"""Phase 3 tests for persistent proactive notifications, dedup, offline recovery & audio serialization."""

from __future__ import annotations

import functools
import time
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from aura_edge.config import ComputeMode, ConnectivityMode, Settings
from aura_edge.data.database import create_db_engine
from aura_edge.data.models import NotificationPreference, NotificationRecord, SmartHomeDevice
from aura_edge.main import create_app
from aura_edge.speech.playback import PlaybackMetrics


def _clean_notifications() -> None:
    cfg = Settings(_env_file=None, connectivity_mode=ConnectivityMode.STRICT_OFFLINE, compute_mode=ComputeMode.CPU)
    from qa.loopback_rehearsal import require_disposable_database

    require_disposable_database(cfg.database_url)
    engine = create_db_engine(cfg.database_url)
    with Session(engine) as session:
        session.execute(delete(NotificationRecord))
        session.execute(delete(NotificationPreference))
        dev = session.get(SmartHomeDevice, "washer.utility")
        if dev is not None:
            dev.state = {"activity": "idle", "cycle": "normal", "remaining_seconds": 0}
        dev_light = session.get(SmartHomeDevice, "light.living_main")
        if dev_light is not None:
            dev_light.available = True
        session.commit()


@pytest.fixture
def _cleanup() -> Generator[None, None, None]:
    _clean_notifications()
    yield
    _clean_notifications()


def _offline_settings() -> Settings:
    return Settings(
        _env_file=None,
        connectivity_mode=ConnectivityMode.STRICT_OFFLINE,
        compute_mode=ComputeMode.CPU,
        audio_post_playback_cooldown_sec=0.0,
    )


@pytest.mark.usefixtures("_cleanup")
def test_notification_persistence_offline_recovery_dedup_and_ws() -> None:
    app = create_app(_offline_settings())
    with TestClient(app) as client:
        svc = app.state.notification_service
        assert client.portal is not None

        # 1. Offline frontend: create notifications while 0 WS clients are connected
        n1, created1 = client.portal.call(
            functools.partial(
                svc.notify,
                notification_type="reminder_due",
                title="Meeting",
                message="Your reminder is due.",
                dedup_key="dedup:test:1",
                speak=False,
            )
        )
        n1_dup, created2 = client.portal.call(
            functools.partial(
                svc.notify,
                notification_type="reminder_due",
                title="Meeting",
                message="Your reminder is due.",
                dedup_key="dedup:test:1",
                speak=False,
            )
        )
        assert created1 is True and created2 is False
        assert n1["id"] == n1_dup["id"]

        # 2. Retrieve missed notifications via REST + unread count
        listed = client.get("/api/v1/notifications").json()
        assert listed["unread_count"] == 1
        assert listed["items"][0]["id"] == n1["id"]

        # Disable speaker audio for simulator completion test
        client.put("/api/v1/notifications/preferences", json={"spoken_enabled": False})

        # 3. WS receives live notification_created + notification_updated when connected
        with client.websocket_connect("/api/v1/ws/live") as ws:
            ws.receive_json()  # initial state
            client.post(f"/api/v1/notifications/{n1['id']}/read")
            ev_read = ws.receive_json()
            assert ev_read["type"] == "notification_updated"
            assert ev_read["payload"]["unread_count"] == 0

            # Simulator washing machine completion creates persistent notification
            client.post("/api/v1/smart-home/simulator/devices/washer.utility/complete")
            frames = [ws.receive_json() for _ in range(2)]
            assert any(f["type"] == "notification_created" for f in frames)

        all_notifs = client.get("/api/v1/notifications").json()
        assert any(
            it["notification_type"] == "washing_machine_completed"
            and "washing machine has finished" in it["message"].lower()
            for it in all_notifs["items"]
        )

        # Mark all read & dismiss
        client.post("/api/v1/notifications/read-all")
        assert client.get("/api/v1/notifications/unread-count").json()["unread_count"] == 0
        client.post(f"/api/v1/notifications/{n1['id']}/dismiss")
        active_list = client.get("/api/v1/notifications").json()["items"]
        assert all(it["id"] != n1["id"] for it in active_list)


@pytest.mark.usefixtures("_cleanup")
def test_notification_audio_quiet_hours_mute_and_failure_retention(monkeypatch: Any) -> None:
    # 2026-11-25 17:30 UTC == 2026-11-25 23:00 IST (inside 22:00..07:00 quiet hours)
    fixed_utc = datetime(2026, 11, 25, 17, 30, tzinfo=UTC)
    app = create_app(_offline_settings(), clock=lambda: fixed_utc)
    with TestClient(app) as client:
        assert client.portal is not None
        orch = app.state.orchestrator
        svc = app.state.notification_service

        spoke_texts: list[str] = []

        async def _spy_speak(
            text: str, *, language: str = "en", **_kwargs: Any
        ) -> tuple[list[Any], PlaybackMetrics]:
            spoke_texts.append(f"{language}:{text}")
            return (
                [],
                PlaybackMetrics(
                    tts_synthesis_ms=2.0,
                    audio_playback_ms=10.0,
                    first_audio_ready_ms=3.0,
                    cache_hits=0,
                    chunks_spoken=1,
                    audio_startup_ms=4.0,
                ),
            )

        monkeypatch.setattr(orch.playback, "speak_text_with_metrics", _spy_speak)
        orch.audio_capture.set_user_muted(True)

        # Quiet hours enabled -> notification persisted visually, NOT spoken
        client.put(
            "/api/v1/notifications/preferences",
            json={
                "spoken_enabled": True,
                "quiet_hours_enabled": True,
                "quiet_hours_start": "22:00",
                "quiet_hours_end": "07:00",
                "timezone_name": "Asia/Kolkata",
            },
        )
        n_quiet, _ = client.portal.call(
            functools.partial(
                svc.notify,
                notification_type="reminder_due",
                title="Quiet test",
                message="Silent alert",
                speak=True,
            )
        )
        client.portal.call(svc.drain_audio_queue)
        assert spoke_texts == []
        assert client.get("/api/v1/notifications").json()["items"][0]["id"] == n_quiet["id"]

        # Disable quiet hours -> spoken notification plays sequentially AND sticky mic mute stays True!
        client.put("/api/v1/notifications/preferences", json={"quiet_hours_enabled": False})
        client.portal.call(
            functools.partial(
                svc.notify,
                notification_type="reminder_due",
                title="Audible test",
                message="Your bedroom light was switched off.",
                speak=True,
            )
        )
        client.portal.call(svc.drain_audio_queue)
        assert len(spoke_texts) == 1
        assert "bedroom light was switched off" in spoke_texts[0]
        assert orch.audio_capture.is_muted is True

        # Even if TTS playback raises OSError, the persistent DB notification is retained
        async def _broken_speak(*_args: Any, **_kwargs: Any) -> Any:
            raise OSError("Speaker unplugged")

        monkeypatch.setattr(orch.playback, "speak_text_with_metrics", _broken_speak)
        n_fail, _ = client.portal.call(
            functools.partial(
                svc.notify,
                notification_type="device_availability_changed",
                title="Offline device",
                message="Living Light is offline.",
                speak=True,
            )
        )
        client.portal.call(svc.drain_audio_queue)
        ids = [it["id"] for it in client.get("/api/v1/notifications").json()["items"]]
        assert n_fail["id"] in ids


@pytest.mark.usefixtures("_cleanup")
def test_timer_audio_once_across_polling_and_restart(monkeypatch: Any) -> None:
    from aura_edge.intelligence.response_catalog import get_phrase
    from aura_edge.speech.playback import AudioPlayback

    heard = []

    async def capture(self, text, *, language, **kwargs):
        heard.append((language, text))
        return [], PlaybackMetrics(1.0, 10.0, 1.0, 0, 1, 1.0)

    monkeypatch.setattr(AudioPlayback, "speak_text_with_metrics", capture)
    ids = {}
    with TestClient(create_app(_offline_settings())) as client:
        client.put("/api/v1/notifications/preferences", json={
            "spoken_enabled": True, "quiet_hours_enabled": False,
        }).raise_for_status()
        for lang in ("en", "hi"):
            result = client.post("/api/v1/timers", json={"duration_seconds": 1, "language": lang})
            ids[lang] = result.raise_for_status().json()["timer"]["id"]
        deadline = time.monotonic() + 5
        while len(heard) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        app = client.app
        for _ in range(3):
            client.portal.call(app.state.scheduler_service.tick_once)
            client.portal.call(app.state.orchestrator.timer_manager.recover_on_startup)
        svc = app.state.notification_service
        client.portal.call(svc.drain_audio_queue)
        for lang, timer_id in ids.items():
            timer = client.get(f"/api/v1/timers/{timer_id}").raise_for_status().json()
            assert timer["status"] == "completed"
            records = client.get("/api/v1/notifications").json()["items"]
            matching = [row for row in records if row["source_id"] == timer_id]
            assert len(matching) == 1 and matching[0]["spoken_delivered"]
            _, created = client.portal.call(functools.partial(
                svc.notify, notification_type="timer_completed", title="Duplicate delivery",
                message=matching[0]["message"], language=lang, source_id=timer_id,
                dedup_key=f"timer:{timer_id}:timer_completed",
            ))
            assert not created
        client.portal.call(svc.drain_audio_queue)

    with TestClient(create_app(_offline_settings())) as client:
        app = client.app
        client.portal.call(app.state.scheduler_service.tick_once)
        client.portal.call(app.state.notification_service.drain_audio_queue)
        records = client.get("/api/v1/notifications").json()["items"]
        assert sum(row["source_id"] in ids.values() for row in records) == 2
    assert sorted(heard) == [(lang, get_phrase("timer_completed", lang, label_part="")) for lang in ("en", "hi")]


def test_qa_database_guard_rejects_production_and_missing_urls() -> None:
    from sqlalchemy.exc import ArgumentError

    from qa.loopback_rehearsal import require_disposable_database

    for url in ("", "sqlite:///audit.db",
                "postgresql://127.0.0.1:5432/audit",
                "postgresql://127.0.0.1:55437/aura_edge",
                "postgresql://remote:55437/audit",
                "postgresql://127.0.0.1:55437/audit?host=remote"):
        with pytest.raises((ValueError, ArgumentError)):
            require_disposable_database(url)
    require_disposable_database("postgresql://127.0.0.1:55437/audit")
