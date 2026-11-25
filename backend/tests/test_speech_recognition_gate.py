"""Focused tests for R2/R3/R4 STT quality gate, noise rejection, AGC, and script validation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np

from aura_edge.config import ComputeMode, ConnectivityMode, Settings
from aura_edge.infrastructure.compute import ComputeManager
from aura_edge.speech.stt import WhisperTranscriber, _validate_kn_ctc_output


def _make_stt(model_size: str = "small") -> WhisperTranscriber:
    cfg = Settings(
        _env_file=None,
        connectivity_mode=ConnectivityMode.STRICT_OFFLINE,
        compute_mode=ComputeMode.CPU,
        stt_model_size=model_size,  # type: ignore[arg-type]
    )
    return WhisperTranscriber(cfg, ComputeManager(cfg))


def test_r4_silence_and_fan_noise_rejection() -> None:
    stt = _make_stt("small")

    # 1. Pure silence -> quality_ok=False, rejection_reason='silence'
    silence = np.zeros(16000, dtype=np.float32)
    sil_res = stt.transcribe(silence, language="kn")
    assert sil_res.quality_ok is False
    assert sil_res.rejection_reason == "silence"
    assert sil_res.is_empty is True
    assert sil_res.text == ""

    # 2. Steady fan/background noise (even with language='kn'!) -> rejected as 'noise', never hallucinated KN
    rng = np.random.default_rng(7)
    fan_noise = rng.normal(0.0, 0.02, 32000).astype(np.float32)
    fan_res = stt.transcribe(fan_noise, language="kn")
    assert fan_res.quality_ok is False
    assert fan_res.rejection_reason == "noise"
    assert fan_res.is_empty is True
    assert fan_res.text == ""

    # 3. VAD-reported zero voiced frames + low SNR -> fast noise rejection without LLM trigger
    low_snr_res = stt.transcribe(
        fan_noise,
        language="auto",
        vad_voiced_ms=0.0,
        noise_floor_rms=0.02,
    )
    assert low_snr_res.quality_ok is False
    assert low_snr_res.rejection_reason == "noise"
    assert low_snr_res.is_empty is True


def test_r4_unclear_garbled_and_repetition_rejection(monkeypatch: Any) -> None:
    stt = _make_stt("small")
    speech_like = np.sin(2 * np.pi * 220 * np.arange(16000) / 16000).astype(np.float32) * 0.2

    # 1. Low avg_logprob garbled speech -> unclear_speech
    class _GarbledWhisper:
        def transcribe(self, *_args: Any, **_kwargs: Any) -> Any:
            seg = SimpleNamespace(
                text="uh blr",
                no_speech_prob=0.35,
                avg_logprob=-1.85,
                compression_ratio=1.1,
            )
            return [seg], SimpleNamespace(language="en", language_probability=0.62)

    monkeypatch.setattr(stt, "_load_model_locked", lambda _s: (_GarbledWhisper(), "cpu", "int8", None))
    garbled_res = stt.transcribe(speech_like, language="auto", vad_voiced_ms=320.0, noise_floor_rms=0.005)
    assert garbled_res.quality_ok is False
    assert garbled_res.rejection_reason == "unclear_speech"

    # 2. Excessive token repetition loop -> unclear_speech
    class _RepeatWhisper:
        def transcribe(self, *_args: Any, **_kwargs: Any) -> Any:
            seg = SimpleNamespace(
                text="the the the the the",
                no_speech_prob=0.1,
                avg_logprob=-0.6,
                compression_ratio=2.8,
            )
            return [seg], SimpleNamespace(language="en", language_probability=0.9)

    monkeypatch.setattr(stt, "_load_model_locked", lambda _s: (_RepeatWhisper(), "cpu", "int8", None))
    rep_res = stt.transcribe(speech_like, language="auto", vad_voiced_ms=320.0, noise_floor_rms=0.005)
    assert rep_res.quality_ok is False
    assert rep_res.rejection_reason == "unclear_speech"

    # 3. Whisper hallucination phrase on noisy low-SNR audio -> noise
    class _HallucinateWhisper:
        def transcribe(self, *_args: Any, **_kwargs: Any) -> Any:
            seg = SimpleNamespace(
                text="Thanks for watching.",
                no_speech_prob=0.68,
                avg_logprob=-1.2,
                compression_ratio=1.0,
            )
            return [seg], SimpleNamespace(language="en", language_probability=0.55)

    monkeypatch.setattr(stt, "_load_model_locked", lambda _s: (_HallucinateWhisper(), "cpu", "int8", None))
    hal_res = stt.transcribe(speech_like, language="auto", vad_voiced_ms=96.0, noise_floor_rms=0.005)
    assert hal_res.quality_ok is False
    assert hal_res.rejection_reason == "noise"
    assert hal_res.is_empty is True


def test_r4_legitimate_short_commands_preserved(monkeypatch: Any) -> None:
    stt = _make_stt("small")
    short_speech = np.sin(2 * np.pi * 240 * np.arange(8000) / 16000).astype(np.float32) * 0.15

    cases = [
        ("stop", "en"),
        ("Yes.", "en"),
        ("no", "en"),
        ("नहीं", "hi"),
        ("हाँ", "hi"),
        ("रुको", "hi"),
    ]
    for phrase, lang in cases:
        class _ShortCmdWhisper:
            def transcribe(self, *_args: Any, _p: str = phrase, _l: str = lang, **_kwargs: Any) -> Any:
                seg = SimpleNamespace(
                    text=_p,
                    no_speech_prob=0.25,
                    avg_logprob=-0.65,
                    compression_ratio=1.0,
                )
                return [seg], SimpleNamespace(language=_l, language_probability=0.88)

        model_stub = _ShortCmdWhisper()
        monkeypatch.setattr(
            stt,
            "_load_model_locked",
            lambda _s, _m=model_stub: (_m, "cpu", "int8", None),
        )
        res = stt.transcribe(short_speech, language="auto", vad_voiced_ms=192.0, noise_floor_rms=0.004)
        assert res.quality_ok is True, f"Short command {phrase!r} was unexpectedly rejected: {res}"
        assert res.rejection_reason is None
        assert res.detected_language == lang

    # Kannada short commands via IndicConformer
    for kn_cmd in ("ಹೌದು", "ಇಲ್ಲ", "ನಿಲ್ಲಿಸು"):
        monkeypatch.setattr(
            stt,
            "_load_model_locked",
            lambda _s: (
                SimpleNamespace(
                    transcribe=lambda *_a, **_kw: (
                        [SimpleNamespace(text="...", no_speech_prob=0.2, avg_logprob=-1.1, compression_ratio=1.0)],
                        SimpleNamespace(language="kn", language_probability=0.81),
                    )
                ),
                "cpu",
                "int8",
                None,
            ),
        )
        monkeypatch.setattr(stt, "_decode_specialist_locked", lambda _s, _d, _c=kn_cmd: (_c, 12.0))
        kn_res = stt.transcribe(short_speech, language="kn", vad_voiced_ms=192.0, noise_floor_rms=0.004)
        assert kn_res.quality_ok is True
        assert kn_res.text == kn_cmd
        assert kn_res.language_probability == 0.81


def test_r3_ctc_script_validation_and_quiet_mic_agc(monkeypatch: Any) -> None:
    # 1. CTC validator rejects stray Bengali/Assamese tokens from 5,633-token head
    ok_bad, _, reason_bad = _validate_kn_ctc_output("আমি বাংলা")
    assert ok_bad is False and reason_bad == "invalid_script"

    ok_good, clean_good, reason_good = _validate_kn_ctc_output("ಈಗ ಎಷ್ಟು ಗಂಟೆ")
    assert ok_good is True and clean_good == "ಈಗ ಎಷ್ಟು ಗಂಟೆ" and reason_good is None

    # 2. Quiet mic AGC boosts quiet speech (peak=0.02 -> ~0.35) without boosting pure silence
    stt = _make_stt("small")
    captured_peaks: list[float] = []

    class _AgcSpyWhisper:
        def transcribe(self, audio: np.ndarray, *_args: Any, **_kwargs: Any) -> Any:
            captured_peaks.append(float(np.max(np.abs(audio))))
            seg = SimpleNamespace(text="what time is it", no_speech_prob=0.05, avg_logprob=-0.25, compression_ratio=1.0)
            return [seg], SimpleNamespace(language="en", language_probability=0.96)

    monkeypatch.setattr(stt, "_load_model_locked", lambda _s: (_AgcSpyWhisper(), "cpu", "int8", None))
    quiet_speech = np.sin(2 * np.pi * 250 * np.arange(16000) / 16000).astype(np.float32) * 0.02
    res = stt.transcribe(quiet_speech, language="auto", vad_voiced_ms=400.0, noise_floor_rms=0.002)
    assert res.quality_ok is True
    assert captured_peaks and captured_peaks[0] >= 0.11  # Capped at 6x (0.02 * 6 = 0.12)
