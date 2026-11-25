"""Real hardware and local runtime inspection (no invented metrics)."""

from __future__ import annotations

import logging
import platform

import httpx
import psutil
from pydantic import BaseModel

from aura_edge.config import ComputeMode, Settings
from aura_edge.infrastructure.network_policy import (
    NetworkPolicyEnforcer,
    NetworkPolicyViolationError,
    build_http_client,
)

logger = logging.getLogger(__name__)


class GpuInfo(BaseModel):
    """NVIDIA GPU telemetry reported via NVML when available."""

    available: bool
    name: str | None = None
    vram_total_mb: int | None = None
    vram_used_mb: int | None = None
    driver_version: str | None = None
    error: str | None = None


class OllamaInfo(BaseModel):
    """Local Ollama server status checked strictly through NetworkPolicyEnforcer."""

    url: str
    reachable: bool
    models: list[str] = []
    error: str | None = None


class HardwareSnapshot(BaseModel):
    """Measured host hardware and runtime capability snapshot."""

    os: str
    architecture: str
    python_version: str
    compute_mode_configured: ComputeMode
    effective_compute_backend: str
    cpu_cores_physical: int | None
    cpu_cores_logical: int | None
    cpu_percent: float
    ram_total_gb: float
    ram_used_gb: float
    ram_percent: float
    gpu: GpuInfo
    ollama: OllamaInfo


def _probe_nvml_gpu() -> GpuInfo:
    try:
        import pynvml  # type: ignore[import-untyped]
    except ImportError as exc:
        return GpuInfo(available=False, error=f"pynvml unavailable: {exc}")

    initialized = False
    try:
        pynvml.nvmlInit()
        initialized = True
        count = pynvml.nvmlDeviceGetCount()
        if count < 1:
            return GpuInfo(available=False, error="No NVIDIA GPU detected by NVML.")
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        raw_name = pynvml.nvmlDeviceGetName(handle)
        name = raw_name.decode("utf-8", errors="replace") if isinstance(raw_name, bytes) else str(raw_name)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        raw_drv = pynvml.nvmlSystemGetDriverVersion()
        driver = raw_drv.decode("utf-8", errors="replace") if isinstance(raw_drv, bytes) else str(raw_drv)
        return GpuInfo(
            available=True,
            name=name,
            vram_total_mb=round(mem.total / (1024 * 1024)),
            vram_used_mb=round(mem.used / (1024 * 1024)),
            driver_version=driver,
        )
    except (pynvml.NVMLError, OSError) as exc:
        return GpuInfo(available=False, error=f"NVML probe failed: {exc}")
    finally:
        if initialized:
            try:
                pynvml.nvmlShutdown()
            except pynvml.NVMLError as exc:
                logger.debug("Ignoring NVML shutdown error: %s", exc)


def _probe_ollama(settings: Settings, enforcer: NetworkPolicyEnforcer) -> OllamaInfo:
    base_url = settings.ollama_url.rstrip("/")
    tags_url = f"{base_url}/api/tags"
    try:
        with build_http_client(enforcer, timeout=0.5) as client:
            resp = client.get(tags_url)
            resp.raise_for_status()
            payload = resp.json()
            raw_models = payload.get("models", []) if isinstance(payload, dict) else []
            names = [str(m.get("name")) for m in raw_models if isinstance(m, dict) and m.get("name")]
            return OllamaInfo(url=base_url, reachable=True, models=names)
    except NetworkPolicyViolationError as exc:
        return OllamaInfo(url=base_url, reachable=False, error=str(exc))
    except (httpx.HTTPError, ValueError, OSError) as exc:
        return OllamaInfo(url=base_url, reachable=False, error=str(exc))


def collect_hardware_snapshot(
    settings: Settings,
    enforcer: NetworkPolicyEnforcer,
) -> HardwareSnapshot:
    """Collect real OS, CPU, RAM, GPU, and loopback Ollama status."""
    mem = psutil.virtual_memory()
    gpu = _probe_nvml_gpu()
    ollama = _probe_ollama(settings, enforcer)

    if settings.compute_mode == ComputeMode.CPU:
        effective = "cpu"
    elif gpu.available:
        effective = "cuda"
    else:
        effective = "cpu_fallback" if settings.compute_mode == ComputeMode.GPU else "cpu"

    return HardwareSnapshot(
        os=f"{platform.system()} {platform.release()}",
        architecture=platform.machine(),
        python_version=platform.python_version(),
        compute_mode_configured=settings.compute_mode,
        effective_compute_backend=effective,
        cpu_cores_physical=psutil.cpu_count(logical=False),
        cpu_cores_logical=psutil.cpu_count(logical=True),
        cpu_percent=round(float(psutil.cpu_percent(interval=None)), 1),
        ram_total_gb=round(mem.total / (1024**3), 2),
        ram_used_gb=round(mem.used / (1024**3), 2),
        ram_percent=round(float(mem.percent), 1),
        gpu=gpu,
        ollama=ollama,
    )
