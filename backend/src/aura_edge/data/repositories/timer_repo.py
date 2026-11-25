"""Authoritative PostgreSQL repository for persistent timers & voice turn metrics."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from aura_edge.data.models import TimerRecord, VoiceTurnMetric


def _utc_now() -> datetime:
    return datetime.now(UTC)


def compute_remaining_seconds(record: TimerRecord, now: datetime | None = None) -> int:
    """Compute exact remaining seconds for an active/paused timer."""
    if record.status in ("completed", "cancelled", "missed"):
        return 0
    if record.status == "paused" or record.fire_at_utc is None:
        return max(0, int(record.remaining_seconds))
    ref = now or _utc_now()
    fire_at = record.fire_at_utc
    if fire_at.tzinfo is None:
        fire_at = fire_at.replace(tzinfo=UTC)
    delta = (fire_at - ref).total_seconds()
    return max(0, math.ceil(delta))


class TimerRepository:
    """Atomic PostgreSQL operations for countdown timers."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_timer(
        self,
        *,
        duration_seconds: int,
        label: str | None = None,
        language: str = "en",
    ) -> TimerRecord:
        if duration_seconds <= 0 or duration_seconds > 86400:
            raise ValueError("Timer duration must be between 1 and 86400 seconds.")
        now = _utc_now()
        fire_at = now + timedelta(seconds=duration_seconds)
        cleaned_label = label.strip()[:128] if label and label.strip() else None
        record = TimerRecord(
            id=uuid.uuid4(),
            label=cleaned_label,
            duration_seconds=duration_seconds,
            remaining_seconds=duration_seconds,
            status="active",
            language=language,
            fire_at_utc=fire_at,
            created_at_utc=now,
            updated_at_utc=now,
        )
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            session.expunge(record)
        return record

    def get_timer(self, timer_id: uuid.UUID) -> TimerRecord | None:
        with Session(self.engine) as session:
            record = session.get(TimerRecord, timer_id)
            if record is not None:
                session.expunge(record)
            return record

    def list_timers(
        self,
        *,
        statuses: tuple[str, ...] | None = ("active", "paused"),
        limit: int = 50,
    ) -> list[TimerRecord]:
        with Session(self.engine) as session:
            stmt = select(TimerRecord)
            if statuses is not None:
                stmt = stmt.where(TimerRecord.status.in_(statuses))
            stmt = stmt.order_by(TimerRecord.created_at_utc.desc()).limit(limit)
            rows = list(session.scalars(stmt).all())
            for row in rows:
                session.expunge(row)
            return rows

    def find_matching_timers(
        self,
        *,
        label: str | None = None,
        statuses: tuple[str, ...] = ("active", "paused"),
    ) -> list[TimerRecord]:
        rows = self.list_timers(statuses=statuses)
        if not label or not label.strip():
            return rows
        needle = label.strip().lower()
        exact = [r for r in rows if r.label and r.label.strip().lower() == needle]
        if exact:
            return exact
        return [r for r in rows if r.label and needle in r.label.strip().lower()]

    def pause_timer(self, timer_id: uuid.UUID) -> TimerRecord | None:
        now = _utc_now()
        with Session(self.engine) as session:
            record = session.get(TimerRecord, timer_id)
            if record is None or record.status != "active":
                if record is not None:
                    session.expunge(record)
                return None if record is None else record
            remaining = compute_remaining_seconds(record, now=now)
            record.status = "paused"
            record.remaining_seconds = min(record.duration_seconds, max(0, remaining))
            record.fire_at_utc = None
            record.updated_at_utc = now
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record

    def resume_timer(self, timer_id: uuid.UUID) -> TimerRecord | None:
        now = _utc_now()
        with Session(self.engine) as session:
            record = session.get(TimerRecord, timer_id)
            if record is None:
                return None
            if record.status != "paused":
                session.expunge(record)
                return record
            rem = max(1, int(record.remaining_seconds))
            record.status = "active"
            record.fire_at_utc = now + timedelta(seconds=rem)
            record.updated_at_utc = now
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record

    def cancel_timer(self, timer_id: uuid.UUID) -> TimerRecord | None:
        now = _utc_now()
        with Session(self.engine) as session:
            record = session.get(TimerRecord, timer_id)
            if record is None:
                return None
            if record.status in ("active", "paused"):
                record.status = "cancelled"
                record.remaining_seconds = 0
                record.fire_at_utc = None
                record.completed_at_utc = now
                record.updated_at_utc = now
                session.commit()
                session.refresh(record)
            session.expunge(record)
            return record

    def cancel_all_open(self) -> list[TimerRecord]:
        now = _utc_now()
        with Session(self.engine) as session:
            rows = list(
                session.scalars(
                    select(TimerRecord).where(TimerRecord.status.in_(("active", "paused")))
                ).all()
            )
            for row in rows:
                row.status = "cancelled"
                row.remaining_seconds = 0
                row.fire_at_utc = None
                row.completed_at_utc = now
                row.updated_at_utc = now
            session.commit()
            for row in rows:
                session.refresh(row)
                session.expunge(row)
            return rows

    def mark_completed_once(
        self,
        timer_id: uuid.UUID,
        *,
        final_status: str = "completed",
    ) -> TimerRecord | None:
        """Atomically transition an active timer to completed/missed exactly once.

        Returns the updated TimerRecord ONLY for the single caller that won the
        compare-and-set update (`status='active' AND notified_at_utc IS NULL`).
        Subsequent or concurrent calls return ``None``, preventing duplicate events.
        """
        if final_status not in ("completed", "missed"):
            raise ValueError("final_status must be 'completed' or 'missed'.")
        now = _utc_now()
        with Session(self.engine) as session:
            stmt = (
                update(TimerRecord)
                .where(
                    TimerRecord.id == timer_id,
                    TimerRecord.status == "active",
                    TimerRecord.notified_at_utc.is_(None),
                )
                .values(
                    status=final_status,
                    remaining_seconds=0,
                    notified_at_utc=now,
                    completed_at_utc=now,
                    updated_at_utc=now,
                )
                .returning(TimerRecord.id)
            )
            won_id = session.execute(stmt).scalar_one_or_none()
            session.commit()
            if won_id is None:
                return None
            record = session.get(TimerRecord, won_id)
            if record is not None:
                session.expunge(record)
            return record


class VoiceMetricRepository:
    """Persists real per-turn timing & accuracy telemetry to PostgreSQL."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def record_turn(
        self,
        *,
        turn_id: str,
        source: str,
        language: str,
        requested_compute_mode: str,
        effective_compute_mode: str,
        intent_name: str,
        intent_source: str,
        final_state: str,
        stt_ms: float | None = None,
        router_ms: float | None = None,
        llm_ms: float | None = None,
        tool_ms: float | None = None,
        tts_ms: float | None = None,
        ttfa_ms: float | None = None,
        e2e_ms: float | None = None,
    ) -> None:
        row = VoiceTurnMetric(
            turn_id=turn_id,
            source=source,
            language=language,
            requested_compute_mode=requested_compute_mode,
            effective_compute_mode=effective_compute_mode,
            intent_name=intent_name,
            intent_source=intent_source,
            final_state=final_state,
            stt_ms=stt_ms,
            router_ms=router_ms,
            llm_ms=llm_ms,
            tool_ms=tool_ms,
            tts_ms=tts_ms,
            ttfa_ms=ttfa_ms,
            e2e_ms=e2e_ms,
        )
        with Session(self.engine) as session:
            session.add(row)
            session.commit()
