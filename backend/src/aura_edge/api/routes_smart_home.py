"""Phase 2 smart-home simulator REST API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from aura_edge.application.smart_home import SmartHomeService

router = APIRouter(prefix="/api/v1/smart-home", tags=["smart-home"])


class DeviceCommand(BaseModel):
    action: str = Field(min_length=1, max_length=48)
    value: Any
    source: str = Field(default="ui", pattern="^(ui|voice|routine|simulator)$")
    context_id: str | None = Field(default=None, max_length=64)


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
    devices = [service.to_dict(row) for row in service.list_devices()]
    activity_devices = [d for d in devices if "activity" in d["state"]]
    return {
        "running": [d for d in activity_devices if d["state"]["activity"] in {"running", "cleaning", "paused"}],
        "scheduled": [],
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


@router.post("/routines/{routine_id}/run")
async def run_routine(routine_id: str, request: Request) -> dict[str, Any]:
    service: SmartHomeService = request.app.state.smart_home
    try:
        return await service.run_routine(routine_id)
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
