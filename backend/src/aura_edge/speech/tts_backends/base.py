"""Base TTS backend protocol and synthesized audio container."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from aura_edge.intelligence.schemas import LanguageCode


@dataclass
class SynthesizedAudio:
    """In-memory mono float32 PCM audio produced by an offline TTS backend."""

    samples: np.ndarray
    sample_rate: int
    language: LanguageCode
    backend: str
    model_name: str
    voice_name: str
    latency_ms: float
    cached: bool = False


class TTSBackend(Protocol):
    """Protocol implemented by every offline TTS backend."""

    name: str

    def supports_language(self, language: LanguageCode) -> bool: ...

    def model_info(self, language: LanguageCode) -> tuple[str, str]: ...

    def synthesize(
        self,
        text: str,
        *,
        language: LanguageCode,
        speed: float = 1.0,
    ) -> SynthesizedAudio: ...
