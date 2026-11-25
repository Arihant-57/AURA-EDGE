"""Phase 4 Local Expense Tracking service with exact integer paise SQL aggregation."""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Engine

from aura_edge.api.ws_events import WebSocketEventHub
from aura_edge.data.models import ExpenseTransactionRecord
from aura_edge.data.repositories.personal import ExpenseRepository


def _to_paise(amount: Decimal | str | float) -> int:
    try:
        dec = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid monetary amount: {amount!r}") from exc
    paise = int((dec * 100).to_integral_value())
    if paise <= 0:
        raise ValueError("Expense amount must be strictly positive.")
    return paise


def _format_rupees(paise: int) -> str:
    return f"{Decimal(paise) / Decimal(100):.2f}"


class ExpenseService:
    """Strictly offline personal expense tracker using integer minor units (paise)."""

    def __init__(
        self,
        engine: Engine,
        ws_hub: WebSocketEventHub,
        *,
        default_timezone: str = "Asia/Kolkata",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repo = ExpenseRepository(engine)
        self.ws_hub = ws_hub
        self.default_timezone = default_timezone
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def to_dict(row: ExpenseTransactionRecord) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "amount_paise": int(row.amount_paise),
            "amount_inr": _format_rupees(int(row.amount_paise)),
            "currency": row.currency,
            "category": row.category,
            "merchant": row.merchant,
            "description": row.description,
            "occurred_at_utc": row.occurred_at_utc.isoformat(),
            "source": row.source,
            "import_id": str(row.import_id) if row.import_id else None,
            "created_at_utc": row.created_at_utc.isoformat(),
        }

    async def add_expense(
        self,
        *,
        amount: Decimal | str | float,
        category: str = "general",
        merchant: str | None = None,
        description: str | None = None,
        occurred_at_utc: datetime | None = None,
        source: str = "manual",
        currency: str = "INR",
    ) -> dict[str, Any]:
        paise = _to_paise(amount)
        when = occurred_at_utc or self._clock()
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        row = self.repo.create(
            amount_paise=paise,
            currency=currency.upper()[:8] or "INR",
            category=(category or "general").strip().lower()[:64],
            merchant=merchant.strip()[:160] if merchant else None,
            description=description,
            occurred_at_utc=when.astimezone(UTC),
            source=source if source in {"manual", "csv_import", "voice"} else "manual",
        )
        view = self.to_dict(row)
        await self.ws_hub.broadcast("expense_updated", {"action": "created", "expense": view})
        return view

    def list_expenses(
        self,
        *,
        category: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return [self.to_dict(r) for r in self.repo.list_transactions(category=category, limit=limit)]

    async def delete_expense(self, expense_id: uuid.UUID) -> bool:
        ok = self.repo.delete(expense_id)
        if not ok:
            raise LookupError(f"Expense '{expense_id}' not found.")
        await self.ws_hub.broadcast(
            "expense_updated", {"action": "deleted", "expense_id": str(expense_id)}
        )
        return True

    def get_summary(self, *, period: str = "month") -> dict[str, Any]:
        tz = ZoneInfo(self.default_timezone)
        now_utc = self._clock()
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=UTC)
        now_local = now_utc.astimezone(tz)

        if period == "day":
            start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            end_local = start_local + timedelta(days=1)
        elif period == "all":
            start_local = datetime(1970, 1, 1, tzinfo=tz)
            end_local = now_local + timedelta(days=3650)
        else:
            start_local = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if start_local.month == 12:
                end_local = start_local.replace(year=start_local.year + 1, month=1)
            else:
                end_local = start_local.replace(month=start_local.month + 1)

        total_paise, count, by_cat = self.repo.exact_summary(
            start_utc=start_local.astimezone(UTC),
            end_utc=end_local.astimezone(UTC),
        )
        return {
            "period": period,
            "timezone": tz.key,
            "window_start_utc": start_local.astimezone(UTC).isoformat(),
            "window_end_utc": end_local.astimezone(UTC).isoformat(),
            "total_paise": total_paise,
            "total_inr": _format_rupees(total_paise),
            "transaction_count": count,
            "by_category": [
                {
                    "category": cat,
                    "total_paise": amt,
                    "total_inr": _format_rupees(amt),
                    "count": cnt,
                }
                for cat, amt, cnt in by_cat
            ],
        }

    async def import_csv(self, *, filename: str, csv_content: str) -> dict[str, Any]:
        normalized = (csv_content or "").strip()
        if not normalized:
            raise ValueError("CSV content cannot be empty.")
        file_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        existing_imp = self.repo.get_import_by_sha256(file_hash)
        if existing_imp is not None:
            return {
                "duplicate_file": True,
                "import_id": str(existing_imp.id),
                "imported_count": 0,
                "skipped_duplicates": existing_imp.row_count,
            }

        reader = csv.DictReader(io.StringIO(normalized))
        rows = list(reader)
        if not rows:
            raise ValueError("CSV must contain at least one transaction row.")

        imp_log = self.repo.create_import_log(
            filename=filename or "expenses.csv",
            file_sha256=file_hash,
            row_count=len(rows),
            imported_count=0,
            skipped_duplicates=0,
        )
        imported = 0
        skipped = 0
        tz = ZoneInfo(self.default_timezone)
        for row in rows:
            raw_amt = row.get("amount") or row.get("amount_inr") or ""
            paise = _to_paise(raw_amt)
            cat = (row.get("category") or "general").strip().lower()[:64]
            merch = (row.get("merchant") or "").strip()[:160] or None
            desc = (row.get("description") or "").strip() or None
            raw_dt = (row.get("date") or row.get("occurred_at") or "").strip()
            if raw_dt:
                parsed_dt = datetime.fromisoformat(raw_dt)
                when_utc = (
                    parsed_dt.replace(tzinfo=tz) if parsed_dt.tzinfo is None else parsed_dt
                ).astimezone(UTC)
            else:
                when_utc = self._clock().astimezone(UTC)
            row_sig = f"{when_utc.isoformat()}|{paise}|{cat}|{merch or ''}|{desc or ''}"
            row_hash = hashlib.sha256(row_sig.encode("utf-8")).hexdigest()
            if self.repo.exists_dedup_hash(row_hash):
                skipped += 1
                continue
            self.repo.create(
                amount_paise=paise,
                currency="INR",
                category=cat,
                merchant=merch,
                description=desc,
                occurred_at_utc=when_utc,
                source="csv_import",
                import_id=imp_log.id,
                dedup_hash=row_hash,
            )
            imported += 1

        await self.ws_hub.broadcast(
            "expense_updated",
            {"action": "csv_imported", "import_id": str(imp_log.id), "imported_count": imported},
        )
        return {
            "duplicate_file": False,
            "import_id": str(imp_log.id),
            "row_count": len(rows),
            "imported_count": imported,
            "skipped_duplicates": skipped,
        }
