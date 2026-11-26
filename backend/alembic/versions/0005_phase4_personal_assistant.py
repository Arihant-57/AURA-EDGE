"""Phase 4 offline personal secretary tables: tasks, calendar, groceries, expenses, parcels.

Revision ID: 0005_phase4_personal_assistant
Revises: 0004_phase3_memory_events
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_phase4_personal_assistant"
down_revision: str | None = "0004_phase3_memory_events"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("priority", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("category", sa.String(64), nullable=False, server_default="general"),
        sa.Column("due_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "reminder_job_id",
            sa.Uuid(),
            sa.ForeignKey("scheduled_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'cancelled')",
            name="ck_tasks_status_enum",
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent')",
            name="ck_tasks_priority_enum",
        ),
    )
    op.create_index("ix_tasks_status_due", "tasks", ["status", "due_at_utc"])
    op.create_index("ix_tasks_category", "tasks", ["category"])

    op.create_table(
        "calendar_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("location", sa.String(160), nullable=True),
        sa.Column("start_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("all_day", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("timezone_name", sa.String(64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column(
            "recurrence_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{" + '"kind":"once"' + "}'::jsonb"),
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="scheduled"),
        sa.Column("reminder_minutes_before", sa.Integer(), nullable=True),
        sa.Column(
            "reminder_job_id",
            sa.Uuid(),
            sa.ForeignKey("scheduled_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("end_at_utc >= start_at_utc", name="ck_calendar_events_end_after_start"),
        sa.CheckConstraint(
            "status IN ('scheduled', 'cancelled')",
            name="ck_calendar_events_status_enum",
        ),
        sa.CheckConstraint(
            "reminder_minutes_before IS NULL OR reminder_minutes_before >= 0",
            name="ck_calendar_events_reminder_nonneg",
        ),
    )
    op.create_index(
        "ix_calendar_events_window", "calendar_events", ["status", "start_at_utc", "end_at_utc"]
    )

    op.create_table(
        "grocery_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("canonical_name", sa.String(120), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column(
            "aliases_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("category", sa.String(64), nullable=False, server_default="pantry"),
        sa.Column("quantity", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("unit", sa.String(32), nullable=False, server_default="unit"),
        sa.Column("min_threshold", sa.Numeric(10, 2), nullable=False, server_default="1"),
        sa.Column("expires_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("quantity >= 0", name="ck_grocery_items_quantity_nonneg"),
        sa.CheckConstraint("min_threshold >= 0", name="ck_grocery_items_threshold_nonneg"),
    )
    op.create_index("ix_grocery_items_category", "grocery_items", ["category"])

    op.create_table(
        "shopping_list_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("item_name", sa.String(120), nullable=False),
        sa.Column("canonical_name", sa.String(120), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 2), nullable=False, server_default="1"),
        sa.Column("unit", sa.String(32), nullable=False, server_default="unit"),
        sa.Column("purchased", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("added_via", sa.String(32), nullable=False, server_default="manual"),
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("quantity > 0", name="ck_shopping_list_items_quantity_pos"),
        sa.CheckConstraint(
            "added_via IN ('manual', 'voice', 'low_stock', 'recipe')",
            name="ck_shopping_list_items_source_enum",
        ),
    )
    op.create_index(
        "ix_shopping_list_items_purchased_created",
        "shopping_list_items",
        ["purchased", "created_at_utc"],
    )

    op.create_table(
        "local_recipes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column(
            "aliases_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("cuisine", sa.String(64), nullable=False, server_default="indian"),
        sa.Column("prep_minutes", sa.Integer(), nullable=False, server_default="20"),
        sa.Column(
            "ingredients_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "steps_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "expense_imports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("filename", sa.String(200), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_duplicates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "imported_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "expense_transactions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="INR"),
        sa.Column("category", sa.String(64), nullable=False, server_default="general"),
        sa.Column("merchant", sa.String(160), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("source", sa.String(24), nullable=False, server_default="manual"),
        sa.Column(
            "import_id",
            sa.Uuid(),
            sa.ForeignKey("expense_imports.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("dedup_hash", sa.String(64), nullable=True, unique=True),
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("amount_paise > 0", name="ck_expense_transactions_amount_pos"),
        sa.CheckConstraint(
            "source IN ('manual', 'csv_import', 'voice')",
            name="ck_expense_transactions_source_enum",
        ),
    )
    op.create_index("ix_expense_transactions_occurred", "expense_transactions", ["occurred_at_utc"])
    op.create_index(
        "ix_expense_transactions_category",
        "expense_transactions",
        ["category", "occurred_at_utc"],
    )

    op.create_table(
        "parcels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("order_name", sa.String(200), nullable=False),
        sa.Column("carrier", sa.String(80), nullable=True),
        sa.Column("tracking_ref", sa.String(120), nullable=True, unique=True),
        sa.Column("expected_delivery_date", sa.Date(), nullable=True),
        sa.Column("last_known_status", sa.String(160), nullable=False, server_default="saved"),
        sa.Column("source", sa.String(24), nullable=False, server_default="manual"),
        sa.Column("is_sample", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "reminder_job_id",
            sa.Uuid(),
            sa.ForeignKey("scheduled_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "last_updated_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'imported', 'sample')",
            name="ck_parcels_source_enum",
        ),
    )
    op.create_index("ix_parcels_delivery_date", "parcels", ["expected_delivery_date"])


def downgrade() -> None:
    op.drop_index("ix_parcels_delivery_date", table_name="parcels")
    op.drop_table("parcels")

    op.drop_index("ix_expense_transactions_category", table_name="expense_transactions")
    op.drop_index("ix_expense_transactions_occurred", table_name="expense_transactions")
    op.drop_table("expense_transactions")
    op.drop_table("expense_imports")

    op.drop_table("local_recipes")

    op.drop_index("ix_shopping_list_items_purchased_created", table_name="shopping_list_items")
    op.drop_table("shopping_list_items")

    op.drop_index("ix_grocery_items_category", table_name="grocery_items")
    op.drop_table("grocery_items")

    op.drop_index("ix_calendar_events_window", table_name="calendar_events")
    op.drop_table("calendar_events")

    op.drop_index("ix_tasks_category", table_name="tasks")
    op.drop_index("ix_tasks_status_due", table_name="tasks")
    op.drop_table("tasks")
