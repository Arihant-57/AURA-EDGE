"""FastAPI application entrypoint for AURA EDGE (Phase 0 + Phase 1 modular monolith)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aura_edge import __version__
from aura_edge.api.routes_health import router as health_router
from aura_edge.api.routes_timers import router as timers_router
from aura_edge.api.routes_voice import router as voice_router
from aura_edge.api.ws_events import WebSocketEventHub, ws_router
from aura_edge.application.voice_orchestrator import VoiceOrchestrator
from aura_edge.config import Settings, apply_offline_env_guards, get_settings
from aura_edge.data.database import create_db_engine
from aura_edge.infrastructure.network_policy import NetworkPolicyEnforcer


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construct the AURA EDGE FastAPI application with strict config & policy wiring."""
    cfg = settings or get_settings()
    apply_offline_env_guards(cfg)

    enforcer = NetworkPolicyEnforcer.from_settings(cfg)
    engine = create_db_engine(cfg.database_url)
    ws_hub = WebSocketEventHub()
    orchestrator = VoiceOrchestrator(cfg, enforcer, engine, ws_hub)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await orchestrator.startup()
        try:
            yield
        finally:
            await orchestrator.shutdown()

    app = FastAPI(
        title="AURA EDGE",
        version=__version__,
        description="Offline-first multilingual personal AI assistant (Phase 0 + Phase 1).",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    app.state.settings = cfg
    app.state.enforcer = enforcer
    app.state.engine = engine
    app.state.ws_hub = ws_hub
    app.state.orchestrator = orchestrator

    app.include_router(health_router)
    app.include_router(voice_router)
    app.include_router(timers_router)
    app.include_router(ws_router)
    return app


app = create_app()
