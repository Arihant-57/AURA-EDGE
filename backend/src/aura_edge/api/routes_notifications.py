"""Phase 3 persistent local notifications & preferences REST API."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from aura_edge.application.notification_service import NotificationService

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class UpdateNotificationPrefsRequest(BaseModel):
    visual_enabled: bool | None = None
    spoken_enabled: bool | None = None
    quiet_hours_enabled: bool | None = None
    quiet_hours_start: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    quiet_hours_end: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    timezone_name: str | None = Field(default=None, max_length=64)


@router.get("")
def list_notifications(
    request: Request,
    unread_only: bool = False,
    notification_type: str | None = None,
    include_dismissed: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    svc: NotificationService = request.app.state.notification_service
    items = svc.list_notifications(
        unread_only=unread_only,
        notification_type=notification_type,
        include_dismissed=include_dismissed,
        limit=limit,
    )
    return {
        "unread_count": svc.unread_count(),
        "count": len(items),
        "items": items,
    }


@router.get("/unread-count")
def get_unread_count(request: Request) -> dict[str, int]:
    svc: NotificationService = request.app.state.notification_service
    return {"unread_count": svc.unread_count()}


@router.post("/read-all")
async def mark_all_read(request: Request) -> dict[str, Any]:
    svc: NotificationService = request.app.state.notification_service
    updated = await svc.mark_all_read()
    return {"status": "verified", "updated_count": updated, "unread_count": svc.unread_count()}


@router.get("/preferences")
def get_preferences(request: Request) -> dict[str, Any]:
    svc: NotificationService = request.app.state.notification_service
    return svc.get_preferences()


@router.put("/preferences")
async def update_preferences(
    body: UpdateNotificationPrefsRequest, request: Request
) -> dict[str, Any]:
    svc: NotificationService = request.app.state.notification_service
    try:
        return await svc.update_preferences(
            visual_enabled=body.visual_enabled,
            spoken_enabled=body.spoken_enabled,
            quiet_hours_enabled=body.quiet_hours_enabled,
            quiet_hours_start=body.quiet_hours_start,
            quiet_hours_end=body.quiet_hours_end,
            timezone_name=body.timezone_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{notification_id}/read")
async def mark_read(notification_id: uuid.UUID, request: Request) -> dict[str, Any]:
    svc: NotificationService = request.app.state.notification_service
    item = await svc.mark_read(notification_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return {"status": "verified", "notification": item, "unread_count": svc.unread_count()}


@router.post("/{notification_id}/dismiss")
async def dismiss_notification(notification_id: uuid.UUID, request: Request) -> dict[str, Any]:
    svc: NotificationService = request.app.state.notification_service
    item = await svc.dismiss(notification_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return {"status": "verified", "notification": item, "unread_count": svc.unread_count()}
