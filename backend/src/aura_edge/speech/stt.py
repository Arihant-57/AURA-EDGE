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
_DOMAIN_INITIAL_PROMPTS: dict[str, str] = {
    "en": "time, date, timer, cancel, pause, resume, remaining, minutes, seconds, hours.",
    "hi": "समय, तारीख, मिनट, सेकंड, घंटे, चाय, टाइमर लगाओ, कितने टाइमर चल रहे हैं, सभी टाइमर रद्द करो।",
}


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
    """Thread-safe offline STT wrapper (Faster-Whisper + IndicConformer KN specialist)."""

    def __init__(self, settings: Settings, compute_manager: ComputeManager) -> None:
        self._settings = settings
        self._compute_manager = compute_manager
        self._lock = threading.Lock()
        self._model: Any = None
        self._loaded_key: tuple[str, str, str] | None = None
        self._kn_recognizer: Any = None
        apply_offline_env_guards(settings)

    def resolve_kannada_specialist_dir(self) -> Path | None:
        """Return prepared offline IndicConformer Kannada directory if available."""
        kn_dir = self._settings.models_dir / "stt" / "indic-conformer-kn"
        if (kn_dir / "model.int8.onnx").is_file() and (kn_dir / "tokens.txt").is_file():
            return kn_dir
        return None

    def _load_kn_recognizer_locked(self, kn_dir: Path) -> Any:
        if self._kn_recognizer is not None:
            return self._kn_recognizer
        import sherpa_onnx

        self._kn_recognizer = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
            model=str(kn_dir / "model.int8.onnx"),
            tokens=str(kn_dir / "tokens.txt"),
            num_threads=4,
            provider="cpu",
        )
        return self._kn_recognizer

    def _transcribe_kn_specialist_locked(
        self,
        samples: np.ndarray,
        kn_dir: Path,
    ) -> tuple[str, float]:
        rec = self._load_kn_recognizer_locked(kn_dir)
        t0 = time.perf_counter()
        stream = rec.create_stream()
        stream.accept_waveform(16000, samples)
        rec.decode_stream(stream)
        text = str(getattr(stream.result, "text", "") or "").strip()
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return text, latency_ms

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
        prompt_arg: str | None = _DOMAIN_INITIAL_PROMPTS.get(language) if language != "auto" else None

        if language == "kn" and size != "base":
            kn_dir = self.resolve_kannada_specialist_dir()
            if kn_dir is not None:
                with self._lock:
                    kn_text, kn_ms = self._transcribe_kn_specialist_locked(samples, kn_dir)
                return TranscriptionResult(
                    text=kn_text,
                    detected_language="kn",
                    language_probability=1.0,
                    model_size="indic-conformer-kn",
                    device="cpu",
                    compute_type="int8",
                    latency_ms=kn_ms,
                    is_empty=(len(kn_text) == 0),
                    fallback_reason=None,
                )

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
                    initial_prompt=prompt_arg,
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
                        initial_prompt=prompt_arg,
                    )
                    text = " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()
                else:
                    raise
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

            raw_lang = getattr(info, "language", None) or fallback_language
            lang_prob = float(getattr(info, "language_probability", 0.0) or 0.0)
            if language == "auto" and raw_lang == "kn" and size != "base":
                kn_dir = self.resolve_kannada_specialist_dir()
                if kn_dir is not None:
                    kn_text, kn_ms = self._transcribe_kn_specialist_locked(samples, kn_dir)
                    if kn_text:
                        return TranscriptionResult(
                            text=kn_text,
                            detected_language="kn",
                            language_probability=round(lang_prob, 4),
                            model_size="indic-conformer-kn",
                            device="cpu",
                            compute_type="int8",
                            latency_ms=round(latency_ms + kn_ms, 2),
                            is_empty=False,
                            fallback_reason=fb_reason,
                        )

        resolved_lang: LanguageCode
        if language != "auto":
            resolved_lang = language
        elif any("\u0C80" <= ch <= "\u0CFF" for ch in text):
            resolved_lang = "kn"
        elif any("\u0900" <= ch <= "\u097F" for ch in text):
            resolved_lang = "hi"
        elif raw_lang in _SUPPORTED_WHISPER_LANGS:
            resolved_lang = raw_lang  # type: ignore[assignment]
        else:
            # Never emit unsupported languages (e.g. Tagalog 'tl'); stay within EN/HI/KN
            resolved_lang = fallback_language if fallback_language in _SUPPORTED_WHISPER_LANGS else "en"

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
