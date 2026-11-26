"""Phase 1A Model Feasibility Gate: verifies real offline TTS, STT, CUDA DLLs, and Qwen 3.5."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import psutil

from aura_edge.config import ComputeMode, ConnectivityMode, Settings, apply_offline_env_guards
from aura_edge.infrastructure.compute import (
    ComputeManager,
    detect_cuda_availability,
    register_windows_cuda_dlls,
)
from aura_edge.infrastructure.network_policy import NetworkPolicyEnforcer
from aura_edge.intelligence.ollama_client import OllamaClient, OllamaUnavailableError
from aura_edge.intelligence.schemas import LanguageCode
from aura_edge.speech.stt import ModelNotFoundError, WhisperTranscriber
from aura_edge.speech.tts_backends.onecore_backend import OneCoreFallbackBackend
from aura_edge.speech.tts_backends.sherpa_onnx_backend import SherpaOnnxBackend
from aura_edge.speech.tts_router import pcm_float32_to_wav_bytes

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_AUDIO_DIR = REPO_ROOT / "models" / "eval_audio"

_TEST_PHRASES: dict[LanguageCode, str] = {
    "en": "Set a timer for five minutes and tell me the current time.",
    "hi": "पांच मिनट का टाइमर लगाओ और अभी क्या समय हुआ है बताओ।",
    "kn": "ಐದು ನಿಮಿಷದ ಟೈಮರ್ ಇಡು ಮತ್ತು ಈಗ ಎಷ್ಟು ಗಂಟೆ ಆಗಿದೆ ಹೇಳು.",
}


def _rss_mb() -> float:
    return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1)


def _audio_16k(samples: np.ndarray, src_sr: int) -> np.ndarray:
    if src_sr == 16000 or samples.size == 0:
        return samples.astype(np.float32, copy=False)
    import math

    from scipy.signal import resample_poly

    g = math.gcd(16000, src_sr)
    return resample_poly(samples, 16000 // g, src_sr // g).astype(np.float32)


def evaluate_tts(settings: Settings) -> tuple[dict[str, Any], dict[LanguageCode, np.ndarray]]:
    EVAL_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    backend = SherpaOnnxBackend(settings.models_dir)
    results: dict[str, Any] = {"sherpa_onnx": {}, "windows_sapi": {}}
    eval_pcm_16k: dict[LanguageCode, np.ndarray] = {}

    for lang in ("en", "hi", "kn"):
        typed_lang: LanguageCode = lang  # type: ignore[assignment]
        phrase = _TEST_PHRASES[typed_lang]
        rss_before = _rss_mb()
        t0 = time.perf_counter()
        cold = backend.synthesize(phrase, language=typed_lang, speed=1.0)
        cold_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        t1 = time.perf_counter()
        warm = backend.synthesize(phrase, language=typed_lang, speed=1.0)
        warm_ms = round((time.perf_counter() - t1) * 1000.0, 2)
        rss_after = _rss_mb()

        rms = float(np.sqrt(np.mean(np.square(warm.samples)))) if warm.samples.size else 0.0
        duration_s = round(len(warm.samples) / max(1, warm.sample_rate), 3)

        wav_path = EVAL_AUDIO_DIR / f"phase1a_tts_{lang}.wav"
        wav_path.write_bytes(pcm_float32_to_wav_bytes(warm.samples, warm.sample_rate))
        eval_pcm_16k[typed_lang] = _audio_16k(warm.samples, warm.sample_rate)

        results["sherpa_onnx"][lang] = {
            "model": warm.model_name,
            "voice": warm.voice_name,
            "sample_rate": warm.sample_rate,
            "duration_sec": duration_s,
            "rms_amplitude": round(rms, 5),
            "non_silent": rms > 0.005 and duration_s > 0.4,
            "cold_ms": cold_ms,
            "warm_ms": warm_ms,
            "rss_before_mb": rss_before,
            "rss_after_mb": rss_after,
            "wav_saved": str(wav_path),
            "cold_samples": len(cold.samples),
        }

    # Also test Windows SAPI5 offline English fallback
    sapi = OneCoreFallbackBackend()
    if sapi.supports_language("en"):
        sapi_audio = sapi.synthesize(_TEST_PHRASES["en"], language="en")
        sapi_rms = (
            float(np.sqrt(np.mean(np.square(sapi_audio.samples))))
            if sapi_audio.samples.size
            else 0.0
        )
        results["windows_sapi"]["en"] = {
            "sample_rate": sapi_audio.sample_rate,
            "duration_sec": round(len(sapi_audio.samples) / max(1, sapi_audio.sample_rate), 3),
            "rms_amplitude": round(sapi_rms, 5),
            "latency_ms": sapi_audio.latency_ms,
            "non_silent": sapi_rms > 0.005,
        }

    return results, eval_pcm_16k


def evaluate_stt(
    settings: Settings,
    eval_pcm_16k: dict[LanguageCode, np.ndarray],
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "cuda_dll_dirs": register_windows_cuda_dlls(settings.cuda_dll_dirs),
        "cuda_probe": {},
        "missing_model_check": {},
        "models": {},
    }
    cuda_ok, cuda_err = detect_cuda_availability(settings.cuda_dll_dirs)
    report["cuda_probe"] = {"usable": cuda_ok, "error": cuda_err}

    # Verify missing model produces explicit ModelNotFoundError
    bad_settings = settings.model_copy(update={"stt_model_size": "small", "models_dir": REPO_ROOT / "nonexistent_models"})
    bad_stt = WhisperTranscriber(bad_settings, ComputeManager(bad_settings))
    try:
        bad_stt.resolve_model_dir("small")
        report["missing_model_check"] = {"raised": False}
    except ModelNotFoundError as exc:
        report["missing_model_check"] = {"raised": True, "error": str(exc)}

    for model_size in ("base", "small"):
        report["models"][model_size] = {}
        for mode_name, mode in (("cpu", ComputeMode.CPU), ("gpu", ComputeMode.GPU)):
            cfg = settings.model_copy(update={"stt_model_size": model_size, "compute_mode": mode})
            cm = ComputeManager(cfg)
            stt = WhisperTranscriber(cfg, cm)

            # Warm load + cold/warm transcribe on real synthesized EN/HI/KN audio
            lang_runs: dict[str, Any] = {}
            for lang, pcm in eval_pcm_16k.items():
                # Auto language detection test
                auto_res = stt.transcribe(pcm, language="auto", model_size=model_size)
                # Explicit language test
                locked_res = stt.transcribe(pcm, language=lang, model_size=model_size)
                lang_runs[lang] = {
                    "auto_detected_lang": auto_res.detected_language,
                    "auto_prob": auto_res.language_probability,
                    "auto_text": auto_res.text,
                    "auto_latency_ms": auto_res.latency_ms,
                    "locked_text": locked_res.text,
                    "locked_latency_ms": locked_res.latency_ms,
                    "device": locked_res.device,
                    "compute_type": locked_res.compute_type,
                    "fallback_reason": locked_res.fallback_reason,
                }
            report["models"][model_size][mode_name] = lang_runs

    return report


async def evaluate_ollama(settings: Settings) -> dict[str, Any]:
    enforcer = NetworkPolicyEnforcer.from_settings(settings)
    out: dict[str, Any] = {}
    prompt = (
        'Classify into JSON {"intent_name": "SetTimer", "duration_seconds": 300}: '
        "पांच मिनट का टाइमर लगाओ"
    )
    for mode_name, mode in (("gpu", ComputeMode.GPU), ("cpu", ComputeMode.CPU)):
        cfg = settings.model_copy(update={"compute_mode": mode})
        cm = ComputeManager(cfg)
        client = OllamaClient(cfg, enforcer, cm)
        try:
            cold = await client.chat_json([{"role": "user", "content": prompt}])
            warm = await client.chat_json([{"role": "user", "content": prompt}])
            out[mode_name] = {
                "reachable": True,
                "model": warm.model,
                "num_gpu": warm.num_gpu,
                "cold_ms": cold.latency_ms,
                "warm_ms": warm.latency_ms,
                "ttft_ms": warm.ttft_ms,
                "tokens_per_sec": warm.tokens_per_sec,
                "parsed_json": warm.parsed_json,
            }
        except OllamaUnavailableError as exc:
            out[mode_name] = {"reachable": False, "error": str(exc)}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AURA EDGE Phase 1A Model Feasibility Gate.")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    settings = Settings(_env_file=None, connectivity_mode=ConnectivityMode.STRICT_OFFLINE)
    apply_offline_env_guards(settings)

    tts_report, eval_pcm_16k = evaluate_tts(settings)
    stt_report = evaluate_stt(settings, eval_pcm_16k)
    ollama_report = asyncio.run(evaluate_ollama(settings))

    full_report = {
        "hf_hub_offline": os.environ.get("HF_HUB_OFFLINE"),
        "tts": tts_report,
        "stt": stt_report,
        "ollama": ollama_report,
    }
    text = json.dumps(full_report, indent=2, ensure_ascii=False)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
