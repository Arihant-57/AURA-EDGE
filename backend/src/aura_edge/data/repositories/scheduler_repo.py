"""Authoritative PostgreSQL repository for persistent scheduled jobs and executions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from aura_edge.data.models import JobExecution, ScheduledJob


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class SchedulerRepository:
    """PostgreSQL job persistence with SKIP LOCKED claiming and unique occurrence dedup."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_job(
        self,
        *,
        job_type: str,
        title: str,
        language: str,
        payload_json: dict[str, Any],
        scheduled_at_utc: datetime,
        timezone_name: str,
        recurrence_json: dict[str, Any],
        missed_policy: str,
        device_id: str | None = None,
        routine_id: str | None = None,
        now_utc: datetime | None = None,
    ) -> ScheduledJob:
        now = now_utc or _utc_now()
        sched_utc = _ensure_utc(scheduled_at_utc)
        job = ScheduledJob(
            id=uuid.uuid4(),
            job_type=job_type,
            title=title[:200],
            language=language if language in ("en", "hi", "kn") else "en",
            payload_json=dict(payload_json),
            scheduled_at_utc=sched_utc,
            timezone_name=timezone_name[:64],
            recurrence_json=dict(recurrence_json or {"kind": "once"}),
            enabled=True,
            status="scheduled",
            missed_policy=missed_policy,
            next_run_at_utc=sched_utc,
            device_id=device_id,
            routine_id=routine_id,
            created_at_utc=now,
            updated_at_utc=now,
        )
        with Session(self.engine) as session:
            session.add(job)
            session.commit()
            session.refresh(job)
            session.expunge(job)
        return job

    def get_job(self, job_id: uuid.UUID) -> ScheduledJob | None:
        with Session(self.engine) as session:
            row = session.get(ScheduledJob, job_id)
            if row is not None:
                session.expunge(row)
            return row

    def list_jobs(
        self,
        *,
        job_type: str | None = None,
        statuses: tuple[str, ...] | None = None,
        enabled_only: bool = False,
        limit: int = 100,
    ) -> list[ScheduledJob]:
        with Session(self.engine) as session:
            stmt = select(ScheduledJob)
            if job_type:
                stmt = stmt.where(ScheduledJob.job_type == job_type)
            if statuses is not None:
                stmt = stmt.where(ScheduledJob.status.in_(statuses))
            if enabled_only:
                stmt = stmt.where(ScheduledJob.enabled.is_(True))
            stmt = stmt.order_by(
                ScheduledJob.next_run_at_utc.asc().nulls_last(),
                ScheduledJob.created_at_utc.desc(),
            ).limit(max(1, min(limit, 500)))
            rows = list(session.scalars(stmt).all())
            for r in rows:
                session.expunge(r)
            return rows

    def update_job(
        self,
        job_id: uuid.UUID,
        *,
        title: str | None = None,
        payload_json: dict[str, Any] | None = None,
        scheduled_at_utc: datetime | None = None,
        timezone_name: str | None = None,
        recurrence_json: dict[str, Any] | None = None,
        missed_policy: str | None = None,
        enabled: bool | None = None,
        next_run_at_utc: datetime | None = None,
        status: str | None = None,
        now_utc: datetime | None = None,
    ) -> ScheduledJob | None:
        now = now_utc or _utc_now()
        with Session(self.engine) as session:
            row = session.get(ScheduledJob, job_id)
            if row is None:
                return None
            if title is not None:
                row.title = title.strip()[:200]
            if payload_json is not None:
                row.payload_json = dict(payload_json)
            if scheduled_at_utc is not None:
                row.scheduled_at_utc = _ensure_utc(scheduled_at_utc)
            if next_run_at_utc is not None:
                row.next_run_at_utc = _ensure_utc(next_run_at_utc)
            if timezone_name is not None:
                row.timezone_name = timezone_name[:64]
            if recurrence_json is not None:
                row.recurrence_json = dict(recurrence_json)
            if missed_policy is not None:
                row.missed_policy = missed_policy
            if enabled is not None:
                row.enabled = bool(enabled)
            if status is not None:
                row.status = status
            row.updated_at_utc = now
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def pause_job(self, job_id: uuid.UUID, *, now_utc: datetime | None = None) -> ScheduledJob | None:
        now = now_utc or _utc_now()
        with Session(self.engine) as session:
            row = session.get(ScheduledJob, job_id)
            if row is None:
                return None
            if row.status in {"scheduled", "due"}:
                row.status = "paused"
                row.enabled = False
                row.updated_at_utc = now
                session.commit()
                session.refresh(row)
            session.expunge(row)
            return row

    def resume_job(
        self,
        job_id: uuid.UUID,
        *,
        next_run_at_utc: datetime | None,
        now_utc: datetime | None = None,
    ) -> ScheduledJob | None:
        now = now_utc or _utc_now()
        with Session(self.engine) as session:
            row = session.get(ScheduledJob, job_id)
            if row is None:
                return None
            if row.status == "paused":
                row.status = "scheduled"
                row.enabled = True
                if next_run_at_utc is not None:
                    row.next_run_at_utc = _ensure_utc(next_run_at_utc)
                row.updated_at_utc = now
                session.commit()
                session.refresh(row)
            session.expunge(row)
            return row

    def cancel_job(self, job_id: uuid.UUID, *, now_utc: datetime | None = None) -> ScheduledJob | None:
        now = now_utc or _utc_now()
        with Session(self.engine) as session:
            row = session.get(ScheduledJob, job_id)
            if row is None:
                return None
            row.status = "cancelled"
            row.enabled = False
            row.next_run_at_utc = None
            row.updated_at_utc = now
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def claim_due_jobs(
        self,
        *,
        now_utc: datetime,
        worker_id: str,
        batch_size: int = 10,
    ) -> list[tuple[ScheduledJob, JobExecution]]:
        """Atomically claim due jobs using FOR UPDATE SKIP LOCKED + unique occurrence insert."""
        now = _ensure_utc(now_utc)
        claimed: list[tuple[ScheduledJob, JobExecution]] = []

        with Session(self.engine) as session:
            stmt = (
                select(ScheduledJob)
                .where(
                    ScheduledJob.enabled.is_(True),
                    ScheduledJob.status.in_(("scheduled", "due")),
                    ScheduledJob.next_run_at_utc.is_not(None),
                    ScheduledJob.next_run_at_utc <= now,
                )
                .order_by(ScheduledJob.next_run_at_utc.asc())
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            jobs = list(session.scalars(stmt).all())
            for job in jobs:
                occ = job.next_run_at_utc
                if occ is None:
                    continue
                exec_id = uuid.uuid4()
                ins_stmt = (
                    pg_insert(JobExecution)
                    .values(
                        id=exec_id,
                        job_id=job.id,
                        scheduled_occurrence_utc=_ensure_utc(occ),
                        status="running",
                        started_at_utc=now,
                        worker_id=worker_id[:64],
                    )
                    .on_conflict_do_nothing(
                        constraint="uq_job_executions_job_occurrence",
                    )
                    .returning(JobExecution.id)
                )
                inserted_id = session.execute(ins_stmt).scalar_one_or_none()
                if inserted_id is None:
                    continue
                job.status = "running"
                job.updated_at_utc = now
                exec_row = session.get(JobExecution, inserted_id)
                assert exec_row is not None
                claimed.append((job, exec_row))

            session.commit()
            for job, exec_row in claimed:
                session.refresh(job)
                session.refresh(exec_row)
                session.expunge(job)
                session.expunge(exec_row)

        return claimed

    def complete_execution(
        self,
        execution_id: uuid.UUID,
        job_id: uuid.UUID,
        *,
        exec_status: str,
        job_status: str,
        next_run_at_utc: datetime | None,
        enabled: bool,
        result_json: dict[str, Any] | None,
        error_message: str | None,
        finished_at_utc: datetime,
    ) -> tuple[ScheduledJob, JobExecution]:
        fin = _ensure_utc(finished_at_utc)
        with Session(self.engine) as session:
            exec_row = session.get(JobExecution, execution_id)
            job_row = session.get(ScheduledJob, job_id)
            assert exec_row is not None and job_row is not None
            exec_row.status = exec_status
            exec_row.finished_at_utc = fin
            exec_row.result_json = result_json
            exec_row.error_message = error_message

            job_row.status = job_status
            job_row.enabled = enabled
            job_row.last_run_at_utc = fin
            job_row.next_run_at_utc = _ensure_utc(next_run_at_utc) if next_run_at_utc else None
            job_row.last_error = error_message
            job_row.updated_at_utc = fin

            session.commit()
            session.refresh(job_row)
            session.refresh(exec_row)
            session.expunge(job_row)
            session.expunge(exec_row)
            return job_row, exec_row

    def record_missed_occurrence(
        self,
        job_id: uuid.UUID,
        occurrence_utc: datetime,
        *,
        new_job_status: str,
        next_run_at_utc: datetime | None,
        enabled: bool,
        reason: str,
        now_utc: datetime,
    ) -> tuple[ScheduledJob, JobExecution] | None:
        now = _ensure_utc(now_utc)
        occ = _ensure_utc(occurrence_utc)
        with Session(self.engine) as session:
            ins_stmt = (
                pg_insert(JobExecution)
                .values(
                    id=uuid.uuid4(),
                    job_id=job_id,
                    scheduled_occurrence_utc=occ,
                    status="missed",
                    started_at_utc=now,
                    finished_at_utc=now,
                    error_message=reason,
                    worker_id="recovery",
                )
                .on_conflict_do_nothing(constraint="uq_job_executions_job_occurrence")
                .returning(JobExecution.id)
            )
            inserted_id = session.execute(ins_stmt).scalar_one_or_none()
            job_row = session.get(ScheduledJob, job_id)
            if job_row is None or inserted_id is None:
                session.rollback()
                return None

            job_row.status = new_job_status
            job_row.next_run_at_utc = _ensure_utc(next_run_at_utc) if next_run_at_utc else None
            job_row.enabled = enabled
            job_row.last_error = reason
            job_row.updated_at_utc = now

            exec_row = session.get(JobExecution, inserted_id)
            assert exec_row is not None
            session.commit()
            session.refresh(job_row)
            session.refresh(exec_row)
            session.expunge(job_row)
            session.expunge(exec_row)
            return job_row, exec_row

    def list_running_executions(self) -> list[tuple[ScheduledJob, JobExecution]]:
        with Session(self.engine) as session:
            stmt = (
                select(ScheduledJob, JobExecution)
                .join(JobExecution, JobExecution.job_id == ScheduledJob.id)
                .where(JobExecution.status == "running")
            )
            pairs = list(session.execute(stmt).all())
            out: list[tuple[ScheduledJob, JobExecution]] = []
            for job, ex in pairs:
                session.expunge(job)
                session.expunge(ex)
                out.append((job, ex))
            return out

    def list_executions(self, job_id: uuid.UUID, *, limit: int = 50) -> list[JobExecution]:
        with Session(self.engine) as session:
            rows = list(
                session.scalars(
                    select(JobExecution)
                    .where(JobExecution.job_id == job_id)
                    .order_by(JobExecution.started_at_utc.desc())
                    .limit(max(1, min(limit, 200)))
                ).all()
            )
            for r in rows:
                session.expunge(r)
            return rows
