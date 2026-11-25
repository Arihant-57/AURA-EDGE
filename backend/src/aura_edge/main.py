"""FastAPI application entrypoint for AURA EDGE (Phase 0 modular monolith)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aura_edge import __version__
from aura_edge.api.routes_health import router as health_router
from aura_edge.config import Settings, get_settings
from aura_edge.data.database import create_db_engine
from aura_edge.infrastructure.network_policy import NetworkPolicyEnforcer


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construct the AURA EDGE FastAPI application with strict config & policy wiring."""
    cfg = settings or get_settings()
    app = FastAPI(
        title="AURA EDGE",
        version=__version__,
        description="Offline-first multilingual personal AI assistant (Phase 0 foundation).",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    app.state.settings = cfg
    app.state.enforcer = NetworkPolicyEnforcer.from_settings(cfg)
    app.state.engine = create_db_engine(cfg.database_url)

    app.include_router(health_router)
    return app


app = create_app()
