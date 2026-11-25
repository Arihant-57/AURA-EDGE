"""Offline CPU Sherpa-ONNX TTS backend supporting English, Hindi, and Kannada."""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from aura_edge.intelligence.schemas import LanguageCode
from aura_edge.speech.tts_backends.base import SynthesizedAudio

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SherpaVoiceSpec:
    language: LanguageCode
    model_name: str
    voice_name: str
    model_path: Path
    tokens_path: Path
    data_dir: Path | None
    speaker_id: int = 0
    license_id: str = "unknown"


def discover_sherpa_voices(models_dir: Path) -> dict[LanguageCode, SherpaVoiceSpec]:
    """Locate prepared offline EN/HI/KN ONNX voice models inside ``models/tts/``."""
    tts_root = models_dir / "tts"
    specs: dict[LanguageCode, SherpaVoiceSpec] = {}

    # English Piper voice
    en_dir = tts_root / "en" / "vits-piper-en_US-lessac-medium"
    en_onnx = en_dir / "en_US-lessac-medium.onnx"
    if en_onnx.is_file() and (en_dir / "tokens.txt").is_file():
        specs["en"] = SherpaVoiceSpec(
            language="en",
            model_name="vits-piper-en_US-lessac-medium",
            voice_name="en_US-lessac-medium",
            model_path=en_onnx,
            tokens_path=en_dir / "tokens.txt",
            data_dir=en_dir / "espeak-ng-data" if (en_dir / "espeak-ng-data").is_dir() else None,
            license_id="MIT / Blizzard-2013",
        )

    # Hindi Piper voice
    hi_dir = tts_root / "hi" / "vits-piper-hi_IN-rohan-medium"
    hi_onnx = hi_dir / "hi_IN-rohan-medium.onnx"
    if hi_onnx.is_file() and (hi_dir / "tokens.txt").is_file():
        specs["hi"] = SherpaVoiceSpec(
            language="hi",
            model_name="vits-piper-hi_IN-rohan-medium",
            voice_name="hi_IN-rohan-medium",
            model_path=hi_onnx,
            tokens_path=hi_dir / "tokens.txt",
            data_dir=hi_dir / "espeak-ng-data" if (hi_dir / "espeak-ng-data").is_dir() else None,
            license_id="IITM-IndicTTS / Piper",
        )

    # Kannada MMS-TTS ONNX (facebook/mms-tts-kan via sherpa-onnx VITS)
    kn_dir = tts_root / "kn" / "vits-mms-kan"
    kn_onnx = kn_dir / "model.onnx"
    if kn_onnx.is_file() and (kn_dir / "tokens.txt").is_file():
        specs["kn"] = SherpaVoiceSpec(
            language="kn",
            model_name="vits-mms-kan",
            voice_name="facebook/mms-tts-kan",
            model_path=kn_onnx,
            tokens_path=kn_dir / "tokens.txt",
            data_dir=None,
            license_id="CC-BY-NC-4.0",
        )

    return specs


class SherpaOnnxBackend:
    """CPU-only Sherpa-ONNX offline speech synthesizer for EN, HI, and KN."""

    name = "sherpa_onnx"

    def __init__(self, models_dir: Path, *, num_threads: int = 8) -> None:
        self.models_dir = models_dir
        self.num_threads = num_threads
        self._specs = discover_sherpa_voices(models_dir)
        self._engines: dict[LanguageCode, Any] = {}
        self._lock = threading.Lock()

    def refresh_voices(self) -> None:
        with self._lock:
            self._specs = discover_sherpa_voices(self.models_dir)

    def available_languages(self) -> list[LanguageCode]:
        return list(self._specs.keys())

    def supports_language(self, language: LanguageCode) -> bool:
        return language in self._specs

    def model_info(self, language: LanguageCode) -> tuple[str, str]:
        spec = self._specs.get(language)
        if spec is None:
            return ("unavailable", "unavailable")
        return (spec.model_name, spec.voice_name)

    def _get_engine(self, language: LanguageCode) -> tuple[Any, SherpaVoiceSpec]:
        spec = self._specs.get(language)
        if spec is None:
            raise FileNotFoundError(
                f"No offline Sherpa-ONNX voice found for language '{language}' under '{self.models_dir / 'tts'}'."
            )
        engine = self._engines.get(language)
        if engine is not None:
            return engine, spec

        import sherpa_onnx

        vits_cfg = sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(spec.model_path),
            tokens=str(spec.tokens_path),
            data_dir=str(spec.data_dir) if spec.data_dir is not None else "",
        )
        model_cfg = sherpa_onnx.OfflineTtsModelConfig(
            vits=vits_cfg,
            num_threads=self.num_threads,
            provider="cpu",
            debug=False,
        )
        tts_cfg = sherpa_onnx.OfflineTtsConfig(model=model_cfg)
        engine = sherpa_onnx.OfflineTts(tts_cfg)
        self._engines[language] = engine
        return engine, spec

    def synthesize(
        self,
        text: str,
        *,
        language: LanguageCode,
        speed: float = 1.0,
    ) -> SynthesizedAudio:
        clean = text.strip()
        if language == "kn":
            clean = re.sub(r"[^\u0C80-\u0CFF0-9\s'\-_]+", " ", clean)
            clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            return SynthesizedAudio(
                samples=np.zeros(0, dtype=np.float32),
                sample_rate=16000,
                language=language,
                backend=self.name,
                model_name=self.model_info(language)[0],
                voice_name=self.model_info(language)[1],
                latency_ms=0.0,
            )

        with self._lock:
            engine, spec = self._get_engine(language)
            t0 = time.perf_counter()
            audio = engine.generate(clean, sid=spec.speaker_id, speed=float(speed))
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        samples = np.asarray(audio.samples, dtype=np.float32)
        sr = int(audio.sample_rate)
        return SynthesizedAudio(
            samples=samples,
            sample_rate=sr,
            language=language,
            backend=self.name,
            model_name=spec.model_name,
            voice_name=spec.voice_name,
            latency_ms=latency_ms,
        )
