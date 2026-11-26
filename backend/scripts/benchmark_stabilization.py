"""Sequential empirical benchmark suite for R3 (STT WER/CER), R7 (Ollama placement/throughput), and R8 (VoiceOrchestrator monotonic latency)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import types
import wave
from pathlib import Path
from typing import Any

import numpy as np
import psutil

from aura_edge.config import ComputeMode, ConnectivityMode, Settings, apply_offline_env_guards
from aura_edge.data.database import create_db_engine
from aura_edge.infrastructure.compute import ComputeManager
from aura_edge.infrastructure.network_policy import NetworkPolicyEnforcer, build_async_http_client
from aura_edge.intelligence.conversation import validate_response_language
from aura_edge.intelligence.ollama_client import OllamaClient
from aura_edge.intelligence.schemas import LanguageCode
from aura_edge.main import create_app
from aura_edge.speech.stt import WhisperTranscriber
from aura_edge.speech.tts_router import TTSRouter

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_AUDIO_DIR = REPO_ROOT / "models" / "eval_audio"

_EVAL_REFERENCE_TEXTS: dict[str, tuple[LanguageCode, str]] = {
    "phase1a_tts_en.wav": ("en", "Set a timer for five minutes and tell me the current time."),
    "phase1a_tts_hi.wav": ("hi", "पांच मिनट का टाइमर लगाओ और अभी क्या समय हुआ है बताओ।"),
    "phase1a_tts_kn.wav": ("kn", "ಐದು ನಿಮಿಷದ ಟೈಮರ್ ಇಡು ಮತ್ತು ಈಗ ಎಷ್ಟು ಗಂಟೆ ಆಗಿದೆ ಹೇಳು."),
}


def _load_wav_16k(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if sr != 16000 and pcm.size > 0:
        from scipy.signal import resample_poly

        g = math.gcd(16000, sr)
        pcm = resample_poly(pcm, 16000 // g, sr // g).astype(np.float32)
    return pcm


def _to_16k(samples: np.ndarray, sr: int) -> np.ndarray:
    if sr == 16000 or samples.size == 0:
        return samples.astype(np.float32, copy=False)
    from scipy.signal import resample_poly

    g = math.gcd(16000, sr)
    return resample_poly(samples.astype(np.float32, copy=False), 16000 // g, sr // g).astype(np.float32)


def _normalize_eval_text(text: str) -> str:
    cleaned = re.sub(r"[.,!?।॥\"'`~;:()\[\]{}\-]+", " ", (text or "").strip().lower())
    return " ".join(cleaned.split())


def _levenshtein(seq_a: list[str] | str, seq_b: list[str] | str) -> int:
    n, m = len(seq_a), len(seq_b)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        curr = [i] + [0] * m
        ai = seq_a[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ai == seq_b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[m]


def compute_wer_cer(reference: str, hypothesis: str) -> tuple[float, float]:
    ref_norm = _normalize_eval_text(reference)
    hyp_norm = _normalize_eval_text(hypothesis)
    ref_words = ref_norm.split()
    hyp_words = hyp_norm.split()
    wer = round(_levenshtein(ref_words, hyp_words) / max(1, len(ref_words)), 4)
    ref_chars = ref_norm.replace(" ", "")
    hyp_chars = hyp_norm.replace(" ", "")
    cer = round(_levenshtein(ref_chars, hyp_chars) / max(1, len(ref_chars)), 4)
    return wer, cer


def _nvidia_smi_snapshot() -> dict[str, Any]:
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        ).strip()
        parts = [p.strip() for p in out.splitlines()[0].split(",")]
        return {
            "vram_used_mib": int(parts[0]),
            "vram_total_mib": int(parts[1]),
            "gpu_util_pct": int(parts[2]),
        }
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        return {"error": str(exc)}


def _ollama_ps_snapshot() -> str:
    try:
        return subprocess.check_output(["ollama", "ps"], text=True, timeout=5).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"error: {exc}"


def evaluate_r3_stt(settings: Settings) -> dict[str, Any]:
    """R3: Evaluate faster-whisper-small, indic-conformer-kn, and decoupled selective fallback."""
    cfg_gpu = settings.model_copy(update={"compute_mode": ComputeMode.GPU, "stt_model_size": "small"})
    cm_gpu = ComputeManager(cfg_gpu)
    stt = WhisperTranscriber(cfg_gpu, cm_gpu)
    tts = TTSRouter(settings)

    samples: list[dict[str, Any]] = []
    for fname, (lang, ref) in _EVAL_REFERENCE_TEXTS.items():
        wav_path = EVAL_AUDIO_DIR / fname
        samples.append(
            {
                "id": fname,
                "source_kind": "synthetic_tts_saved",
                "true_language": lang,
                "reference_text": ref,
                "audio": _load_wav_16k(wav_path),
            }
        )

    # Generate additional synthetic clean, noisy (~15 dB SNR), and code-switched samples
    extra_specs: list[tuple[str, str, LanguageCode, str, bool]] = [
        ("synth_en_time", "synthetic_tts", "en", "What is today's date?", False),
        ("synth_hi_date", "synthetic_tts", "hi", "आज तारीख क्या है?", False),
        ("synth_kn_time", "synthetic_tts", "kn", "ಈಗ ಎಷ್ಟು ಗಂಟೆ ಆಗಿದೆ?", False),
        ("synth_kn_room", "synthetic_tts", "kn", "ಬೆಡ್‌ರೂಮ್ ಲೈಟ್ ಆನ್ ಮಾಡು.", False),
        ("synth_en_noisy", "synthetic_tts_noisy_15db", "en", "Turn on the bedroom fan.", True),
        ("synth_kn_noisy", "synthetic_tts_noisy_15db", "kn", "ಐದು ನಿಮಿಷದ ಟೈಮರ್ ಇಡು.", True),
    ]
    rng = np.random.default_rng(123)
    for sid, skind, lang, ref, add_noise in extra_specs:
        synth = tts.primary.synthesize(ref, language=lang, speed=1.0)
        pcm = _to_16k(synth.samples, synth.sample_rate)
        if add_noise and pcm.size > 0:
            sig_rms = float(np.sqrt(np.mean(np.square(pcm))))
            noise_rms = sig_rms / (10 ** (15.0 / 20.0))
            pcm = np.clip(pcm + rng.normal(0.0, noise_rms, pcm.size).astype(np.float32), -1.0, 1.0)
        samples.append(
            {
                "id": sid,
                "source_kind": skind,
                "true_language": lang,
                "reference_text": ref,
                "audio": pcm,
            }
        )

    # Warm Whisper & IndicConformer once so reported per-sample latencies reflect steady-state or note cold
    results_table: list[dict[str, Any]] = []
    for sample in samples:
        audio = sample["audio"]
        ref = sample["reference_text"]
        true_lang = sample["true_language"]

        # 1. Raw Whisper-small multilingual (language=None, beam_size=1 matching production)
        model, dev, ctype, fb = stt._load_model_locked("small")
        t_w0 = time.perf_counter()
        segs_iter, info = model.transcribe(
            audio,
            language=None,
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
            repetition_penalty=1.2,
            no_repeat_ngram_size=3,
            max_new_tokens=128,
        )
        segs = list(segs_iter)
        w_ms = round((time.perf_counter() - t_w0) * 1000.0, 2)
        w_text = " ".join(s.text.strip() for s in segs if s.text and s.text.strip()).strip()
        w_wer, w_cer = compute_wer_cer(ref, w_text)

        # 2. Direct IndicConformer-kn (for comparison on all samples)
        kn_dir = stt.resolve_kannada_specialist_dir()
        if kn_dir is not None:
            ic_text, ic_ms = stt._transcribe_kn_specialist_locked(audio, kn_dir)
            ic_wer, ic_cer = compute_wer_cer(ref, ic_text)
        else:
            ic_text, ic_ms, ic_wer, ic_cer = "", 0.0, 1.0, 1.0

        # 3. Our Decoupled Selective Fallback (preferred_reply_language="kn", recognition_mode="multilingual")
        dec = stt.transcribe(
            audio,
            language="auto",
            preferred_reply_language="kn",
            recognition_mode="multilingual",
        )
        d_wer, d_cer = compute_wer_cer(ref, dec.text)

        results_table.append(
            {
                "id": sample["id"],
                "source_kind": sample["source_kind"],
                "true_language": true_lang,
                "reference_text": ref,
                "whisper_small": {
                    "device": dev,
                    "compute_type": ctype,
                    "fallback_reason": fb,
                    "detected_language": getattr(info, "language", None),
                    "language_probability": round(float(getattr(info, "language_probability", 0.0) or 0.0), 4),
                    "text": w_text,
                    "wer": w_wer,
                    "cer": w_cer,
                    "latency_ms": w_ms,
                },
                "indic_conformer_kn_direct": {
                    "text": ic_text,
                    "wer": ic_wer,
                    "cer": ic_cer,
                    "latency_ms": ic_ms,
                },
                "decoupled_selective_kn_reply": {
                    "model_size": dec.model_size,
                    "detected_language": dec.detected_language,
                    "preferred_reply_language": dec.preferred_reply_language,
                    "language_probability": dec.language_probability,
                    "transcript_confidence": dec.transcript_confidence,
                    "avg_logprob": dec.avg_logprob,
                    "no_speech_prob": dec.no_speech_prob,
                    "quality_ok": dec.quality_ok,
                    "rejection_reason": dec.rejection_reason,
                    "text": dec.text,
                    "wer": d_wer,
                    "cer": d_cer,
                    "latency_ms": dec.latency_ms,
                },
            }
        )

    # Pure steady fan noise probe (R4)
    fan_noise = rng.normal(0.0, 0.02, 32000).astype(np.float32)
    fan_dec = stt.transcribe(fan_noise, language="auto", preferred_reply_language="kn")
    kn_dir = stt.resolve_kannada_specialist_dir()
    fan_ic_text, fan_ic_ms = (
        stt._transcribe_kn_specialist_locked(fan_noise, kn_dir) if kn_dir is not None else ("", 0.0)
    )

    # Release Whisper CUDA reference if loaded before Ollama GPU benchmarks
    stt._model = None
    stt._loaded_key = None

    return {
        "note": "0 real human microphone recordings exist in workspace; all WER/CER metrics below are measured on synthetic TTS waveforms (clean and 15 dB noise).",
        "samples": results_table,
        "fan_noise_probe": {
            "decoupled_gate": {
                "quality_ok": fan_dec.quality_ok,
                "rejection_reason": fan_dec.rejection_reason,
                "is_empty": fan_dec.is_empty,
                "text": fan_dec.text,
                "latency_ms": fan_dec.latency_ms,
            },
            "indic_conformer_direct_hallucination": {
                "text": fan_ic_text,
                "latency_ms": fan_ic_ms,
            },
        },
    }


async def _unload_ollama_model(enforcer: NetworkPolicyEnforcer, base_url: str, model: str) -> None:
    with contextlib.suppress(OSError, ValueError):
        async with build_async_http_client(enforcer, timeout=15.0) as client:
            await client.post(
                f"{base_url.rstrip('/')}/api/chat",
                json={"model": model, "messages": [], "keep_alive": 0},
            )


async def _measure_single_ollama_call(
    ollama: OllamaClient,
    *,
    model: str,
    prompt: str,
    language: LanguageCode,
) -> dict[str, Any]:
    messages = [
        {
            "role": "system",
            "content": (
                f"Reply concisely in {language} script only (1-2 short sentences, no markdown). "
                "Do not claim unsupported languages."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    t0 = time.perf_counter()
    first_sentence_ms: float | None = None
    buf = ""
    parts: list[str] = []
    stats: dict[str, Any] = {}
    async for tok, st in ollama.stream_conversation(
        messages, model=model, temperature=0.2, num_predict=100
    ):
        if st is not None:
            stats = st
            break
        if tok:
            parts.append(tok)
            buf += tok
            if first_sentence_ms is None and any(p in buf for p in (".", "!", "?", "।", "॥")):
                first_sentence_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    text = "".join(parts).strip()
    if first_sentence_ms is None and text:
        first_sentence_ms = stats.get("latency_ms")
    valid, reason = validate_response_language(text, language)
    return {
        "language": language,
        "ttft_ms": stats.get("ttft_ms"),
        "first_valid_sentence_ms": first_sentence_ms,
        "total_ms": stats.get("latency_ms"),
        "eval_count": stats.get("eval_count"),
        "tokens_per_sec": stats.get("tokens_per_sec"),
        "script_valid": valid,
        "validation_reason": reason,
        "response_preview": text[:160],
    }


async def evaluate_r7_ollama(settings: Settings) -> dict[str, Any]:
    """R7: Sequential Ollama placement, VRAM/RAM, cold start, warm tok/s, and EN/HI/KN quality."""
    cfg = settings.model_copy(update={"compute_mode": ComputeMode.GPU})
    enforcer = NetworkPolicyEnforcer.from_settings(cfg)
    cm = ComputeManager(cfg)
    ollama = OllamaClient(cfg, enforcer, cm)

    models_to_test = ["qwen3.5:4b", "qwen3.5:9b", "nemotron-3-nano:4b"]
    prompts: list[tuple[LanguageCode, str]] = [
        ("en", "Why is the sky blue in simple words?"),
        ("hi", "आकाश नीला क्यों दिखता है?"),
        ("kn", "ಆಕಾಶ ಏಕೆ ನೀಲಿಯಾಗಿ ಕಾಣುತ್ತದೆ?"),
    ]

    # Ensure clean start
    for m in models_to_test:
        await _unload_ollama_model(enforcer, cfg.ollama_url, m)
    await asyncio.sleep(1.0)

    results: dict[str, Any] = {}
    for model_name in models_to_test:
        ram_before_mb = round(psutil.virtual_memory().used / (1024 * 1024), 1)
        gpu_before = _nvidia_smi_snapshot()

        # Cold start on EN prompt
        cold_en = await _measure_single_ollama_call(
            ollama, model=model_name, prompt=prompts[0][1], language="en"
        )
        ps_after_cold = _ollama_ps_snapshot()
        gpu_active = _nvidia_smi_snapshot()
        ram_active_mb = round(psutil.virtual_memory().used / (1024 * 1024), 1)

        # Warm runs across EN, HI, KN
        warm_runs: list[dict[str, Any]] = []
        for lang, prompt in prompts:
            warm_runs.append(
                await _measure_single_ollama_call(ollama, model=model_name, prompt=prompt, language=lang)
            )

        results[model_name] = {
            "cold_en": cold_en,
            "warm_runs": warm_runs,
            "ollama_ps": ps_after_cold,
            "gpu_before": gpu_before,
            "gpu_active": gpu_active,
            "system_ram_used_mb_before": ram_before_mb,
            "system_ram_used_mb_active": ram_active_mb,
        }

        await _unload_ollama_model(enforcer, cfg.ollama_url, model_name)
        await asyncio.sleep(1.0)

    return results


def _install_null_speaker_callback_sim() -> Any:
    """Install a real-time simulated PortAudio OutputStream (zero speaker noise, real callback timing)."""
    class _SimOutputStream:
        def __init__(self, *, samplerate: int, channels: int, callback: Any, **_kwargs: Any) -> None:
            self.samplerate = samplerate
            self.channels = channels
            self.callback = callback
            self.latency = 0.01
            self._stop_ev = threading.Event()
            self._thread: threading.Thread | None = None
            self._block_frames = max(256, round(samplerate * 0.016))
            self._interval = self._block_frames / float(samplerate)

        def _run(self) -> None:
            buf = np.zeros((self._block_frames, self.channels), dtype=np.float32)
            while not self._stop_ev.is_set():
                buf.fill(0.0)
                self.callback(buf, self._block_frames, None, None)
                self._stop_ev.wait(self._interval)

        def start(self) -> None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

        def abort(self) -> None:
            self._stop_ev.set()
            if self._thread is not None:
                self._thread.join(timeout=0.5)

        def stop(self) -> None:
            self._stop_ev.set()
            if self._thread is not None:
                self._thread.join(timeout=0.5)

        def close(self) -> None:
            self._stop_ev.set()
            if self._thread is not None:
                self._thread.join(timeout=0.5)

    orig_sd = sys.modules.get("sounddevice")
    fake_sd = types.SimpleNamespace(
        OutputStream=_SimOutputStream,
        query_devices=lambda *args, **kwargs: (
            {"default_samplerate": 48000.0, "max_output_channels": 2}
            if args
            else [{"name": "SimSpeaker48k", "max_output_channels": 2, "default_samplerate": 48000.0}]
        ),
    )
    sys.modules["sounddevice"] = fake_sd  # type: ignore[assignment]
    return orig_sd


def _restore_sounddevice(orig_sd: Any) -> None:
    if orig_sd is None:
        sys.modules.pop("sounddevice", None)
    else:
        sys.modules["sounddevice"] = orig_sd


async def evaluate_r8_pipeline_latencies(settings: Settings) -> dict[str, Any]:
    """R8: Sequential cold + warm VoiceOrchestrator monotonic latency benchmark."""
    orig_sd = _install_null_speaker_callback_sim()
    cfg = settings.model_copy(
        update={
            "compute_mode": ComputeMode.GPU,
            "stt_model_size": "small",
            "ollama_model": "qwen3.5:4b",
            "audio_post_playback_cooldown_sec": 0.0,
        }
    )
    app = create_app(cfg)
    engine = create_db_engine(cfg.database_url)
    enforcer = NetworkPolicyEnforcer.from_settings(cfg)

    try:
        # Ensure clean cold start for Ollama & Whisper before measuring cold turns
        await _unload_ollama_model(enforcer, cfg.ollama_url, cfg.ollama_model)
        await app.state.smart_home.startup()
        orch = app.state.orchestrator
        # Do NOT call prewarm before cold measurements; clear TTS cache to observe true cold
        orch.tts_router._cache.clear()
        await orch.timer_manager.recover_on_startup()

        def _extract_row(label: str, phase: str, out: Any) -> dict[str, Any]:
            ts = out.stage_timestamps
            return {
                "label": label,
                "phase": phase,
                "intent": out.intent,
                "outcome": out.outcome,
                "action_executed": out.action_executed,
                "detected_language": out.detected_language,
                "response_language": out.response_language,
                "stt_model": out.stt_model,
                "stt_ms": out.stt_ms,
                "router_ms": out.router_ms,
                "tool_ms": out.tool_ms,
                "llm_ttft_ms": out.llm_ttft_ms,
                "llm_ms": out.llm_ms,
                "tts_ms": out.tts_ms,
                "audio_startup_ms": out.audio_startup_ms,
                "playback_ms": out.playback_ms,
                "ttfa_ms": out.ttfa_ms,
                "tts_cache_hit": out.tts_cache_hit,
                "time_to_first_text_ms": out.time_to_first_text_ms,
                "time_to_first_synthesized_audio_ms": out.time_to_first_synthesized_audio_ms,
                "time_to_first_audio_output_ms": out.time_to_first_audio_output_ms,
                "playback_duration_ms": out.playback_duration_ms,
                "total_turn_duration_ms": out.total_turn_duration_ms,
                "stage_timestamps_present": [k for k, v in ts.items() if v is not None],
            }

        rows: list[dict[str, Any]] = []
        en_wav = _load_wav_16k(EVAL_AUDIO_DIR / "phase1a_tts_en.wav")
        kn_wav = _load_wav_16k(EVAL_AUDIO_DIR / "phase1a_tts_kn.wav")

        # 1. Cold deterministic voice turn (EN WAV -> EN time/timer)
        cold_voice_en = await orch.process_audio_samples(en_wav, source="voice_ptt", language="auto", speak=True)
        rows.append(_extract_row("voice_wav_en_timer", "cold", cold_voice_en))

        # 2. Cold deterministic voice turn (KN WAV -> KN time/timer)
        cold_voice_kn = await orch.process_audio_samples(kn_wav, source="voice_ptt", language="auto", speak=True)
        rows.append(_extract_row("voice_wav_kn_timer", "cold", cold_voice_kn))

        # 3. Cold general conversation (EN -> loads qwen3.5:4b)
        cold_conv_en = await orch.process_text("Why is the sky blue in simple words?", language="en", speak=True)
        rows.append(_extract_row("conversation_en", "cold", cold_conv_en))

        # Now warm deterministic + cross-lingual + conversation runs
        orch.tts_router.prewarm_common_phrases()

        warm_specs = [
            ("text_en_time", "What is the time?", "en"),
            ("text_en_date", "Today's date?", "en"),
            ("text_en_timer", "Set a 1 minute timer", "en"),
            ("text_en_smarthome", "Turn on the bedroom light", "en"),
            ("text_hi_time", "अभी कितने बजे हैं?", "hi"),
            ("text_kn_time", "ಈಗ ಎಷ್ಟು ಗಂಟೆ?", "kn"),
            ("conversation_en", "Why is the sky blue in simple words?", "en"),
            ("conversation_kn", "ಆಕಾಶ ಏಕೆ ನೀಲಿಯಾಗಿ ಕಾಣುತ್ತದೆ?", "kn"),
        ]
        for lbl, txt, lang in warm_specs:
            out = await orch.process_text(txt, language=lang, speak=True)  # type: ignore[arg-type]
            rows.append(_extract_row(lbl, "warm", out))

        # Warm voice WAV turns (EN->EN, EN->KN cross-lingual, KN->KN)
        warm_voice_en = await orch.process_audio_samples(en_wav, source="voice_ptt", language="auto", speak=True)
        rows.append(_extract_row("voice_wav_en_timer", "warm", warm_voice_en))

        orch.set_language("kn")
        warm_voice_en_to_kn = await orch.process_audio_samples(en_wav, source="voice_ptt", speak=True)
        rows.append(_extract_row("voice_wav_en_to_kn_reply", "warm", warm_voice_en_to_kn))

        warm_voice_kn = await orch.process_audio_samples(kn_wav, source="voice_ptt", speak=True)
        rows.append(_extract_row("voice_wav_kn_timer", "warm", warm_voice_kn))

        # Cleanup timers & restore bedroom light off
        await orch.timer_manager.cancel_all(language="en")
        await orch.process_text("Turn off the bedroom light", language="en", speak=False)
        from sqlalchemy import delete
        from sqlalchemy.orm import Session

        from aura_edge.data.models import TimerRecord, VoiceTurnMetric

        with Session(engine) as session:
            session.execute(delete(TimerRecord))
            session.execute(delete(VoiceTurnMetric))
            session.commit()

        await orch.shutdown()
        await app.state.smart_home.shutdown()
        return {
            "note": (
                "Measured with real WhisperTranscriber, MultilingualFastRouter, Ollama qwen3.5:4b, "
                "SherpaOnnx TTSRouter, and real AudioPlayback._BoundedPcmBuffer driven by a simulated "
                "48kHz OutputStream callback thread (callback timing, not physical acoustic arrival)."
            ),
            "gpu_placement": orch.compute_manager.resolve_placement().model_dump(),
            "runs": rows,
        }
    finally:
        engine.dispose()
        _restore_sounddevice(orig_sd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run R3/R7/R8 stabilization benchmarks sequentially.")
    parser.add_argument(
        "--output",
        type=Path,
        default=EVAL_AUDIO_DIR / "phase1_5_stabilization_benchmarks.json",
    )
    args = parser.parse_args()

    settings = Settings(_env_file=None, connectivity_mode=ConnectivityMode.STRICT_OFFLINE)
    apply_offline_env_guards(settings)

    print("[1/3] Running R3 STT evaluation (faster-whisper-small vs indic-conformer-kn vs decoupled)...", flush=True)
    r3 = evaluate_r3_stt(settings)

    print("[2/3] Running R7 Ollama runtime placement & performance evaluation...", flush=True)
    r7 = asyncio.run(evaluate_r7_ollama(settings))

    print("[3/3] Running R8 VoiceOrchestrator cold & warm monotonic latency evaluation...", flush=True)
    r8 = asyncio.run(evaluate_r8_pipeline_latencies(settings))

    payload = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "r3_stt": r3,
        "r7_ollama": r7,
        "r8_pipeline": r8,
        "process_rss_mb": round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved stabilization benchmark report to {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
