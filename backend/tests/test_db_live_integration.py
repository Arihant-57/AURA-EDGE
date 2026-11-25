"""Live PostgreSQL + pgvector persistence & vector query integration tests."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from aura_edge.config import get_settings
from aura_edge.data.database import check_database_readiness, create_db_engine
from aura_edge.data.models import ConnectivityAuditEvent, Phase0VectorSmoke, SystemSetting


def test_live_postgres_pgvector_roundtrip_and_distance_query() -> None:
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    try:
        readiness = check_database_readiness(engine)
        if not readiness.connected:
            if os.environ.get("AURA_EDGE_REQUIRE_LIVE_DB") == "1":
                pytest.fail(f"PostgreSQL required but unreachable: {readiness.error}")
            pytest.skip(f"Local PostgreSQL unreachable: {readiness.error}")

        assert readiness.pgvector_installed is True, readiness.error
        assert readiness.alembic_revision in {
            "0001_phase0_foundation",
            "0002_phase1_voice_timers",
            "0003_phase2_smart_home",
            "0004_phase3_memory_events",
            "0005_phase4_personal_assistant",
        }

        token = uuid.uuid4().hex[:12]
        key = f"pytest.setting.{token}"
        label = f"pytest-vec-{token}"

        with Session(engine) as session:
            session.add(SystemSetting(key=key, value_json={"token": token, "ok": True}))
            session.add(Phase0VectorSmoke(label=label, embedding=[0.2, 0.4, 0.8]))
            session.add(
                ConnectivityAuditEvent(
                    mode="strict_offline",
                    target="https://example.com",
                    allowed=False,
                    reason="pytest verification",
                )
            )
            session.commit()

        with Session(engine) as session:
            loaded_setting = session.get(SystemSetting, key)
            assert loaded_setting is not None
            assert loaded_setting.value_json["token"] == token

            row = session.execute(
                select(
                    Phase0VectorSmoke,
                    Phase0VectorSmoke.embedding.l2_distance([0.2, 0.4, 0.8]).label("dist"),
                ).where(Phase0VectorSmoke.label == label)
            ).one()
            vec_row, distance = row
            assert float(distance) < 1e-6

            # Cleanup test records
            session.delete(loaded_setting)
            session.delete(vec_row)
            session.query(ConnectivityAuditEvent).filter(
                ConnectivityAuditEvent.reason == "pytest verification"
            ).delete()
            session.commit()
    finally:
        engine.dispose()
