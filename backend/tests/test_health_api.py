"""API integration tests for Phase 0 health, readiness, and policy endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aura_edge.config import ComputeMode, ConnectivityMode, Settings
from aura_edge.data.database import DatabaseReadiness
from aura_edge.infrastructure.hardware import GpuInfo, HardwareSnapshot, OllamaInfo
from aura_edge.main import create_app


def _sample_hardware() -> HardwareSnapshot:
    return HardwareSnapshot(
        os="Windows 11",
        architecture="AMD64",
        python_version="3.13.2",
        compute_mode_configured=ComputeMode.AUTO,
        effective_compute_backend="cpu",
        cpu_cores_physical=14,
        cpu_cores_logical=20,
        cpu_percent=5.0,
        ram_total_gb=15.78,
        ram_used_gb=7.5,
        ram_percent=47.5,
        gpu=GpuInfo(available=False, error="test stub"),
        ollama=OllamaInfo(url="http://127.0.0.1:11434", reachable=False, models=[]),
    )


def test_health_and_policy_endpoints() -> None:
    app = create_app(Settings(_env_file=None))
    client = TestClient(app)

    health = client.get("/api/v1/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["service"] == "aura-edge"
    assert body["phase"] == 0
    assert body["connectivity_mode"] == ConnectivityMode.STRICT_OFFLINE.value

    policy = client.get("/api/v1/policy")
    assert policy.status_code == 200
    assert policy.json()["external_requests_blocked"] is True

    denied = client.post("/api/v1/policy/check", json={"url": "https://example.com"})
    assert denied.status_code == 200
    assert denied.json()["allowed"] is False

    allowed = client.post("/api/v1/policy/check", json={"url": "http://127.0.0.1:11434/api/tags"})
    assert allowed.status_code == 200
    assert allowed.json()["allowed"] is True


def test_readiness_returns_200_when_postgres_and_pgvector_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aura_edge.api.routes_health.check_database_readiness",
        lambda _engine: DatabaseReadiness(
            status="ok",
            connected=True,
            postgres_version="PostgreSQL 16.4",
            pgvector_installed=True,
            pgvector_version="0.8.0",
            alembic_revision="0001_phase0_foundation",
            error=None,
        ),
    )
    monkeypatch.setattr(
        "aura_edge.api.routes_health.collect_hardware_snapshot",
        lambda _s, _e: _sample_hardware(),
    )

    client = TestClient(create_app(Settings(_env_file=None)))
    resp = client.get("/api/v1/readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is True
    assert data["database"]["pgvector_installed"] is True
    assert data["database"]["alembic_revision"] == "0001_phase0_foundation"


def test_readiness_returns_503_when_postgres_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aura_edge.api.routes_health.check_database_readiness",
        lambda _engine: DatabaseReadiness(
            status="unavailable",
            connected=False,
            error="connection refused on 127.0.0.1:5432",
        ),
    )
    monkeypatch.setattr(
        "aura_edge.api.routes_health.collect_hardware_snapshot",
        lambda _s, _e: _sample_hardware(),
    )

    client = TestClient(create_app(Settings(_env_file=None)))
    resp = client.get("/api/v1/readiness")
    assert resp.status_code == 503
    data = resp.json()
    assert data["ready"] is False
    assert data["database"]["status"] == "unavailable"
    assert "connection refused" in data["database"]["error"]
