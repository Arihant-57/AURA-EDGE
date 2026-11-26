"""Phase 4 Personal Secretary REST endpoints (tasks, calendar, briefing, groceries, expenses, parcels)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from aura_edge.intelligence.schemas import LanguageCode

router = APIRouter(prefix="/api/v1", tags=["personal-assistant"])


class CreateTaskBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    priority: str = "normal"
    category: str = "general"
    due_at_utc: datetime | None = None
    create_reminder: bool = True
    language: LanguageCode = "en"


class UpdateTaskBody(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    category: str | None = None
    due_at_utc: datetime | None = None


class CreateEventBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start_at_utc: datetime
    end_at_utc: datetime | None = None
    description: str | None = None
    location: str | None = None
    all_day: bool = False
    timezone_name: str | None = None
    recurrence: dict[str, Any] | None = None
    reminder_minutes_before: int | None = None
    language: LanguageCode = "en"


class UpdateEventBody(BaseModel):
    title: str | None = None
    start_at_utc: datetime | None = None
    end_at_utc: datetime | None = None
    location: str | None = None
    description: str | None = None
    status: str | None = None


class ShoppingAddBody(BaseModel):
    item_name: str = Field(min_length=1, max_length=120)
    quantity: Decimal = Decimal(1)
    unit: str = "unit"
    added_via: str = "manual"


class ShoppingUpdateBody(BaseModel):
    purchased: bool | None = None
    quantity: Decimal | None = None
    unit: str | None = None


class InventoryUpsertBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    quantity: Decimal = Decimal(1)
    unit: str = "unit"
    category: str = "pantry"
    min_threshold: Decimal = Decimal(1)
    expires_at_utc: datetime | None = None
    aliases: list[str] | None = None


class ExpenseCreateBody(BaseModel):
    amount: Decimal = Field(gt=0)
    category: str = "general"
    merchant: str | None = None
    description: str | None = None
    occurred_at_utc: datetime | None = None
    currency: str = "INR"


class ExpenseCsvImportBody(BaseModel):
    filename: str = "expenses.csv"
    csv_content: str = Field(min_length=1)


class ParcelCreateBody(BaseModel):
    order_name: str = Field(min_length=1, max_length=200)
    carrier: str | None = None
    tracking_ref: str | None = None
    expected_delivery_date: date | None = None
    last_known_status: str = "saved"
    source: str = "manual"
    is_sample: bool = False
    notes: str | None = None
    create_delivery_reminder: bool = True
    language: LanguageCode = "en"


class ParcelUpdateBody(BaseModel):
    last_known_status: str | None = None
    expected_delivery_date: date | None = None
    notes: str | None = None


# ---- Tasks ----
@router.get("/tasks")
def list_tasks(request: Request, status: str | None = None, category: str | None = None) -> dict[str, Any]:
    svc = request.app.state.task_service
    try:
        items = svc.list_tasks(status=status, category=category)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"items": items, "count": len(items)}


@router.post("/tasks", status_code=201)
async def create_task(request: Request, body: CreateTaskBody) -> dict[str, Any]:
    svc = request.app.state.task_service
    try:
        task = await svc.create_task(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"task": task}


@router.patch("/tasks/{task_id}")
async def update_task(request: Request, task_id: uuid.UUID, body: UpdateTaskBody) -> dict[str, Any]:
    svc = request.app.state.task_service
    try:
        task = await svc.update_task(task_id, **body.model_dump(exclude_none=True))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"task": task}


@router.post("/tasks/{task_id}/complete")
async def complete_task(request: Request, task_id: uuid.UUID) -> dict[str, Any]:
    svc = request.app.state.task_service
    try:
        task = await svc.complete_task(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"task": task}


@router.delete("/tasks/{task_id}")
async def delete_task(request: Request, task_id: uuid.UUID) -> dict[str, Any]:
    svc = request.app.state.task_service
    try:
        task = await svc.delete_task(task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True, "task": task}


# ---- Calendar ----
@router.get("/calendar/events")
def list_calendar_events(request: Request, range_kind: str = "upcoming") -> dict[str, Any]:
    svc = request.app.state.calendar_service
    items = svc.list_events(range_kind=range_kind)
    return {"items": items, "count": len(items)}


@router.post("/calendar/events", status_code=201)
async def create_calendar_event(request: Request, body: CreateEventBody) -> dict[str, Any]:
    svc = request.app.state.calendar_service
    try:
        ev, conflicts = await svc.create_event(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"event": ev, "conflicts": conflicts}


@router.patch("/calendar/events/{event_id}")
async def update_calendar_event(
    request: Request, event_id: uuid.UUID, body: UpdateEventBody
) -> dict[str, Any]:
    svc = request.app.state.calendar_service
    try:
        ev, conflicts = await svc.update_event(event_id, **body.model_dump(exclude_none=True))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"event": ev, "conflicts": conflicts}


@router.delete("/calendar/events/{event_id}")
async def delete_calendar_event(request: Request, event_id: uuid.UUID) -> dict[str, Any]:
    svc = request.app.state.calendar_service
    try:
        ev = await svc.delete_event(event_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True, "event": ev}


# ---- Daily Briefing ----
@router.get("/briefing")
def get_daily_briefing(request: Request, language: LanguageCode = "en") -> dict[str, Any]:
    svc = request.app.state.briefing_service
    return svc.build_daily_briefing(language=language)


# ---- Groceries & Recipes ----
@router.get("/groceries/list")
def list_shopping(request: Request, include_purchased: bool = True) -> dict[str, Any]:
    svc = request.app.state.grocery_service
    items = svc.list_shopping(include_purchased=include_purchased)
    return {"items": items, "count": len(items)}


@router.post("/groceries/list", status_code=201)
async def add_shopping(request: Request, body: ShoppingAddBody) -> dict[str, Any]:
    svc = request.app.state.grocery_service
    try:
        item = await svc.add_to_shopping_list(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"item": item}


@router.patch("/groceries/list/{item_id}")
async def update_shopping(
    request: Request, item_id: uuid.UUID, body: ShoppingUpdateBody
) -> dict[str, Any]:
    svc = request.app.state.grocery_service
    try:
        item = await svc.update_shopping_item(item_id, **body.model_dump(exclude_none=True))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"item": item}


@router.delete("/groceries/list/{item_id}")
async def delete_shopping(request: Request, item_id: uuid.UUID) -> dict[str, Any]:
    svc = request.app.state.grocery_service
    try:
        await svc.delete_shopping_item(item_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@router.get("/groceries/inventory")
def list_inventory(request: Request) -> dict[str, Any]:
    svc = request.app.state.grocery_service
    items = svc.list_inventory()
    return {"items": items, "count": len(items)}


@router.post("/groceries/inventory", status_code=201)
async def upsert_inventory(request: Request, body: InventoryUpsertBody) -> dict[str, Any]:
    svc = request.app.state.grocery_service
    try:
        item = await svc.upsert_inventory_item(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"item": item}


@router.delete("/groceries/inventory/{item_id}")
async def delete_inventory(request: Request, item_id: uuid.UUID) -> dict[str, Any]:
    svc = request.app.state.grocery_service
    try:
        await svc.delete_inventory_item(item_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@router.get("/groceries/missing")
def list_missing_groceries(request: Request) -> dict[str, Any]:
    svc = request.app.state.grocery_service
    items = svc.list_missing_or_low_stock()
    return {"items": items, "count": len(items)}


@router.get("/recipes/suggest")
def suggest_recipes(request: Request, q: str | None = None) -> dict[str, Any]:
    svc = request.app.state.grocery_service
    recipes = svc.suggest_recipes(query=q)
    return {"recipes": recipes, "count": len(recipes)}


# ---- Expenses ----
@router.get("/expenses")
def list_expenses(request: Request, category: str | None = None) -> dict[str, Any]:
    svc = request.app.state.expense_service
    items = svc.list_expenses(category=category)
    return {"items": items, "count": len(items)}


@router.post("/expenses", status_code=201)
async def create_expense(request: Request, body: ExpenseCreateBody) -> dict[str, Any]:
    svc = request.app.state.expense_service
    try:
        exp = await svc.add_expense(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"expense": exp}


@router.delete("/expenses/{expense_id}")
async def delete_expense(request: Request, expense_id: uuid.UUID) -> dict[str, Any]:
    svc = request.app.state.expense_service
    try:
        await svc.delete_expense(expense_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@router.get("/expenses/summary")
def expense_summary(request: Request, period: str = "month") -> dict[str, Any]:
    svc = request.app.state.expense_service
    return svc.get_summary(period=period)


@router.post("/expenses/import-csv")
async def import_expenses_csv(request: Request, body: ExpenseCsvImportBody) -> dict[str, Any]:
    svc = request.app.state.expense_service
    try:
        return await svc.import_csv(filename=body.filename, csv_content=body.csv_content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---- Parcels ----
@router.get("/parcels")
def list_parcels(request: Request) -> dict[str, Any]:
    svc = request.app.state.parcel_service
    items = svc.list_parcels()
    return {"items": items, "count": len(items)}


@router.post("/parcels", status_code=201)
async def create_parcel(request: Request, body: ParcelCreateBody) -> dict[str, Any]:
    svc = request.app.state.parcel_service
    try:
        parcel = await svc.create_parcel(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"parcel": parcel}


@router.patch("/parcels/{parcel_id}")
async def update_parcel(
    request: Request, parcel_id: uuid.UUID, body: ParcelUpdateBody
) -> dict[str, Any]:
    svc = request.app.state.parcel_service
    try:
        parcel = await svc.update_parcel(parcel_id, **body.model_dump(exclude_none=True))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"parcel": parcel}


@router.delete("/parcels/{parcel_id}")
async def delete_parcel(request: Request, parcel_id: uuid.UUID) -> dict[str, Any]:
    svc = request.app.state.parcel_service
    try:
        parcel = await svc.delete_parcel(parcel_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True, "parcel": parcel}
