"""Phase 2 smart-home simulator REST API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from aura_edge.application.smart_home import SmartHomeService

router = APIRouter(prefix="/api/v1/smart-home", tags=["smart-home"])


class DeviceCommand(BaseModel):
    action: str = Field(min_length=1, max_length=48)
    value: Any
    source: str = Field(
        default="ui", pattern="^(ui|voice|routine|simulator|schedule|event_routine)$"
    )
    context_id: str | None = Field(default=None, max_length=64)


class CreateRoutineRequest(BaseModel):
    id: str | None = Field(default=None, max_length=48)
    name: str = Field(..., min_length=1, max_length=96)
    description: str | None = Field(default=None, max_length=500)
    aliases: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    triggers: list[dict[str, Any]] = Field(default_factory=lambda: [{"type": "manual"}])
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(..., min_length=1)


class UpdateRoutineRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=96)
    description: str | None = Field(default=None, max_length=500)
    aliases: dict[str, Any] | None = None
    enabled: bool | None = None
    triggers: list[dict[str, Any]] | None = None
    conditions: list[dict[str, Any]] | None = None
    actions: list[dict[str, Any]] | None = None


class AvailabilityRequest(BaseModel):
    available: bool


class SensorInjectionRequest(BaseModel):
    state: dict[str, float]


@router.get("/rooms")
def list_rooms(request: Request) -> list[dict[str, Any]]:
    service: SmartHomeService = request.app.state.smart_home
    devices = service.list_devices()
    rooms = sorted({device.room for device in devices})
    return [{"id": room, "device_count": sum(d.room == room for d in devices)} for room in rooms]


@router.get("/devices")
def list_devices(request: Request, room: str | None = None) -> list[dict[str, Any]]:
    service: SmartHomeService = request.app.state.smart_home
    return [service.to_dict(row) for row in service.list_devices(room)]


@router.get("/devices/{device_id}")
def get_device(device_id: str, request: Request) -> dict[str, Any]:
    service: SmartHomeService = request.app.state.smart_home
    row = service.get_device(device_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Device not found.")
    return service.to_dict(row)


@router.post("/devices/{device_id}/command")
async def command_device(device_id: str, body: DeviceCommand, request: Request) -> dict[str, Any]:
    service: SmartHomeService = request.app.state.smart_home
    try:
        row = await service.command(device_id, body.action, body.value, source=body.source, context_id=body.context_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "verified", "device": service.to_dict(row)}


@router.get("/activities")
def list_activities(request: Request) -> dict[str, list[dict[str, Any]]]:
    service: SmartHomeService = request.app.state.smart_home
    scheduler = getattr(request.app.state, "scheduler_service", None)
    devices = [service.to_dict(row) for row in service.list_devices()]
    activity_devices = [d for d in devices if "activity" in d["state"]]
    scheduled_items = (
        scheduler.list_upcoming_smart_home_schedules() if scheduler is not None else []
    )
    return {
        "running": [d for d in activity_devices if d["state"]["activity"] in {"running", "cleaning", "paused"}],
        "scheduled": scheduled_items,
        "completed": [d for d in activity_devices if d["state"]["activity"] in {"completed", "docked", "idle"}],
    }


@router.get("/history")
def list_history(request: Request, device_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    service: SmartHomeService = request.app.state.smart_home
    return service.list_history(device_id, limit)


@router.get("/energy")
def energy_summary(request: Request) -> dict[str, Any]:
    service: SmartHomeService = request.app.state.smart_home
    return service.energy_summary()


@router.get("/routines")
def list_routines(request: Request) -> list[dict[str, Any]]:
    service: SmartHomeService = request.app.state.smart_home
    return service.list_routines()


@router.post("/routines", status_code=status.HTTP_201_CREATED)
async def create_routine(body: CreateRoutineRequest, request: Request) -> dict[str, Any]:
    service: SmartHomeService = request.app.state.smart_home
    try:
        routine = await service.create_routine(
            routine_id=body.id,
            name=body.name,
            description=body.description,
            aliases=body.aliases,
            enabled=body.enabled,
            triggers=body.triggers,
            conditions=body.conditions,
            actions=body.actions,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "verified", "routine": routine}


@router.get("/routines/{routine_id}")
def get_routine(routine_id: str, request: Request) -> dict[str, Any]:
    service: SmartHomeService = request.app.state.smart_home
    routine = service.get_routine(routine_id)
    if routine is None:
        raise HTTPException(status_code=404, detail="Routine not found.")
    return routine


@router.patch("/routines/{routine_id}")
async def update_routine(
    routine_id: str, body: UpdateRoutineRequest, request: Request
) -> dict[str, Any]:
    service: SmartHomeService = request.app.state.smart_home
    try:
        routine = await service.update_routine(
            routine_id,
            name=body.name,
            description=body.description,
            aliases=body.aliases,
            enabled=body.enabled,
            triggers=body.triggers,
            conditions=body.conditions,
            actions=body.actions,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "verified", "routine": routine}


@router.delete("/routines/{routine_id}")
async def delete_routine(routine_id: str, request: Request) -> dict[str, Any]:
    service: SmartHomeService = request.app.state.smart_home
    try:
        deleted = await service.delete_routine(routine_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Routine not found.")
    return {"status": "verified", "deleted": True, "id": routine_id}


@router.post("/routines/{routine_id}/run")
async def run_routine(
    routine_id: str, request: Request, check_conditions: bool = True
) -> dict[str, Any]:
    service: SmartHomeService = request.app.state.smart_home
    try:
        return await service.run_routine(routine_id, check_conditions=check_conditions)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/simulator/devices/{device_id}/availability")
async def set_availability(device_id: str, body: AvailabilityRequest, request: Request) -> dict[str, Any]:
    service: SmartHomeService = request.app.state.smart_home
    try:
        row = await service.set_availability(device_id, body.available)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "verified", "device": service.to_dict(row)}


@router.post("/simulator/devices/{device_id}/complete")
async def complete_activity(device_id: str, request: Request) -> dict[str, Any]:
    service: SmartHomeService = request.app.state.smart_home
    try:
        row = await service.complete_activity(device_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "verified", "device": service.to_dict(row)}


@router.post("/simulator/devices/{device_id}/sensor")
async def inject_sensor(device_id: str, body: SensorInjectionRequest, request: Request) -> dict[str, Any]:
    service: SmartHomeService = request.app.state.smart_home
    try:
        row = await service.inject_sensor(device_id, body.state)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "verified", "is_simulated": True, "device": service.to_dict(row)}
