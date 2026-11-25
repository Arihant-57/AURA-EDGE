"""Measure real Phase 3 memory, embedding, scheduler, and simple-command latencies."""

from __future__ import annotations

import functools
import json
import statistics
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from aura_edge.config import ComputeMode, ConnectivityMode, Settings
from aura_edge.data.database import create_db_engine
from aura_edge.data.models import (
    JobExecution,
    MemoryItem,
    MemorySetting,
    NotificationRecord,
    ScheduledJob,
)
from aura_edge.main import create_app


def _cleanup(engine: Any) -> None:
    with Session(engine) as session:
        session.execute(delete(JobExecution))
        session.execute(delete(ScheduledJob))
        session.execute(delete(NotificationRecord))
        session.execute(delete(MemoryItem))
        session.execute(delete(MemorySetting))
        session.commit()


def main() -> None:
    cfg = Settings(
        _env_file=None,
        connectivity_mode=ConnectivityMode.STRICT_OFFLINE,
        compute_mode=ComputeMode.CPU,
        audio_post_playback_cooldown_sec=0.0,
        scheduler_poll_interval_sec=60.0,
    )
    engine = create_db_engine(cfg.database_url)
    _cleanup(engine)

    now_ref = datetime(2026, 11, 25, 16, 0, tzinfo=UTC)
    app = create_app(cfg, clock=lambda: now_ref)
    results: dict[str, Any] = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "embedding_model": cfg.embedding_model,
        "embedding_dim": cfg.embedding_dim,
        "measurements_ms": {},
    }

    with TestClient(app) as client:
        client.put("/api/v1/notifications/preferences", json={"spoken_enabled": False})
        # 1. Seed a memory with CPU embedding
        t0 = time.perf_counter()
        client.post(
            "/api/v1/memory",
            json={"original_text": "Remember I prefer bedroom AC at 24 degrees", "language": "en"},
        )
        results["measurements_ms"]["memory_save_with_cpu_embed_ms"] = round(
            (time.perf_counter() - t0) * 1000.0, 2
        )

        # 2. Exact structured lookup latency (10 trials)
        exact_ms: list[float] = []
        for _ in range(10):
            ts = time.perf_counter()
            r = client.get("/api/v1/memory/search", params={"q": "bedroom AC temperature"})
            assert r.status_code == 200
            exact_ms.append((time.perf_counter() - ts) * 1000.0)
        results["measurements_ms"]["exact_lookup_median_ms"] = round(statistics.median(exact_ms), 2)

        # 3. Semantic pgvector lookup latency (5 warm trials)
        client.post(
            "/api/v1/memory",
            json={"original_text": "I like warm lighting while reading novels at night", "language": "en"},
        )
        sem_ms: list[float] = []
        for _ in range(5):
            ts = time.perf_counter()
            r = client.get("/api/v1/memory/search", params={"q": "studying books after sunset"})
            assert r.status_code == 200
            sem_ms.append((time.perf_counter() - ts) * 1000.0)
        results["measurements_ms"]["semantic_search_warm_median_ms"] = round(
            statistics.median(sem_ms), 2
        )

        # 4. Schedule create + tick execution latency
        ts = time.perf_counter()
        sched_r = client.post(
            "/api/v1/schedules",
            json={
                "job_type": "device_action",
                "device_id": "light.bedroom_main",
                "action": "power",
                "value": True,
                "run_at_utc": (now_ref + timedelta(seconds=10)).isoformat(),
            },
        )
        assert sched_r.status_code == 201
        results["measurements_ms"]["schedule_create_ms"] = round((time.perf_counter() - ts) * 1000.0, 2)

        now_ref = now_ref + timedelta(seconds=15)
        ts = time.perf_counter()
        assert client.portal is not None
        client.portal.call(functools.partial(app.state.scheduler_service.tick_once, now_utc=now_ref))
        results["measurements_ms"]["scheduler_tick_claim_exec_ms"] = round(
            (time.perf_counter() - ts) * 1000.0, 2
        )

        # 5. Simple command latency unaffected (GetTime, DeviceControl)
        time_ms: list[float] = []
        for _ in range(10):
            ts = time.perf_counter()
            client.post("/api/v1/intent/process", json={"text": "What time is it?", "speak": False})
            time_ms.append((time.perf_counter() - ts) * 1000.0)
        results["measurements_ms"]["simple_get_time_median_ms"] = round(
            statistics.median(time_ms), 2
        )

    _cleanup(engine)
    out_path = Path(__file__).resolve().parents[2] / "models" / "eval_audio" / "phase3_benchmarks.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
