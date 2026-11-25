"""Compute placement management (CPU / GPU / Auto) and Windows CUDA DLL guard."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from aura_edge.config import ComputeMode, Settings

logger = logging.getLogger(__name__)

# Windows os.add_dll_directory() returns handles that remove the search path if GC'd.
_DLL_DIR_HANDLES: list[Any] = []
_REGISTERED_DLL_DIRS: set[str] = set()


def register_windows_cuda_dlls(extra_dirs: list[str] | None = None) -> list[str]:
    """Register CUDA/cuBLAS/cuDNN DLL directories on Windows and keep handles alive.

    Searches explicit ``extra_dirs``, ``.venv`` NVIDIA/torch lib dirs, and ``sys.base_prefix``
    site-packages ``torch/lib`` (where ``cublas64_12.dll`` & ``cudnn64_9.dll`` commonly reside
    when global PyTorch + cu128 is installed on the host).
    """
    if sys.platform != "win32":
        return []

    candidates: list[Path] = []
    if extra_dirs:
        candidates.extend(Path(d) for d in extra_dirs if d.strip())

    env_dirs = os.environ.get("AURA_EDGE_CUDA_DLL_DIRS", "")
    if env_dirs.strip():
        candidates.extend(Path(p.strip()) for p in env_dirs.split(os.pathsep) if p.strip())

    # Search active venv and base Python installation site-packages
    roots = [
        Path(sys.prefix) / "Lib" / "site-packages",
        Path(sys.base_prefix) / "Lib" / "site-packages",
    ]
    for root in roots:
        candidates.append(root / "torch" / "lib")
        candidates.append(root / "nvidia" / "cublas" / "bin")
        candidates.append(root / "nvidia" / "cudnn" / "bin")
        candidates.append(root / "nvidia" / "cuda_runtime" / "bin")

    registered: list[str] = []
    current_path = os.environ.get("PATH", "")
    path_parts = current_path.split(os.pathsep) if current_path else []

    for candidate in candidates:
        resolved = str(candidate.resolve()) if candidate.exists() else ""
        if not resolved or not candidate.is_dir():
            continue
        if resolved in _REGISTERED_DLL_DIRS:
            registered.append(resolved)
            continue
        try:
            if hasattr(os, "add_dll_directory"):
                handle = os.add_dll_directory(resolved)
                _DLL_DIR_HANDLES.append(handle)
            if resolved not in path_parts:
                os.environ["PATH"] = resolved + os.pathsep + os.environ.get("PATH", "")
                path_parts.insert(0, resolved)
            _REGISTERED_DLL_DIRS.add(resolved)
            registered.append(resolved)
        except OSError as exc:
            logger.debug("Failed to add CUDA DLL directory '%s': %s", resolved, exc)

    return registered


def detect_cuda_availability(extra_dll_dirs: list[str] | None = None) -> tuple[bool, str | None]:
    """Return ``(cuda_usable, error_reason)`` for CTranslate2 / Faster-Whisper."""
    register_windows_cuda_dlls(extra_dll_dirs)
    try:
        import ctranslate2  # type: ignore[import-untyped]
    except ImportError as exc:
        return False, f"ctranslate2 unavailable: {exc}"

    try:
        count = int(ctranslate2.get_cuda_device_count())
    except (RuntimeError, OSError, ValueError) as exc:
        return False, f"ctranslate2 CUDA probe failed: {exc}"

    if count < 1:
        return False, "No CUDA device detected by ctranslate2."

    # Verify cublas64_12.dll can actually be found on Windows if present
    if sys.platform == "win32":
        has_cublas = any(
            (Path(d) / "cublas64_12.dll").is_file() for d in _REGISTERED_DLL_DIRS
        )
        if not has_cublas:
            # Also check PATH directly
            for p in os.environ.get("PATH", "").split(os.pathsep):
                if p and (Path(p) / "cublas64_12.dll").is_file():
                    has_cublas = True
                    break
        if not has_cublas:
            return False, "cublas64_12.dll not found in registered CUDA DLL directories or PATH."

    return True, None


class ComputePlacement(BaseModel):
    """Effective compute placement across STT, LLM, VAD, and TTS components."""

    requested_mode: ComputeMode
    effective_mode: Literal["cpu", "gpu"]
    stt_device: Literal["cpu", "cuda"]
    stt_compute_type: Literal["int8", "float16", "int8_float16"]
    llm_device: Literal["cpu", "gpu"]
    llm_num_gpu: int | None  # 0 forces CPU-only in Ollama; None lets Ollama schedule GPU
    vad_device: Literal["cpu"] = "cpu"
    tts_device: Literal["cpu"] = "cpu"
    cuda_dll_dirs_registered: list[str] = []
    fallback_reason: str | None = None


class ComputeManager:
    """Resolves and tracks effective CPU vs GPU placement without silent misrepresentation."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._requested_mode: ComputeMode = settings.compute_mode
        self._last_placement: ComputePlacement | None = None

    @property
    def requested_mode(self) -> ComputeMode:
        return self._requested_mode

    def set_mode(self, mode: ComputeMode) -> ComputePlacement:
        self._requested_mode = mode
        return self.resolve_placement(force_refresh=True)

    def resolve_placement(self, *, force_refresh: bool = False) -> ComputePlacement:
        if self._last_placement is not None and not force_refresh:
            return self._last_placement

        if self._requested_mode == ComputeMode.CPU:
            placement = ComputePlacement(
                requested_mode=ComputeMode.CPU,
                effective_mode="cpu",
                stt_device="cpu",
                stt_compute_type="int8",
                llm_device="cpu",
                llm_num_gpu=0,
                vad_device="cpu",
                tts_device="cpu",
                cuda_dll_dirs_registered=[],
                fallback_reason=None,
            )
            self._last_placement = placement
            return placement

        cuda_ok, cuda_err = detect_cuda_availability(self._settings.cuda_dll_dirs)
        if cuda_ok:
            placement = ComputePlacement(
                requested_mode=self._requested_mode,
                effective_mode="gpu",
                stt_device="cuda",
                stt_compute_type="int8_float16",
                llm_device="gpu",
                llm_num_gpu=None,
                vad_device="cpu",
                tts_device="cpu",
                cuda_dll_dirs_registered=list(_REGISTERED_DLL_DIRS),
                fallback_reason=None,
            )
        else:
            reason = cuda_err or "CUDA unavailable; falling back to CPU."
            placement = ComputePlacement(
                requested_mode=self._requested_mode,
                effective_mode="cpu",
                stt_device="cpu",
                stt_compute_type="int8",
                llm_device="cpu",
                llm_num_gpu=0,
                vad_device="cpu",
                tts_device="cpu",
                cuda_dll_dirs_registered=list(_REGISTERED_DLL_DIRS),
                fallback_reason=reason,
            )

        self._last_placement = placement
        return placement

    def record_runtime_fallback(self, reason: str) -> ComputePlacement:
        """Downgrade effective placement to CPU when CUDA initialization fails at runtime."""
        placement = ComputePlacement(
            requested_mode=self._requested_mode,
            effective_mode="cpu",
            stt_device="cpu",
            stt_compute_type="int8",
            llm_device="cpu",
            llm_num_gpu=0,
            vad_device="cpu",
            tts_device="cpu",
            cuda_dll_dirs_registered=list(_REGISTERED_DLL_DIRS),
            fallback_reason=reason,
        )
        self._last_placement = placement
        return placement
