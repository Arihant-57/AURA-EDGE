"""Tests for offline EN/HI/KN TTSRouter, true OrderedDict LRU cache, and AudioPlayback."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from aura_edge.config import Settings
from aura_edge.intelligence.schemas import LanguageCode
from aura_edge.speech.playback import AudioPlayback, apply_fade_out
from aura_edge.speech.tts_backends.base import SynthesizedAudio
from aura_edge.speech.tts_router import (
    TTSRouter,
    normalize_for_pronunciation,
    pcm_float32_to_wav_bytes,
    split_sentences,
)


def test_tts_router_real_offline_en_hi_kn_synthesis() -> None:
    settings = Settings(_env_file=None)
    router = TTSRouter(settings)
    assert set(router.available_languages()) == {"en", "hi", "kn"}

    samples_by_lang: dict[LanguageCode, str] = {
        "en": "The time is 10:05 AM.",
        "hi": "पांच मिनट का टाइमर सेट कर दिया गया है।",
        "kn": "ಐದು ನಿಮಿಷದ ಟೈಮರ್ ಸೆಟ್ ಮಾಡಲಾಗಿದೆ.",
    }
    for lang, text in samples_by_lang.items():
        wav_bytes, audio = router.synthesize_wav(text, language=lang)
        assert wav_bytes[:4] == b"RIFF"
        assert len(wav_bytes) > 4000
        rms = float(np.sqrt(np.mean(np.square(audio.samples))))
        assert rms > 0.005, f"Silent audio produced for {lang}"
        assert audio.backend == "sherpa_onnx"


def test_tts_router_true_lru_cache_eviction_and_key_dimensions() -> None:
    calls: list[tuple[str, str, float]] = []

    class _FakeBackend:
        name = "fake_tts"

        def supports_language(self, _lang: LanguageCode) -> bool:
            return True

        def model_info(self, language: LanguageCode) -> tuple[str, str]:
            return (f"model-{language}", f"voice-{language}")

        def synthesize(
            self,
            text: str,
            *,
            language: LanguageCode,
            speed: float = 1.0,
        ) -> SynthesizedAudio:
            calls.append((language, text, speed))
            return SynthesizedAudio(
                samples=np.ones(400, dtype=np.float32) * 0.1,
                sample_rate=16000,
                language=language,
                backend=self.name,
                model_name=f"model-{language}",
                voice_name=f"voice-{language}",
                latency_ms=1.0,
            )

    settings = Settings(_env_file=None, tts_cache_max_entries=2)
    router = TTSRouter(settings, primary_backend=_FakeBackend())

    # Insert A, then B
    router.synthesize("alpha", language="en", speed=1.0)
    router.synthesize("beta", language="en", speed=1.0)
    # Access A again -> promotes A to MRU so B becomes LRU!
    hit_a = router.synthesize("alpha", language="en", speed=1.0)
    assert hit_a.cached is True
    # Insert C -> must evict B, NOT A
    router.synthesize("gamma", language="en", speed=1.0)

    # A must still be cached, B must miss, and changing speed/language must also miss
    assert router.synthesize("alpha", language="en", speed=1.0).cached is True
    assert router.synthesize("beta", language="en", speed=1.0).cached is False
    assert router.synthesize("alpha", language="en", speed=1.25).cached is False
    assert router.synthesize("alpha", language="hi", speed=1.0).cached is False


def test_sentence_splitting_pronunciation_and_playback_barge_in() -> None:
    hi_sentences = split_sentences("पहला वाक्य। दूसरा वाक्य! What about 3.5 minutes? Done.")
    assert hi_sentences == [
        "पहला वाक्य।",
        "दूसरा वाक्य!",
        "What about 3.5 minutes?",
        "Done.",
    ]
    assert "percent" in normalize_for_pronunciation("50%", "en")
    assert "प्रतिशत" in normalize_for_pronunciation("50%", "hi")
    assert "ಶೇಕಡಾ" in normalize_for_pronunciation("50%", "kn")

    faded = apply_fade_out(np.ones(128, dtype=np.float32), fade_samples=64)
    assert faded[-1] == 0.0
    assert faded[0] == 1.0

    # Test AudioPlayback headless synthesis + external cancellation without hardware speaker dependency
    settings = Settings(_env_file=None)
    router = TTSRouter(settings)
    playback = AudioPlayback(router)
    cancel_ev = asyncio.Event()
    cancel_ev.set()
    spoken = asyncio.run(
        playback.speak_text(
            "Sentence one. Sentence two.",
            language="en",
            play_to_speaker=False,
            external_cancel=cancel_ev,
        )
    )
    assert spoken == []
    playback.close()

    wav = pcm_float32_to_wav_bytes(np.zeros(1600, dtype=np.float32), 16000)
    assert wav[:4] == b"RIFF"



def test_disabled_output_synthesizes_without_opening_speaker(monkeypatch) -> None:
    router = TTSRouter(Settings(_env_file=None))
    playback = AudioPlayback(router, output_enabled=False)

    def unexpected_output(*args, **kwargs):
        pytest.fail("A silent rehearsal tried to open a speaker stream")

    monkeypatch.setattr(playback, "_open_stream_blocking", unexpected_output)
    try:
        clips, metrics = asyncio.run(playback.speak_text_with_metrics("Test.", play_to_speaker=True))
        assert clips and metrics.first_non_silent_callback_mono is None
    finally:
        playback.close()
