"""Phase 4 integration tests: Groceries, Offline Recipes, Expenses (paise), and Parcels."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from aura_edge.config import ComputeMode, ConnectivityMode, Settings
from aura_edge.data.database import create_db_engine
from aura_edge.data.models import (
    ExpenseImportRecord,
    ExpenseTransactionRecord,
    GroceryInventoryItem,
    JobExecution,
    NotificationPreference,
    NotificationRecord,
    ParcelRecord,
    ScheduledJob,
    ShoppingListItem,
)
from aura_edge.main import create_app


def _offline_settings() -> Settings:
    return Settings(
        _env_file=None,
        connectivity_mode=ConnectivityMode.STRICT_OFFLINE,
        compute_mode=ComputeMode.CPU,
    )


def _cleanup_phase4_part2() -> None:
    engine = create_db_engine(_offline_settings().database_url)
    with Session(engine) as session:
        session.execute(delete(ShoppingListItem))
        session.execute(delete(GroceryInventoryItem))
        session.execute(delete(ExpenseTransactionRecord))
        session.execute(delete(ExpenseImportRecord))
        session.execute(delete(ParcelRecord))
        session.execute(delete(JobExecution))
        session.execute(delete(ScheduledJob))
        session.execute(delete(NotificationRecord))
        session.execute(delete(NotificationPreference))
        session.commit()


@pytest.fixture(autouse=True)
def _cleanup() -> Generator[None, None, None]:
    _cleanup_phase4_part2()
    yield
    _cleanup_phase4_part2()


def test_groceries_inventory_and_recipe_matching() -> None:
    app = create_app(_offline_settings())
    with TestClient(app) as client:
        client.put("/api/v1/notifications/preferences", json={"spoken_enabled": False})

        # Add shopping item via voice & check list
        v_add = client.post(
            "/api/v1/intent/process",
            json={"text": "Add fresh milk to my grocery list", "speak": False},
        ).json()
        assert v_add["intent"] == "AddGroceryItem"
        assert v_add["action_executed"] is True
        item_id = v_add["data"]["item"]["id"]

        patched = client.patch(f"/api/v1/groceries/list/{item_id}", json={"purchased": True})
        assert patched.status_code == 200
        assert patched.json()["item"]["purchased"] is True

        # Upsert pantry inventory: milk (in stock), tea leaves (in stock), rice (low stock)
        client.post(
            "/api/v1/groceries/inventory",
            json={"name": "दूध", "quantity": 2, "unit": "litre", "min_threshold": 1},
        )
        client.post(
            "/api/v1/groceries/inventory",
            json={"name": "tea leaves", "quantity": 1, "unit": "pack", "min_threshold": 1},
        )
        client.post(
            "/api/v1/groceries/inventory",
            json={"name": "rice", "quantity": 0.5, "unit": "kg", "min_threshold": 2},
        )

        low = client.get("/api/v1/groceries/missing").json()
        assert low["count"] == 1
        assert low["items"][0]["canonical_name"] == "rice"

        # Recipe match distinguishes available vs missing ingredients accurately
        chai = client.get("/api/v1/recipes/suggest", params={"q": "chai"}).json()["recipes"][0]
        assert chai["id"] == "masala_chai"
        assert set(chai["available_ingredients"]) == {"milk", "tea_leaves"}
        assert chai["missing_ingredients"] == ["sugar"]
        assert chai["can_make_now"] is False

        # Add sugar -> Masala Chai becomes makeable now
        client.post(
            "/api/v1/groceries/inventory",
            json={"name": "sugar", "quantity": 1, "unit": "kg", "min_threshold": 0.5},
        )
        chai_ready = client.get("/api/v1/recipes/suggest", params={"q": "chai"}).json()["recipes"][0]
        assert chai_ready["can_make_now"] is True
        assert chai_ready["missing_ingredients"] == []


def test_expenses_exact_paise_csv_dedup_and_parcels_provenance() -> None:
    fixed_utc = datetime(2026, 11, 26, 5, 0, tzinfo=UTC)
    app = create_app(_offline_settings(), clock=lambda: fixed_utc)

    with TestClient(app) as client:
        client.put("/api/v1/notifications/preferences", json={"spoken_enabled": False})

        # Manual decimal + voice expense -> exact integer paise SQL SUM
        e1 = client.post(
            "/api/v1/expenses",
            json={"amount": "125.50", "category": "groceries", "merchant": "Local Mart"},
        )
        assert e1.status_code == 201
        assert e1.json()["expense"]["amount_paise"] == 12550

        v_exp = client.post(
            "/api/v1/intent/process",
            json={"text": "Spent 74.50 on snacks", "speak": False},
        ).json()
        assert v_exp["intent"] == "AddExpense"
        assert v_exp["data"]["expense"]["amount_paise"] == 7450

        # CSV import with SHA-256 deduplication
        csv_body = (
            "date,amount,category,merchant,description\n"
            "2026-11-26T09:00:00+05:30,300.00,transport,Metro,Card topup\n"
        )
        imp1 = client.post(
            "/api/v1/expenses/import-csv",
            json={"filename": "nov.csv", "csv_content": csv_body},
        ).json()
        assert imp1["duplicate_file"] is False
        assert imp1["imported_count"] == 1

        imp2 = client.post(
            "/api/v1/expenses/import-csv",
            json={"filename": "nov.csv", "csv_content": csv_body},
        ).json()
        assert imp2["duplicate_file"] is True

        summary = client.get("/api/v1/expenses/summary", params={"period": "month"}).json()
        assert summary["total_paise"] == 50000
        assert summary["total_inr"] == "500.00"
        assert summary["transaction_count"] == 3

        # Cached parcel management with honest offline provenance & delivery reminder
        exp_date = (fixed_utc.date() + timedelta(days=2)).isoformat()
        p = client.post(
            "/api/v1/parcels",
            json={
                "order_name": "USB Microphone",
                "carrier": "BlueDart",
                "tracking_ref": "BD-OFFLINE-001",
                "expected_delivery_date": exp_date,
                "last_known_status": "Dispatched from hub",
                "source": "manual",
                "create_delivery_reminder": True,
            },
        )
        assert p.status_code == 201
        parcel = p.json()["parcel"]
        assert parcel["source"] == "manual"
        assert parcel["last_updated_at_utc"]
        assert "offline" in parcel["offline_provenance"].lower()
        assert parcel["reminder_job_id"] is not None

        v_parcels = client.post(
            "/api/v1/intent/process",
            json={"text": "List my parcels", "speak": False},
        ).json()
        assert v_parcels["intent"] == "ListParcels"
        assert v_parcels["action_executed"] is True
        assert "USB Microphone" in v_parcels["response_text"]
