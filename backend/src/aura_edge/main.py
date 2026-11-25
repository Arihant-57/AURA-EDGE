"""FastAPI application entrypoint for AURA EDGE (Phase 0-4 modular monolith)."""

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
from aura_edge.api.routes_personal import router as personal_router
from aura_edge.api.routes_scheduler import router as scheduler_router
from aura_edge.api.routes_smart_home import router as smart_home_router
from aura_edge.api.routes_timers import router as timers_router
from aura_edge.api.routes_voice import router as voice_router
from aura_edge.api.ws_events import WebSocketEventHub, ws_router
from aura_edge.application.memory_service import MemoryService
from aura_edge.application.notification_service import NotificationService
from aura_edge.application.personal import (
    BriefingService,
    CalendarService,
    ExpenseService,
    GroceryService,
    ParcelService,
    TaskService,
)
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
    task_service = TaskService(engine, ws_hub, scheduler_service=scheduler_service, clock=clock)
    calendar_service = CalendarService(
        engine,
        ws_hub,
        scheduler_service=scheduler_service,
        default_timezone=cfg.default_timezone,
        clock=clock,
    )
    grocery_service = GroceryService(engine, ws_hub, clock=clock)
    expense_service = ExpenseService(
        engine, ws_hub, default_timezone=cfg.default_timezone, clock=clock
    )
    parcel_service = ParcelService(
        engine,
        ws_hub,
        scheduler_service=scheduler_service,
        default_timezone=cfg.default_timezone,
        clock=clock,
    )
    briefing_service = BriefingService(
        task_service=task_service,
        calendar_service=calendar_service,
        scheduler_service=scheduler_service,
        notification_service=notification_service,
        grocery_service=grocery_service,
        parcel_service=parcel_service,
        default_timezone=cfg.default_timezone,
        clock=clock,
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
        task_service=task_service,
        calendar_service=calendar_service,
        briefing_service=briefing_service,
        grocery_service=grocery_service,
        expense_service=expense_service,
        parcel_service=parcel_service,
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
        try:
            grocery_service.ensure_seeded()
        except (SQLAlchemyError, OSError, RuntimeError) as exc:
            logger.warning("Recipe seed skipped (DB unavailable): %s", exc)
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
        description="Offline-first multilingual personal AI assistant (Phase 0-4).",
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
    app.state.task_service = task_service
    app.state.calendar_service = calendar_service
    app.state.briefing_service = briefing_service
    app.state.grocery_service = grocery_service
    app.state.expense_service = expense_service
    app.state.parcel_service = parcel_service
    app.state.orchestrator = orchestrator

    app.include_router(health_router)
    app.include_router(voice_router)
    app.include_router(timers_router)
    app.include_router(smart_home_router)
    app.include_router(memory_router)
    app.include_router(scheduler_router)
    app.include_router(notifications_router)
    app.include_router(personal_router)
    app.include_router(ws_router)
    return app


app = create_app()
