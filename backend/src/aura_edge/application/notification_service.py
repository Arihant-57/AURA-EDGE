"""Proactive persistent notification service with bounded serialized audio queue."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from datetime import time as dt_time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Engine

from aura_edge.api.ws_events import WebSocketEventHub
from aura_edge.config import Settings
from aura_edge.data.models import NotificationPreference, NotificationRecord
from aura_edge.data.repositories.notification_repo import NotificationRepository
from aura_edge.intelligence.schemas import LanguageCode

logger = logging.getLogger(__name__)

SpeakCallback = Callable[[str, LanguageCode], Awaitable[bool]]


def _parse_hhmm(raw: str, default: dt_time) -> dt_time:
    try:
        parts = raw.strip().split(":")
        return dt_time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return default


def is_in_quiet_hours(prefs: NotificationPreference, now_utc: datetime) -> bool:
    """Return True if *now_utc* falls inside configured local quiet hours."""
    if not prefs.quiet_hours_enabled:
        return False
    try:
        tz = ZoneInfo(prefs.timezone_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("Asia/Kolkata")
    ref = now_utc if now_utc.tzinfo is not None else now_utc.replace(tzinfo=UTC)
    local_t = ref.astimezone(tz).time().replace(tzinfo=None)
    start_t = _parse_hhmm(prefs.quiet_hours_start, dt_time(22, 0))
    end_t = _parse_hhmm(prefs.quiet_hours_end, dt_time(7, 0))
    if start_t == end_t:
        return False
    if start_t < end_t:
        return start_t <= local_t < end_t
    return local_t >= start_t or local_t < end_t


class NotificationService:
    """Persists notifications before WS broadcast and serializes spoken alerts safely."""

    def __init__(
        self,
        engine: Engine,
        ws_hub: WebSocketEventHub,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repo = NotificationRepository(engine)
        self.ws_hub = ws_hub
        self.settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._speak_cb: SpeakCallback | None = None
        self._audio_queue: asyncio.Queue[tuple[uuid.UUID, str, LanguageCode]] = asyncio.Queue(
            maxsize=max(1, int(settings.notification_audio_queue_max))
        )
        self._worker_task: asyncio.Task[None] | None = None

    def set_speak_callback(self, cb: SpeakCallback | None) -> None:
        self._speak_cb = cb

    @staticmethod
    def to_dict(row: NotificationRecord) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "notification_type": row.notification_type,
            "title": row.title,
            "message": row.message,
            "language": row.language,
            "source_id": row.source_id,
            "priority": row.priority,
            "is_read": row.is_read,
            "is_dismissed": row.is_dismissed,
            "spoken_delivered": row.spoken_delivered,
            "dedup_key": row.dedup_key,
            "metadata": dict(row.metadata_json or {}),
            "created_at_utc": row.created_at_utc.isoformat(),
            "read_at_utc": row.read_at_utc.isoformat() if row.read_at_utc else None,
            "dismissed_at_utc": row.dismissed_at_utc.isoformat() if row.dismissed_at_utc else None,
        }

    @staticmethod
    def prefs_to_dict(row: NotificationPreference) -> dict[str, Any]:
        return {
            "profile_id": row.profile_id,
            "visual_enabled": row.visual_enabled,
            "spoken_enabled": row.spoken_enabled,
            "quiet_hours_enabled": row.quiet_hours_enabled,
            "quiet_hours_start": row.quiet_hours_start,
            "quiet_hours_end": row.quiet_hours_end,
            "timezone_name": row.timezone_name,
            "updated_at_utc": row.updated_at_utc.isoformat(),
        }

    async def startup(self) -> None:
        self.repo.get_preferences()
        if self._worker_task is None or self._worker_task.done():
            self._audio_queue = asyncio.Queue(
                maxsize=max(1, int(self.settings.notification_audio_queue_max))
            )
            self._worker_task = asyncio.create_task(self._audio_worker())

    async def shutdown(self) -> None:
        task = self._worker_task
        self._worker_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _audio_worker(self) -> None:
        while True:
            notif_id, text, lang = await self._audio_queue.get()
            try:
                if self._speak_cb is not None:
                    spoke = await self._speak_cb(text, lang)
                    if spoke:
                        self.repo.mark_spoken_delivered(notif_id)
            except asyncio.CancelledError:
                raise
            except (OSError, RuntimeError, ValueError) as exc:
                # Retain notification record even if TTS/speaker is unavailable
                logger.debug("Notification spoken playback skipped (%s): %s", notif_id, exc)
            finally:
                self._audio_queue.task_done()

    async def notify(
        self,
        *,
        notification_type: str,
        title: str,
        message: str,
        language: LanguageCode = "en",
        source_id: str | None = None,
        priority: str = "normal",
        dedup_key: str | None = None,
        metadata: dict[str, Any] | None = None,
        speak: bool = True,
    ) -> tuple[dict[str, Any], bool]:
        """Persist notification to PostgreSQL first, then broadcast WS & queue audio."""
        now_utc = self._clock()
        row, created = self.repo.create_notification(
            notification_type=notification_type,
            title=title,
            message=message,
            language=language,
            source_id=source_id,
            priority=priority,
            dedup_key=dedup_key,
            metadata_json=metadata,
            now_utc=now_utc,
        )
        view = self.to_dict(row)
        if not created:
            return view, False

        prefs = self.repo.get_preferences()
        unread = self.repo.unread_count()

        await self.ws_hub.broadcast(
            "notification_created",
            {
                "notification": view,
                "unread_count": unread,
                "visual_enabled": prefs.visual_enabled,
            },
        )

        if (speak and self.settings.proactive_audio_enabled and self.settings.audio_output_enabled
                and prefs.spoken_enabled and not is_in_quiet_hours(prefs, now_utc)):
            try:
                self._audio_queue.put_nowait((row.id, message, language))
            except asyncio.QueueFull:
                logger.warning("Notification audio queue full; keeping DB record %s.", row.id)

        return view, True

    async def drain_audio_queue(self, timeout: float = 2.0) -> None:
        if self._audio_queue.empty():
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._audio_queue.join(), timeout=timeout)

    def list_notifications(
        self,
        *,
        unread_only: bool = False,
        notification_type: str | None = None,
        include_dismissed: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        rows = self.repo.list_notifications(
            unread_only=unread_only,
            notification_type=notification_type,
            include_dismissed=include_dismissed,
            limit=limit,
        )
        return [self.to_dict(r) for r in rows]

    def unread_count(self) -> int:
        return self.repo.unread_count()

    async def mark_read(self, notification_id: uuid.UUID) -> dict[str, Any] | None:
        row = self.repo.mark_read(notification_id)
        if row is None:
            return None
        view = self.to_dict(row)
        await self.ws_hub.broadcast(
            "notification_updated",
            {"notification": view, "unread_count": self.repo.unread_count()},
        )
        return view

    async def mark_all_read(self) -> int:
        count = self.repo.mark_all_read()
        await self.ws_hub.broadcast(
            "notification_updated",
            {"action": "read_all", "updated_count": count, "unread_count": self.repo.unread_count()},
        )
        return count

    async def dismiss(self, notification_id: uuid.UUID) -> dict[str, Any] | None:
        row = self.repo.dismiss(notification_id)
        if row is None:
            return None
        view = self.to_dict(row)
        await self.ws_hub.broadcast(
            "notification_updated",
            {"notification": view, "unread_count": self.repo.unread_count()},
        )
        return view

    def get_preferences(self, profile_id: str = "default") -> dict[str, Any]:
        return self.prefs_to_dict(self.repo.get_preferences(profile_id))

    async def update_preferences(
        self,
        profile_id: str = "default",
        **kwargs: Any,
    ) -> dict[str, Any]:
        for key in ("quiet_hours_start", "quiet_hours_end"):
            if key in kwargs and kwargs[key] is not None:
                raw = str(kwargs[key]).strip()
                parts = raw.split(":")
                if len(parts) != 2 or not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
                    raise ValueError(f"{key} must be HH:MM (24-hour).")
        if "timezone_name" in kwargs and kwargs["timezone_name"] is not None:
            try:
                ZoneInfo(str(kwargs["timezone_name"]).strip())
            except ZoneInfoNotFoundError as exc:
                raise ValueError(f"Unknown timezone: {kwargs['timezone_name']}") from exc
        row = self.repo.update_preferences(profile_id, **kwargs)
        return self.prefs_to_dict(row)
