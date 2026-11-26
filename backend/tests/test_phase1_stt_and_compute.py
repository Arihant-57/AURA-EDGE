"""Tests for Faster-Whisper offline STT (EN/HI/KN), CUDA DLL guard, and CPU/GPU modes."""

from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np
import pytest

from aura_edge.config import ComputeMode, ConnectivityMode, Settings
from aura_edge.infrastructure.compute import ComputeManager, detect_cuda_availability
from aura_edge.speech.stt import ModelNotFoundError, WhisperTranscriber


def _load_wav_16k_float32(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if sr != 16000 and pcm.size > 0:
        import math

        from scipy.signal import resample_poly

        g = math.gcd(16000, sr)
        pcm = resample_poly(pcm, 16000 // g, sr // g).astype(np.float32)
    return pcm


def test_compute_manager_cpu_strictly_avoids_gpu_and_reports_fallback() -> None:
    cpu_cfg = Settings(_env_file=None, compute_mode=ComputeMode.CPU)
    cpu_cm = ComputeManager(cpu_cfg)
    p = cpu_cm.resolve_placement()
    assert p.effective_mode == "cpu"
    assert p.stt_device == "cpu"
    assert p.stt_compute_type == "int8"
    assert p.llm_device == "cpu"
    assert p.llm_num_gpu == 0
    assert p.cuda_dll_dirs_registered == []

    auto_cfg = Settings(_env_file=None, compute_mode=ComputeMode.GPU)
    auto_cm = ComputeManager(auto_cfg)
    fb = auto_cm.record_runtime_fallback("Simulated CUDA OOM")
    assert fb.effective_mode == "cpu"
    assert fb.fallback_reason == "Simulated CUDA OOM"


def test_missing_stt_model_raises_clear_error() -> None:
    cfg = Settings(
        _env_file=None,
        connectivity_mode=ConnectivityMode.STRICT_OFFLINE,
        models_dir=Path("C:/nonexistent_aura_models_dir"),
    )
    stt = WhisperTranscriber(cfg, ComputeManager(cfg))
    with pytest.raises(ModelNotFoundError, match="prepare_models.py"):
        stt.resolve_model_dir("small")


def test_offline_faster_whisper_cpu_and_cuda_on_recorded_en_hi_kn() -> None:
    cfg_cpu = Settings(
        _env_file=None,
        connectivity_mode=ConnectivityMode.STRICT_OFFLINE,
        compute_mode=ComputeMode.CPU,
        stt_model_size="base",
    )
    stt_cpu = WhisperTranscriber(cfg_cpu, ComputeManager(cfg_cpu))
    assert os.environ.get("HF_HUB_OFFLINE") == "1"

    # Empty audio produces clean empty result without crashing
    empty_res = stt_cpu.transcribe(np.zeros(16000, dtype=np.float32))
    assert empty_res.is_empty is True
    assert empty_res.text == ""

    # Transcribe real synthesized/recorded EN, HI, KN WAV files on CPU
    for lang in ("en", "hi", "kn"):
        wav_path = cfg_cpu.models_dir / "eval_audio" / f"phase1a_tts_{lang}.wav"
        assert wav_path.is_file(), f"Missing eval fixture {wav_path}"
        audio = _load_wav_16k_float32(wav_path)
        res = stt_cpu.transcribe(audio, language=lang, model_size="base")  # type: ignore[arg-type]
        assert res.device == "cpu"
        assert res.compute_type == "int8"
        assert res.is_empty is False
        assert len(res.text) > 0

    # If CUDA is available on host, verify GPU inference also succeeds with DLL guard
    cuda_ok, _ = detect_cuda_availability(cfg_cpu.cuda_dll_dirs)
    if cuda_ok:
        cfg_gpu = cfg_cpu.model_copy(update={"compute_mode": ComputeMode.GPU})
        stt_gpu = WhisperTranscriber(cfg_gpu, ComputeManager(cfg_gpu))
        audio_en = _load_wav_16k_float32(cfg_cpu.models_dir / "eval_audio" / "phase1a_tts_en.wav")
        gpu_res = stt_gpu.transcribe(audio_en, language="en", model_size="base")
        assert gpu_res.device == "cuda"
        assert gpu_res.is_empty is False


def test_r2_decoupled_stt_en_and_hi_speech_when_kannada_reply_preferred() -> None:
    cfg = Settings(
        _env_file=None,
        connectivity_mode=ConnectivityMode.STRICT_OFFLINE,
        compute_mode=ComputeMode.CPU,
        stt_model_size="small",
    )
    stt = WhisperTranscriber(cfg, ComputeManager(cfg))

    # EN speech with language='kn' (reply preference) must detect English, NOT force IndicConformer KN
    audio_en = _load_wav_16k_float32(cfg.models_dir / "eval_audio" / "phase1a_tts_en.wav")
    en_res = stt.transcribe(audio_en, language="kn", preferred_reply_language="kn")
    assert en_res.detected_language == "en"
    assert en_res.preferred_reply_language == "kn"
    assert en_res.recognition_mode == "multilingual"
    assert en_res.model_size == "small"
    assert en_res.language_probability is not None and 0.5 < en_res.language_probability < 1.0
    assert en_res.quality_ok is True
    assert en_res.avg_logprob is not None
    assert en_res.no_speech_prob is not None
    assert en_res.transcript_confidence is not None and en_res.transcript_confidence < 1.0
    assert "timer" in en_res.text.lower()

    # HI speech with preferred_reply_language='kn' must detect Hindi, NOT force IndicConformer KN
    audio_hi = _load_wav_16k_float32(cfg.models_dir / "eval_audio" / "phase1a_tts_hi.wav")
    hi_res = stt.transcribe(audio_hi, language="kn", preferred_reply_language="kn")
    assert hi_res.detected_language == "hi"
    assert hi_res.preferred_reply_language == "kn"
    assert hi_res.model_size == "small"
    assert hi_res.language_probability is not None and 0.5 < hi_res.language_probability < 1.0
    assert hi_res.quality_ok is True

    # KN speech in multilingual mode uses Whisper detection -> selective IndicConformer with real prob < 1.0
    audio_kn = _load_wav_16k_float32(cfg.models_dir / "eval_audio" / "phase1a_tts_kn.wav")
    kn_res = stt.transcribe(audio_kn, language="kn")
    assert kn_res.detected_language == "kn"
    assert kn_res.model_size == "indic-conformer-kn"
    assert kn_res.language_probability is not None and 0.0 < kn_res.language_probability < 1.0
    assert kn_res.quality_ok is True

    # Explicit strict mode sets language_probability=None (never fabricated 1.0)
    strict_res = stt.transcribe(audio_kn, language="kn", recognition_mode="strict")
    assert strict_res.recognition_mode == "strict"
    assert strict_res.model_size == "indic-conformer-kn"
    assert strict_res.language_probability is None
    assert strict_res.quality_ok is True
