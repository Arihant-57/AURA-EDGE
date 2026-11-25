"""Offline multilingual Faster-Whisper STT with Windows CUDA DLL guard & CPU fallback."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel

from aura_edge.config import Settings, apply_offline_env_guards
from aura_edge.infrastructure.compute import ComputeManager
from aura_edge.intelligence.schemas import LanguageCode, RequestedLanguage

logger = logging.getLogger(__name__)

_SUPPORTED_WHISPER_LANGS = {"en", "hi", "kn"}


class ModelNotFoundError(FileNotFoundError):
    """Raised when local offline model weights are missing."""


class TranscriptionResult(BaseModel):
    """Structured offline speech transcription result."""

    text: str
    detected_language: LanguageCode
    language_probability: float
    model_size: str
    device: Literal["cpu", "cuda"]
    compute_type: str
    latency_ms: float
    is_empty: bool = False
    fallback_reason: str | None = None


class WhisperTranscriber:
    """Thread-safe offline Faster-Whisper wrapper enforcing ``local_files_only=True``."""

    def __init__(self, settings: Settings, compute_manager: ComputeManager) -> None:
        self._settings = settings
        self._compute_manager = compute_manager
        self._lock = threading.Lock()
        self._model: Any = None
        self._loaded_key: tuple[str, str, str] | None = None
        apply_offline_env_guards(settings)

    def resolve_model_dir(self, model_size: str | None = None) -> Path:
        size = model_size or self._settings.stt_model_size
        model_dir = self._settings.models_dir / "stt" / f"faster-whisper-{size}"
        if not model_dir.is_dir() or not (model_dir / "model.bin").is_file():
            raise ModelNotFoundError(
                f"Local Faster-Whisper '{size}' model not found at '{model_dir}'. "
                "Run backend/scripts/prepare_models.py before offline execution."
            )
        return model_dir

    def _load_model_locked(self, model_size: str | None = None) -> tuple[Any, str, str, str | None]:
        size = model_size or self._settings.stt_model_size
        model_dir = self.resolve_model_dir(size)
        placement = self._compute_manager.resolve_placement()
        target_key = (str(model_dir), placement.stt_device, placement.stt_compute_type)

        if self._model is not None and self._loaded_key == target_key:
            return self._model, placement.stt_device, placement.stt_compute_type, placement.fallback_reason

        from faster_whisper import WhisperModel

        try:
            self._model = WhisperModel(
                str(model_dir),
                device=placement.stt_device,
                compute_type=placement.stt_compute_type,
                local_files_only=True,
            )
            self._loaded_key = target_key
            return self._model, placement.stt_device, placement.stt_compute_type, placement.fallback_reason
        except Exception as exc:
            if placement.stt_device == "cuda":
                reason = f"CUDA Whisper init failed ({exc}); fell back to CPU int8."
                logger.warning(reason)
                fallback = self._compute_manager.record_runtime_fallback(reason)
                self._model = WhisperModel(
                    str(model_dir),
                    device="cpu",
                    compute_type="int8",
                    local_files_only=True,
                )
                self._loaded_key = (str(model_dir), "cpu", "int8")
                return self._model, "cpu", "int8", fallback.fallback_reason
            raise

    def transcribe(
        self,
        audio_float32: np.ndarray,
        *,
        language: RequestedLanguage = "auto",
        model_size: str | None = None,
        fallback_language: LanguageCode = "en",
    ) -> TranscriptionResult:
        """Transcribe 16 kHz mono float32 audio offline without network access."""
        size = model_size or self._settings.stt_model_size
        if audio_float32.size == 0 or float(np.max(np.abs(audio_float32))) < 1e-4:
            placement = self._compute_manager.resolve_placement()
            return TranscriptionResult(
                text="",
                detected_language=(fallback_language if language == "auto" else language),
                language_probability=0.0,
                model_size=size,
                device=placement.stt_device,
                compute_type=placement.stt_compute_type,
                latency_ms=0.0,
                is_empty=True,
                fallback_reason=placement.fallback_reason,
            )

        samples = audio_float32.astype(np.float32, copy=False).reshape(-1)
        peak = float(np.max(np.abs(samples)))
        if peak > 1.0:
            samples = samples / peak

        lang_arg: str | None = None if language == "auto" else language

        with self._lock:
            model, used_device, used_ctype, fb_reason = self._load_model_locked(size)
            t0 = time.perf_counter()
            try:
                segments, info = model.transcribe(
                    samples,
                    language=lang_arg,
                    beam_size=1,
                    vad_filter=False,
                    condition_on_previous_text=False,
                    repetition_penalty=1.2,
                    no_repeat_ngram_size=3,
                    max_new_tokens=128,
                )
                text = " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()
            except Exception as exc:
                if used_device == "cuda":
                    reason = f"CUDA Whisper transcribe failed ({exc}); retrying on CPU int8."
                    logger.warning(reason)
                    self._compute_manager.record_runtime_fallback(reason)
                    self._model = None
                    self._loaded_key = None
                    model, used_device, used_ctype, fb_reason = self._load_model_locked(size)
                    t0 = time.perf_counter()
                    segments, info = model.transcribe(
                        samples,
                        language=lang_arg,
                        beam_size=1,
                        vad_filter=False,
                        condition_on_previous_text=False,
                        repetition_penalty=1.2,
                        no_repeat_ngram_size=3,
                        max_new_tokens=128,
                    )
                    text = " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()
                else:
                    raise
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        raw_lang = getattr(info, "language", None) or fallback_language
        lang_prob = float(getattr(info, "language_probability", 0.0) or 0.0)
        resolved_lang: LanguageCode
        if language != "auto":
            resolved_lang = language
        elif raw_lang in _SUPPORTED_WHISPER_LANGS:
            resolved_lang = raw_lang  # type: ignore[assignment]
        else:
            resolved_lang = fallback_language

        return TranscriptionResult(
            text=text,
            detected_language=resolved_lang,
            language_probability=round(lang_prob, 4),
            model_size=size,
            device=used_device,  # type: ignore[arg-type]
            compute_type=used_ctype,
            latency_ms=latency_ms,
            is_empty=(len(text) == 0),
            fallback_reason=fb_reason,
        )
