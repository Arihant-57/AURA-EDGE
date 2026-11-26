"""Phase 4 Local Offline Calendar service with recurrence, conflict detection, and reminders."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Engine

from aura_edge.api.ws_events import WebSocketEventHub
from aura_edge.application.scheduler_service import SchedulerService
from aura_edge.data.models import CalendarEventRecord
from aura_edge.data.repositories.personal import CalendarRepository
from aura_edge.intelligence.schemas import LanguageCode


def _ensure_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _resolve_tz(tz_name: str | None, default_tz: str = "Asia/Kolkata") -> ZoneInfo:
    name = (tz_name or default_tz).strip()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone '{name}'.") from exc


def _expand_event_occurrences(
    row: CalendarEventRecord,
    window_start_utc: datetime,
    window_end_utc: datetime,
) -> list[tuple[datetime, datetime]]:
    """Expand a scheduled event (once/daily/weekly) into occurrences overlapping window."""
    s0 = _ensure_utc(row.start_at_utc)
    e0 = _ensure_utc(row.end_at_utc)
    dur = e0 - s0
    rec = dict(row.recurrence_json or {"kind": "once"})
    kind = str(rec.get("kind", "once")).lower()
    if kind == "once":
        if e0 > window_start_utc and s0 < window_end_utc:
            return [(s0, e0)]
        return []

    tz = _resolve_tz(row.timezone_name)
    start_local = s0.astimezone(tz)
    win_start_local = window_start_utc.astimezone(tz)
    win_end_local = window_end_utc.astimezone(tz)
    weekdays = {int(d) % 7 for d in (rec.get("weekdays") or [start_local.weekday()])}

    out: list[tuple[datetime, datetime]] = []
    cur_date = max(start_local.date(), (win_start_local - dur).date())
    end_date = win_end_local.date()
    step = 0
    while cur_date <= end_date and step < 90:
        step += 1
        if kind == "daily" or (kind == "weekly" and cur_date.weekday() in weekdays):
            occ_local = datetime(
                cur_date.year,
                cur_date.month,
                cur_date.day,
                start_local.hour,
                start_local.minute,
                start_local.second,
                tzinfo=tz,
            )
            occ_start = occ_local.astimezone(UTC)
            occ_end = occ_start + dur
            if occ_start >= s0 and occ_end > window_start_utc and occ_start < window_end_utc:
                out.append((occ_start, occ_end))
        cur_date = cur_date + timedelta(days=1)
    return out


class CalendarService:
    def __init__(
        self,
        engine: Engine,
        ws_hub: WebSocketEventHub,
        *,
        scheduler_service: SchedulerService | None = None,
        default_timezone: str = "Asia/Kolkata",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repo = CalendarRepository(engine)
        self.ws_hub = ws_hub
        self.scheduler_service = scheduler_service
        self.default_timezone = default_timezone
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def to_dict(
        row: CalendarEventRecord,
        *,
        occ_start: datetime | None = None,
        occ_end: datetime | None = None,
    ) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "title": row.title,
            "description": row.description,
            "location": row.location,
            "start_at_utc": (occ_start or row.start_at_utc).isoformat(),
            "end_at_utc": (occ_end or row.end_at_utc).isoformat(),
            "all_day": row.all_day,
            "timezone": row.timezone_name,
            "recurrence": dict(row.recurrence_json or {"kind": "once"}),
            "status": row.status,
            "reminder_minutes_before": row.reminder_minutes_before,
            "reminder_job_id": str(row.reminder_job_id) if row.reminder_job_id else None,
            "created_at_utc": row.created_at_utc.isoformat(),
            "updated_at_utc": row.updated_at_utc.isoformat(),
        }

    def detect_conflicts(
        self,
        start_at_utc: datetime,
        end_at_utc: datetime,
        *,
        exclude_event_id: uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        s_utc = _ensure_utc(start_at_utc)
        e_utc = _ensure_utc(end_at_utc)
        conflicts: list[dict[str, Any]] = []
        for row in self.repo.list_scheduled(limit=250):
            if exclude_event_id is not None and row.id == exclude_event_id:
                continue
            for occ_s, occ_e in _expand_event_occurrences(row, s_utc, e_utc):
                conflicts.append(self.to_dict(row, occ_start=occ_s, occ_end=occ_e))
                break
        return conflicts

    async def create_event(
        self,
        *,
        title: str,
        start_at_utc: datetime,
        end_at_utc: datetime | None = None,
        description: str | None = None,
        location: str | None = None,
        all_day: bool = False,
        timezone_name: str | None = None,
        recurrence: dict[str, Any] | None = None,
        reminder_minutes_before: int | None = None,
        language: LanguageCode = "en",
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        clean = " ".join((title or "").split()).strip()
        if not clean:
            raise ValueError("Event title cannot be empty.")
        tz_name = timezone_name or self.default_timezone
        _resolve_tz(tz_name)
        s_utc = _ensure_utc(start_at_utc)
        e_utc = _ensure_utc(end_at_utc) if end_at_utc is not None else (s_utc + timedelta(hours=1))
        if e_utc < s_utc:
            raise ValueError("end_at_utc must be on or after start_at_utc.")
        if reminder_minutes_before is not None and reminder_minutes_before < 0:
            raise ValueError("reminder_minutes_before cannot be negative.")

        rec = dict(recurrence or {"kind": "once"})
        kind = str(rec.get("kind", "once")).lower()
        if kind not in {"once", "daily", "weekly"}:
            raise ValueError("Recurrence kind must be 'once', 'daily', or 'weekly'.")
        rec["kind"] = kind

        conflicts = self.detect_conflicts(s_utc, e_utc)

        rem_job_id: uuid.UUID | None = None
        now_utc = _ensure_utc(self._clock())
        if reminder_minutes_before is not None and self.scheduler_service is not None:
            rem_at = s_utc - timedelta(minutes=int(reminder_minutes_before))
            if rem_at > now_utc:
                local_t = s_utc.astimezone(_resolve_tz(tz_name)).strftime("%I:%M %p")
                sched_rec: dict[str, Any] = {"kind": "once"}
                if kind in {"daily", "weekly"}:
                    rem_local = rem_at.astimezone(_resolve_tz(tz_name))
                    sched_rec = {
                        "kind": kind,
                        "hour": rem_local.hour,
                        "minute": rem_local.minute,
                        **({"weekdays": rec["weekdays"]} if rec.get("weekdays") else {}),
                    }
                job = await self.scheduler_service.create_reminder(
                    message=f"Upcoming event at {local_t}: {clean}",
                    run_at_utc=rem_at,
                    title=f"Event: {clean}"[:200],
                    timezone_name=tz_name,
                    recurrence=sched_rec,
                    language=language,
                )
                rem_job_id = uuid.UUID(job["id"])

        row = self.repo.create(
            title=clean,
            start_at_utc=s_utc,
            end_at_utc=e_utc,
            description=description,
            location=location,
            all_day=all_day,
            timezone_name=tz_name,
            recurrence_json=rec,
            reminder_minutes_before=reminder_minutes_before,
            reminder_job_id=rem_job_id,
        )
        view = self.to_dict(row)
        await self.ws_hub.broadcast(
            "calendar_updated",
            {"action": "created", "event": view, "conflicts": conflicts},
        )
        return view, conflicts

    def list_events(
        self,
        *,
        range_kind: str = "upcoming",
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
    ) -> list[dict[str, Any]]:
        tz = _resolve_tz(self.default_timezone)
        now_utc = _ensure_utc(self._clock())
        now_local = now_utc.astimezone(tz)
        if start_utc is not None and end_utc is not None:
            w_start, w_end = _ensure_utc(start_utc), _ensure_utc(end_utc)
        elif range_kind == "today":
            day0 = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            w_start = day0.astimezone(UTC)
            w_end = (day0 + timedelta(days=1)).astimezone(UTC)
        elif range_kind == "tomorrow":
            day1 = (now_local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            w_start = day1.astimezone(UTC)
            w_end = (day1 + timedelta(days=1)).astimezone(UTC)
        else:
            w_start = now_utc
            w_end = now_utc + timedelta(days=14)

        events: list[dict[str, Any]] = []
        for row in self.repo.list_scheduled(limit=250):
            for occ_s, occ_e in _expand_event_occurrences(row, w_start, w_end):
                events.append(self.to_dict(row, occ_start=occ_s, occ_end=occ_e))
        events.sort(key=lambda item: item["start_at_utc"])
        return events

    async def update_event(
        self,
        event_id: uuid.UUID,
        *,
        title: str | None = None,
        start_at_utc: datetime | None = None,
        end_at_utc: datetime | None = None,
        location: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        existing = self.repo.get(event_id)
        if existing is None:
            raise LookupError(f"Calendar event '{event_id}' not found.")
        fields: dict[str, Any] = {}
        new_start = _ensure_utc(start_at_utc) if start_at_utc else existing.start_at_utc
        new_end = _ensure_utc(end_at_utc) if end_at_utc else existing.end_at_utc
        if new_end < new_start:
            raise ValueError("end_at_utc must be on or after start_at_utc.")
        if title is not None:
            clean = " ".join(title.split()).strip()
            if not clean:
                raise ValueError("Event title cannot be empty.")
            fields["title"] = clean[:200]
        if start_at_utc is not None:
            fields["start_at_utc"] = new_start
        if end_at_utc is not None:
            fields["end_at_utc"] = new_end
        if location is not None:
            fields["location"] = location[:160]
        if description is not None:
            fields["description"] = description
        if status is not None:
            if status not in {"scheduled", "cancelled"}:
                raise ValueError("Status must be 'scheduled' or 'cancelled'.")
            fields["status"] = status
        conflicts = self.detect_conflicts(new_start, new_end, exclude_event_id=event_id)
        updated = self.repo.update(event_id, **fields)
        assert updated is not None
        view = self.to_dict(updated)
        await self.ws_hub.broadcast(
            "calendar_updated",
            {"action": "updated", "event": view, "conflicts": conflicts},
        )
        return view, conflicts

    async def delete_event(self, event_id: uuid.UUID) -> dict[str, Any]:
        existing = self.repo.get(event_id)
        if existing is None:
            raise LookupError(f"Calendar event '{event_id}' not found.")
        if existing.reminder_job_id and self.scheduler_service is not None:
            with contextlib.suppress(LookupError):
                await self.scheduler_service.cancel_job(existing.reminder_job_id)
        deleted = self.repo.delete(event_id)
        assert deleted is not None
        view = self.to_dict(deleted)
        await self.ws_hub.broadcast("calendar_updated", {"action": "deleted", "event": view})
        return view
