"""FastAPI application entrypoint for AURA EDGE (Phase 0-3 modular monolith)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from aura_edge import __version__
from aura_edge.api.routes_health import router as health_router
from aura_edge.api.routes_memory import router as memory_router
from aura_edge.api.routes_notifications import router as notifications_router
from aura_edge.api.routes_scheduler import router as scheduler_router
from aura_edge.api.routes_smart_home import router as smart_home_router
from aura_edge.api.routes_timers import router as timers_router
from aura_edge.api.routes_voice import router as voice_router
from aura_edge.api.ws_events import WebSocketEventHub, ws_router
from aura_edge.application.memory_service import MemoryService
from aura_edge.application.notification_service import NotificationService
from aura_edge.application.scheduler_service import SchedulerService
from aura_edge.application.smart_home import SmartHomeService
from aura_edge.application.voice_orchestrator import VoiceOrchestrator
from aura_edge.config import Settings, apply_offline_env_guards, get_settings
from aura_edge.data.database import create_db_engine
from aura_edge.infrastructure.network_policy import NetworkPolicyEnforcer
from aura_edge.intelligence.embedder import LocalEmbedder

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Construct the AURA EDGE FastAPI application with strict config & policy wiring."""
    cfg = settings or get_settings()
    apply_offline_env_guards(cfg)

    enforcer = NetworkPolicyEnforcer.from_settings(cfg)
    engine = create_db_engine(cfg.database_url)
    ws_hub = WebSocketEventHub()
    embedder = LocalEmbedder(cfg, enforcer)
    notification_service = NotificationService(engine, ws_hub, cfg, clock=clock)
    smart_home = SmartHomeService(engine, ws_hub, notification_service=notification_service)
    memory_service = MemoryService(engine, ws_hub, embedder=embedder, smart_home=smart_home)
    scheduler_service = SchedulerService(
        engine, smart_home, notification_service, ws_hub, cfg, clock=clock
    )
    orchestrator = VoiceOrchestrator(
        cfg,
        enforcer,
        engine,
        ws_hub,
        smart_home,
        memory_service=memory_service,
        scheduler_service=scheduler_service,
        notification_service=notification_service,
        clock=clock,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await smart_home.startup()
        try:
            await notification_service.startup()
        except (SQLAlchemyError, OSError, RuntimeError) as exc:
            logger.warning("Notification startup skipped (DB unavailable): %s", exc)
        try:
            await scheduler_service.startup()
        except (SQLAlchemyError, OSError, RuntimeError) as exc:
            logger.warning("Scheduler startup skipped (DB unavailable): %s", exc)
        await orchestrator.startup()
        try:
            yield
        finally:
            await orchestrator.shutdown()
            await scheduler_service.shutdown()
            await notification_service.shutdown()
            await smart_home.shutdown()

    app = FastAPI(
        title="AURA EDGE",
        version=__version__,
        description="Offline-first multilingual personal AI assistant (Phase 0-3).",
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
    app.state.embedder = embedder
    app.state.notification_service = notification_service
    app.state.smart_home = smart_home
    app.state.memory_service = memory_service
    app.state.scheduler_service = scheduler_service
    app.state.orchestrator = orchestrator

    app.include_router(health_router)
    app.include_router(voice_router)
    app.include_router(timers_router)
    app.include_router(smart_home_router)
    app.include_router(memory_router)
    app.include_router(scheduler_router)
    app.include_router(notifications_router)
    app.include_router(ws_router)
    return app


app = create_app()
