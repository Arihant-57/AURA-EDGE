"""PostgreSQL repositories for Phase 4 Personal Secretary entities."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from aura_edge.data.models import (
    CalendarEventRecord,
    ExpenseImportRecord,
    ExpenseTransactionRecord,
    GroceryInventoryItem,
    LocalRecipeRecord,
    ParcelRecord,
    ShoppingListItem,
    TaskRecord,
)


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


class TaskRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create(
        self,
        *,
        title: str,
        description: str | None = None,
        priority: str = "normal",
        category: str = "general",
        due_at_utc: datetime | None = None,
        reminder_job_id: uuid.UUID | None = None,
    ) -> TaskRecord:
        with Session(self.engine) as session:
            row = TaskRecord(
                title=title[:200],
                description=description,
                status="pending",
                priority=priority,
                category=category[:64],
                due_at_utc=_utc(due_at_utc) if due_at_utc else None,
                reminder_job_id=reminder_job_id,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def get(self, task_id: uuid.UUID) -> TaskRecord | None:
        with Session(self.engine) as session:
            return session.get(TaskRecord, task_id)

    def list_tasks(
        self,
        *,
        status: str | None = None,
        category: str | None = None,
        limit: int = 100,
    ) -> list[TaskRecord]:
        with Session(self.engine) as session:
            stmt = select(TaskRecord)
            if status:
                stmt = stmt.where(TaskRecord.status == status)
            if category:
                stmt = stmt.where(TaskRecord.category == category)
            stmt = stmt.order_by(
                TaskRecord.due_at_utc.asc().nullslast(),
                TaskRecord.created_at_utc.desc(),
            ).limit(limit)
            return list(session.scalars(stmt))

    def update(self, task_id: uuid.UUID, **fields: Any) -> TaskRecord | None:
        with Session(self.engine) as session:
            row = session.get(TaskRecord, task_id)
            if row is None:
                return None
            for k, v in fields.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            row.updated_at_utc = datetime.now(UTC)
            session.commit()
            session.refresh(row)
            return row

    def delete(self, task_id: uuid.UUID) -> TaskRecord | None:
        with Session(self.engine) as session:
            row = session.get(TaskRecord, task_id)
            if row is None:
                return None
            session.delete(row)
            session.commit()
            return row


class CalendarRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create(
        self,
        *,
        title: str,
        start_at_utc: datetime,
        end_at_utc: datetime,
        description: str | None = None,
        location: str | None = None,
        all_day: bool = False,
        timezone_name: str = "Asia/Kolkata",
        recurrence_json: dict[str, Any] | None = None,
        reminder_minutes_before: int | None = None,
        reminder_job_id: uuid.UUID | None = None,
    ) -> CalendarEventRecord:
        with Session(self.engine) as session:
            row = CalendarEventRecord(
                title=title[:200],
                description=description,
                location=location[:160] if location else None,
                start_at_utc=_utc(start_at_utc),
                end_at_utc=_utc(end_at_utc),
                all_day=all_day,
                timezone_name=timezone_name,
                recurrence_json=dict(recurrence_json or {"kind": "once"}),
                status="scheduled",
                reminder_minutes_before=reminder_minutes_before,
                reminder_job_id=reminder_job_id,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def get(self, event_id: uuid.UUID) -> CalendarEventRecord | None:
        with Session(self.engine) as session:
            return session.get(CalendarEventRecord, event_id)

    def list_scheduled(self, *, limit: int = 200) -> list[CalendarEventRecord]:
        with Session(self.engine) as session:
            stmt = (
                select(CalendarEventRecord)
                .where(CalendarEventRecord.status == "scheduled")
                .order_by(CalendarEventRecord.start_at_utc.asc())
                .limit(limit)
            )
            return list(session.scalars(stmt))

    def update(self, event_id: uuid.UUID, **fields: Any) -> CalendarEventRecord | None:
        with Session(self.engine) as session:
            row = session.get(CalendarEventRecord, event_id)
            if row is None:
                return None
            for k, v in fields.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            row.updated_at_utc = datetime.now(UTC)
            session.commit()
            session.refresh(row)
            return row

    def delete(self, event_id: uuid.UUID) -> CalendarEventRecord | None:
        with Session(self.engine) as session:
            row = session.get(CalendarEventRecord, event_id)
            if row is None:
                return None
            session.delete(row)
            session.commit()
            return row


class GroceryRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def list_shopping(self, *, include_purchased: bool = True) -> list[ShoppingListItem]:
        with Session(self.engine) as session:
            stmt = select(ShoppingListItem)
            if not include_purchased:
                stmt = stmt.where(ShoppingListItem.purchased.is_(False))
            stmt = stmt.order_by(
                ShoppingListItem.purchased.asc(),
                ShoppingListItem.created_at_utc.desc(),
            )
            return list(session.scalars(stmt))

    def get_shopping_item(self, item_id: uuid.UUID) -> ShoppingListItem | None:
        with Session(self.engine) as session:
            return session.get(ShoppingListItem, item_id)

    def add_or_increment_shopping(
        self,
        *,
        item_name: str,
        canonical_name: str,
        quantity: Decimal,
        unit: str,
        added_via: str,
    ) -> ShoppingListItem:
        with Session(self.engine) as session:
            existing = session.scalar(
                select(ShoppingListItem).where(
                    ShoppingListItem.canonical_name == canonical_name,
                    ShoppingListItem.purchased.is_(False),
                )
            )
            if existing is not None:
                existing.quantity = existing.quantity + quantity
                existing.updated_at_utc = datetime.now(UTC)
                session.commit()
                session.refresh(existing)
                return existing
            row = ShoppingListItem(
                item_name=item_name[:120],
                canonical_name=canonical_name[:120],
                quantity=quantity,
                unit=unit[:32],
                purchased=False,
                added_via=added_via,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def update_shopping(self, item_id: uuid.UUID, **fields: Any) -> ShoppingListItem | None:
        with Session(self.engine) as session:
            row = session.get(ShoppingListItem, item_id)
            if row is None:
                return None
            for k, v in fields.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            row.updated_at_utc = datetime.now(UTC)
            session.commit()
            session.refresh(row)
            return row

    def delete_shopping(self, item_id: uuid.UUID) -> bool:
        with Session(self.engine) as session:
            row = session.get(ShoppingListItem, item_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def list_inventory(self) -> list[GroceryInventoryItem]:
        with Session(self.engine) as session:
            stmt = select(GroceryInventoryItem).order_by(GroceryInventoryItem.display_name.asc())
            return list(session.scalars(stmt))

    def get_inventory_item(self, item_id: uuid.UUID) -> GroceryInventoryItem | None:
        with Session(self.engine) as session:
            return session.get(GroceryInventoryItem, item_id)

    def upsert_inventory(
        self,
        *,
        canonical_name: str,
        display_name: str,
        aliases: list[str],
        category: str,
        quantity: Decimal,
        unit: str,
        min_threshold: Decimal,
        expires_at_utc: datetime | None,
    ) -> GroceryInventoryItem:
        with Session(self.engine) as session:
            row = session.scalar(
                select(GroceryInventoryItem).where(
                    GroceryInventoryItem.canonical_name == canonical_name
                )
            )
            if row is not None:
                row.display_name = display_name[:120]
                row.aliases_json = list(dict.fromkeys([*row.aliases_json, *aliases]))
                row.category = category[:64]
                row.quantity = quantity
                row.unit = unit[:32]
                row.min_threshold = min_threshold
                row.expires_at_utc = _utc(expires_at_utc) if expires_at_utc else None
                row.updated_at_utc = datetime.now(UTC)
                session.commit()
                session.refresh(row)
                return row
            row = GroceryInventoryItem(
                canonical_name=canonical_name[:120],
                display_name=display_name[:120],
                aliases_json=aliases,
                category=category[:64],
                quantity=quantity,
                unit=unit[:32],
                min_threshold=min_threshold,
                expires_at_utc=_utc(expires_at_utc) if expires_at_utc else None,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def update_inventory(self, item_id: uuid.UUID, **fields: Any) -> GroceryInventoryItem | None:
        with Session(self.engine) as session:
            row = session.get(GroceryInventoryItem, item_id)
            if row is None:
                return None
            for k, v in fields.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            row.updated_at_utc = datetime.now(UTC)
            session.commit()
            session.refresh(row)
            return row

    def delete_inventory(self, item_id: uuid.UUID) -> bool:
        with Session(self.engine) as session:
            row = session.get(GroceryInventoryItem, item_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def ensure_recipes(self, recipes: list[dict[str, Any]]) -> None:
        with Session(self.engine) as session:
            existing_ids = set(session.scalars(select(LocalRecipeRecord.id)))
            for rec in recipes:
                if rec["id"] not in existing_ids:
                    session.add(
                        LocalRecipeRecord(
                            id=rec["id"],
                            title=rec["title"],
                            aliases_json=rec.get("aliases", []),
                            cuisine=rec.get("cuisine", "indian"),
                            prep_minutes=int(rec.get("prep_minutes", 20)),
                            ingredients_json=rec.get("ingredients", []),
                            steps_json=rec.get("steps", []),
                        )
                    )
            session.commit()

    def list_recipes(self) -> list[LocalRecipeRecord]:
        with Session(self.engine) as session:
            return list(session.scalars(select(LocalRecipeRecord).order_by(LocalRecipeRecord.title.asc())))


class ExpenseRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create(
        self,
        *,
        amount_paise: int,
        currency: str,
        category: str,
        merchant: str | None,
        description: str | None,
        occurred_at_utc: datetime,
        source: str,
        import_id: uuid.UUID | None = None,
        dedup_hash: str | None = None,
    ) -> ExpenseTransactionRecord:
        with Session(self.engine) as session:
            row = ExpenseTransactionRecord(
                amount_paise=amount_paise,
                currency=currency[:8],
                category=category[:64],
                merchant=merchant[:160] if merchant else None,
                description=description,
                occurred_at_utc=_utc(occurred_at_utc),
                source=source,
                import_id=import_id,
                dedup_hash=dedup_hash,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def list_transactions(
        self,
        *,
        category: str | None = None,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
        limit: int = 200,
    ) -> list[ExpenseTransactionRecord]:
        with Session(self.engine) as session:
            stmt = select(ExpenseTransactionRecord)
            if category:
                stmt = stmt.where(ExpenseTransactionRecord.category == category)
            if start_utc:
                stmt = stmt.where(ExpenseTransactionRecord.occurred_at_utc >= _utc(start_utc))
            if end_utc:
                stmt = stmt.where(ExpenseTransactionRecord.occurred_at_utc < _utc(end_utc))
            stmt = stmt.order_by(ExpenseTransactionRecord.occurred_at_utc.desc()).limit(limit)
            return list(session.scalars(stmt))

    def delete(self, expense_id: uuid.UUID) -> bool:
        with Session(self.engine) as session:
            row = session.get(ExpenseTransactionRecord, expense_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def exact_summary(
        self,
        *,
        start_utc: datetime,
        end_utc: datetime,
    ) -> tuple[int, int, list[tuple[str, int, int]]]:
        """Compute exact SQL SUM(amount_paise) total, count, and per-category breakdown."""
        s_utc = _utc(start_utc)
        e_utc = _utc(end_utc)
        with Session(self.engine) as session:
            total_row = session.execute(
                select(
                    func.coalesce(func.sum(ExpenseTransactionRecord.amount_paise), 0),
                    func.count(ExpenseTransactionRecord.id),
                ).where(
                    ExpenseTransactionRecord.occurred_at_utc >= s_utc,
                    ExpenseTransactionRecord.occurred_at_utc < e_utc,
                )
            ).one()
            cat_rows = session.execute(
                select(
                    ExpenseTransactionRecord.category,
                    func.coalesce(func.sum(ExpenseTransactionRecord.amount_paise), 0),
                    func.count(ExpenseTransactionRecord.id),
                )
                .where(
                    ExpenseTransactionRecord.occurred_at_utc >= s_utc,
                    ExpenseTransactionRecord.occurred_at_utc < e_utc,
                )
                .group_by(ExpenseTransactionRecord.category)
                .order_by(func.sum(ExpenseTransactionRecord.amount_paise).desc())
            ).all()
            return (
                int(total_row[0]),
                int(total_row[1]),
                [(str(c), int(amt), int(cnt)) for c, amt, cnt in cat_rows],
            )

    def get_import_by_sha256(self, file_sha256: str) -> ExpenseImportRecord | None:
        with Session(self.engine) as session:
            return session.scalar(
                select(ExpenseImportRecord).where(ExpenseImportRecord.file_sha256 == file_sha256)
            )

    def exists_dedup_hash(self, dedup_hash: str) -> bool:
        with Session(self.engine) as session:
            return (
                session.scalar(
                    select(ExpenseTransactionRecord.id).where(
                        ExpenseTransactionRecord.dedup_hash == dedup_hash
                    )
                )
                is not None
            )

    def create_import_log(
        self,
        *,
        filename: str,
        file_sha256: str,
        row_count: int,
        imported_count: int,
        skipped_duplicates: int,
    ) -> ExpenseImportRecord:
        with Session(self.engine) as session:
            row = ExpenseImportRecord(
                filename=filename[:200],
                file_sha256=file_sha256,
                row_count=row_count,
                imported_count=imported_count,
                skipped_duplicates=skipped_duplicates,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row


class ParcelRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create(
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
        reminder_job_id: uuid.UUID | None = None,
    ) -> ParcelRecord:
        with Session(self.engine) as session:
            row = ParcelRecord(
                order_name=order_name[:200],
                carrier=carrier[:80] if carrier else None,
                tracking_ref=tracking_ref[:120] if tracking_ref else None,
                expected_delivery_date=expected_delivery_date,
                last_known_status=last_known_status[:160],
                source=source,
                is_sample=is_sample,
                notes=notes,
                reminder_job_id=reminder_job_id,
                last_updated_at_utc=datetime.now(UTC),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def get(self, parcel_id: uuid.UUID) -> ParcelRecord | None:
        with Session(self.engine) as session:
            return session.get(ParcelRecord, parcel_id)

    def list_parcels(self, *, limit: int = 100) -> list[ParcelRecord]:
        with Session(self.engine) as session:
            stmt = (
                select(ParcelRecord)
                .order_by(
                    ParcelRecord.expected_delivery_date.asc().nullslast(),
                    ParcelRecord.created_at_utc.desc(),
                )
                .limit(limit)
            )
            return list(session.scalars(stmt))

    def update(self, parcel_id: uuid.UUID, **fields: Any) -> ParcelRecord | None:
        with Session(self.engine) as session:
            row = session.get(ParcelRecord, parcel_id)
            if row is None:
                return None
            for k, v in fields.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            row.last_updated_at_utc = datetime.now(UTC)
            session.commit()
            session.refresh(row)
            return row

    def delete(self, parcel_id: uuid.UUID) -> ParcelRecord | None:
        with Session(self.engine) as session:
            row = session.get(ParcelRecord, parcel_id)
            if row is None:
                return None
            session.delete(row)
            session.commit()
            return row
