"""Tests for Settings defaults, strict bind-host rules, and PostgreSQL enforcement."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aura_edge.config import ConnectivityMode, Settings
from aura_edge.data.database import create_db_engine


def test_default_settings_enforce_strict_offline_and_postgres() -> None:
    settings = Settings(_env_file=None)
    assert settings.connectivity_mode == ConnectivityMode.STRICT_OFFLINE
    assert settings.bind_host == "127.0.0.1"
    assert settings.database_url.startswith("postgresql")


def test_sqlite_database_url_is_rejected() -> None:
    with pytest.raises(ValidationError, match="PostgreSQL"):
        Settings(_env_file=None, database_url="sqlite:///./test.db")

    with pytest.raises(ValueError, match="PostgreSQL"):
        create_db_engine("sqlite+pysqlite:///:memory:")


def test_strict_offline_rejects_non_loopback_bind_host() -> None:
    with pytest.raises(ValidationError, match="strict_offline"):
        Settings(
            _env_file=None,
            connectivity_mode=ConnectivityMode.STRICT_OFFLINE,
            bind_host="0.0.0.0",
        )


def test_lan_only_permits_non_loopback_bind_host() -> None:
    settings = Settings(
        _env_file=None,
        connectivity_mode=ConnectivityMode.LAN_ONLY,
        bind_host="0.0.0.0",
    )
    assert settings.bind_host == "0.0.0.0"


def test_csv_env_parsing_handles_empty_and_multiple_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AURA_EDGE_ONLINE_ALLOWED_HOSTS", "")
    monkeypatch.setenv("AURA_EDGE_CORS_ORIGINS", "http://127.0.0.1:5173, http://localhost:5173")
    settings = Settings(_env_file=None)
    assert settings.online_allowed_hosts == []
    assert settings.cors_origins == ["http://127.0.0.1:5173", "http://localhost:5173"]
