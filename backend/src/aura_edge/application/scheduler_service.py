"""PostgreSQL-backed persistent scheduler for reminders, smart-home actions, and routines."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Engine

from aura_edge.api.ws_events import WebSocketEventHub
from aura_edge.application.notification_service import NotificationService
from aura_edge.application.smart_home import SmartHomeService
from aura_edge.config import Settings
from aura_edge.data.models import JobExecution, ScheduledJob
from aura_edge.data.repositories.scheduler_repo import SchedulerRepository
from aura_edge.intelligence.schemas import LanguageCode

logger = logging.getLogger(__name__)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _resolve_tz(tz_name: str | None, default_tz: str = "Asia/Kolkata") -> ZoneInfo:
    name = (tz_name or default_tz).strip()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone '{name}'.") from exc


def compute_next_occurrence_utc(
    recurrence: dict[str, Any] | None,
    tz_name: str,
    after_utc: datetime,
) -> datetime | None:
    """Compute the next valid future UTC run timestamp from a recurrence rule."""
    rec = recurrence or {"kind": "once"}
    kind = str(rec.get("kind", "once")).lower()
    if kind == "once":
        return None

    tz = _resolve_tz(tz_name)
    ref_local = _ensure_utc(after_utc).astimezone(tz)
    hour = int(rec.get("hour", ref_local.hour))
    minute = int(rec.get("minute", ref_local.minute))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Recurrence hour must be 0..23 and minute 0..59.")

    if kind == "daily":
        candidate = ref_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= ref_local:
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)

    if kind == "weekly":
        raw_days = rec.get("weekdays") or [ref_local.weekday()]
        weekdays = sorted({int(d) % 7 for d in raw_days})
        if not weekdays:
            raise ValueError("Weekly recurrence requires at least one weekday (0..6).")
        for offset in range(15):
            day_dt = (ref_local + timedelta(days=offset)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            if day_dt.weekday() in weekdays and day_dt > ref_local:
                return day_dt.astimezone(UTC)
        return None

    raise ValueError(f"Unsupported recurrence kind '{kind}'.")


def _format_local_due(dt_utc: datetime, tz_name: str) -> str:
    tz = _resolve_tz(tz_name)
    local_dt = _ensure_utc(dt_utc).astimezone(tz)
    return local_dt.strftime("%Y-%m-%d %I:%M %p %Z")


class SchedulerService:
    """Persistent job scheduler with restart recovery, atomic claiming, and missed-job policies."""

    def __init__(
        self,
        engine: Engine,
        smart_home: SmartHomeService,
        notification_service: NotificationService,
        ws_hub: WebSocketEventHub,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.repo = SchedulerRepository(engine)
        self.smart_home = smart_home
        self.notification_service = notification_service
        self.ws_hub = ws_hub
        self.settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self.worker_id = worker_id or f"sched-{uuid.uuid4().hex[:8]}"
        self._poll_task: asyncio.Task[None] | None = None

    @staticmethod
    def job_to_dict(row: ScheduledJob) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "job_type": row.job_type,
            "title": row.title,
            "language": row.language,
            "payload": dict(row.payload_json or {}),
            "scheduled_at_utc": row.scheduled_at_utc.isoformat(),
            "timezone": row.timezone_name,
            "recurrence": dict(row.recurrence_json or {"kind": "once"}),
            "enabled": row.enabled,
            "status": row.status,
            "missed_policy": row.missed_policy,
            "next_run_at_utc": row.next_run_at_utc.isoformat() if row.next_run_at_utc else None,
            "last_run_at_utc": row.last_run_at_utc.isoformat() if row.last_run_at_utc else None,
            "last_error": row.last_error,
            "device_id": row.device_id,
            "routine_id": row.routine_id,
            "created_at_utc": row.created_at_utc.isoformat(),
            "updated_at_utc": row.updated_at_utc.isoformat(),
        }

    @staticmethod
    def execution_to_dict(row: JobExecution) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "job_id": str(row.job_id),
            "scheduled_occurrence_utc": row.scheduled_occurrence_utc.isoformat(),
            "status": row.status,
            "started_at_utc": row.started_at_utc.isoformat(),
            "finished_at_utc": row.finished_at_utc.isoformat() if row.finished_at_utc else None,
            "result": row.result_json,
            "error_message": row.error_message,
            "worker_id": row.worker_id,
        }

    async def startup(self) -> dict[str, int]:
        stats = await self.recover_on_startup()
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())
        return stats

    async def shutdown(self) -> None:
        task = self._poll_task
        self._poll_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _poll_loop(self) -> None:
        interval = max(0.2, float(self.settings.scheduler_poll_interval_sec))
        while True:
            try:
                await asyncio.sleep(interval)
                await self.tick_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler poll loop error")

    def _validate_recurrence(self, recurrence: dict[str, Any] | None, tz_name: str) -> dict[str, Any]:
        rec = dict(recurrence or {"kind": "once"})
        kind = str(rec.get("kind", "once")).lower()
        if kind not in {"once", "daily", "weekly"}:
            raise ValueError("Recurrence kind must be 'once', 'daily', or 'weekly'.")
        _resolve_tz(tz_name, self.settings.default_timezone)
        if kind in {"daily", "weekly"}:
            hour = int(rec.get("hour", 0))
            minute = int(rec.get("minute", 0))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("Recurrence hour/minute out of bounds.")
            rec["hour"] = hour
            rec["minute"] = minute
        if kind == "weekly":
            days = rec.get("weekdays")
            if not isinstance(days, list) or not days:
                raise ValueError("Weekly recurrence requires non-empty weekdays list (0..6).")
            rec["weekdays"] = sorted({int(d) % 7 for d in days})
        rec["kind"] = kind
        return rec

    async def create_reminder(
        self,
        *,
        message: str,
        run_at_utc: datetime,
        title: str | None = None,
        timezone_name: str | None = None,
        recurrence: dict[str, Any] | None = None,
        language: LanguageCode = "en",
        missed_policy: str = "notify_missed",
    ) -> dict[str, Any]:
        clean_msg = " ".join((message or "").split()).strip()
        if not clean_msg:
            raise ValueError("Reminder content cannot be empty.")
        tz_name = timezone_name or self.settings.default_timezone
        _resolve_tz(tz_name)
        rec = self._validate_recurrence(recurrence, tz_name)
        if missed_policy not in {"notify_missed", "skip", "execute_if_safe"}:
            raise ValueError("Invalid missed_policy.")
        sched_utc = _ensure_utc(run_at_utc)
        now = _ensure_utc(self._clock())
        job = self.repo.create_job(
            job_type="reminder",
            title=(title or clean_msg)[:200],
            language=language,
            payload_json={"message": clean_msg},
            scheduled_at_utc=sched_utc,
            timezone_name=tz_name,
            recurrence_json=rec,
            missed_policy=missed_policy,
            now_utc=now,
        )
        view = self.job_to_dict(job)
        await self.ws_hub.broadcast("reminder_created", {"reminder": view})
        await self.ws_hub.broadcast("schedule_updated", {"action": "created", "schedule": view})
        return view

    async def create_device_schedule(
        self,
        *,
        device_id: str,
        action: str,
        value: Any,
        run_at_utc: datetime,
        title: str | None = None,
        timezone_name: str | None = None,
        recurrence: dict[str, Any] | None = None,
        language: LanguageCode = "en",
        missed_policy: str = "skip",
    ) -> dict[str, Any]:
        dev = self.smart_home.validate_device_command(device_id, action, value)
        tz_name = timezone_name or self.settings.default_timezone
        _resolve_tz(tz_name)
        rec = self._validate_recurrence(recurrence, tz_name)
        if missed_policy not in {"notify_missed", "skip", "execute_if_safe"}:
            raise ValueError("Invalid missed_policy.")
        sched_utc = _ensure_utc(run_at_utc)
        now = _ensure_utc(self._clock())
        default_title = f"{dev.name}: {action}={value}"
        job = self.repo.create_job(
            job_type="device_action",
            title=(title or default_title)[:200],
            language=language,
            payload_json={"device_id": device_id, "action": action, "value": value},
            scheduled_at_utc=sched_utc,
            timezone_name=tz_name,
            recurrence_json=rec,
            missed_policy=missed_policy,
            device_id=device_id,
            now_utc=now,
        )
        view = self.job_to_dict(job)
        await self.ws_hub.broadcast("schedule_updated", {"action": "created", "schedule": view})
        return view

    async def create_routine_schedule(
        self,
        *,
        routine_id: str,
        run_at_utc: datetime,
        title: str | None = None,
        timezone_name: str | None = None,
        recurrence: dict[str, Any] | None = None,
        language: LanguageCode = "en",
        missed_policy: str = "skip",
    ) -> dict[str, Any]:
        routine = self.smart_home.get_routine(routine_id)
        if routine is None:
            raise LookupError(f"Routine '{routine_id}' not found.")
        tz_name = timezone_name or self.settings.default_timezone
        _resolve_tz(tz_name)
        rec = self._validate_recurrence(recurrence, tz_name)
        if missed_policy not in {"notify_missed", "skip", "execute_if_safe"}:
            raise ValueError("Invalid missed_policy.")
        sched_utc = _ensure_utc(run_at_utc)
        now = _ensure_utc(self._clock())
        job = self.repo.create_job(
            job_type="routine",
            title=(title or f"Run {routine['name']} routine")[:200],
            language=language,
            payload_json={"routine_id": routine_id},
            scheduled_at_utc=sched_utc,
            timezone_name=tz_name,
            recurrence_json=rec,
            missed_policy=missed_policy,
            routine_id=routine_id,
            now_utc=now,
        )
        view = self.job_to_dict(job)
        await self.ws_hub.broadcast("schedule_updated", {"action": "created", "schedule": view})
        return view

    def list_jobs(
        self,
        *,
        job_type: str | None = None,
        statuses: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return [
            self.job_to_dict(j)
            for j in self.repo.list_jobs(job_type=job_type, statuses=statuses, limit=limit)
        ]

    def get_job(self, job_id: uuid.UUID) -> dict[str, Any] | None:
        row = self.repo.get_job(job_id)
        return self.job_to_dict(row) if row is not None else None

    def list_executions(self, job_id: uuid.UUID, *, limit: int = 50) -> list[dict[str, Any]]:
        return [self.execution_to_dict(e) for e in self.repo.list_executions(job_id, limit=limit)]

    def list_upcoming_smart_home_schedules(self) -> list[dict[str, Any]]:
        rows = self.repo.list_jobs(statuses=("scheduled", "due", "running", "paused"), limit=100)
        return [
            self.job_to_dict(r)
            for r in rows
            if r.job_type in {"device_action", "routine"}
        ]

    async def update_job(
        self,
        job_id: uuid.UUID,
        *,
        title: str | None = None,
        message: str | None = None,
        run_at_utc: datetime | None = None,
        timezone_name: str | None = None,
        recurrence: dict[str, Any] | None = None,
        missed_policy: str | None = None,
    ) -> dict[str, Any] | None:
        existing = self.repo.get_job(job_id)
        if existing is None:
            return None
        tz_name = timezone_name or existing.timezone_name
        _resolve_tz(tz_name)
        rec = (
            self._validate_recurrence(recurrence, tz_name)
            if recurrence is not None
            else existing.recurrence_json
        )
        payload = dict(existing.payload_json or {})
        if message is not None and existing.job_type == "reminder":
            clean_msg = " ".join(message.split()).strip()
            if not clean_msg:
                raise ValueError("Reminder content cannot be empty.")
            payload["message"] = clean_msg
            if title is None:
                title = clean_msg

        now = _ensure_utc(self._clock())
        updated = self.repo.update_job(
            job_id,
            title=title,
            payload_json=payload,
            scheduled_at_utc=run_at_utc,
            next_run_at_utc=run_at_utc,
            timezone_name=tz_name,
            recurrence_json=rec,
            missed_policy=missed_policy,
            status="scheduled" if run_at_utc is not None and existing.status in {"missed", "completed", "failed"} else None,
            enabled=True if run_at_utc is not None and existing.status in {"missed", "completed", "failed"} else None,
            now_utc=now,
        )
        if updated is None:
            return None
        view = self.job_to_dict(updated)
        await self.ws_hub.broadcast("schedule_updated", {"action": "updated", "schedule": view})
        return view

    async def pause_job(self, job_id: uuid.UUID) -> dict[str, Any] | None:
        row = self.repo.pause_job(job_id, now_utc=_ensure_utc(self._clock()))
        if row is None:
            return None
        view = self.job_to_dict(row)
        await self.ws_hub.broadcast("schedule_updated", {"action": "paused", "schedule": view})
        return view

    async def resume_job(self, job_id: uuid.UUID) -> dict[str, Any] | None:
        existing = self.repo.get_job(job_id)
        if existing is None:
            return None
        now = _ensure_utc(self._clock())
        next_run = existing.next_run_at_utc
        if next_run is None or _ensure_utc(next_run) <= now:
            rec_next = compute_next_occurrence_utc(existing.recurrence_json, existing.timezone_name, now)
            if rec_next is not None:
                next_run = rec_next
        row = self.repo.resume_job(job_id, next_run_at_utc=next_run, now_utc=now)
        if row is None:
            return None
        view = self.job_to_dict(row)
        await self.ws_hub.broadcast("schedule_updated", {"action": "resumed", "schedule": view})
        return view

    async def cancel_job(self, job_id: uuid.UUID) -> dict[str, Any] | None:
        row = self.repo.cancel_job(job_id, now_utc=_ensure_utc(self._clock()))
        if row is None:
            return None
        view = self.job_to_dict(row)
        await self.ws_hub.broadcast("schedule_updated", {"action": "cancelled", "schedule": view})
        return view

    async def tick_once(self, *, now_utc: datetime | None = None) -> list[dict[str, Any]]:
        """Claim and execute all due scheduled jobs atomically at *now_utc*."""
        now = _ensure_utc(now_utc or self._clock())
        claimed = self.repo.claim_due_jobs(now_utc=now, worker_id=self.worker_id)
        outputs: list[dict[str, Any]] = []
        for job, execution in claimed:
            out = await self._execute_claimed_job(job, execution, now)
            outputs.append(out)
        return outputs

    async def _execute_claimed_job(
        self,
        job: ScheduledJob,
        execution: JobExecution,
        now_utc: datetime,
    ) -> dict[str, Any]:
        occ_iso = _ensure_utc(execution.scheduled_occurrence_utc).isoformat()
        dedup_key = f"job:{job.id}:{occ_iso}"
        lang: LanguageCode = job.language if job.language in ("en", "hi", "kn") else "en"  # type: ignore[assignment]
        payload = dict(job.payload_json or {})

        exec_status = "completed"
        result_json: dict[str, Any] = {}
        err_msg: str | None = None

        try:
            if job.job_type == "reminder":
                rem_msg = str(payload.get("message") or job.title)
                spoken = {
                    "en": f"Reminder: {rem_msg}",
                    "hi": f"रिमाइंडर: {rem_msg}",
                    "kn": f"ಜ್ಞಾಪನೆ: {rem_msg}",
                }[lang]
                notif, _ = await self.notification_service.notify(
                    notification_type="reminder_due",
                    title=job.title,
                    message=spoken,
                    language=lang,
                    source_id=str(job.id),
                    priority="high",
                    dedup_key=dedup_key,
                    metadata={"job_id": str(job.id), "occurrence_utc": occ_iso},
                    speak=True,
                )
                result_json = {"notification_id": notif["id"], "message": rem_msg}
            elif job.job_type == "device_action":
                dev_id = str(payload.get("device_id", ""))
                action = str(payload.get("action", ""))
                value = payload.get("value")
                self.smart_home.validate_device_command(dev_id, action, value)
                updated_dev = await self.smart_home.command(
                    dev_id,
                    action,
                    value,
                    source="schedule",
                    context_id=str(execution.id),
                )
                dev_dict = self.smart_home.to_dict(updated_dev)
                result_json = {"device": dev_dict}
                notif_msg = {
                    "en": f"Scheduled action completed: {updated_dev.name}.",
                    "hi": f"निर्धारित कार्य पूरा हुआ: {updated_dev.name}।",
                    "kn": f"ನಿಗದಿತ ಕ್ರಿಯೆ ಪೂರ್ಣಗೊಂಡಿದೆ: {updated_dev.name}.",
                }[lang]
                await self.notification_service.notify(
                    notification_type="scheduled_action_completed",
                    title=job.title,
                    message=notif_msg,
                    language=lang,
                    source_id=str(job.id),
                    priority="normal",
                    dedup_key=dedup_key,
                    metadata={"job_id": str(job.id), "device": dev_dict},
                    speak=True,
                )
            elif job.job_type == "routine":
                routine_id = str(payload.get("routine_id") or job.routine_id or "")
                res = await self.smart_home.run_routine(routine_id, source="schedule")
                result_json = res
                if res["status"] == "partial":
                    exec_status = "partial"
                elif res["status"] == "skipped_condition":
                    exec_status = "failed"
                    err_msg = "Routine conditions were not met. No steps ran."
                elif res["status"] == "failed":
                    exec_status = "failed"
                    err_msg = "All routine actions failed."
        except (LookupError, RuntimeError, ValueError) as exc:
            exec_status = "failed"
            err_msg = str(exc)
            result_json = {"error": err_msg}
            fail_msg = {
                "en": f"Scheduled task '{job.title}' failed: {err_msg}",
                "hi": f"निर्धारित कार्य '{job.title}' विफल रहा: {err_msg}",
                "kn": f"ನಿಗದಿತ ಕೆಲಸ '{job.title}' ವಿಫಲವಾಗಿದೆ: {err_msg}",
            }[lang]
            await self.notification_service.notify(
                notification_type="scheduled_action_failed",
                title=f"Failed: {job.title}",
                message=fail_msg,
                language=lang,
                source_id=str(job.id),
                priority="high",
                dedup_key=f"{dedup_key}:failed",
                metadata={"job_id": str(job.id), "error": err_msg},
                speak=True,
            )

        next_occ = compute_next_occurrence_utc(
            job.recurrence_json,
            job.timezone_name,
            _ensure_utc(execution.scheduled_occurrence_utc),
        )
        # Bounded catch-up: if next_occ is already in the past, jump strictly into the future
        if next_occ is not None and next_occ <= now_utc:
            next_occ = compute_next_occurrence_utc(job.recurrence_json, job.timezone_name, now_utc)

        if next_occ is not None:
            job_status = "scheduled"
            job_enabled = True
        else:
            job_status = "completed" if exec_status in {"completed", "partial"} else "failed"
            job_enabled = False

        updated_job, updated_exec = self.repo.complete_execution(
            execution.id,
            job.id,
            exec_status=exec_status,
            job_status=job_status,
            next_run_at_utc=next_occ,
            enabled=job_enabled,
            result_json=result_json,
            error_message=err_msg,
            finished_at_utc=now_utc,
        )
        job_view = self.job_to_dict(updated_job)
        exec_view = self.execution_to_dict(updated_exec)

        if job.job_type == "reminder" and exec_status == "completed":
            await self.ws_hub.broadcast(
                "reminder_due",
                {"reminder": job_view, "execution": exec_view},
            )
        if exec_status == "failed":
            await self.ws_hub.broadcast(
                "schedule_failed",
                {"schedule": job_view, "execution": exec_view, "error": err_msg},
            )
        else:
            await self.ws_hub.broadcast(
                "schedule_executed",
                {"schedule": job_view, "execution": exec_view},
            )
        await self.ws_hub.broadcast("schedule_updated", {"action": "executed", "schedule": job_view})
        return {"schedule": job_view, "execution": exec_view}

    async def recover_on_startup(self, *, now_utc: datetime | None = None) -> dict[str, int]:
        """Recover interrupted running jobs safely and apply explicit missed-job policies."""
        now = _ensure_utc(now_utc or self._clock())
        interrupted_count = 0
        missed_count = 0
        due_soon_count = 0

        # 1. Crash recovery for any execution stuck in 'running' (do NOT blindly re-execute!)
        for job, ex in self.repo.list_running_executions():
            next_occ = compute_next_occurrence_utc(job.recurrence_json, job.timezone_name, now)
            updated_job, _updated_ex = self.repo.complete_execution(
                ex.id,
                job.id,
                exec_status="failed",
                job_status="scheduled" if next_occ is not None else "failed",
                next_run_at_utc=next_occ,
                enabled=next_occ is not None,
                result_json={"uncertain_state": True},
                error_message="interrupted_by_restart_uncertain_state",
                finished_at_utc=now,
            )
            interrupted_count += 1
            await self.notification_service.notify(
                notification_type="scheduled_action_failed",
                title=f"Interrupted task: {job.title}",
                message=f"'{job.title}' was interrupted during restart and was not re-run automatically.",
                language=job.language if job.language in ("en", "hi", "kn") else "en",  # type: ignore[arg-type]
                source_id=str(job.id),
                priority="high",
                dedup_key=f"interrupted:{ex.id}",
                metadata={"job_id": str(job.id), "execution_id": str(ex.id)},
                speak=False,
            )
            await self.ws_hub.broadcast(
                "schedule_failed",
                {"schedule": self.job_to_dict(updated_job), "error": "interrupted_by_restart_uncertain_state"},
            )

        # 2. Apply explicit missed-job policies for overdue scheduled jobs
        grace = timedelta(seconds=max(0.0, float(self.settings.scheduler_missed_grace_sec)))
        cutoff = now - grace
        active_jobs = self.repo.list_jobs(statuses=("scheduled", "due"), enabled_only=True, limit=250)

        for job in active_jobs:
            if job.next_run_at_utc is None:
                continue
            occ = _ensure_utc(job.next_run_at_utc)
            if occ > cutoff:
                if occ <= now:
                    due_soon_count += 1
                continue

            if job.missed_policy == "execute_if_safe":
                due_soon_count += 1
                continue

            next_occ = compute_next_occurrence_utc(job.recurrence_json, job.timezone_name, now)
            new_status = "scheduled" if next_occ is not None else "missed"
            recorded = self.repo.record_missed_occurrence(
                job.id,
                occ,
                new_job_status=new_status,
                next_run_at_utc=next_occ,
                enabled=next_occ is not None,
                reason=f"missed_while_offline ({job.missed_policy})",
                now_utc=now,
            )
            if recorded is None:
                continue
            updated_job, _ = recorded
            missed_count += 1

            due_str = _format_local_due(occ, job.timezone_name)
            lang: LanguageCode = job.language if job.language in ("en", "hi", "kn") else "en"  # type: ignore[assignment]
            if job.job_type == "reminder":
                msg = {
                    "en": f"Missed reminder from {due_str}: {job.payload_json.get('message', job.title)}",
                    "hi": f"{due_str} का छूटा हुआ रिमाइंडर: {job.payload_json.get('message', job.title)}",
                    "kn": f"{due_str} ರಂದು ತಪ್ಪಿದ ಜ್ಞಾಪನೆ: {job.payload_json.get('message', job.title)}",
                }[lang]
                notif_type = "reminder_missed"
            else:
                msg = {
                    "en": f"Skipped overdue scheduled task '{job.title}' (was due {due_str}).",
                    "hi": f"समय निकल जाने के कारण '{job.title}' ({due_str}) को नहीं चलाया गया।",
                    "kn": f"ಸಮಯ ಮೀರಿದ ಕಾರಣ '{job.title}' ({due_str}) ಅನ್ನು ಬಿಟ್ಟುಬಿಡಲಾಗಿದೆ.",
                }[lang]
                notif_type = "scheduled_action_missed"

            await self.notification_service.notify(
                notification_type=notif_type,
                title=f"Missed: {job.title}",
                message=msg,
                language=lang,
                source_id=str(job.id),
                priority="high" if job.job_type == "reminder" else "normal",
                dedup_key=f"missed:{job.id}:{occ.isoformat()}",
                metadata={"job_id": str(job.id), "original_due_utc": occ.isoformat()},
                speak=(job.job_type == "reminder"),
            )
            await self.ws_hub.broadcast(
                "schedule_updated",
                {"action": "missed_recovered", "schedule": self.job_to_dict(updated_job)},
            )

        return {
            "interrupted": interrupted_count,
            "missed": missed_count,
            "due_ready": due_soon_count,
        }
