"""Phase 4 integration tests: Tasks, Offline Calendar, and Daily Briefing."""

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
    CalendarEventRecord,
    JobExecution,
    NotificationPreference,
    NotificationRecord,
    ScheduledJob,
    TaskRecord,
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


def _offline_settings() -> Settings:
    return Settings(
        _env_file=None,
        connectivity_mode=ConnectivityMode.STRICT_OFFLINE,
        compute_mode=ComputeMode.CPU,
    )


def _cleanup_phase4() -> None:
    engine = create_db_engine(_offline_settings().database_url)
    with Session(engine) as session:
        session.execute(delete(TaskRecord))
        session.execute(delete(CalendarEventRecord))
        session.execute(delete(JobExecution))
        session.execute(delete(ScheduledJob))
        session.execute(delete(NotificationRecord))
        session.execute(delete(NotificationPreference))
        session.commit()


@pytest.fixture(autouse=True)
def _cleanup() -> Generator[None, None, None]:
    _cleanup_phase4()
    yield
    _cleanup_phase4()


def test_task_lifecycle_with_scheduler_reminder_and_restart() -> None:
    # 2026-11-26 09:00 IST == 03:30 UTC
    clock = _ControllableClock(datetime(2026, 11, 26, 3, 30, tzinfo=UTC))
    app = create_app(_offline_settings(), clock=clock)

    with TestClient(app) as client:
        client.put("/api/v1/notifications/preferences", json={"spoken_enabled": False})

        due = (clock.now + timedelta(minutes=30)).isoformat()
        created = client.post(
            "/api/v1/tasks",
            json={
                "title": "Submit project slides",
                "priority": "high",
                "category": "work",
                "due_at_utc": due,
                "create_reminder": True,
            },
        )
        assert created.status_code == 201
        task = created.json()["task"]
        assert task["status"] == "pending"
        assert task["reminder_job_id"] is not None

        # Voice intent add & list tasks
        v_add = client.post(
            "/api/v1/intent/process",
            json={"text": "Add task buy notebooks", "speak": False},
        ).json()
        assert v_add["intent"] == "CreateTask"
        assert v_add["action_executed"] is True

        v_list = client.post(
            "/api/v1/intent/process",
            json={"text": "List my pending tasks", "speak": False},
        ).json()
        assert v_list["intent"] == "ListTasks"
        assert v_list["data"]["count"] == 2

        # Complete task via REST -> also cancels linked reminder job
        comp = client.post(f"/api/v1/tasks/{task['id']}/complete")
        assert comp.status_code == 200
        assert comp.json()["task"]["status"] == "completed"
        sched = client.get("/api/v1/reminders").json()
        assert all(r["id"] != task["reminder_job_id"] or r["status"] == "cancelled" for r in sched)

    # Verify persistence across app restart
    app2 = create_app(_offline_settings(), clock=clock)
    with TestClient(app2) as client2:
        pending = client2.get("/api/v1/tasks", params={"status": "pending"}).json()["items"]
        assert len(pending) == 1
        assert pending[0]["title"] == "buy notebooks"


def test_offline_calendar_conflicts_recurrence_and_daily_briefing() -> None:
    # 2026-11-26 08:00 IST == 02:30 UTC
    clock = _ControllableClock(datetime(2026, 11, 26, 2, 30, tzinfo=UTC))
    app = create_app(_offline_settings(), clock=clock)

    with TestClient(app) as client:
        client.put("/api/v1/notifications/preferences", json={"spoken_enabled": False})

        # Create primary event 10:00-11:00 IST (04:30-05:30 UTC), daily recurring, reminder 15m before
        ev1 = client.post(
            "/api/v1/calendar/events",
            json={
                "title": "Team Standup",
                "start_at_utc": datetime(2026, 11, 26, 4, 30, tzinfo=UTC).isoformat(),
                "end_at_utc": datetime(2026, 11, 26, 5, 30, tzinfo=UTC).isoformat(),
                "location": "Lab 2",
                "recurrence": {"kind": "daily"},
                "reminder_minutes_before": 15,
            },
        )
        assert ev1.status_code == 201
        assert ev1.json()["conflicts"] == []
        assert ev1.json()["event"]["reminder_job_id"] is not None

        # Overlapping event at 10:30-11:30 IST -> conflict reported accurately
        ev2 = client.post(
            "/api/v1/calendar/events",
            json={
                "title": "Demo Dry Run",
                "start_at_utc": datetime(2026, 11, 26, 5, 0, tzinfo=UTC).isoformat(),
                "end_at_utc": datetime(2026, 11, 26, 6, 0, tzinfo=UTC).isoformat(),
            },
        )
        assert ev2.status_code == 201
        assert len(ev2.json()["conflicts"]) == 1
        assert ev2.json()["conflicts"][0]["title"] == "Team Standup"

        # Tomorrow query expands daily recurring Standup!
        tmrw = client.get("/api/v1/calendar/events", params={"range_kind": "tomorrow"}).json()
        assert any(it["title"] == "Team Standup" for it in tmrw["items"])

        # Add a task & a smart-home schedule so Daily Briefing aggregates real DB data
        client.post("/api/v1/tasks", json={"title": "Charge battery pack", "create_reminder": False})
        client.post(
            "/api/v1/intent/process",
            json={"text": "Turn off bedroom light in 10 minutes", "speak": False},
        )

        briefing = client.get("/api/v1/briefing", params={"language": "en"}).json()
        assert briefing["counts"]["events_today"] == 2
        assert briefing["counts"]["pending_tasks"] == 1
        assert briefing["counts"]["scheduled_device_actions"] == 1
        assert "Team Standup" in briefing["summary_text"]
        assert "Charge battery pack" in briefing["summary_text"]

        # Voice Daily Briefing in Hindi & Kannada (verified DB data, zero fabrication)
        v_hi = client.post(
            "/api/v1/intent/process",
            json={"text": "आज की ब्रीफिंग", "language": "hi", "speak": False},
        ).json()
        assert v_hi["intent"] == "DailyBriefing"
        assert v_hi["action_executed"] is True
        assert "2 कैलेंडर इवेंट" in v_hi["response_text"]

        v_kn = client.post(
            "/api/v1/intent/process",
            json={"text": "ಇಂದಿನ ಬ್ರೀಫಿಂಗ್", "language": "kn", "speak": False},
        ).json()
        assert v_kn["intent"] == "DailyBriefing"
        assert v_kn["action_executed"] is True
        assert "2 ಕ್ಯಾಲೆಂಡರ್ ಕಾರ್ಯಕ್ರಮಗಳು" in v_kn["response_text"]
