"""Acceptance tests for Kannada IndicConformer STT, clean spoken time strings, and voice turns."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from aura_edge.config import ComputeMode, ConnectivityMode, Settings
from aura_edge.execution.tools.datetime_tool import DateTimeTool
from aura_edge.infrastructure.compute import ComputeManager
from aura_edge.intelligence.fast_router import MultilingualFastRouter
from aura_edge.intelligence.schemas import IntentName
from aura_edge.main import create_app
from aura_edge.speech.stt import WhisperTranscriber
from aura_edge.speech.tts_router import normalize_for_pronunciation, pcm_float32_to_wav_bytes


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


def test_indic_conformer_kannada_stt_accuracy_and_routing() -> None:
    cfg = Settings(
        _env_file=None,
        connectivity_mode=ConnectivityMode.STRICT_OFFLINE,
        compute_mode=ComputeMode.CPU,
        stt_model_size="small",
    )
    stt = WhisperTranscriber(cfg, ComputeManager(cfg))
    assert stt.resolve_kannada_specialist_dir() is not None

    audio_kn = _load_wav_16k_float32(cfg.models_dir / "eval_audio" / "phase1a_tts_kn.wav")
    res = stt.transcribe(audio_kn, language="kn")
    assert res.model_size == "indic-conformer-kn"
    assert res.detected_language == "kn"
    assert res.device == "cpu"
    assert res.compute_type == "int8"
    assert res.is_empty is False

    # Confirm the Kannada transcript routes deterministically to SetTimer + GetTime
    router = MultilingualFastRouter()
    intent, _ = router.route(res.text, requested_language="kn")
    assert intent is not None
    assert len(intent.sub_actions) == 2
    assert intent.sub_actions[0].intent_name == IntentName.SET_TIMER
    assert intent.sub_actions[0].slots.get("duration_seconds") == 300
    assert intent.sub_actions[1].intent_name == IntentName.GET_TIME


def test_clean_indic_time_text_and_kannada_tts_normalization() -> None:
    dt = DateTimeTool()
    hi_time = dt.get_time("hi")
    kn_time = dt.get_time("kn")
    assert "India Standard" not in hi_time.message
    assert "India Standard" not in kn_time.message
    assert "(" not in kn_time.message

    sanitized_kn = normalize_for_pronunciation("ಈಗ ಮಧ್ಯಾಹ್ನ 4 ಗಂಟೆ (IST).", "kn")
    assert "IST" not in sanitized_kn
    assert "(" not in sanitized_kn
    assert "ಗಂಟೆ" in sanitized_kn


def test_api_voice_transcribe_multilingual_turns() -> None:
    cfg = Settings(
        _env_file=None,
        connectivity_mode=ConnectivityMode.STRICT_OFFLINE,
        compute_mode=ComputeMode.CPU,
        stt_model_size="small",
    )
    app = create_app(settings=cfg)
    with TestClient(app) as client:
        status = client.get("/api/v1/voice/status").json()
        assert status["stt_kannada_specialist"] == "indic-conformer-kn"

        for lang, wav_name, expected in [
            ("en", "phase1a_tts_en.wav", "SetTimer"),
            ("kn", "phase1a_tts_kn.wav", "SetTimer"),
        ]:
            samples = _load_wav_16k_float32(cfg.models_dir / "eval_audio" / wav_name)
            wav_bytes = pcm_float32_to_wav_bytes(samples, 16000)
            resp = client.post(
                "/api/v1/voice/transcribe",
                files={"file": (wav_name, wav_bytes, "audio/wav")},
                data={"language": lang, "speak": "false", "allow_llm_fallback": "false"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["detected_language"] == lang
            assert body["intent"] == expected

        # Clean up any timers created in DB during API test
        for t in client.get("/api/v1/timers").json():
            client.delete(f"/api/v1/timers/{t['id']}")
