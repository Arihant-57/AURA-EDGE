"""Health, readiness, hardware telemetry, and network policy endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aura_edge import __version__
from aura_edge.config import ComputeMode, ConnectivityMode, Settings
from aura_edge.data.database import DatabaseReadiness, check_database_readiness
from aura_edge.data.models import ConnectivityAuditEvent
from aura_edge.infrastructure.hardware import HardwareSnapshot, collect_hardware_snapshot
from aura_edge.infrastructure.network_policy import NetworkPolicyEnforcer, PolicyDecision

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["system"])


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    phase: int
    connectivity_mode: ConnectivityMode
    compute_mode: ComputeMode
    bind_host: str
    timestamp_utc: str


class PolicySummaryResponse(BaseModel):
    mode: ConnectivityMode
    external_requests_blocked: bool
    lan_allowed_cidrs: list[str]
    online_allowed_hosts: list[str]


class PolicyCheckRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=1024)


class ReadinessResponse(BaseModel):
    ready: bool
    phase: int
    service: str
    version: str
    connectivity: PolicySummaryResponse
    database: DatabaseReadiness
    hardware: HardwareSnapshot
    timestamp_utc: str


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _policy_summary(settings: Settings) -> PolicySummaryResponse:
    blocked = (
        settings.connectivity_mode != ConnectivityMode.ONLINE_OPT_IN
        or len(settings.online_allowed_hosts) == 0
    )
    return PolicySummaryResponse(
        mode=settings.connectivity_mode,
        external_requests_blocked=blocked,
        lan_allowed_cidrs=settings.lan_allowed_cidrs,
        online_allowed_hosts=settings.online_allowed_hosts,
    )


@router.get("/health", response_model=HealthResponse)
def get_health(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    return HealthResponse(
        status="ok",
        service="aura-edge",
        version=__version__,
        phase=0,
        connectivity_mode=settings.connectivity_mode,
        compute_mode=settings.compute_mode,
        bind_host=settings.bind_host,
        timestamp_utc=_utc_iso(),
    )


@router.get(
    "/readiness",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
def get_readiness(request: Request) -> JSONResponse | ReadinessResponse:
    settings: Settings = request.app.state.settings
    enforcer: NetworkPolicyEnforcer = request.app.state.enforcer
    engine = request.app.state.engine

    db_status = check_database_readiness(engine)
    hw = collect_hardware_snapshot(settings, enforcer)
    ready = db_status.connected and db_status.pgvector_installed and bool(db_status.alembic_revision)

    payload = ReadinessResponse(
        ready=ready,
        phase=0,
        service="aura-edge",
        version=__version__,
        connectivity=_policy_summary(settings),
        database=db_status,
        hardware=hw,
        timestamp_utc=_utc_iso(),
    )
    if not ready:
        return JSONResponse(status_code=503, content=payload.model_dump(mode="json"))
    return payload


@router.get("/policy", response_model=PolicySummaryResponse)
def get_policy(request: Request) -> PolicySummaryResponse:
    settings: Settings = request.app.state.settings
    return _policy_summary(settings)


@router.post("/policy/check", response_model=PolicyDecision)
def check_policy_url(body: PolicyCheckRequest, request: Request) -> PolicyDecision:
    enforcer: NetworkPolicyEnforcer = request.app.state.enforcer
    decision = enforcer.evaluate_url(body.url)

    # Best-effort audit record if PostgreSQL is connected and migrated; never mask decision.
    try:
        engine = request.app.state.engine
        with Session(engine) as session:
            session.add(
                ConnectivityAuditEvent(
                    mode=decision.mode.value,
                    target=decision.target[:512],
                    allowed=decision.allowed,
                    reason=decision.reason[:256],
                )
            )
            session.commit()
    except SQLAlchemyError as exc:
        logger.debug("Skipping connectivity audit DB write (DB unavailable): %s", exc)

    return decision
