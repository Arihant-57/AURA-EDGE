"""PostgreSQL + pgvector engine factory and honest readiness checks."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError


class DatabaseReadiness(BaseModel):
    """Reported PostgreSQL + pgvector + Alembic state."""

    status: Literal["ok", "unavailable"]
    connected: bool
    postgres_version: str | None = None
    pgvector_installed: bool = False
    pgvector_version: str | None = None
    alembic_revision: str | None = None
    error: str | None = None


def create_db_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine strictly enforcing PostgreSQL."""
    if not database_url.strip().lower().startswith("postgresql"):
        raise ValueError(
            "Only PostgreSQL database URLs are permitted; SQLite substitution is disallowed."
        )
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 2},
    )


def check_database_readiness(engine: Engine) -> DatabaseReadiness:
    """Inspect real database connectivity, pgvector extension, and Alembic head."""
    try:
        with engine.connect() as conn:
            pg_version = conn.execute(text("SELECT version()")).scalar_one_or_none()
            ext_row = conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            ).first()
            pgvector_installed = ext_row is not None
            pgvector_version = str(ext_row[0]) if ext_row is not None else None

            alembic_reg = conn.execute(
                text("SELECT to_regclass('public.alembic_version')")
            ).scalar_one_or_none()
            alembic_rev: str | None = None
            if alembic_reg is not None:
                alembic_rev = conn.execute(
                    text("SELECT version_num FROM alembic_version LIMIT 1")
                ).scalar_one_or_none()

            issues: list[str] = []
            if not pgvector_installed:
                issues.append("pgvector extension ('vector') is not installed")
            if not alembic_rev:
                issues.append("Alembic migrations have not been applied")

            is_ready = pgvector_installed and bool(alembic_rev)
            return DatabaseReadiness(
                status="ok" if is_ready else "unavailable",
                connected=True,
                postgres_version=str(pg_version) if pg_version is not None else None,
                pgvector_installed=pgvector_installed,
                pgvector_version=pgvector_version,
                alembic_revision=str(alembic_rev) if alembic_rev is not None else None,
                error="; ".join(issues) if issues else None,
            )
    except SQLAlchemyError as exc:
        return DatabaseReadiness(
            status="unavailable",
            connected=False,
            error=str(exc),
        )
