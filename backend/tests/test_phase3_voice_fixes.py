"""Focused regression tests for the 3 remaining voice stabilization defects."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import numpy as np
from fastapi.testclient import TestClient

from aura_edge.config import ComputeMode, ConnectivityMode, Settings
from aura_edge.intelligence.fast_router import MultilingualFastRouter
from aura_edge.intelligence.normalizer import normalize_utterance
from aura_edge.intelligence.schemas import IntentName
from aura_edge.main import create_app
from aura_edge.speech.playback import PlaybackMetrics
from aura_edge.speech.stt import TranscriptionResult


def _offline_settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        connectivity_mode=ConnectivityMode.STRICT_OFFLINE,
        compute_mode=ComputeMode.CPU,
        audio_post_playback_cooldown_sec=overrides.pop("cooldown", 0.0),
        **overrides,
    )


def test_kannada_time_word_not_corrupted_into_timer() -> None:
    norm = normalize_utterance("ಟೈಮ್ ಎಷ್ಟು", requested_language="kn")
    assert norm.normalized == "ಟೈಮ್ ಎಷ್ಟು"

    router = MultilingualFastRouter()
    intent, _ms = router.route("ಟೈಮ್ ಎಷ್ಟು", requested_language="kn")
    assert intent is not None
    assert intent.intent_name == IntentName.GET_TIME

    # Preserves genuine Kannada ASR timer slip normalization
    timer_norm = normalize_utterance("5 ನಿಮಿಷ ಟೈಮ ಇಡು", requested_language="kn")
    assert "ಟೈಮರ್" in timer_norm.normalized


def test_mid_stt_cancellation_emits_response_completed_once(monkeypatch: Any) -> None:
    app = create_app(_offline_settings())
    with TestClient(app):
        orch = app.state.orchestrator
        events: list[tuple[str, dict[str, Any]]] = []
        orig_broadcast = orch.ws_hub.broadcast

        async def _spy_broadcast(event_type: str, payload: dict[str, Any]) -> None:
            events.append((event_type, dict(payload)))
            await orig_broadcast(event_type, payload)

        monkeypatch.setattr(orch.ws_hub, "broadcast", _spy_broadcast)

        entered_stt = threading.Event()

        def _slow_transcribe(_audio: np.ndarray, **_kwargs: Any) -> TranscriptionResult:
            entered_stt.set()
            time.sleep(0.18)
            return TranscriptionResult(
                text="what time is it",
                detected_language="en",
                language_probability=0.95,
                model_size="small",
                device="cpu",
                compute_type="int8",
                latency_ms=180.0,
                is_empty=False,
            )

        monkeypatch.setattr(orch.stt, "transcribe", _slow_transcribe)

        async def _run() -> Any:
            turn_task = asyncio.create_task(
                orch.process_audio_samples(
                    np.ones(1600, dtype=np.float32) * 0.1,
                    source="voice_ptt",
                    speak=False,
                )
            )
            while not entered_stt.is_set():
                await asyncio.sleep(0.01)
            await orch.cancel_turn()
            return await turn_task

        out = asyncio.run(_run())
        assert out.outcome == "cancelled"
        assert out.status == "cancelled"

        completed_events = [p for kind, p in events if kind == "response_completed" and p["turn_id"] == out.turn_id]
        assert len(completed_events) == 1
        assert completed_events[0]["outcome"] == "cancelled"
        assert completed_events[0]["status"] == "cancelled"


def test_cancel_turn_waits_for_lock_and_resumes_mic_once(monkeypatch: Any) -> None:
    app = create_app(_offline_settings(cooldown=0.65))
    with TestClient(app):
        orch = app.state.orchestrator
        orch.audio_capture.set_user_muted(True)

        resume_calls = {"count": 0}
        orig_resume = orch.audio_capture.resume_after_playback

        def _spy_resume() -> None:
            resume_calls["count"] += 1
            orig_resume()

        monkeypatch.setattr(orch.audio_capture, "resume_after_playback", _spy_resume)

        entered_speak = asyncio.Event()

        async def _slow_speak(*_args: Any, **kwargs: Any) -> tuple[list[Any], PlaybackMetrics]:
            entered_speak.set()
            cancel_ev = kwargs.get("external_cancel")
            for _ in range(30):
                if cancel_ev is not None and cancel_ev.is_set():
                    break
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.25)
            return (
                [],
                PlaybackMetrics(
                    tts_synthesis_ms=5.0,
                    audio_playback_ms=120.0,
                    first_audio_ready_ms=5.0,
                    cache_hits=0,
                    chunks_spoken=1,
                    audio_startup_ms=6.0,
                ),
            )

        monkeypatch.setattr(orch.playback, "speak_text_with_metrics", _slow_speak)

        async def _run() -> dict[str, Any]:
            turn_task = asyncio.create_task(orch.process_text("Hello", speak=True))
            await entered_speak.wait()
            cancel_res = await orch.cancel_turn()
            # _turn_lock must already be released when cancel_turn returns
            assert orch._turn_lock.locked() is False
            await turn_task
            return cancel_res

        cancel_res = asyncio.run(_run())
        assert cancel_res["cancelled"] is True
        assert resume_calls["count"] == 1
        assert orch.audio_capture.is_muted is True
        assert orch.audio_capture.is_suspended is False
