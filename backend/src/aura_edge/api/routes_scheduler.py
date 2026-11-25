"""Phase 3 persistent reminders and scheduled job REST API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from aura_edge.application.scheduler_service import SchedulerService
from aura_edge.intelligence.schemas import LanguageCode

router = APIRouter(prefix="/api/v1", tags=["scheduler"])

MissedPolicy = Literal["notify_missed", "skip", "execute_if_safe"]


class CreateReminderRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    title: str | None = Field(default=None, max_length=200)
    run_at_utc: datetime
    timezone: str | None = Field(default=None, max_length=64)
    recurrence: dict[str, Any] | None = None
    language: LanguageCode = "en"
    missed_policy: MissedPolicy = "notify_missed"


class UpdateReminderRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    message: str | None = Field(default=None, max_length=500)
    run_at_utc: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)
    recurrence: dict[str, Any] | None = None
    missed_policy: MissedPolicy | None = None


class CreateScheduleRequest(BaseModel):
    job_type: Literal["device_action", "routine", "reminder"]
    title: str | None = Field(default=None, max_length=200)
    run_at_utc: datetime
    timezone: str | None = Field(default=None, max_length=64)
    recurrence: dict[str, Any] | None = None
    language: LanguageCode = "en"
    missed_policy: MissedPolicy | None = None
    # Fields by job_type
    message: str | None = Field(default=None, max_length=500)
    device_id: str | None = Field(default=None, max_length=96)
    action: str | None = Field(default=None, max_length=48)
    value: Any = None
    routine_id: str | None = Field(default=None, max_length=48)


class UpdateScheduleRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    message: str | None = Field(default=None, max_length=500)
    run_at_utc: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)
    recurrence: dict[str, Any] | None = None
    missed_policy: MissedPolicy | None = None


@router.get("/reminders")
def list_reminders(
    request: Request, include_closed: bool = False, limit: int = 100
) -> list[dict[str, Any]]:
    sched: SchedulerService = request.app.state.scheduler_service
    statuses = None if include_closed else ("scheduled", "due", "running", "paused")
    return sched.list_jobs(job_type="reminder", statuses=statuses, limit=limit)


@router.post("/reminders", status_code=status.HTTP_201_CREATED)
async def create_reminder(body: CreateReminderRequest, request: Request) -> dict[str, Any]:
    sched: SchedulerService = request.app.state.scheduler_service
    try:
        item = await sched.create_reminder(
            message=body.message,
            title=body.title,
            run_at_utc=body.run_at_utc,
            timezone_name=body.timezone,
            recurrence=body.recurrence,
            language=body.language,
            missed_policy=body.missed_policy,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "verified", "reminder": item}


@router.patch("/reminders/{job_id}")
async def update_reminder(
    job_id: uuid.UUID, body: UpdateReminderRequest, request: Request
) -> dict[str, Any]:
    sched: SchedulerService = request.app.state.scheduler_service
    try:
        item = await sched.update_job(
            job_id,
            title=body.title,
            message=body.message,
            run_at_utc=body.run_at_utc,
            timezone_name=body.timezone,
            recurrence=body.recurrence,
            missed_policy=body.missed_policy,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Reminder not found.")
    return {"status": "verified", "reminder": item}


@router.delete("/reminders/{job_id}")
async def cancel_reminder(job_id: uuid.UUID, request: Request) -> dict[str, Any]:
    sched: SchedulerService = request.app.state.scheduler_service
    item = await sched.cancel_job(job_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Reminder not found.")
    return {"status": "verified", "reminder": item}


@router.get("/schedules")
def list_schedules(
    request: Request,
    job_type: Literal["reminder", "device_action", "routine"] | None = None,
    include_closed: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    sched: SchedulerService = request.app.state.scheduler_service
    statuses = None if include_closed else ("scheduled", "due", "running", "paused")
    return sched.list_jobs(job_type=job_type, statuses=statuses, limit=limit)


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
async def create_schedule(body: CreateScheduleRequest, request: Request) -> dict[str, Any]:
    sched: SchedulerService = request.app.state.scheduler_service
    try:
        if body.job_type == "reminder":
            item = await sched.create_reminder(
                message=body.message or body.title or "",
                title=body.title,
                run_at_utc=body.run_at_utc,
                timezone_name=body.timezone,
                recurrence=body.recurrence,
                language=body.language,
                missed_policy=body.missed_policy or "notify_missed",
            )
        elif body.job_type == "device_action":
            if not body.device_id or not body.action:
                raise ValueError("device_action schedule requires device_id and action.")
            item = await sched.create_device_schedule(
                device_id=body.device_id,
                action=body.action,
                value=body.value,
                title=body.title,
                run_at_utc=body.run_at_utc,
                timezone_name=body.timezone,
                recurrence=body.recurrence,
                language=body.language,
                missed_policy=body.missed_policy or "skip",
            )
        else:
            if not body.routine_id:
                raise ValueError("routine schedule requires routine_id.")
            item = await sched.create_routine_schedule(
                routine_id=body.routine_id,
                title=body.title,
                run_at_utc=body.run_at_utc,
                timezone_name=body.timezone,
                recurrence=body.recurrence,
                language=body.language,
                missed_policy=body.missed_policy or "skip",
            )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "verified", "schedule": item}


@router.get("/schedules/{job_id}")
def get_schedule(job_id: uuid.UUID, request: Request) -> dict[str, Any]:
    sched: SchedulerService = request.app.state.scheduler_service
    item = sched.get_job(job_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return item


@router.patch("/schedules/{job_id}")
async def update_schedule(
    job_id: uuid.UUID, body: UpdateScheduleRequest, request: Request
) -> dict[str, Any]:
    sched: SchedulerService = request.app.state.scheduler_service
    try:
        item = await sched.update_job(
            job_id,
            title=body.title,
            message=body.message,
            run_at_utc=body.run_at_utc,
            timezone_name=body.timezone,
            recurrence=body.recurrence,
            missed_policy=body.missed_policy,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return {"status": "verified", "schedule": item}


@router.post("/schedules/{job_id}/pause")
async def pause_schedule(job_id: uuid.UUID, request: Request) -> dict[str, Any]:
    sched: SchedulerService = request.app.state.scheduler_service
    item = await sched.pause_job(job_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return {"status": "verified", "schedule": item}


@router.post("/schedules/{job_id}/resume")
async def resume_schedule(job_id: uuid.UUID, request: Request) -> dict[str, Any]:
    sched: SchedulerService = request.app.state.scheduler_service
    item = await sched.resume_job(job_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return {"status": "verified", "schedule": item}


@router.delete("/schedules/{job_id}")
async def cancel_schedule(job_id: uuid.UUID, request: Request) -> dict[str, Any]:
    sched: SchedulerService = request.app.state.scheduler_service
    item = await sched.cancel_job(job_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return {"status": "verified", "schedule": item}


@router.get("/schedules/{job_id}/executions")
def list_schedule_executions(
    job_id: uuid.UUID, request: Request, limit: int = 50
) -> list[dict[str, Any]]:
    sched: SchedulerService = request.app.state.scheduler_service
    if sched.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return sched.list_executions(job_id, limit=limit)
