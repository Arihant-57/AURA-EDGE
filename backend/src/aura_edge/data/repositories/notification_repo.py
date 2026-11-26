"""Authoritative PostgreSQL repository for persistent local notifications."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from aura_edge.data.models import NotificationPreference, NotificationRecord


def _utc_now() -> datetime:
    return datetime.now(UTC)


class NotificationRepository:
    """PostgreSQL persistence & deduplication for proactive local notifications."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get_preferences(self, profile_id: str = "default") -> NotificationPreference:
        with Session(self.engine) as session:
            row = session.get(NotificationPreference, profile_id)
            if row is None:
                row = NotificationPreference(profile_id=profile_id)
                session.add(row)
                session.commit()
                session.refresh(row)
            session.expunge(row)
            return row

    def update_preferences(
        self,
        profile_id: str = "default",
        *,
        visual_enabled: bool | None = None,
        spoken_enabled: bool | None = None,
        quiet_hours_enabled: bool | None = None,
        quiet_hours_start: str | None = None,
        quiet_hours_end: str | None = None,
        timezone_name: str | None = None,
    ) -> NotificationPreference:
        with Session(self.engine) as session:
            row = session.get(NotificationPreference, profile_id)
            if row is None:
                row = NotificationPreference(profile_id=profile_id)
                session.add(row)
            if visual_enabled is not None:
                row.visual_enabled = bool(visual_enabled)
            if spoken_enabled is not None:
                row.spoken_enabled = bool(spoken_enabled)
            if quiet_hours_enabled is not None:
                row.quiet_hours_enabled = bool(quiet_hours_enabled)
            if quiet_hours_start is not None:
                row.quiet_hours_start = quiet_hours_start.strip()[:5]
            if quiet_hours_end is not None:
                row.quiet_hours_end = quiet_hours_end.strip()[:5]
            if timezone_name is not None:
                row.timezone_name = timezone_name.strip()[:64]
            row.updated_at_utc = _utc_now()
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def create_notification(
        self,
        *,
        notification_type: str,
        title: str,
        message: str,
        language: str = "en",
        source_id: str | None = None,
        priority: str = "normal",
        dedup_key: str | None = None,
        metadata_json: dict[str, Any] | None = None,
        now_utc: datetime | None = None,
    ) -> tuple[NotificationRecord, bool]:
        """Insert notification atomically; deduplicates on non-null dedup_key.

        Returns ``(record, created)``.
        """
        now = now_utc or _utc_now()
        clean_dedup = dedup_key.strip()[:160] if dedup_key and dedup_key.strip() else None
        new_id = uuid.uuid4()
        values = {
            "id": new_id,
            "notification_type": notification_type[:48],
            "title": title[:200],
            "message": message,
            "language": language if language in ("en", "hi", "kn") else "en",
            "source_id": source_id[:128] if source_id else None,
            "priority": priority if priority in ("low", "normal", "high", "urgent") else "normal",
            "is_read": False,
            "is_dismissed": False,
            "spoken_delivered": False,
            "dedup_key": clean_dedup,
            "metadata_json": dict(metadata_json or {}),
            "created_at_utc": now,
        }

        with Session(self.engine) as session:
            if clean_dedup is not None:
                stmt = (
                    pg_insert(NotificationRecord)
                    .values(**values)
                    .on_conflict_do_nothing(
                        index_elements=["dedup_key"],
                        index_where=NotificationRecord.dedup_key.is_not(None),
                    )
                    .returning(NotificationRecord.id)
                )
                inserted_id = session.execute(stmt).scalar_one_or_none()
                session.commit()
                if inserted_id is None:
                    existing = session.scalar(
                        select(NotificationRecord).where(NotificationRecord.dedup_key == clean_dedup)
                    )
                    assert existing is not None
                    session.expunge(existing)
                    return existing, False
                row = session.get(NotificationRecord, inserted_id)
                assert row is not None
                session.expunge(row)
                return row, True

            row = NotificationRecord(**values)
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row, True

    def list_notifications(
        self,
        *,
        unread_only: bool = False,
        notification_type: str | None = None,
        include_dismissed: bool = False,
        limit: int = 50,
    ) -> list[NotificationRecord]:
        with Session(self.engine) as session:
            stmt = select(NotificationRecord)
            if not include_dismissed:
                stmt = stmt.where(NotificationRecord.is_dismissed.is_(False))
            if unread_only:
                stmt = stmt.where(NotificationRecord.is_read.is_(False))
            if notification_type:
                stmt = stmt.where(NotificationRecord.notification_type == notification_type)
            stmt = stmt.order_by(NotificationRecord.created_at_utc.desc()).limit(max(1, min(limit, 500)))
            rows = list(session.scalars(stmt).all())
            for r in rows:
                session.expunge(r)
            return rows

    def unread_count(self) -> int:
        with Session(self.engine) as session:
            count = session.scalar(
                select(func.count())
                .select_from(NotificationRecord)
                .where(
                    NotificationRecord.is_dismissed.is_(False),
                    NotificationRecord.is_read.is_(False),
                )
            )
            return int(count or 0)

    def mark_read(self, notification_id: uuid.UUID) -> NotificationRecord | None:
        now = _utc_now()
        with Session(self.engine) as session:
            row = session.get(NotificationRecord, notification_id)
            if row is None:
                return None
            if not row.is_read:
                row.is_read = True
                row.read_at_utc = now
                session.commit()
                session.refresh(row)
            session.expunge(row)
            return row

    def mark_all_read(self) -> int:
        now = _utc_now()
        with Session(self.engine) as session:
            res = session.execute(
                update(NotificationRecord)
                .where(
                    NotificationRecord.is_dismissed.is_(False),
                    NotificationRecord.is_read.is_(False),
                )
                .values(is_read=True, read_at_utc=now)
            )
            session.commit()
            return int(res.rowcount or 0)

    def dismiss(self, notification_id: uuid.UUID) -> NotificationRecord | None:
        now = _utc_now()
        with Session(self.engine) as session:
            row = session.get(NotificationRecord, notification_id)
            if row is None:
                return None
            row.is_dismissed = True
            row.dismissed_at_utc = now
            if not row.is_read:
                row.is_read = True
                row.read_at_utc = now
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def mark_spoken_delivered(self, notification_id: uuid.UUID) -> None:
        with Session(self.engine) as session:
            session.execute(
                update(NotificationRecord)
                .where(NotificationRecord.id == notification_id)
                .values(spoken_delivered=True)
            )
            session.commit()
