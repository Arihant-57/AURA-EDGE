"""Live PostgreSQL tests for persistent timers, restart recovery, and duplicate prevention."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from aura_edge.config import get_settings
from aura_edge.data.database import check_database_readiness, create_db_engine
from aura_edge.data.models import TimerRecord
from aura_edge.execution.tools.timer_tool import TimerManager


async def test_live_postgres_timers_crud_ambiguity_restart_and_dedup() -> None:
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    created_ids: list[uuid.UUID] = []

    try:
        readiness = check_database_readiness(engine)
        if not readiness.connected:
            if os.environ.get("AURA_EDGE_REQUIRE_LIVE_DB") == "1":
                pytest.fail(f"PostgreSQL required but unreachable: {readiness.error}")
            pytest.skip(f"PostgreSQL unreachable: {readiness.error}")

        assert readiness.alembic_revision in {
            "0003_phase2_smart_home",
            "0004_phase3_memory_events",
        }

        events: list[tuple[str, str]] = []

        async def on_event(ev_type: str, view) -> None:
            events.append((ev_type, view.id))

        mgr = TimerManager(engine, on_timer_event=on_event)

        # 1. Create multiple named timers
        r1 = await mgr.create_timer(duration_seconds=300, label="chai", language="hi")
        r2 = await mgr.create_timer(duration_seconds=600, label="rice", language="kn")
        assert r1.success and r2.success
        t1_id = uuid.UUID(r1.data["timer"]["id"])
        t2_id = uuid.UUID(r2.data["timer"]["id"])
        created_ids.extend([t1_id, t2_id])

        # 2. Ambiguous pause without label when 2 timers active -> asks clarification, mutates nothing
        ambig = await mgr.pause_timer(language="en")
        assert ambig.success is False
        assert ambig.status == "clarification_required"

        # 3. Named pause -> remaining -> resume -> cancel
        paused = await mgr.pause_timer(label="chai", language="hi")
        assert paused.success and paused.data["timer"]["status"] == "paused"
        rem = mgr.get_remaining(label="chai", language="hi")
        assert rem.success and rem.data["timer"]["remaining_seconds"] > 0
        resumed = await mgr.resume_timer(label="chai", language="hi")
        assert resumed.success and resumed.data["timer"]["status"] == "active"

        # 4. Duplicate completion guard
        won1 = mgr.repo.mark_completed_once(t1_id, final_status="completed")
        won2 = mgr.repo.mark_completed_once(t1_id, final_status="completed")
        assert won1 is not None and won1.status == "completed"
        assert won2 is None

        # 5. Simulate restart recovery: stop in-memory manager, create a past-due active timer,
        #    and instantiate a brand-new TimerManager calling recover_on_startup()
        await mgr.shutdown()

        overdue = mgr.repo.create_timer(duration_seconds=10, label="missed_toast", language="en")
        created_ids.append(overdue.id)
        from sqlalchemy.orm import Session

        with Session(engine) as session:
            db_row = session.get(TimerRecord, overdue.id)
            assert db_row is not None
            db_row.fire_at_utc = datetime.now(UTC) - timedelta(seconds=5)
            session.commit()

        recovered_events: list[tuple[str, str]] = []

        async def on_recovered(ev_type: str, view) -> None:
            recovered_events.append((ev_type, view.id))

        mgr2 = TimerManager(engine, on_timer_event=on_recovered)
        rec_stats = await mgr2.recover_on_startup()
        assert rec_stats["recovered"] >= 1  # rice timer still active
        assert rec_stats["missed"] >= 1  # missed_toast marked missed once
        assert ("timer_missed", str(overdue.id)) in recovered_events

        # Calling recover_on_startup again must NOT emit duplicate missed notification
        rec_stats2 = await mgr2.recover_on_startup()
        assert rec_stats2["missed"] == 0

        await mgr2.cancel_timer(timer_id=t2_id, language="kn")
        await mgr2.shutdown()
    finally:
        if created_ids:
            from sqlalchemy.orm import Session

            with Session(engine) as session:
                session.execute(delete(TimerRecord).where(TimerRecord.id.in_(created_ids)))
                session.commit()
        engine.dispose()
