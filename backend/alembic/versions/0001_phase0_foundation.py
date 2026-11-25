"""Phase 0 foundation tables and pgvector extension.

Revision ID: 0001_phase0_foundation
Revises:
Create Date: 2026-11-25 11:50:00
"""

from __future__ import annotations

import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_phase0_foundation"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("value_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "connectivity_audit_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("target", sa.String(length=512), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "phase0_vector_smoke",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("label", name="uq_phase0_vector_smoke_label"),
    )


def downgrade() -> None:
    op.drop_table("phase0_vector_smoke")
    op.drop_table("connectivity_audit_events")
    op.drop_table("system_settings")
