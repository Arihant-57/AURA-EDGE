"""Application configuration and strict-offline / database boundary guards."""

from __future__ import annotations

import ipaddress
import os
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_MODELS_DIR = _DEFAULT_REPO_ROOT / "models"


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

    # Phase 1 Voice, Speech & Local Intelligence Settings
    models_dir: Path = _DEFAULT_MODELS_DIR
    stt_model_size: Literal["base", "small"] = "small"
    ollama_model: str = "qwen3.5:4b"
    ollama_num_ctx: int = 4096
    ollama_timeout_sec: float = 30.0
    default_language: Literal["auto", "en", "hi", "kn"] = "auto"

    audio_sample_rate: int = 16000
    audio_frame_samples: int = 512
    audio_preroll_sec: float = 3.0
    audio_input_device: int | str | None = None
    audio_output_device: int | str | None = None
    vad_threshold: float = 0.45
    vad_initial_timeout_sec: float = 3.0
    vad_trailing_silence_sec: float = 1.2
    audio_post_playback_cooldown_sec: float = 0.65

    tts_backend: str = "sherpa_onnx"
    tts_cache_max_entries: int = 64
    tts_default_speed: float = 1.0
    cuda_dll_dirs: Annotated[list[str], NoDecode] = []

    @field_validator("audio_post_playback_cooldown_sec")
    @classmethod
    def _non_negative_cooldown(cls, value: float) -> float:
        return max(0.0, float(value))

    @field_validator("tts_default_speed")
    @classmethod
    def _clamp_tts_speed(cls, value: float) -> float:
        return max(0.5, min(2.0, float(value)))

    @field_validator(
        "lan_allowed_cidrs",
        "online_allowed_hosts",
        "cors_origins",
        "cuda_dll_dirs",
        mode="before",
    )
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


def apply_offline_env_guards(settings: Settings) -> None:
    """Disable runtime Hugging Face / Transformers network downloads in offline/LAN modes."""
    if settings.connectivity_mode != ConnectivityMode.ONLINE_OPT_IN:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"


def get_settings() -> Settings:
    """Load fresh settings from environment / .env."""
    return Settings()
