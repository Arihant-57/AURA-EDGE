"""Application configuration and strict-offline / database boundary guards."""

from __future__ import annotations

import ipaddress
from enum import Enum
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class ConnectivityMode(str, Enum):
    """Supported connectivity policies enforced centrally by NetworkPolicyEnforcer."""

    STRICT_OFFLINE = "strict_offline"
    LAN_ONLY = "lan_only"
    ONLINE_OPT_IN = "online_opt_in"


class ComputeMode(str, Enum):
    """Compute placement preference."""

    AUTO = "auto"
    CPU = "cpu"
    GPU = "gpu"


def is_loopback_host(host: str) -> bool:
    """Return True if *host* represents a local loopback address or hostname."""
    cleaned = host.strip().lower().strip("[]")
    if not cleaned:
        return False
    if cleaned == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(cleaned)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_loopback


def _parse_csv_list(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not value or not value.strip():
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


class Settings(BaseSettings):
    """Centralized configuration for AURA EDGE (Phase 0)."""

    model_config = SettingsConfigDict(
        env_prefix="AURA_EDGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    connectivity_mode: ConnectivityMode = ConnectivityMode.STRICT_OFFLINE
    compute_mode: ComputeMode = ComputeMode.AUTO
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000

    database_url: str = "postgresql+psycopg://aura_edge:aura_edge_dev@127.0.0.1:5432/aura_edge"
    ollama_url: str = "http://127.0.0.1:11434"

    lan_allowed_cidrs: Annotated[list[str], NoDecode] = [
        "192.168.0.0/16",
        "10.0.0.0/8",
        "172.16.0.0/12",
    ]
    online_allowed_hosts: Annotated[list[str], NoDecode] = []
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]

    @field_validator("lan_allowed_cidrs", "online_allowed_hosts", "cors_origins", mode="before")
    @classmethod
    def _coerce_csv_list(cls, value: str | list[str]) -> list[str]:
        return _parse_csv_list(value)

    @field_validator("lan_allowed_cidrs")
    @classmethod
    def _validate_lan_cidrs(cls, cidrs: list[str]) -> list[str]:
        for cidr in cidrs:
            ipaddress.ip_network(cidr, strict=False)
        return cidrs

    @field_validator("database_url")
    @classmethod
    def _require_postgresql_url(cls, url: str) -> str:
        normalized = url.strip().lower()
        if not normalized.startswith("postgresql"):
            raise ValueError(
                "AURA_EDGE_DATABASE_URL must use PostgreSQL (postgresql+psycopg://...); "
                "SQLite or alternative backends are disallowed without explicit approval."
            )
        return url.strip()

    @model_validator(mode="after")
    def _enforce_strict_offline_bind_host(self) -> Settings:
        if (
            self.connectivity_mode == ConnectivityMode.STRICT_OFFLINE
            and not is_loopback_host(self.bind_host)
        ):
            raise ValueError(
                f"bind_host '{self.bind_host}' is not allowed in strict_offline mode; "
                "only loopback addresses (127.0.0.1, ::1, localhost) are permitted."
            )
        return self


def get_settings() -> Settings:
    """Load fresh settings from environment / .env."""
    return Settings()
