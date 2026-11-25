"""End-to-end 10-step Phase 3 acceptance demonstration & multilingual regression."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from aura_edge.config import ComputeMode, ConnectivityMode, Settings
from aura_edge.data.database import create_db_engine
from aura_edge.data.models import (
    ConversationHistoryItem,
    JobExecution,
    MemoryItem,
    MemorySetting,
    NotificationPreference,
    NotificationRecord,
    ScheduledJob,
    SmartHomeDevice,
)
from aura_edge.main import create_app


class _ControllableClock:
    def __init__(self, start_utc: datetime) -> None:
        self.now = start_utc

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: Any) -> datetime:
        self.now = self.now + timedelta(**kwargs)
        return self.now


def _cleanup_all_phase3() -> None:
    cfg = Settings(_env_file=None, connectivity_mode=ConnectivityMode.STRICT_OFFLINE, compute_mode=ComputeMode.CPU)
    engine = create_db_engine(cfg.database_url)
    with Session(engine) as session:
        session.execute(delete(JobExecution))
        session.execute(delete(ScheduledJob))
        session.execute(delete(NotificationRecord))
        session.execute(delete(NotificationPreference))
        session.execute(delete(ConversationHistoryItem))
        session.execute(delete(MemoryItem))
        session.execute(delete(MemorySetting))
        ac = session.get(SmartHomeDevice, "ac.bedroom")
        if ac is not None:
            ac.available = True
            ac.state = {"power": False, "temperature": 24, "mode": "cool"}
        session.commit()


@pytest.fixture(autouse=True)
def _cleanup() -> Generator[None, None, None]:
    _cleanup_all_phase3()
    yield
    _cleanup_all_phase3()


def _offline_settings() -> Settings:
    return Settings(
        _env_file=None,
        connectivity_mode=ConnectivityMode.STRICT_OFFLINE,
        compute_mode=ComputeMode.CPU,
        audio_post_playback_cooldown_sec=0.0,
        scheduler_poll_interval_sec=3600.0,
    )


def test_phase3_10_step_end_to_end_demonstration_and_multilingual() -> None:
    clock = _ControllableClock(datetime(2026, 11, 25, 14, 0, tzinfo=UTC))
    app = create_app(_offline_settings(), clock=clock)

    with TestClient(app) as client:
        client.put("/api/v1/notifications/preferences", json={"spoken_enabled": False})
        # Step 1: Save structured preference
        s1 = client.post(
            "/api/v1/intent/process",
            json={"text": "Remember that I prefer the bedroom AC at 24 degrees.", "language": "en", "speak": False},
        ).json()
        assert s1["intent"] == "SaveMemory"
        assert s1["action_executed"] is True
        assert s1["data"]["memory"]["canonical_key"] == "appliance:ac.bedroom:temperature"

        # Step 2: Retrieve exact value from PostgreSQL
        s2 = client.post(
            "/api/v1/intent/process",
            json={"text": "What temperature do I prefer?", "language": "en", "speak": False},
        ).json()
        assert s2["intent"] == "RetrieveMemory"
        assert s2["action_executed"] is True
        assert "24" in s2["response_text"]

        # Step 3: Set bedroom AC to preferred temperature and verify persisted state
        s3 = client.post(
            "/api/v1/intent/process",
            json={"text": "Set my bedroom AC to my preferred temperature.", "language": "en", "speak": False},
        ).json()
        assert s3["intent"] == "DeviceControl"
        assert s3["action_executed"] is True
        assert s3["data"]["device"]["id"] == "ac.bedroom"
        assert s3["data"]["device"]["state"]["power"] is True
        assert s3["data"]["device"]["state"]["temperature"] == 24

        # Step 4: "Turn off the bedroom AC in one minute." -> persistent scheduled job
        s4 = client.post(
            "/api/v1/intent/process",
            json={"text": "Turn off the bedroom AC in one minute.", "language": "en", "speak": False},
        ).json()
        assert s4["intent"] == "ScheduleDeviceAction"
        assert s4["action_executed"] is True
        job_id = s4["data"]["schedule"]["id"]

        # Step 5: Observe upcoming scheduled operation in /schedules and /smart-home/activities
        upcoming = client.get("/api/v1/schedules").json()
        assert any(j["id"] == job_id and j["status"] == "scheduled" for j in upcoming)
        activities = client.get("/api/v1/smart-home/activities").json()
        assert any(j["id"] == job_id for j in activities["scheduled"])

        # Step 6: Advance controlled clock by 65s and run scheduler tick
        clock.advance(seconds=65)
        assert client.portal is not None
        ran = client.portal.call(app.state.scheduler_service.tick_once)
        assert len(ran) == 1
        assert ran[0]["execution"]["status"] == "completed"

        # Step 7: Verify bedroom AC actually switched off in PostgreSQL
        ac_after = client.get("/api/v1/smart-home/devices/ac.bedroom").json()
        assert ac_after["state"]["power"] is False

        # Step 8: Verify persistent notification exists in Notification Center
        notifs = client.get("/api/v1/notifications").json()
        assert notifs["unread_count"] >= 1
        assert any(
            it["notification_type"] == "scheduled_action_completed" and it["source_id"] == job_id
            for it in notifs["items"]
        )

    # Step 9: Restart the application completely
    app_restarted = create_app(_offline_settings(), clock=clock)
    with TestClient(app_restarted) as client_restarted:
        # Step 10: Verify saved memory, job execution history, and notifications survive restart
        mems = client_restarted.get("/api/v1/memory").json()
        assert mems["count"] == 1
        assert mems["items"][0]["structured_value"]["value"] == 24

        execs = client_restarted.get(f"/api/v1/schedules/{job_id}/executions").json()
        assert len(execs) == 1
        assert execs[0]["status"] == "completed"

        notifs_after = client_restarted.get("/api/v1/notifications").json()
        assert any(it["source_id"] == job_id for it in notifs_after["items"])

        # Repeat representative commands in Hindi and Kannada
        hi_rem = client_restarted.post(
            "/api/v1/intent/process",
            json={"text": "10 मिनट में मुझे पानी पीने की याद दिलाओ", "language": "hi", "speak": False},
        ).json()
        assert hi_rem["intent"] == "CreateReminder"
        assert hi_rem["action_executed"] is True
        assert hi_rem["response_language"] == "hi"

        kn_mem = client_restarted.post(
            "/api/v1/intent/process",
            json={"text": "ನೆನಪಿಡು ಬೆಡ್‌ರೂಮ್ ಏಸಿ 23 ಡಿಗ್ರಿ ಇರಲಿ", "language": "kn", "speak": False},
        ).json()
        assert kn_mem["intent"] == "SaveMemory"
        assert kn_mem["action_executed"] is True
        assert kn_mem["response_language"] == "kn"

        kn_pref = client_restarted.post(
            "/api/v1/intent/process",
            json={"text": "ನನ್ನ ಇಷ್ಟದ ಏಸಿ ತಾಪಮಾನ ಎಷ್ಟು?", "language": "kn", "speak": False},
        ).json()
        assert kn_pref["intent"] == "RetrieveMemory"
        assert kn_pref["action_executed"] is True
        assert "23" in kn_pref["response_text"]
