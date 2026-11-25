"""PostgreSQL-authoritative persistent countdown timer manager."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import Engine

from aura_edge.data.models import TimerRecord
from aura_edge.data.repositories.timer_repo import TimerRepository, compute_remaining_seconds
from aura_edge.intelligence.response_catalog import format_duration_spoken, get_phrase
from aura_edge.intelligence.schemas import LanguageCode, VerifiedActionResult

logger = logging.getLogger(__name__)


class TimerView(BaseModel):
    """Serializable timer DTO returned by REST endpoints, tools, and WS events."""

    id: str
    label: str | None
    duration_seconds: int
    remaining_seconds: int
    status: str
    language: str
    fire_at_utc: str | None
    created_at_utc: str
    updated_at_utc: str
    completed_at_utc: str | None

    @classmethod
    def from_record(cls, record: TimerRecord) -> TimerView:
        return cls(
            id=str(record.id),
            label=record.label,
            duration_seconds=record.duration_seconds,
            remaining_seconds=compute_remaining_seconds(record),
            status=record.status,
            language=record.language,
            fire_at_utc=record.fire_at_utc.isoformat() if record.fire_at_utc else None,
            created_at_utc=record.created_at_utc.isoformat(),
            updated_at_utc=record.updated_at_utc.isoformat(),
            completed_at_utc=(
                record.completed_at_utc.isoformat() if record.completed_at_utc else None
            ),
        )


TimerEventCallback = Callable[[str, TimerView], Awaitable[None]]


def _label_part(label: str | None, lang: LanguageCode) -> str:
    if not label:
        return ""
    if lang == "hi":
        return f' "{label}"'
    if lang == "kn":
        return f' "{label}"'
    return f' "{label}"'


class TimerManager:
    """Manages PostgreSQL-persisted timers with async wakeups and restart recovery."""

    def __init__(
        self,
        engine: Engine,
        on_timer_event: TimerEventCallback | None = None,
    ) -> None:
        self.repo = TimerRepository(engine)
        self._on_timer_event = on_timer_event
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    def set_event_callback(self, callback: TimerEventCallback | None) -> None:
        self._on_timer_event = callback

    async def _emit(self, event_type: str, record: TimerRecord) -> None:
        if self._on_timer_event is None:
            return
        try:
            await self._on_timer_event(event_type, TimerView.from_record(record))
        except (RuntimeError, OSError, ValueError) as exc:
            logger.warning("Timer event callback error (%s): %s", event_type, exc)

    def _schedule_task(self, record: TimerRecord) -> None:
        """Schedule an asyncio wakeup task if an event loop is running."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        existing = self._tasks.pop(record.id, None)
        if existing is not None and not existing.done():
            existing.cancel()

        delay = max(0.0, float(compute_remaining_seconds(record)))
        if record.fire_at_utc is not None:
            fire_at = record.fire_at_utc
            if fire_at.tzinfo is None:
                fire_at = fire_at.replace(tzinfo=UTC)
            delay = max(0.0, (fire_at - datetime.now(UTC)).total_seconds())

        task = loop.create_task(self._wait_and_fire(record.id, delay))
        self._tasks[record.id] = task

    async def _wait_and_fire(self, timer_id: uuid.UUID, delay_seconds: float) -> None:
        try:
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            won = self.repo.mark_completed_once(timer_id, final_status="completed")
            if won is not None:
                await self._emit("timer_completed", won)
        except asyncio.CancelledError:
            return
        finally:
            self._tasks.pop(timer_id, None)

    async def recover_on_startup(self) -> dict[str, int]:
        """Reload active PostgreSQL timers after restart; trigger missed ones once."""
        recovered = 0
        missed = 0
        active_rows = self.repo.list_timers(statuses=("active",), limit=200)
        now = datetime.now(UTC)

        for row in active_rows:
            fire_at = row.fire_at_utc
            if fire_at is not None and fire_at.tzinfo is None:
                fire_at = fire_at.replace(tzinfo=UTC)
            if fire_at is None or fire_at <= now:
                won = self.repo.mark_completed_once(row.id, final_status="missed")
                if won is not None:
                    missed += 1
                    await self._emit("timer_missed", won)
            else:
                self._schedule_task(row)
                recovered += 1

        return {"recovered": recovered, "missed": missed}

    async def shutdown(self) -> None:
        """Cancel in-memory wakeup tasks without modifying DB timer state."""
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for t in tasks:
            if not t.done():
                t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def create_timer(
        self,
        *,
        duration_seconds: int,
        label: str | None = None,
        language: LanguageCode = "en",
    ) -> VerifiedActionResult:
        if duration_seconds <= 0 or duration_seconds > 86400:
            return VerifiedActionResult(
                success=False,
                action="SetTimer",
                status="failed",
                message=get_phrase("invalid_duration", language),
                error="Timer duration out of bounds (1..86400s).",
            )

        record = self.repo.create_timer(
            duration_seconds=duration_seconds,
            label=label,
            language=language,
        )
        self._schedule_task(record)
        view = TimerView.from_record(record)
        await self._emit("timer_updated", record)

        dur_text = format_duration_spoken(duration_seconds, language)
        msg = get_phrase(
            "timer_set",
            language,
            label_part=_label_part(record.label, language),
            duration=dur_text,
        )
        return VerifiedActionResult(
            success=True,
            action="SetTimer",
            status="verified",
            data={"timer": view.model_dump()},
            message=msg,
        )

    def list_timers(
        self,
        *,
        include_closed: bool = False,
        language: LanguageCode = "en",
    ) -> VerifiedActionResult:
        statuses = None if include_closed else ("active", "paused")
        rows = self.repo.list_timers(statuses=statuses)
        views = [TimerView.from_record(r) for r in rows]

        if not views:
            msg = get_phrase("no_active_timers", language)
        else:
            summaries = []
            for v in views:
                lbl = f"{v.label}: " if v.label else ""
                summaries.append(f"{lbl}{format_duration_spoken(v.remaining_seconds, language)}")
            joined = "; ".join(summaries)
            if language == "hi":
                msg = f"आपके टाइमर: {joined}।"
            elif language == "kn":
                msg = f"ನಿಮ್ಮ ಟೈಮರ್‌ಗಳು: {joined}."
            else:
                msg = f"Active timers: {joined}."

        return VerifiedActionResult(
            success=True,
            action="ListTimers",
            status="verified",
            data={"timers": [v.model_dump() for v in views], "count": len(views)},
            message=msg,
        )

    def _resolve_single_target(
        self,
        *,
        timer_id: uuid.UUID | None,
        label: str | None,
        statuses: tuple[str, ...],
        language: LanguageCode,
        action: str,
    ) -> tuple[TimerRecord | None, VerifiedActionResult | None]:
        if timer_id is not None:
            found = self.repo.get_timer(timer_id)
            if found is None or found.status not in statuses:
                return None, VerifiedActionResult(
                    success=False,
                    action=action,
                    status="failed",
                    message=get_phrase("timer_not_found", language),
                    error="Timer not found or not in valid state.",
                )
            return found, None

        matches = self.repo.find_matching_timers(label=label, statuses=statuses)
        if not matches:
            key = "no_active_timers" if not label else "timer_not_found"
            return None, VerifiedActionResult(
                success=False,
                action=action,
                status="failed",
                message=get_phrase(key, language),
                error="No matching timer found.",
            )

        if len(matches) > 1:
            desc = ", ".join(
                m.label or format_duration_spoken(m.duration_seconds, language) for m in matches
            )
            return None, VerifiedActionResult(
                success=False,
                action=action,
                status="clarification_required",
                data={"candidates": [TimerView.from_record(m).model_dump() for m in matches]},
                message=get_phrase("ambiguous_timer", language, labels=desc),
            )

        return matches[0], None

    def get_remaining(
        self,
        *,
        timer_id: uuid.UUID | None = None,
        label: str | None = None,
        language: LanguageCode = "en",
    ) -> VerifiedActionResult:
        target, err = self._resolve_single_target(
            timer_id=timer_id,
            label=label,
            statuses=("active", "paused"),
            language=language,
            action="GetTimerRemaining",
        )
        if err is not None or target is None:
            return err  # type: ignore[return-value]

        view = TimerView.from_record(target)
        dur_text = format_duration_spoken(view.remaining_seconds, language)
        msg = get_phrase(
            "timer_remaining",
            language,
            label_part=_label_part(target.label, language),
            duration=dur_text,
        )
        return VerifiedActionResult(
            success=True,
            action="GetTimerRemaining",
            status="verified",
            data={"timer": view.model_dump()},
            message=msg,
        )

    async def pause_timer(
        self,
        *,
        timer_id: uuid.UUID | None = None,
        label: str | None = None,
        language: LanguageCode = "en",
    ) -> VerifiedActionResult:
        target, err = self._resolve_single_target(
            timer_id=timer_id,
            label=label,
            statuses=("active",),
            language=language,
            action="PauseTimer",
        )
        if err is not None or target is None:
            return err  # type: ignore[return-value]

        task = self._tasks.pop(target.id, None)
        if task is not None and not task.done():
            task.cancel()

        updated = self.repo.pause_timer(target.id)
        if updated is None:
            return VerifiedActionResult(
                success=False,
                action="PauseTimer",
                status="failed",
                message=get_phrase("timer_not_found", language),
                error="Timer disappeared before pause.",
            )
        view = TimerView.from_record(updated)
        await self._emit("timer_updated", updated)
        msg = get_phrase(
            "timer_paused",
            language,
            label_part=_label_part(updated.label, language),
            duration=format_duration_spoken(view.remaining_seconds, language),
        )
        return VerifiedActionResult(
            success=True,
            action="PauseTimer",
            status="verified",
            data={"timer": view.model_dump()},
            message=msg,
        )

    async def resume_timer(
        self,
        *,
        timer_id: uuid.UUID | None = None,
        label: str | None = None,
        language: LanguageCode = "en",
    ) -> VerifiedActionResult:
        target, err = self._resolve_single_target(
            timer_id=timer_id,
            label=label,
            statuses=("paused",),
            language=language,
            action="ResumeTimer",
        )
        if err is not None or target is None:
            return err  # type: ignore[return-value]

        updated = self.repo.resume_timer(target.id)
        if updated is None:
            return VerifiedActionResult(
                success=False,
                action="ResumeTimer",
                status="failed",
                message=get_phrase("timer_not_found", language),
                error="Timer disappeared before resume.",
            )
        self._schedule_task(updated)
        view = TimerView.from_record(updated)
        await self._emit("timer_updated", updated)
        msg = get_phrase(
            "timer_resumed",
            language,
            label_part=_label_part(updated.label, language),
            duration=format_duration_spoken(view.remaining_seconds, language),
        )
        return VerifiedActionResult(
            success=True,
            action="ResumeTimer",
            status="verified",
            data={"timer": view.model_dump()},
            message=msg,
        )

    async def cancel_timer(
        self,
        *,
        timer_id: uuid.UUID | None = None,
        label: str | None = None,
        language: LanguageCode = "en",
    ) -> VerifiedActionResult:
        target, err = self._resolve_single_target(
            timer_id=timer_id,
            label=label,
            statuses=("active", "paused"),
            language=language,
            action="CancelTimer",
        )
        if err is not None or target is None:
            return err  # type: ignore[return-value]

        task = self._tasks.pop(target.id, None)
        if task is not None and not task.done():
            task.cancel()

        cancelled = self.repo.cancel_timer(target.id)
        if cancelled is None:
            return VerifiedActionResult(
                success=False,
                action="CancelTimer",
                status="failed",
                message=get_phrase("timer_not_found", language),
                error="Timer could not be cancelled.",
            )
        view = TimerView.from_record(cancelled)
        await self._emit("timer_updated", cancelled)
        msg = get_phrase(
            "timer_cancelled",
            language,
            label_part=_label_part(cancelled.label, language),
        )
        return VerifiedActionResult(
            success=True,
            action="CancelTimer",
            status="verified",
            data={"timer": view.model_dump()},
            message=msg,
        )

    async def cancel_all(self, *, language: LanguageCode = "en") -> VerifiedActionResult:
        rows = self.repo.cancel_all_open()
        for row in rows:
            task = self._tasks.pop(row.id, None)
            if task is not None and not task.done():
                task.cancel()
            await self._emit("timer_updated", row)

        if not rows:
            return VerifiedActionResult(
                success=False,
                action="CancelAllTimers",
                status="failed",
                data={"cancelled_count": 0},
                message=get_phrase("no_active_timers", language),
                error="No active timers to cancel.",
            )

        return VerifiedActionResult(
            success=True,
            action="CancelAllTimers",
            status="verified",
            data={"cancelled_count": len(rows)},
            message=get_phrase("timers_cancelled_all", language, count=len(rows)),
        )
