"""Phase 4 Locally Cached Parcel Management service (strictly offline provenance)."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Engine

from aura_edge.api.ws_events import WebSocketEventHub
from aura_edge.application.scheduler_service import SchedulerService
from aura_edge.data.models import ParcelRecord
from aura_edge.data.repositories.personal import ParcelRepository
from aura_edge.intelligence.schemas import LanguageCode

_VALID_SOURCES = {"manual", "imported", "sample"}


class ParcelService:
    """Stores user-entered or cached parcel details locally; never fabricates live carrier polling."""

    def __init__(
        self,
        engine: Engine,
        ws_hub: WebSocketEventHub,
        *,
        scheduler_service: SchedulerService | None = None,
        default_timezone: str = "Asia/Kolkata",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repo = ParcelRepository(engine)
        self.ws_hub = ws_hub
        self.scheduler_service = scheduler_service
        self.default_timezone = default_timezone
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def to_dict(row: ParcelRecord) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "order_name": row.order_name,
            "carrier": row.carrier,
            "tracking_ref": row.tracking_ref,
            "expected_delivery_date": (
                row.expected_delivery_date.isoformat() if row.expected_delivery_date else None
            ),
            "last_known_status": row.last_known_status,
            "source": row.source,
            "is_sample": row.is_sample,
            "notes": row.notes,
            "reminder_job_id": str(row.reminder_job_id) if row.reminder_job_id else None,
            "last_updated_at_utc": row.last_updated_at_utc.isoformat(),
            "created_at_utc": row.created_at_utc.isoformat(),
            "offline_provenance": (
                "Locally cached/manual record only; live carrier lookup is disabled offline."
            ),
        }

    async def create_parcel(
        self,
        *,
        order_name: str,
        carrier: str | None = None,
        tracking_ref: str | None = None,
        expected_delivery_date: date | None = None,
        last_known_status: str = "saved",
        source: str = "manual",
        is_sample: bool = False,
        notes: str | None = None,
        create_delivery_reminder: bool = True,
        language: LanguageCode = "en",
    ) -> dict[str, Any]:
        clean = " ".join((order_name or "").split()).strip()
        if not clean:
            raise ValueError("Parcel order_name cannot be empty.")
        src = source if source in _VALID_SOURCES else "manual"

        rem_job_id: uuid.UUID | None = None
        now_utc = self._clock()
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=UTC)
        if (
            create_delivery_reminder
            and expected_delivery_date is not None
            and self.scheduler_service is not None
        ):
            tz = ZoneInfo(self.default_timezone)
            rem_local = datetime.combine(expected_delivery_date, time(9, 0), tzinfo=tz)
            rem_utc = rem_local.astimezone(UTC)
            if rem_utc > now_utc:
                job = await self.scheduler_service.create_reminder(
                    message=f"Expected delivery today: {clean}",
                    run_at_utc=rem_utc,
                    title=f"Parcel: {clean}"[:200],
                    timezone_name=self.default_timezone,
                    language=language,
                )
                rem_job_id = uuid.UUID(job["id"])

        row = self.repo.create(
            order_name=clean,
            carrier=carrier,
            tracking_ref=tracking_ref,
            expected_delivery_date=expected_delivery_date,
            last_known_status=last_known_status or "saved",
            source=src,
            is_sample=is_sample,
            notes=notes,
            reminder_job_id=rem_job_id,
        )
        view = self.to_dict(row)
        await self.ws_hub.broadcast("parcel_updated", {"action": "created", "parcel": view})
        return view

    def list_parcels(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [self.to_dict(r) for r in self.repo.list_parcels(limit=limit)]

    async def update_parcel(
        self,
        parcel_id: uuid.UUID,
        *,
        last_known_status: str | None = None,
        expected_delivery_date: date | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if last_known_status is not None:
            fields["last_known_status"] = last_known_status[:160]
        if expected_delivery_date is not None:
            fields["expected_delivery_date"] = expected_delivery_date
        if notes is not None:
            fields["notes"] = notes
        row = self.repo.update(parcel_id, **fields)
        if row is None:
            raise LookupError(f"Parcel '{parcel_id}' not found.")
        view = self.to_dict(row)
        await self.ws_hub.broadcast("parcel_updated", {"action": "updated", "parcel": view})
        return view

    async def delete_parcel(self, parcel_id: uuid.UUID) -> dict[str, Any]:
        existing = self.repo.get(parcel_id)
        if existing is None:
            raise LookupError(f"Parcel '{parcel_id}' not found.")
        if existing.reminder_job_id and self.scheduler_service is not None:
            with contextlib.suppress(LookupError):
                await self.scheduler_service.cancel_job(existing.reminder_job_id)
        deleted = self.repo.delete(parcel_id)
        assert deleted is not None
        view = self.to_dict(deleted)
        await self.ws_hub.broadcast("parcel_updated", {"action": "deleted", "parcel": view})
        return view
