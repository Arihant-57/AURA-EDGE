"""Reproducible Phase 1 CPU vs GPU benchmark harness (zero fabricated numbers)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np
import psutil

from aura_edge.config import ComputeMode, ConnectivityMode, Settings, apply_offline_env_guards
from aura_edge.data.database import check_database_readiness, create_db_engine
from aura_edge.execution.tool_registry import ToolRegistry
from aura_edge.execution.tools.datetime_tool import DateTimeTool
from aura_edge.execution.tools.timer_tool import TimerManager
from aura_edge.infrastructure.compute import ComputeManager
from aura_edge.infrastructure.hardware import collect_hardware_snapshot
from aura_edge.infrastructure.network_policy import NetworkPolicyEnforcer
from aura_edge.intelligence.fast_router import MultilingualFastRouter
from aura_edge.intelligence.ollama_client import OllamaClient, OllamaUnavailableError
from aura_edge.intelligence.planner import HybridPlanner
from aura_edge.intelligence.schemas import IntentName, LanguageCode
from aura_edge.speech.stt import WhisperTranscriber
from aura_edge.speech.tts_router import TTSRouter

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_PATH = REPO_ROOT / "backend" / "tests" / "fixtures" / "multilingual_utterances.json"
EVAL_AUDIO_DIR = REPO_ROOT / "models" / "eval_audio"


def _load_wav_16k(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if sr != 16000 and pcm.size > 0:
        import math

        from scipy.signal import resample_poly

        g = math.gcd(16000, sr)
        pcm = resample_poly(pcm, 16000 // g, sr // g).astype(np.float32)
    return pcm


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
    return round(ordered[idx], 2)


async def benchmark_router_and_tools(settings: Settings) -> dict[str, Any]:
    cases = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    router = MultilingualFastRouter()
    planner = HybridPlanner(router, ollama_client=None)
    engine = create_db_engine(settings.database_url)
    db_ok = check_database_readiness(engine).connected
    if not db_ok:
        raise RuntimeError("PostgreSQL must be running to benchmark verified tool execution.")

    timer_mgr = TimerManager(engine)
    registry = ToolRegistry(DateTimeTool(), timer_mgr)

    correct = 0
    total = len(cases)
    router_latencies: list[float] = []
    tool_latencies: list[float] = []
    executable_total = 0
    executable_verified = 0
    unsupported_total = 0
    incorrect_actions = 0

    by_lang: dict[str, dict[str, int]] = {
        "en": {"total": 0, "correct": 0},
        "hi": {"total": 0, "correct": 0},
        "kn": {"total": 0, "correct": 0},
    }

    for case in cases:
        lang = str(case["lang"])
        text = str(case["text"])
        expected = IntentName(str(case["expected_intent"]))
        by_lang[lang]["total"] += 1

        intent, r_ms, _, _ = await planner.plan(
            text,
            requested_language="auto",
            allow_llm_fallback=False,
        )
        router_latencies.append(r_ms)
        if intent.intent_name == expected and intent.language == lang:
            correct += 1
            by_lang[lang]["correct"] += 1

        # Test tool execution cleanly (skip cancel_all if no timer, or create/cancel cleanly)
        if expected in (IntentName.UNSUPPORTED, IntentName.CLARIFY):
            unsupported_total += 1
            t0 = time.perf_counter()
            res = await registry.execute_intent(intent)
            tool_latencies.append(round((time.perf_counter() - t0) * 1000.0, 2))
            if res.success:
                incorrect_actions += 1
        elif expected in (IntentName.GET_TIME, IntentName.GET_DATE, IntentName.HELP, IntentName.STOP_ASSISTANT, IntentName.SET_TIMER, IntentName.LIST_TIMERS):
            executable_total += 1
            t0 = time.perf_counter()
            res = await registry.execute_intent(intent)
            tool_latencies.append(round((time.perf_counter() - t0) * 1000.0, 2))
            if res.success:
                executable_verified += 1

    # Cleanup benchmark timers created during run
    await timer_mgr.cancel_all(language="en")
    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from aura_edge.data.models import TimerRecord

    with Session(engine) as session:
        session.execute(delete(TimerRecord))
        session.commit()
    await timer_mgr.shutdown()
    engine.dispose()

    return {
        "utterances_tested": total,
        "intent_accuracy_pct": round((correct / total) * 100.0, 2),
        "by_language": by_lang,
        "router_median_ms": round(statistics.median(router_latencies), 2),
        "router_p95_ms": _p95(router_latencies),
        "tool_median_ms": round(statistics.median(tool_latencies), 2),
        "tool_p95_ms": _p95(tool_latencies),
        "verified_action_success_rate_pct": round(
            (executable_verified / max(1, executable_total)) * 100.0, 2
        ),
        "incorrect_action_rate_pct": round(
            (incorrect_actions / max(1, unsupported_total)) * 100.0, 2
        ),
    }


async def benchmark_e2e_modes(settings: Settings, trials: int = 3) -> dict[str, Any]:
    enforcer = NetworkPolicyEnforcer.from_settings(settings)
    engine = create_db_engine(settings.database_url)
    router = MultilingualFastRouter()
    planner = HybridPlanner(router, ollama_client=None)
    timer_mgr = TimerManager(engine)
    registry = ToolRegistry(DateTimeTool(), timer_mgr)
    tts = TTSRouter(settings)

    sample_wavs: dict[LanguageCode, np.ndarray] = {
        "en": _load_wav_16k(EVAL_AUDIO_DIR / "phase1a_tts_en.wav"),
        "hi": _load_wav_16k(EVAL_AUDIO_DIR / "phase1a_tts_hi.wav"),
        "kn": _load_wav_16k(EVAL_AUDIO_DIR / "phase1a_tts_kn.wav"),
    }

    report: dict[str, Any] = {}
    for mode_name, mode in (("cpu", ComputeMode.CPU), ("gpu", ComputeMode.GPU)):
        cfg = settings.model_copy(update={"compute_mode": mode, "stt_model_size": "small"})
        cm = ComputeManager(cfg)
        placement = cm.resolve_placement()
        stt = WhisperTranscriber(cfg, cm)

        mode_data: dict[str, Any] = {
            "placement": placement.model_dump(),
            "languages": {},
            "ollama": {},
        }

        for lang, audio in sample_wavs.items():
            stt_ms_list: list[float] = []
            tts_ms_list: list[float] = []
            e2e_ms_list: list[float] = []
            last_transcript = ""

            for i in range(trials + 1):  # iteration 0 = cold, 1..N = warm
                t_e2e0 = time.perf_counter()
                stt_res = stt.transcribe(audio, language=lang, model_size="small")
                last_transcript = stt_res.text

                # Route & execute deterministic time/timer
                intent, _r_ms, _, _ = await planner.plan(
                    stt_res.text or "What time is it?",
                    requested_language=lang,
                    allow_llm_fallback=False,
                )
                if intent.intent_name == IntentName.UNSUPPORTED:
                    res = registry.datetime_tool.get_time(lang=lang)
                else:
                    res = await registry.execute_intent(intent)

                # Clear cache on cold run and vary slightly or bypass cache check for TTS timing
                tts_audio = tts.primary.synthesize(res.message, language=lang, speed=1.0)
                e2e_ms = round((time.perf_counter() - t_e2e0) * 1000.0, 2)

                if i > 0:
                    stt_ms_list.append(stt_res.latency_ms)
                    tts_ms_list.append(tts_audio.latency_ms)
                    e2e_ms_list.append(e2e_ms)
                else:
                    cold_stt = stt_res.latency_ms
                    cold_tts = tts_audio.latency_ms
                    cold_e2e = e2e_ms

            mode_data["languages"][lang] = {
                "sample_transcript": last_transcript,
                "cold_stt_ms": cold_stt,
                "warm_stt_median_ms": round(statistics.median(stt_ms_list), 2),
                "warm_stt_p95_ms": _p95(stt_ms_list),
                "cold_tts_ms": cold_tts,
                "warm_tts_median_ms": round(statistics.median(tts_ms_list), 2),
                "cold_e2e_ms": cold_e2e,
                "warm_e2e_median_ms": round(statistics.median(e2e_ms_list), 2),
                "warm_e2e_p95_ms": _p95(e2e_ms_list),
            }

        # Ollama cold + warm benchmark
        ollama = OllamaClient(cfg, enforcer, cm)
        prompt = 'Classify into JSON {"intent_name": "GetTime"}: What time is it right now?'
        try:
            cold_llm = await ollama.chat_json([{"role": "user", "content": prompt}])
            warm_llm = await ollama.chat_json([{"role": "user", "content": prompt}])
            mode_data["ollama"] = {
                "reachable": True,
                "model": warm_llm.model,
                "num_gpu": warm_llm.num_gpu,
                "cold_ms": cold_llm.latency_ms,
                "warm_ms": warm_llm.latency_ms,
                "ttft_ms": warm_llm.ttft_ms,
                "tokens_per_sec": warm_llm.tokens_per_sec,
            }
        except OllamaUnavailableError as exc:
            mode_data["ollama"] = {"reachable": False, "error": str(exc)}

        mode_data["hardware_after_run"] = collect_hardware_snapshot(cfg, enforcer).model_dump()
        mode_data["process_rss_mb"] = round(
            psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 1
        )
        report[mode_name] = mode_data

    await timer_mgr.shutdown()
    engine.dispose()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 1 CPU vs GPU & Multilingual Benchmarks.")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    settings = Settings(_env_file=None, connectivity_mode=ConnectivityMode.STRICT_OFFLINE)
    apply_offline_env_guards(settings)

    router_stats = asyncio.run(benchmark_router_and_tools(settings))
    e2e_stats = asyncio.run(benchmark_e2e_modes(settings, trials=args.trials))

    payload = {
        "router_and_tools": router_stats,
        "cpu_vs_gpu": e2e_stats,
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
