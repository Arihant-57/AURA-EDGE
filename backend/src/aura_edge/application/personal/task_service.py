"""Phase 4 persistent Task Management service integrated with SchedulerService."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine

from aura_edge.api.ws_events import WebSocketEventHub
from aura_edge.application.scheduler_service import SchedulerService
from aura_edge.data.models import TaskRecord
from aura_edge.data.repositories.personal import TaskRepository
from aura_edge.intelligence.schemas import LanguageCode

_VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
_VALID_PRIORITIES = {"low", "normal", "high", "urgent"}


def _ensure_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


class TaskService:
    def __init__(
        self,
        engine: Engine,
        ws_hub: WebSocketEventHub,
        *,
        scheduler_service: SchedulerService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repo = TaskRepository(engine)
        self.ws_hub = ws_hub
        self.scheduler_service = scheduler_service
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def to_dict(row: TaskRecord) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "title": row.title,
            "description": row.description,
            "status": row.status,
            "priority": row.priority,
            "category": row.category,
            "due_at_utc": row.due_at_utc.isoformat() if row.due_at_utc else None,
            "completed_at_utc": row.completed_at_utc.isoformat() if row.completed_at_utc else None,
            "reminder_job_id": str(row.reminder_job_id) if row.reminder_job_id else None,
            "created_at_utc": row.created_at_utc.isoformat(),
            "updated_at_utc": row.updated_at_utc.isoformat(),
        }

    async def create_task(
        self,
        *,
        title: str,
        description: str | None = None,
        priority: str = "normal",
        category: str = "general",
        due_at_utc: datetime | None = None,
        create_reminder: bool = True,
        language: LanguageCode = "en",
    ) -> dict[str, Any]:
        clean = " ".join((title or "").split()).strip()
        if not clean:
            raise ValueError("Task title cannot be empty.")
        prio = priority.lower() if priority else "normal"
        if prio not in _VALID_PRIORITIES:
            raise ValueError(f"Priority must be one of {sorted(_VALID_PRIORITIES)}.")

        rem_job_id: uuid.UUID | None = None
        due_utc = _ensure_utc(due_at_utc) if due_at_utc is not None else None
        now_utc = _ensure_utc(self._clock())
        if create_reminder and due_utc is not None and due_utc > now_utc and self.scheduler_service is not None:
            job = await self.scheduler_service.create_reminder(
                message=f"Task due: {clean}",
                run_at_utc=due_utc,
                title=f"Task: {clean}"[:200],
                language=language,
            )
            rem_job_id = uuid.UUID(job["id"])

        row = self.repo.create(
            title=clean,
            description=description,
            priority=prio,
            category=(category or "general").strip().lower()[:64],
            due_at_utc=due_utc,
            reminder_job_id=rem_job_id,
        )
        view = self.to_dict(row)
        await self.ws_hub.broadcast("task_updated", {"action": "created", "task": view})
        return view

    def list_tasks(
        self,
        *,
        status: str | None = None,
        category: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if status and status not in _VALID_STATUSES:
            raise ValueError(f"Status must be one of {sorted(_VALID_STATUSES)}.")
        return [
            self.to_dict(r)
            for r in self.repo.list_tasks(status=status, category=category, limit=limit)
        ]

    async def complete_task(self, task_id: uuid.UUID) -> dict[str, Any]:
        existing = self.repo.get(task_id)
        if existing is None:
            raise LookupError(f"Task '{task_id}' not found.")
        if existing.reminder_job_id and self.scheduler_service is not None:
            with contextlib.suppress(LookupError):
                await self.scheduler_service.cancel_job(existing.reminder_job_id)
        updated = self.repo.update(
            task_id,
            status="completed",
            completed_at_utc=_ensure_utc(self._clock()),
        )
        assert updated is not None
        view = self.to_dict(updated)
        await self.ws_hub.broadcast("task_updated", {"action": "completed", "task": view})
        return view

    async def complete_matching_task(self, query: str | None = None) -> dict[str, Any] | None:
        pending = self.repo.list_tasks(status="pending", limit=50)
        if not pending:
            pending = self.repo.list_tasks(status="in_progress", limit=50)
        if not pending:
            return None
        q = (query or "").strip().lower()
        target = pending[0]
        if q:
            for row in pending:
                if q in row.title.lower() or row.title.lower() in q:
                    target = row
                    break
        return await self.complete_task(target.id)

    async def update_task(
        self,
        task_id: uuid.UUID,
        *,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        category: str | None = None,
        due_at_utc: datetime | None = None,
    ) -> dict[str, Any]:
        existing = self.repo.get(task_id)
        if existing is None:
            raise LookupError(f"Task '{task_id}' not found.")
        fields: dict[str, Any] = {}
        if title is not None:
            clean = " ".join(title.split()).strip()
            if not clean:
                raise ValueError("Task title cannot be empty.")
            fields["title"] = clean[:200]
        if description is not None:
            fields["description"] = description
        if status is not None:
            if status not in _VALID_STATUSES:
                raise ValueError(f"Status must be one of {sorted(_VALID_STATUSES)}.")
            fields["status"] = status
            if status == "completed" and existing.completed_at_utc is None:
                fields["completed_at_utc"] = _ensure_utc(self._clock())
        if priority is not None:
            if priority not in _VALID_PRIORITIES:
                raise ValueError(f"Priority must be one of {sorted(_VALID_PRIORITIES)}.")
            fields["priority"] = priority
        if category is not None:
            fields["category"] = category.strip().lower()[:64] or "general"
        if due_at_utc is not None:
            fields["due_at_utc"] = _ensure_utc(due_at_utc)
        updated = self.repo.update(task_id, **fields)
        assert updated is not None
        view = self.to_dict(updated)
        await self.ws_hub.broadcast("task_updated", {"action": "updated", "task": view})
        return view

    async def delete_task(self, task_id: uuid.UUID) -> dict[str, Any]:
        existing = self.repo.get(task_id)
        if existing is None:
            raise LookupError(f"Task '{task_id}' not found.")
        if existing.reminder_job_id and self.scheduler_service is not None:
            with contextlib.suppress(LookupError):
                await self.scheduler_service.cancel_job(existing.reminder_job_id)
        deleted = self.repo.delete(task_id)
        assert deleted is not None
        view = self.to_dict(deleted)
        await self.ws_hub.broadcast("task_updated", {"action": "deleted", "task": view})
        return view
