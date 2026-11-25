"""Language-aware offline TTSRouter with pronunciation normalization and true LRU cache."""

from __future__ import annotations

import io
import re
import threading
import wave
from collections import OrderedDict

import numpy as np

from aura_edge.config import Settings
from aura_edge.intelligence.schemas import LanguageCode
from aura_edge.speech.tts_backends.base import SynthesizedAudio, TTSBackend
from aura_edge.speech.tts_backends.onecore_backend import OneCoreFallbackBackend
from aura_edge.speech.tts_backends.sherpa_onnx_backend import SherpaOnnxBackend

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?।॥])\s+(?=[^\d])")


def normalize_for_pronunciation(text: str, language: LanguageCode) -> str:
    """Normalize symbols, times, and units for cleaner EN/HI/KN speech synthesis."""
    out = re.sub(r"\s+", " ", text or "").strip()
    if not out:
        return ""

    if language == "hi":
        out = out.replace("%", " प्रतिशत")
        out = out.replace("°C", " डिग्री सेल्सियस")
    elif language == "kn":
        out = out.replace("%", " ಶೇಕಡಾ")
        out = out.replace("°C", " ಡಿಗ್ರಿ ಸೆಲ್ಸಿಯಸ್")
    else:
        out = out.replace("%", " percent")
        out = out.replace("°C", " degrees Celsius")

    # Normalize 12h clock colons (e.g., 10:05 AM -> 10 05 AM) for VITS prosody
    out = re.sub(r"\b(\d{1,2}):00\b", r"\1", out)
    out = re.sub(r"\b(\d{1,2}):(\d{2})\b", r"\1 \2", out)
    return out.strip()


def split_sentences(text: str) -> list[str]:
    """Split text cleanly along English, Hindi (।॥), and Kannada sentence boundaries."""
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return []
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(cleaned) if p.strip()]
    return parts or [cleaned]


def pcm_float32_to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    """Encode 1-D float32 PCM samples into standard 16-bit mono RIFF WAV bytes."""
    clipped = np.clip(samples.astype(np.float32, copy=False), -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


class TTSRouter:
    """Routes synthesis to verified offline TTS backends with a true OrderedDict LRU cache."""

    def __init__(
        self,
        settings: Settings,
        *,
        primary_backend: TTSBackend | None = None,
        fallback_backend: TTSBackend | None = None,
    ) -> None:
        self._settings = settings
        self.primary = primary_backend or SherpaOnnxBackend(settings.models_dir)
        self.fallback = fallback_backend or OneCoreFallbackBackend()
        self._cache_max = max(1, int(settings.tts_cache_max_entries))
        self._cache: OrderedDict[tuple[str, str, str, str, float, str], SynthesizedAudio] = (
            OrderedDict()
        )
        self._cache_lock = threading.Lock()
        self.cache_hits = 0
        self.cache_misses = 0

    def _select_backend(self, language: LanguageCode) -> TTSBackend:
        if self.primary.supports_language(language):
            return self.primary
        if self.fallback.supports_language(language):
            return self.fallback
        raise RuntimeError(
            f"No offline TTS backend is available for language '{language}'. "
            "Run backend/scripts/prepare_models.py to prepare EN/HI/KN voices."
        )

    def available_languages(self) -> list[LanguageCode]:
        langs: list[LanguageCode] = []
        for lang in ("en", "hi", "kn"):
            typed_lang: LanguageCode = lang  # type: ignore[assignment]
            if self.primary.supports_language(typed_lang) or self.fallback.supports_language(
                typed_lang
            ):
                langs.append(typed_lang)
        return langs

    def active_engine_summary(self) -> dict[str, object]:
        voices: dict[str, dict[str, str]] = {}
        for lang in ("en", "hi", "kn"):
            typed_lang: LanguageCode = lang  # type: ignore[assignment]
            if self.primary.supports_language(typed_lang):
                m, v = self.primary.model_info(typed_lang)
                voices[lang] = {"backend": self.primary.name, "model": m, "voice": v}
            elif self.fallback.supports_language(typed_lang):
                m, v = self.fallback.model_info(typed_lang)
                voices[lang] = {"backend": self.fallback.name, "model": m, "voice": v}
        return {
            "primary_backend": self.primary.name,
            "voices": voices,
            "cache_entries": len(self._cache),
            "cache_max_entries": self._cache_max,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
        }

    def synthesize(
        self,
        text: str,
        *,
        language: LanguageCode = "en",
        speed: float | None = None,
    ) -> SynthesizedAudio:
        """Synthesize offline speech with LRU caching keyed by language/model/voice/speed/text."""
        spd = round(float(speed if speed is not None else self._settings.tts_default_speed), 2)
        normalized = normalize_for_pronunciation(text, language)
        backend = self._select_backend(language)
        model_name, voice_name = backend.model_info(language)
        cache_key = (language, backend.name, model_name, voice_name, spd, normalized)

        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                self.cache_hits += 1
                return SynthesizedAudio(
                    samples=cached.samples,
                    sample_rate=cached.sample_rate,
                    language=cached.language,
                    backend=cached.backend,
                    model_name=cached.model_name,
                    voice_name=cached.voice_name,
                    latency_ms=0.0,
                    cached=True,
                )

        audio = backend.synthesize(normalized, language=language, speed=spd)

        with self._cache_lock:
            self.cache_misses += 1
            self._cache[cache_key] = audio
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self._cache_max:
                self._cache.popitem(last=False)

        return audio

    def synthesize_wav(
        self,
        text: str,
        *,
        language: LanguageCode = "en",
        speed: float | None = None,
    ) -> tuple[bytes, SynthesizedAudio]:
        audio = self.synthesize(text, language=language, speed=speed)
        return pcm_float32_to_wav_bytes(audio.samples, audio.sample_rate), audio
