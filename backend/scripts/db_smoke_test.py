"""Real PostgreSQL + pgvector write/restart/read persistence smoke test."""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from aura_edge.config import get_settings
from aura_edge.data.database import create_db_engine
from aura_edge.data.models import Phase0VectorSmoke, SystemSetting

_SMOKE_VECTOR = [0.1, 0.2, 0.7]


def _setting_key(token: str) -> str:
    return f"phase0.smoke.{token}"


def _vector_label(token: str) -> str:
    return f"smoke-{token}"


def run_write(token: str) -> dict[str, object]:
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    with Session(engine) as session:
        ext_ver = session.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one_or_none()
        if not ext_ver:
            raise RuntimeError("pgvector extension ('vector') is not installed in database.")

        key = _setting_key(token)
        label = _vector_label(token)

        existing_setting = session.get(SystemSetting, key)
        if existing_setting is not None:
            session.delete(existing_setting)
        existing_vec = session.execute(
            select(Phase0VectorSmoke).where(Phase0VectorSmoke.label == label)
        ).scalar_one_or_none()
        if existing_vec is not None:
            session.delete(existing_vec)
        session.flush()

        session.add(
            SystemSetting(
                key=key,
                value_json={"token": token, "phase": 0, "persisted": True},
            )
        )
        session.add(
            Phase0VectorSmoke(
                label=label,
                embedding=_SMOKE_VECTOR,
            )
        )
        session.commit()

    engine.dispose()
    return {
        "phase": "write",
        "token": token,
        "pgvector_version": str(ext_ver),
        "setting_key": key,
        "vector_label": label,
        "status": "committed",
    }


def run_verify(token: str) -> dict[str, object]:
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    key = _setting_key(token)
    label = _vector_label(token)

    with Session(engine) as session:
        setting = session.get(SystemSetting, key)
        if setting is None or setting.value_json.get("token") != token:
            raise AssertionError(f"Expected persisted SystemSetting '{key}' not found or mismatched.")

        nearest = session.execute(
            select(
                Phase0VectorSmoke,
                Phase0VectorSmoke.embedding.l2_distance(_SMOKE_VECTOR).label("distance"),
            )
            .where(Phase0VectorSmoke.label == label)
            .order_by("distance")
        ).first()

        if nearest is None:
            raise AssertionError(f"Expected persisted Phase0VectorSmoke '{label}' not found.")

        row, distance = nearest
        if float(distance) > 1e-6:
            raise AssertionError(f"Unexpected pgvector L2 distance {distance} for '{label}'.")

        persisted_payload = dict(setting.value_json)
        persisted_distance = float(distance)

        # Clean up smoke rows after verification
        session.delete(setting)
        session.delete(row)
        session.commit()

    engine.dispose()
    return {
        "phase": "verify",
        "token": token,
        "setting_key": key,
        "vector_label": label,
        "value_json": persisted_payload,
        "l2_distance": persisted_distance,
        "cleaned_up": True,
        "status": "verified",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="AURA EDGE Phase 0 PostgreSQL + pgvector smoke test")
    parser.add_argument("--phase", choices=["write", "verify", "all-in-one"], required=True)
    parser.add_argument("--token", default="phase0-default")
    args = parser.parse_args()

    if args.phase == "write":
        print(json.dumps(run_write(args.token), indent=2))
    elif args.phase == "verify":
        print(json.dumps(run_verify(args.token), indent=2))
    else:
        w = run_write(args.token)
        v = run_verify(args.token)
        print(json.dumps({"write": w, "verify": v}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
