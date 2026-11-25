"""Phase 3 persistent personal memory & privacy settings REST API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from aura_edge.application.memory_service import (
    MemoryDisabledError,
    MemorySafetyError,
    MemoryService,
)
from aura_edge.intelligence.schemas import LanguageCode

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])

MemoryCategory = Literal[
    "appliance_preference", "preference", "fact", "instruction", "profile"
]
MemoryProvenance = Literal[
    "explicit_voice", "explicit_text", "ui_edit", "api", "imported"
]


class CreateMemoryRequest(BaseModel):
    original_text: str = Field(..., min_length=2, max_length=1000)
    category: MemoryCategory | None = None
    canonical_key: str | None = Field(default=None, max_length=160)
    structured_value: dict[str, Any] | None = None
    language: LanguageCode = "en"
    provenance: MemoryProvenance = "api"
    enabled: bool = True
    expires_at_utc: datetime | None = None


class UpdateMemoryRequest(BaseModel):
    original_text: str | None = Field(default=None, min_length=2, max_length=1000)
    category: MemoryCategory | None = None
    canonical_key: str | None = Field(default=None, max_length=160)
    structured_value: dict[str, Any] | None = None
    language: LanguageCode | None = None
    enabled: bool | None = None
    expires_at_utc: datetime | None = None
    clear_expiration: bool = False


class UpdateMemorySettingsRequest(BaseModel):
    memory_enabled: bool | None = None
    auto_save_preferences: bool | None = None
    persist_conversation_history: bool | None = None
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    clear_retention_days: bool = False
    semantic_search_enabled: bool | None = None


@router.get("")
def list_memories(
    request: Request,
    category: MemoryCategory | None = None,
    enabled_only: bool = True,
    limit: int = 100,
) -> dict[str, Any]:
    svc: MemoryService = request.app.state.memory_service
    settings = svc.get_settings()
    if not settings["memory_enabled"]:
        return {"memory_enabled": False, "count": 0, "items": []}
    items = svc.list_memories(category=category, enabled_only=enabled_only, limit=limit)
    return {"memory_enabled": True, "count": len(items), "items": items}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_memory(body: CreateMemoryRequest, request: Request) -> dict[str, Any]:
    svc: MemoryService = request.app.state.memory_service
    try:
        item, created = await svc.save_memory(
            body.original_text,
            category=body.category,
            canonical_key=body.canonical_key,
            structured_value=body.structured_value,
            language=body.language,
            provenance=body.provenance,
            enabled=body.enabled,
            expires_at_utc=body.expires_at_utc,
        )
    except MemoryDisabledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MemorySafetyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "verified", "created": created, "item": item}


@router.get("/search")
async def search_memories(
    request: Request,
    q: str = "",
    category: MemoryCategory | None = None,
    limit: int = 8,
    use_semantic: bool = True,
) -> dict[str, Any]:
    svc: MemoryService = request.app.state.memory_service
    return await svc.search_memory(q, category=category, limit=limit, use_semantic=use_semantic)


@router.get("/settings")
def get_memory_settings(request: Request) -> dict[str, Any]:
    svc: MemoryService = request.app.state.memory_service
    return svc.get_settings()


@router.put("/settings")
async def update_memory_settings(
    body: UpdateMemorySettingsRequest, request: Request
) -> dict[str, Any]:
    svc: MemoryService = request.app.state.memory_service
    try:
        return await svc.update_settings(
            memory_enabled=body.memory_enabled,
            auto_save_preferences=body.auto_save_preferences,
            persist_conversation_history=body.persist_conversation_history,
            retention_days=body.retention_days,
            clear_retention_days=body.clear_retention_days,
            semantic_search_enabled=body.semantic_search_enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{memory_id}")
def get_memory(memory_id: uuid.UUID, request: Request) -> dict[str, Any]:
    svc: MemoryService = request.app.state.memory_service
    row = svc.repo.get_item(memory_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Memory item not found.")
    return svc.item_to_dict(row)


@router.patch("/{memory_id}")
async def update_memory(
    memory_id: uuid.UUID, body: UpdateMemoryRequest, request: Request
) -> dict[str, Any]:
    svc: MemoryService = request.app.state.memory_service
    try:
        item = await svc.update_memory(
            memory_id,
            original_text=body.original_text,
            category=body.category,
            canonical_key=body.canonical_key,
            structured_value=body.structured_value,
            language=body.language,
            enabled=body.enabled,
            expires_at_utc=body.expires_at_utc,
            clear_expiration=body.clear_expiration,
        )
    except MemorySafetyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Memory item not found.")
    return {"status": "verified", "item": item}


@router.delete("/{memory_id}")
async def delete_memory(memory_id: uuid.UUID, request: Request) -> dict[str, Any]:
    svc: MemoryService = request.app.state.memory_service
    deleted = await svc.delete_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory item not found.")
    return {"status": "verified", "deleted": True, "id": str(memory_id)}


@router.delete("")
async def clear_memories(request: Request) -> dict[str, Any]:
    svc: MemoryService = request.app.state.memory_service
    count = await svc.clear_memories()
    return {"status": "verified", "deleted_count": count}
