"""Phase 3 tests for PostgreSQL scheduler, missed-job recovery, concurrency, and custom routines."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from aura_edge.config import ComputeMode, ConnectivityMode, Settings
from aura_edge.data.database import create_db_engine
from aura_edge.data.models import (
    JobExecution,
    NotificationPreference,
    NotificationRecord,
    ScheduledJob,
    SmartHomeDevice,
    SmartHomeRoutine,
)
from aura_edge.main import create_app


class _FakeClock:
    def __init__(self, start_utc: datetime) -> None:
        self.now = start_utc

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: Any) -> datetime:
        self.now = self.now + timedelta(**kwargs)
        return self.now


def _cleanup_phase3_tables() -> None:
    cfg = Settings(_env_file=None, connectivity_mode=ConnectivityMode.STRICT_OFFLINE, compute_mode=ComputeMode.CPU)
    engine = create_db_engine(cfg.database_url)
    with Session(engine) as session:
        session.execute(delete(JobExecution))
        session.execute(delete(ScheduledJob))
        session.execute(delete(NotificationRecord))
        session.execute(delete(NotificationPreference))
        session.execute(delete(SmartHomeRoutine).where(SmartHomeRoutine.is_builtin.is_(False)))
        # Ensure built-in routines remain enabled and ac.bedroom / light.bedroom_main available
        for dev_id in ("ac.bedroom", "light.bedroom_main", "sensor.bedroom_climate"):
            dev = session.get(SmartHomeDevice, dev_id)
            if dev is not None:
                dev.available = True
        session.commit()


@pytest.fixture(autouse=True)
def _cleanup() -> Generator[None, None, None]:
    _cleanup_phase3_tables()
    yield
    _cleanup_phase3_tables()


def _offline_settings() -> Settings:
    return Settings(
        _env_file=None,
        connectivity_mode=ConnectivityMode.STRICT_OFFLINE,
        compute_mode=ComputeMode.CPU,
        audio_post_playback_cooldown_sec=0.0,
        scheduler_poll_interval_sec=60.0,  # Avoid background interference; drive via tick_once()
    )


def test_one_time_daily_weekly_tz_pause_resume_and_cancel() -> None:
    # 2026-11-25 18:00 UTC == 2026-11-25 23:30 IST (Asia/Kolkata)
    clock = _FakeClock(datetime(2026, 11, 25, 18, 0, tzinfo=UTC))
    app = create_app(_offline_settings(), clock=clock)
    with TestClient(app) as client:
        client.put("/api/v1/notifications/preferences", json={"spoken_enabled": False})
        sched = app.state.scheduler_service
        ist = ZoneInfo("Asia/Kolkata")

        # 1. Daily recurring curtain opening at 7:00 AM IST (= 01:30 UTC)
        first_run_ist = datetime(2026, 11, 26, 7, 0, tzinfo=ist)
        daily = client.post(
            "/api/v1/schedules",
            json={
                "job_type": "device_action",
                "title": "Morning curtains",
                "device_id": "curtain.living",
                "action": "position",
                "value": 100,
                "run_at_utc": first_run_ist.astimezone(UTC).isoformat(),
                "timezone": "Asia/Kolkata",
                "recurrence": {"kind": "daily", "hour": 7, "minute": 0},
            },
        )
        assert daily.status_code == 201
        job = daily.json()["schedule"]
        job_id = job["id"]

        # Weekly reminder every Monday at 09:00 IST
        mon_ist = datetime(2026, 11, 30, 9, 0, tzinfo=ist)
        rem = client.post(
            "/api/v1/reminders",
            json={
                "message": "Weekly review",
                "run_at_utc": mon_ist.astimezone(UTC).isoformat(),
                "timezone": "Asia/Kolkata",
                "recurrence": {"kind": "weekly", "weekdays": [0], "hour": 9, "minute": 0},
            },
        )
        assert rem.status_code == 201
        rem_id = rem.json()["reminder"]["id"]

        # Pause & resume reminder
        paused = client.post(f"/api/v1/schedules/{rem_id}/pause").json()["schedule"]
        assert paused["status"] == "paused" and paused["enabled"] is False
        resumed = client.post(f"/api/v1/schedules/{rem_id}/resume").json()["schedule"]
        assert resumed["status"] == "scheduled" and resumed["enabled"] is True

        # Advance clock to 2026-11-26 07:01 IST -> daily curtain runs once & advances to Nov 27
        clock.now = datetime(2026, 11, 26, 7, 1, tzinfo=ist).astimezone(UTC)
        assert client.portal is not None
        ran = client.portal.call(sched.tick_once)
        assert len(ran) == 1
        assert ran[0]["execution"]["status"] == "completed"
        assert ran[0]["schedule"]["status"] == "scheduled"
        next_local = datetime.fromisoformat(ran[0]["schedule"]["next_run_at_utc"]).astimezone(ist)
        assert (next_local.month, next_local.day, next_local.hour) == (11, 27, 7)

        # Cancel daily job cleanly
        cancelled = client.delete(f"/api/v1/schedules/{job_id}").json()["schedule"]
        assert cancelled["status"] == "cancelled"
        assert cancelled["enabled"] is False


def test_missed_jobs_crash_recovery_and_concurrent_skip_locked_dedup() -> None:
    clock = _FakeClock(datetime(2026, 11, 25, 10, 0, tzinfo=UTC))
    app1 = create_app(_offline_settings(), clock=clock)
    with TestClient(app1) as client1:
        client1.put("/api/v1/notifications/preferences", json={"spoken_enabled": False})
        # A. Reminder scheduled for 10:05 UTC (missed_policy=notify_missed)
        r_rem = client1.post(
            "/api/v1/reminders",
            json={
                "message": "Take medicine",
                "run_at_utc": (clock.now + timedelta(minutes=5)).isoformat(),
                "missed_policy": "notify_missed",
            },
        ).json()["reminder"]

        # B. Appliance turn-on scheduled for 10:05 UTC (missed_policy=skip)
        r_dev = client1.post(
            "/api/v1/schedules",
            json={
                "job_type": "device_action",
                "device_id": "light.bedroom_main",
                "action": "power",
                "value": True,
                "run_at_utc": (clock.now + timedelta(minutes=5)).isoformat(),
                "missed_policy": "skip",
            },
        ).json()["schedule"]

        # C. Recurring daily routine scheduled at 10:05 UTC
        r_rec = client1.post(
            "/api/v1/schedules",
            json={
                "job_type": "routine",
                "routine_id": "morning",
                "run_at_utc": (clock.now + timedelta(minutes=5)).isoformat(),
                "recurrence": {"kind": "daily", "hour": 16, "minute": 0},
                "missed_policy": "skip",
            },
        ).json()["schedule"]

        # Ensure light starts OFF
        client1.post(
            "/api/v1/smart-home/devices/light.bedroom_main/command",
            json={"action": "power", "value": False, "source": "ui"},
        )

    # Simulate laptop asleep for 3 days -> restart at 2026-11-28 10:10 UTC
    clock.advance(days=3)
    app2 = create_app(_offline_settings(), clock=clock)
    with TestClient(app2) as client2:
        # Check missed reminder notification created & overdue device NOT executed
        notifs = client2.get("/api/v1/notifications").json()["items"]
        assert any(n["notification_type"] == "reminder_missed" and "Take medicine" in n["message"] for n in notifs)

        light = client2.get("/api/v1/smart-home/devices/light.bedroom_main").json()
        assert light["state"]["power"] is False

        assert client2.get(f"/api/v1/schedules/{r_rem['id']}").json()["status"] == "missed"
        assert client2.get(f"/api/v1/schedules/{r_dev['id']}").json()["status"] == "missed"

        # Recurring routine jumped directly to future occurrence without replaying 3 days!
        rec_after = client2.get(f"/api/v1/schedules/{r_rec['id']}").json()
        assert rec_after["status"] == "scheduled"
        assert len(client2.get(f"/api/v1/schedules/{r_rec['id']}/executions").json()) == 1

        # D. Concurrent claiming test: 2 workers attempt to claim same due job simultaneously
        due_time = clock.now + timedelta(seconds=30)
        conc_job = client2.post(
            "/api/v1/reminders",
            json={"message": "Concurrent test", "run_at_utc": due_time.isoformat()},
        ).json()["reminder"]

        clock.advance(seconds=35)
        repo = app2.state.scheduler_service.repo

        import anyio

        async def _race_claim() -> list[Any]:
            claims: list[Any] = []
            async with anyio.create_task_group() as tg:
                async def _w(wid: str) -> None:
                    res = await anyio.to_thread.run_sync(
                        lambda: repo.claim_due_jobs(now_utc=clock.now, worker_id=wid)
                    )
                    claims.append(res)

                tg.start_soon(_w, "worker-A")
                tg.start_soon(_w, "worker-B")
            return claims

        assert client2.portal is not None
        claims_a, claims_b = client2.portal.call(_race_claim)
        total_claimed = len(claims_a) + len(claims_b)
        assert total_claimed == 1

    # E. Crash recovery on next restart for stuck 'running' execution (from concurrent claim above)
    app3 = create_app(_offline_settings(), clock=clock)
    with TestClient(app3) as client3:
        ex_rows = client3.get(f"/api/v1/schedules/{conc_job['id']}/executions").json()
        assert ex_rows[0]["status"] == "failed"
        assert ex_rows[0]["error_message"] == "interrupted_by_restart_uncertain_state"


def test_unavailable_device_scheduled_failure_and_custom_routine_engine() -> None:
    clock = _FakeClock(datetime(2026, 11, 25, 12, 0, tzinfo=UTC))
    app = create_app(_offline_settings(), clock=clock)
    with TestClient(app) as client:
        client.put("/api/v1/notifications/preferences", json={"spoken_enabled": False})
        # 1. Validation rejects impossible capability when creating custom routine or schedule
        bad_sched = client.post(
            "/api/v1/schedules",
            json={
                "job_type": "device_action",
                "device_id": "fan.bedroom_ceiling",
                "action": "temperature",
                "value": 24,
                "run_at_utc": (clock.now + timedelta(minutes=1)).isoformat(),
            },
        )
        assert bad_sched.status_code == 422

        bad_routine = client.post(
            "/api/v1/smart-home/routines",
            json={
                "name": "Bad Routine",
                "actions": [{"device_id": "ac.bedroom", "action": "temperature", "value": 99}],
            },
        )
        assert bad_routine.status_code == 422

        # 2. Schedule AC turn-on, then mark device unavailable -> execution reports failed honestly
        sched_res = client.post(
            "/api/v1/schedules",
            json={
                "job_type": "device_action",
                "device_id": "ac.bedroom",
                "action": "power",
                "value": True,
                "run_at_utc": (clock.now + timedelta(seconds=30)).isoformat(),
            },
        ).json()["schedule"]
        client.put("/api/v1/smart-home/simulator/devices/ac.bedroom/availability", json={"available": False})
        clock.advance(seconds=35)
        assert client.portal is not None
        client.portal.call(app.state.scheduler_service.tick_once)
        ex = client.get(f"/api/v1/schedules/{sched_res['id']}/executions").json()
        assert ex[0]["status"] == "failed"
        assert "unavailable" in (ex[0]["error_message"] or "").lower()

        # 3. Custom routine with condition, partial failure, edit, and delete protection on builtins
        cool_routine = client.post(
            "/api/v1/smart-home/routines",
            json={
                "name": "Hot Afternoon",
                "triggers": [
                    {
                        "type": "simulator_event",
                        "event": "sensor_threshold",
                        "device_id": "sensor.bedroom_climate",
                        "property": "temperature",
                        "op": "gte",
                        "value": 30,
                        "cooldown_sec": 5,
                    }
                ],
                "conditions": [
                    {
                        "kind": "sensor_threshold",
                        "device_id": "sensor.bedroom_climate",
                        "property": "temperature",
                        "op": "gte",
                        "value": 30,
                    }
                ],
                "actions": [
                    {"device_id": "fan.bedroom_ceiling", "action": "speed", "value": 4},
                    {"device_id": "ac.bedroom", "action": "power", "value": True},
                ],
            },
        )
        assert cool_routine.status_code == 201
        r_id = cool_routine.json()["routine"]["id"]

        # Manual run when sensor temp is 25 (<30) -> skipped_condition
        client.post(
            "/api/v1/smart-home/simulator/devices/sensor.bedroom_climate/sensor",
            json={"state": {"temperature": 25.0}},
        )
        skipped = client.post(f"/api/v1/smart-home/routines/{r_id}/run").json()
        assert skipped["status"] == "skipped_condition"

        # Inject temp 32 (>=30) -> simulator_event triggers routine automatically;
        # fan succeeds while unavailable ac.bedroom fails -> status == "partial"!
        client.post(
            "/api/v1/smart-home/simulator/devices/sensor.bedroom_climate/sensor",
            json={"state": {"temperature": 32.0}},
        )
        fan = client.get("/api/v1/smart-home/devices/fan.bedroom_ceiling").json()
        assert fan["state"]["speed"] == 4

        partial = client.post(f"/api/v1/smart-home/routines/{r_id}/run").json()
        assert partial["status"] == "partial"
        assert partial["results"][0]["status"] == "verified"
        assert partial["results"][1]["status"] == "failed"

        # Loop prevention: nested routine action rejected at validation & runtime
        loop_bad = client.post(
            "/api/v1/smart-home/routines",
            json={
                "name": "Loop Bad",
                "actions": [{"device_id": f"routine.{r_id}", "action": "routine", "value": r_id}],
            },
        )
        assert loop_bad.status_code == 422

        # Built-in routines cannot be deleted, custom ones can
        assert client.delete("/api/v1/smart-home/routines/morning").status_code == 409
        assert client.delete(f"/api/v1/smart-home/routines/{r_id}").status_code == 200
