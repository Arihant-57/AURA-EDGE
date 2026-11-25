"""Persistent PostgreSQL timer CRUD endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from aura_edge.application.voice_orchestrator import VoiceOrchestrator
from aura_edge.execution.tools.timer_tool import TimerView
from aura_edge.intelligence.schemas import LanguageCode

router = APIRouter(prefix="/api/v1/timers", tags=["timers"])


class CreateTimerRequest(BaseModel):
    duration_seconds: int = Field(..., ge=1, le=86400)
    label: str | None = Field(default=None, max_length=128)
    language: LanguageCode = "en"


class TimerActionResponse(BaseModel):
    timer: TimerView | None = None
    message: str
    status: str


@router.get("", response_model=list[TimerView])
def list_timers(request: Request, include_closed: bool = False) -> list[TimerView]:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    statuses = None if include_closed else ("active", "paused")
    rows = orch.timer_manager.repo.list_timers(statuses=statuses)
    return [TimerView.from_record(r) for r in rows]


@router.post("", response_model=TimerActionResponse, status_code=status.HTTP_201_CREATED)
async def create_timer(body: CreateTimerRequest, request: Request) -> TimerActionResponse:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    res = await orch.timer_manager.create_timer(
        duration_seconds=body.duration_seconds,
        label=body.label,
        language=body.language,
    )
    if not res.success:
        raise HTTPException(status_code=400, detail=res.error or res.message)
    return TimerActionResponse(
        timer=TimerView(**res.data["timer"]),
        message=res.message,
        status=res.status,
    )


@router.get("/{timer_id}", response_model=TimerView)
def get_timer(timer_id: uuid.UUID, request: Request) -> TimerView:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    row = orch.timer_manager.repo.get_timer(timer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Timer not found.")
    return TimerView.from_record(row)


@router.post("/{timer_id}/pause", response_model=TimerActionResponse)
async def pause_timer(
    timer_id: uuid.UUID,
    request: Request,
    language: LanguageCode = "en",
) -> TimerActionResponse:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    res = await orch.timer_manager.pause_timer(timer_id=timer_id, language=language)
    if not res.success:
        raise HTTPException(status_code=404, detail=res.message)
    return TimerActionResponse(
        timer=TimerView(**res.data["timer"]),
        message=res.message,
        status=res.status,
    )


@router.post("/{timer_id}/resume", response_model=TimerActionResponse)
async def resume_timer(
    timer_id: uuid.UUID,
    request: Request,
    language: LanguageCode = "en",
) -> TimerActionResponse:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    res = await orch.timer_manager.resume_timer(timer_id=timer_id, language=language)
    if not res.success:
        raise HTTPException(status_code=404, detail=res.message)
    return TimerActionResponse(
        timer=TimerView(**res.data["timer"]),
        message=res.message,
        status=res.status,
    )


@router.delete("/{timer_id}", response_model=TimerActionResponse)
async def cancel_timer(
    timer_id: uuid.UUID,
    request: Request,
    language: LanguageCode = "en",
) -> TimerActionResponse:
    orch: VoiceOrchestrator = request.app.state.orchestrator
    res = await orch.timer_manager.cancel_timer(timer_id=timer_id, language=language)
    if not res.success:
        raise HTTPException(status_code=404, detail=res.message)
    return TimerActionResponse(
        timer=TimerView(**res.data["timer"]),
        message=res.message,
        status=res.status,
    )
