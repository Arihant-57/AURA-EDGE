"""Focused tests for R2/R4/R5/R7/R8 orchestrator outcomes, streaming guards, cancellation, and timings."""

from __future__ import annotations

import asyncio
import json
import math
import sys
import threading
import time
import types
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from aura_edge.config import ComputeMode, ConnectivityMode, Settings
from aura_edge.execution.state_machine import AssistantState
from aura_edge.infrastructure.compute import ComputeManager
from aura_edge.infrastructure.network_policy import NetworkPolicyEnforcer
from aura_edge.intelligence.ollama_client import OllamaClient
from aura_edge.main import create_app
from aura_edge.speech.playback import PlaybackMetrics
from aura_edge.speech.stt import TranscriptionResult


def _make_offline_app() -> Any:
    cfg = Settings(
        _env_file=None,
        connectivity_mode=ConnectivityMode.STRICT_OFFLINE,
        compute_mode=ComputeMode.CPU,
        audio_post_playback_cooldown_sec=0.0,
    )
    return create_app(cfg)


@pytest.mark.parametrize(
    ("primary_text", "english_text", "preferred", "expected", "expected_calls", "expected_detected", "expected_reply"),
    [
        ("ಅದು ಹೇಗಿದೆ", "What time is it?", "auto", "GetTime", ["auto", "en"], "en", "en"),
        ("ಅದು ಹೇಗಿದೆ", "What time is it?", "kn", "GetTime", ["auto", "en"], "en", "kn"),
        ("ಈಗ ಎಷ್ಟು ಗಂಟೆ?", "What time is it?", "auto", "GetTime", ["auto"], "kn", "kn"),
        ("ಅದು ಹೇಗಿದೆ", "Turn on the bedroom light.", "auto", "Unsupported", ["auto", "en"], "kn", "kn"),
    ],
)
def test_short_misclassified_clock_request_can_retry_in_english(
    monkeypatch: Any, primary_text: str, english_text: str, preferred: str, expected: str,
    expected_calls: list[str], expected_detected: str, expected_reply: str,
) -> None:
    app = _make_offline_app()
    with TestClient(app) as client:
        orch = app.state.orchestrator
        calls = []
        client.put("/api/v1/voice/language", json={"language": preferred}).raise_for_status()

        def transcribe(_audio: np.ndarray, **kwargs: Any) -> TranscriptionResult:
            language = kwargs["language"]
            calls.append(language)
            english = language == "en"
            return TranscriptionResult(
                text=english_text if english else primary_text,
                detected_language="en" if english else "kn",
                model_size="small" if english else "indic-conformer-kn",
                avg_logprob=-0.45 if english else -1.15,
                language_probability=None if english else 0.42,
                device="cpu", compute_type="int8", latency_ms=20,
            )

        monkeypatch.setattr(orch.stt, "transcribe", transcribe)
        result = asyncio.run(orch.process_audio_samples(
            np.ones(16000, dtype=np.float32) * 0.1, source="voice_ptt", speak=False, allow_llm_fallback=False,
        ))
        assert calls == expected_calls
        assert result.intent == expected
        assert result.detected_language == expected_detected
        assert result.response_language == expected_reply
        assert result.action_executed == (expected == "GetTime")


def test_ollama_conversation_filters_split_thinking_tags(monkeypatch: Any) -> None:
    cfg = Settings(_env_file=None)
    ollama = OllamaClient(cfg, NetworkPolicyEnforcer(cfg), ComputeManager(cfg))

    async def chunks(*_args: Any, **_kwargs: Any):
        yield {"message": {"thinking": "structured reasoning"}, "done": False}
        for content in ("<th", "ink>hidden words</th", "ink>", "Here is ", "the answer."):
            yield {"message": {"content": content}, "done": False}
        yield {"message": {"content": "<think>unfinished reasoning"}, "done": True}

    monkeypatch.setattr(ollama, "stream_chat", chunks)

    async def collect() -> str:
        return "".join([token async for token, _ in ollama.stream_conversation([])])

    assert asyncio.run(collect()) == "Here is the answer."


def test_r2_decoupled_stt_language_and_capture_metrics_forwarding(monkeypatch: Any) -> None:
    app = _make_offline_app()
    captured_calls: list[dict[str, Any]] = []

    with TestClient(app) as client:
        orch = app.state.orchestrator
        # Set Kannada as preferred reply language, keeping recognition_mode="multilingual"
        lang_resp = client.put("/api/v1/voice/language", json={"language": "kn"})
        assert lang_resp.status_code == 200
        assert lang_resp.json()["recognition_mode"] == "multilingual"
        assert lang_resp.json()["stt_language"] == "auto"

        # Simulate capture metrics from AudioCapture
        metrics_stub = SimpleNamespace(
            voiced_ms=420.0,
            noise_floor_rms=0.008,
            speech_end_mono=time.perf_counter() - 0.02,
        )
        monkeypatch.setattr(
            type(orch.audio_capture),
            "last_capture_metrics",
            property(lambda _self: metrics_stub),
        )

        def _fake_transcribe(audio: np.ndarray, **kwargs: Any) -> TranscriptionResult:
            captured_calls.append(dict(kwargs))
            return TranscriptionResult(
                text="What is today's date?",
                detected_language="en",
                language_probability=0.94,
                model_size="small",
                device="cpu",
                compute_type="int8",
                latency_ms=12.0,
                is_empty=False,
            )

        monkeypatch.setattr(orch.stt, "transcribe", _fake_transcribe)

        out = asyncio.run(
            orch.process_audio_samples(
                np.ones(1600, dtype=np.float32) * 0.1,
                source="voice_ptt",
                speak=False,
            )
        )
        assert len(captured_calls) == 1
        assert captured_calls[0]["language"] == "auto"
        assert captured_calls[0]["preferred_reply_language"] == "kn"
        assert captured_calls[0]["recognition_mode"] == "multilingual"
        assert captured_calls[0]["strict_language"] is False
        assert captured_calls[0]["vad_voiced_ms"] == 420.0
        assert captured_calls[0]["noise_floor_rms"] == 0.008

        # English speech recognized as 'en', date fetched from system clock, replied in Kannada!
        assert out.detected_language == "en"
        assert out.preferred_reply_language == "kn"
        assert out.response_language == "kn"
        assert out.intent == "GetDate"
        assert out.outcome == "success"
        assert out.action_executed is True
        assert any("\u0C80" <= ch <= "\u0CFF" for ch in out.response_text)
        assert out.stage_timestamps["speech_end"] is not None
        assert out.stage_timestamps["stt_start"] is not None
        assert out.stage_timestamps["stt_completion"] is not None
        assert out.stage_timestamps["routing_completion"] is not None
        assert out.stage_timestamps["tool_completion"] is not None
        assert out.total_turn_duration_ms is not None and out.total_turn_duration_ms >= 0.0

        # Explicit strict recognition mode passes target language & strict_language=True
        client.put("/api/v1/voice/language", json={"language": "kn", "recognition_mode": "strict"})
        asyncio.run(
            orch.process_audio_samples(
                np.ones(1600, dtype=np.float32) * 0.1,
                source="voice_ptt",
                speak=False,
            )
        )
        assert captured_calls[-1]["language"] == "kn"
        assert captured_calls[-1]["recognition_mode"] == "strict"
        assert captured_calls[-1]["strict_language"] is True

        # The previous microphone endpoint and noise measurements belong to
        # that capture, not to this independent uploaded WAV.
        uploaded = asyncio.run(orch.process_audio_samples(
            np.ones(1600, dtype=np.float32) * 0.1, language="auto", recognition_mode="multilingual",
        ))
        assert captured_calls[-1]["vad_voiced_ms"] is None
        assert captured_calls[-1]["noise_floor_rms"] is None
        assert uploaded.stage_timestamps["speech_end"] is None
        assert uploaded.response_language == "en"
        assert orch.session_language == "kn" and orch.stt_recognition_mode == "strict"


def test_r4_quality_gate_silence_vs_unclear_speech(monkeypatch: Any) -> None:
    app = _make_offline_app()

    with TestClient(app) as client:
        orch = app.state.orchestrator
        planner_called = {"count": 0}
        speak_called = {"count": 0}

        orig_plan = orch.planner.plan

        async def _spy_plan(*args: Any, **kwargs: Any) -> Any:
            planner_called["count"] += 1
            return await orig_plan(*args, **kwargs)

        async def _spy_speak(*args: Any, **kwargs: Any) -> Any:
            speak_called["count"] += 1
            return (
                [],
                PlaybackMetrics(
                    tts_synthesis_ms=2.0,
                    audio_playback_ms=15.0,
                    first_audio_ready_ms=3.0,
                    cache_hits=0,
                    chunks_spoken=1,
                    audio_startup_ms=4.0,
                ),
            )

        monkeypatch.setattr(orch.planner, "plan", _spy_plan)
        monkeypatch.setattr(orch.playback, "speak_text_with_metrics", _spy_speak)

        with client.websocket_connect("/api/v1/ws/live") as ws:
            ws.receive_json()  # initial IDLE

            # 1. Silence / Noise -> do NOTHING (no Qwen, no speak, no clarification)
            def _noise_transcribe(_audio: np.ndarray, **_kwargs: Any) -> TranscriptionResult:
                res = TranscriptionResult(
                    text="Thanks for watching!",
                    detected_language="en",
                    language_probability=0.3,
                    model_size="small",
                    device="cpu",
                    compute_type="int8",
                    latency_ms=5.0,
                    is_empty=False,
                )
                # Simulate Agent 1 R4 quality gate attributes
                object.__setattr__(res, "quality_ok", False)
                object.__setattr__(res, "rejection_reason", "noise")
                return res

            monkeypatch.setattr(orch.stt, "transcribe", _noise_transcribe)
            silent_out = asyncio.run(
                orch.process_audio_samples(np.zeros(1600, dtype=np.float32), speak=True)
            )
            assert planner_called["count"] == 0
            assert speak_called["count"] == 0
            assert silent_out.outcome == "success"
            assert silent_out.status == "ignored_silence"
            assert silent_out.action_executed is False
            assert silent_out.response_text == ""
            assert silent_out.spoken_text == ""
            assert orch.fsm.state == AssistantState.IDLE

            # 2. Unclear speech -> ONE short clarification, zero Qwen / device control
            def _unclear_transcribe(_audio: np.ndarray, **_kwargs: Any) -> TranscriptionResult:
                res = TranscriptionResult(
                    text="turn on",
                    detected_language="en",
                    language_probability=0.5,
                    model_size="small",
                    device="cpu",
                    compute_type="int8",
                    latency_ms=6.0,
                    is_empty=False,
                )
                object.__setattr__(res, "quality_ok", False)
                object.__setattr__(res, "rejection_reason", "unclear_speech")
                return res

            monkeypatch.setattr(orch.stt, "transcribe", _unclear_transcribe)
            clarify_out = asyncio.run(
                orch.process_audio_samples(np.ones(1600, dtype=np.float32) * 0.05, speak=True)
            )
            assert planner_called["count"] == 0
            assert speak_called["count"] == 1
            assert clarify_out.outcome == "clarification"
            assert clarify_out.status == "clarification_required"
            assert clarify_out.action_executed is False
            assert clarify_out.confidence == 0.0
            assert clarify_out.spoken_text == clarify_out.response_text
            assert orch.fsm.state == AssistantState.IDLE

            # Verify WS events emitted cleanly
            events = []
            for _ in range(15):
                try:
                    ev = ws.receive_json()
                    events.append(ev)
                    if (
                        ev["type"] == "response_completed"
                        and ev["payload"].get("turn_id") == clarify_out.turn_id
                    ):
                        break
                except (RuntimeError, OSError, ValueError, TimeoutError):
                    break
            completed_events = [e for e in events if e["type"] == "response_completed"]
            assert any(e["payload"].get("silent") is True for e in completed_events)
            assert any(
                e["payload"].get("outcome") == "clarification"
                and e["payload"].get("action_executed") is False
                for e in completed_events
            )


def test_r5_every_turn_emits_response_completed_after_playback_and_clarifies_ambiguous(
    monkeypatch: Any,
) -> None:
    app = _make_offline_app()

    with TestClient(app) as client:
        orch = app.state.orchestrator
        timeline: list[str] = []

        orig_broadcast = orch.ws_hub.broadcast

        async def _spy_broadcast(event_type: str, payload: dict[str, Any]) -> None:
            if event_type in ("response_completed", "clarification_required", "action_verified"):
                timeline.append(f"ws:{event_type}:{payload.get('intent', payload.get('action', ''))}")
            await orig_broadcast(event_type, payload)

        async def _spy_speak_metrics(
            text: str, *, language: str = "en", **_kwargs: Any
        ) -> tuple[list[Any], PlaybackMetrics]:
            timeline.append(f"speak:{language}:{text[:20]}")
            return (
                [],
                PlaybackMetrics(
                    tts_synthesis_ms=3.0,
                    audio_playback_ms=10.0,
                    first_audio_ready_ms=4.0,
                    cache_hits=0,
                    chunks_spoken=1,
                    audio_startup_ms=5.0,
                ),
            )

        monkeypatch.setattr(orch.ws_hub, "broadcast", _spy_broadcast)
        monkeypatch.setattr(orch.playback, "speak_text_with_metrics", _spy_speak_metrics)

        with client.websocket_connect("/api/v1/ws/live") as ws:
            ws.receive_json()

            # Deterministic tool (GetTime) -> speak happens BEFORE response_completed
            timeline.clear()
            r_time = client.post(
                "/api/v1/intent/process", json={"text": "What's the time?", "speak": True}
            ).json()
            assert r_time["outcome"] == "success"
            assert r_time["action_executed"] is True
            assert timeline.index("ws:action_verified:GetTime") < next(
                i for i, x in enumerate(timeline) if x.startswith("speak:")
            )
            assert next(i for i, x in enumerate(timeline) if x.startswith("speak:")) < timeline.index(
                "ws:response_completed:GetTime"
            )

            # Ambiguous device -> VERIFYING -> CLARIFYING + clarification_required + response_completed
            timeline.clear()
            r_amb = client.post(
                "/api/v1/intent/process", json={"text": "Turn on the fan", "speak": True}
            ).json()
            assert r_amb["outcome"] == "clarification"
            assert r_amb["status"] == "clarification_required"
            assert r_amb["action_executed"] is False
            assert "ws:clarification_required:" in timeline
            assert "ws:response_completed:DeviceControl" in timeline
            assert next(i for i, x in enumerate(timeline) if x.startswith("speak:")) < timeline.index(
                "ws:response_completed:DeviceControl"
            )

            # Unsupported refusal -> emits response_completed(outcome="refusal")
            timeline.clear()
            r_ref = client.post(
                "/api/v1/intent/process", json={"text": "Order pizza online", "speak": True}
            ).json()
            assert r_ref["outcome"] == "refusal"
            assert r_ref["action_executed"] is False
            assert "ws:response_completed:Unsupported" in timeline

            # Greeting -> emits response_completed AFTER speak, action_executed=False
            timeline.clear()
            r_hi = client.post("/api/v1/intent/process", json={"text": "Hello", "speak": True}).json()
            assert r_hi["outcome"] == "success"
            assert r_hi["action_executed"] is False
            assert next(i for i, x in enumerate(timeline) if x.startswith("speak:")) < timeline.index(
                "ws:response_completed:Greeting"
            )

            # Verify WS payload carries monotonic stage_timestamps & timings
            found_payload: dict[str, Any] | None = None
            for _ in range(30):
                ev = ws.receive_json()
                if ev["type"] == "response_completed" and ev["payload"].get("turn_id") == r_time["turn_id"]:
                    found_payload = ev["payload"]
                    break
            assert found_payload is not None
            assert found_payload["outcome"] == "success"
            assert found_payload["action_executed"] is True
            assert "timings" in found_payload
            assert "stage_timestamps" in found_payload
            assert found_payload["stage_timestamps"]["playback_completion"] is not None


def test_r8_streaming_per_chunk_validation_and_spoken_text_parity(monkeypatch: Any) -> None:
    app = _make_offline_app()

    with TestClient(app) as client:
        orch = app.state.orchestrator
        queued_sentences: list[str] = []

        async def _fake_stream_sentence(
            sentence_queue: asyncio.Queue[str | None], **_kwargs: Any
        ) -> tuple[list[Any], PlaybackMetrics]:
            while True:
                item = await sentence_queue.get()
                if item is None:
                    break
                queued_sentences.append(item)
            return (
                [],
                PlaybackMetrics(
                    tts_synthesis_ms=4.0,
                    audio_playback_ms=20.0,
                    first_audio_ready_ms=5.0,
                    cache_hits=0,
                    chunks_spoken=len(queued_sentences),
                    audio_startup_ms=6.0,
                ),
            )

        async def _fake_static_speak(
            text: str, **_kwargs: Any
        ) -> tuple[list[Any], PlaybackMetrics]:
            queued_sentences.append(text)
            return (
                [],
                PlaybackMetrics(
                    tts_synthesis_ms=3.0,
                    audio_playback_ms=12.0,
                    first_audio_ready_ms=4.0,
                    cache_hits=0,
                    chunks_spoken=1,
                    audio_startup_ms=5.0,
                ),
            )

        monkeypatch.setattr(orch.playback, "speak_sentence_stream", _fake_stream_sentence)
        monkeypatch.setattr(orch.playback, "speak_text_with_metrics", _fake_static_speak)

        # Case 1 (EN): Sentence 1 valid, Sentence 2 invalid Tagalog claim -> Sentence 2 MUST NOT be spoken!
        async def _stream_en_mixed(_messages: Any, **_kwargs: Any):
            yield "The sky looks bright blue today. ", None
            yield "I also support Tagalog and Spanish.", None
            yield "", {"ttft_ms": 15.0, "latency_ms": 30.0, "eval_count": 10, "tokens_per_sec": 35.0}

        queued_sentences.clear()
        monkeypatch.setattr(orch.ollama_client, "stream_conversation", _stream_en_mixed)
        res1 = client.post(
            "/api/v1/intent/process", json={"text": "Tell me about the sky", "language": "en", "speak": True}
        ).json()
        assert queued_sentences == ["The sky looks bright blue today."]
        assert res1["spoken_text"] == "The sky looks bright blue today."
        assert res1["response_text"] == res1["spoken_text"]
        assert "Tagalog" not in res1["response_text"]

        # Case 2 (EN): Sentence 1 itself is invalid Tagalog claim -> stream TTS must NOT start early!
        async def _stream_en_bad_first(_messages: Any, **_kwargs: Any):
            yield "I support Tagalog and English.", None
            yield "", {"ttft_ms": 10.0, "latency_ms": 20.0, "eval_count": 6, "tokens_per_sec": 30.0}

        queued_sentences.clear()
        monkeypatch.setattr(orch.ollama_client, "stream_conversation", _stream_en_bad_first)
        res2 = client.post(
            "/api/v1/intent/process", json={"text": "Which languages work?", "language": "en", "speak": True}
        ).json()
        assert len(queued_sentences) == 1
        assert "Tagalog" not in queued_sentences[0]
        assert res2["spoken_text"] == res2["response_text"]

        # Case 3 (KN): Sentence 1 valid Kannada, Sentence 2 drifts to English -> Sentence 2 never spoken!
        async def _stream_kn_drift(_messages: Any, **_kwargs: Any):
            yield "ಆಕಾಶವು ನೀಲಿಯಾಗಿ ಕಾಣುತ್ತದೆ. ", None
            yield "Rayleigh scattering causes blue sky.", None
            yield "", {"ttft_ms": 12.0, "latency_ms": 28.0, "eval_count": 10, "tokens_per_sec": 30.0}

        queued_sentences.clear()
        monkeypatch.setattr(orch.ollama_client, "stream_conversation", _stream_kn_drift)
        res3 = client.post(
            "/api/v1/intent/process", json={"text": "ಆಕಾಶ ಏಕೆ ನೀಲಿ?", "language": "kn", "speak": True}
        ).json()
        assert queued_sentences == ["ಆಕಾಶವು ನೀಲಿಯಾಗಿ ಕಾಣುತ್ತದೆ."]
        assert res3["spoken_text"] == "ಆಕಾಶವು ನೀಲಿಯಾಗಿ ಕಾಣುತ್ತದೆ."
        assert res3["response_text"] == res3["spoken_text"]

        async def two_replies(_messages: Any, **_kwargs: Any):
            yield "The sky looks bright blue today. ", None
            yield "Sunlight scatters through the atmosphere.", None
            yield "", {"ttft_ms": 12.0}

        queued_sentences.clear()
        monkeypatch.setattr(orch.ollama_client, "stream_conversation", two_replies)
        result = client.post(
            "/api/v1/intent/process", json={"text": "Tell me about the sky", "language": "en", "speak": True}
        ).json()
        assert queued_sentences == ["The sky looks bright blue today. Sunlight scatters through the atmosphere."]
        assert result["spoken_text"] == result["response_text"] == queued_sentences[0]


def test_r7_r8_ollama_cancellation_and_half_duplex_turn_serialization(monkeypatch: Any) -> None:
    # 1. Ollama stream_conversation aborts immediately when cancel_event is set mid-wait
    cfg = Settings(_env_file=None, ollama_timeout_sec=10.0, compute_mode=ComputeMode.CPU)
    enforcer = NetworkPolicyEnforcer(cfg)
    client_obj = OllamaClient(cfg, enforcer, ComputeManager(cfg))

    async def _hang_handler(request: httpx.Request) -> httpx.Response:
        class _HangingStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield json.dumps({"message": {"content": "Hello "}, "done": False}).encode() + b"\n"
                await asyncio.sleep(5.0)

        return httpx.Response(200, stream=_HangingStream())

    orig_build = httpx.AsyncClient

    def _mock_build_client(*_args: Any, **_kwargs: Any) -> httpx.AsyncClient:
        return orig_build(transport=httpx.MockTransport(_hang_handler))

    monkeypatch.setattr("aura_edge.intelligence.ollama_client.build_async_http_client", _mock_build_client)

    async def _run_cancel_stream() -> float:
        ev = asyncio.Event()
        t0 = time.perf_counter()
        chunks: list[str] = []

        async def _trigger_cancel() -> None:
            await asyncio.sleep(0.05)
            ev.set()

        canceller = asyncio.create_task(_trigger_cancel())
        try:
            async for tok, _stats in client_obj.stream_conversation(
                [{"role": "user", "content": "hi"}],
                cancel_event=ev,
            ):
                if tok:
                    chunks.append(tok)
        finally:
            await canceller
        elapsed = time.perf_counter() - t0
        assert chunks == ["Hello "]
        return elapsed

    elapsed_cancel = asyncio.run(_run_cancel_stream())
    assert elapsed_cancel < 0.6  # Aborted promptly without waiting 5s or 10s timeout

    # 2. Half-duplex cancel_turn stops playback BEFORE resuming mic, and confidence=0.0 on cancel
    app = _make_offline_app()
    with TestClient(app):
        orch = app.state.orchestrator
        order: list[str] = []

        orig_stop = orch.playback.stop
        orig_resume = orch.audio_capture.resume_after_playback

        def _spy_stop() -> None:
            order.append("playback.stop")
            orig_stop()

        def _spy_resume() -> None:
            order.append("audio.resume")
            orig_resume()

        monkeypatch.setattr(orch.playback, "stop", _spy_stop)
        monkeypatch.setattr(orch.audio_capture, "resume_after_playback", _spy_resume)

        async def _run_half_duplex_cancel() -> None:
            ctx = await orch.fsm.start_turn(
                source="voice_ptt", language="en", initial_state=AssistantState.SPEAKING
            )
            assert orch.audio_capture.is_suspended is True
            res = await orch.cancel_turn(turn_id=ctx.turn_id)
            assert res["cancelled"] is True
            assert order[0] == "playback.stop"
            assert "audio.resume" in order
            assert order.index("playback.stop") < order.index("audio.resume")
            cancelled = orch._cancelled_output(ctx, time.perf_counter())
            assert cancelled.confidence == 0.0
            assert cancelled.outcome == "cancelled"

        asyncio.run(_run_half_duplex_cancel())


def _load_wav_16k(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if sr != 16000 and pcm.size > 0:
        from scipy.signal import resample_poly

        g = math.gcd(16000, sr)
        pcm = resample_poly(pcm, 16000 // g, sr // g).astype(np.float32)
    return pcm


def test_cross_lingual_real_wav_en_and_hi_speech_to_kannada_reply() -> None:
    """Real WhisperTranscriber + VoiceOrchestrator: EN & HI speech reply in Kannada when preferred."""
    cfg = Settings(
        _env_file=None,
        connectivity_mode=ConnectivityMode.STRICT_OFFLINE,
        compute_mode=ComputeMode.CPU,
        stt_model_size="small",
        audio_post_playback_cooldown_sec=0.0,
    )
    app = create_app(cfg)
    with TestClient(app) as client:
        orch = app.state.orchestrator
        assert client.put("/api/v1/voice/language", json={"language": "kn"}).status_code == 200
        assert client.portal is not None

        en_audio = _load_wav_16k(cfg.models_dir / "eval_audio" / "phase1a_tts_en.wav")
        en_out = client.portal.call(
            lambda: orch.process_audio_samples(en_audio, source="audio_upload", speak=False)
        )
        assert en_out.detected_language == "en"
        assert en_out.preferred_reply_language == "kn"
        assert en_out.response_language == "kn"
        assert en_out.outcome == "success"
        assert en_out.action_executed is True
        assert any("\u0C80" <= ch <= "\u0CFF" for ch in en_out.response_text)

        hi_audio = _load_wav_16k(cfg.models_dir / "eval_audio" / "phase1a_tts_hi.wav")
        hi_out = client.portal.call(
            lambda: orch.process_audio_samples(hi_audio, source="audio_upload", speak=False)
        )
        assert hi_out.detected_language == "hi"
        assert hi_out.preferred_reply_language == "kn"
        assert hi_out.response_language == "kn"
        assert hi_out.outcome == "success"
        assert hi_out.action_executed is True
        assert any("\u0C80" <= ch <= "\u0CFF" for ch in hi_out.response_text)

        for t in client.get("/api/v1/timers").json():
            client.delete(f"/api/v1/timers/{t['id']}")


def test_hinglish_and_kanglish_mixed_utterances_end_to_end() -> None:
    app = _make_offline_app()
    with TestClient(app) as client:
        cases = [
            ("aaj ki date kya hai", "GetDate"),
            ("bedroom ki light on karo", "DeviceControl"),
            ("ivattu tareekhu eshtu", "GetDate"),
            ("ಬೆಡ್‌ರೂಮ್ light on ಮಾಡು", "DeviceControl"),
            ("bedroom fan off maadu", "DeviceControl"),
        ]
        for text, expected_intent in cases:
            resp = client.post(
                "/api/v1/intent/process",
                json={"text": text, "language": "auto", "speak": False, "allow_llm_fallback": False},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["intent"] == expected_intent, f"{text!r} -> {body}"
            assert body["outcome"] == "success"
            assert body["action_executed"] is True


def test_concurrent_turn_cancellation_and_audio_callback_timing(monkeypatch: Any) -> None:
    """Verify monotonic stage_timestamps from simulated audio callback + concurrent request cancellation."""
    sim_state: dict[str, Any] = {"first_non_silent_mono": None}

    class _SimOutputStream:
        def __init__(self, *, samplerate: int, channels: int, callback: Any, **_kwargs: Any) -> None:
            self.samplerate = samplerate
            self.channels = channels
            self.callback = callback
            self.latency = 0.01
            self._stop_ev = threading.Event()
            self._thread: threading.Thread | None = None

        def _run(self) -> None:
            buf = np.zeros((512, self.channels), dtype=np.float32)
            while not self._stop_ev.is_set():
                buf.fill(0.0)
                self.callback(buf, 512, None, None)
                if sim_state["first_non_silent_mono"] is None and float(np.max(np.abs(buf[:, 0]))) > 1e-6:
                    sim_state["first_non_silent_mono"] = time.perf_counter()
                self._stop_ev.wait(0.006)

        def start(self) -> None:
            self._thread = threading.Thread(target=_run_proxy(self._run), daemon=True)
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

    def _run_proxy(fn: Any) -> Any:
        return fn

    fake_sd = types.SimpleNamespace(
        OutputStream=_SimOutputStream,
        query_devices=lambda *args, **kwargs: (
            {"default_samplerate": 16000.0, "max_output_channels": 2}
            if args
            else [{"name": "SimSpeaker", "max_output_channels": 2, "default_samplerate": 16000.0}]
        ),
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    app = _make_offline_app()
    with TestClient(app):
        orch = app.state.orchestrator

        async def _verify_callback_and_concurrency() -> None:
            # 1. Deterministic turn with simulated PortAudio callback populates all monotonic stage timestamps
            out = await orch.process_text("What time is it?", language="en", speak=True)
            assert out.outcome == "success"
            assert out.action_executed is True
            ts = out.stage_timestamps
            assert ts["routing_completion"] is not None
            assert ts["tool_completion"] is not None
            assert ts["first_valid_speech_chunk"] is not None
            assert ts["tts_completion"] is not None
            assert ts["first_non_silent_output_callback"] is not None
            assert ts["playback_completion"] is not None
            assert ts["routing_completion"] <= ts["tool_completion"] <= ts["first_valid_speech_chunk"]
            assert ts["first_valid_speech_chunk"] <= ts["tts_completion"]
            assert ts["first_non_silent_output_callback"] <= ts["playback_completion"]
            assert out.time_to_first_text_ms is not None and out.time_to_first_text_ms >= 0.0
            assert out.time_to_first_synthesized_audio_ms is not None
            assert out.time_to_first_audio_output_ms is not None
            assert out.playback_duration_ms is not None and out.playback_duration_ms > 0.0
            assert out.total_turn_duration_ms is not None and out.total_turn_duration_ms >= out.playback_duration_ms

            # 2. Mid-stream cancellation of slow streaming turn + immediate follow-up turn
            async def _slow_stream(_messages: Any, *, cancel_event: asyncio.Event | None = None, **_kwargs: Any):
                yield "First part. ", None
                if cancel_event is not None:
                    try:
                        await asyncio.wait_for(cancel_event.wait(), timeout=2.0)
                    except TimeoutError:
                        pass
                yield "", {"ttft_ms": 5.0, "latency_ms": 100.0, "eval_count": 2, "tokens_per_sec": 20.0}

            monkeypatch.setattr(orch.ollama_client, "stream_conversation", _slow_stream)
            slow_task = asyncio.create_task(orch.process_text("Explain gravity", language="en", speak=False))
            await asyncio.sleep(0.08)
            cancel_res = await orch.cancel_turn()
            assert cancel_res["cancelled"] is True
            slow_out = await slow_task
            assert slow_out.outcome == "cancelled"

            next_out = await orch.process_text("What is the time?", language="en", speak=False)
            assert next_out.outcome == "success"
            assert next_out.action_executed is True
            assert orch.fsm.state == AssistantState.IDLE

        asyncio.run(_verify_callback_and_concurrency())


def test_auto_hindi_retry_preserves_negation_and_mixed_speech(monkeypatch, tmp_path):
    app = _make_offline_app()
    (tmp_path / "model.int8.onnx").touch()
    (tmp_path / "tokens.txt").touch()
    with TestClient(app) as client:
        orch = app.state.orchestrator
        monkeypatch.setattr(orch.settings, "hindi_specialist_dir", tmp_path)
        pcm = np.ones(16000, dtype=np.float32) * 0.1
        for heard, probability, second_pass in (
            ("आ भी कित ने बज हे", 0.94, True),
            ("बेडरूम की लाइट चालू मत करो", 0.95, False),
            ("Bedroom की light", 0.96, False),
            ("आ भी कित ने बज हे", 0.45, False),
        ):
            calls = []
            def transcribe(_samples, **options):
                strict = options["strict_language"]
                calls.append(strict)
                return TranscriptionResult(text="अभी कितने बजे हैं" if strict else heard,
                    detected_language="hi", model_size="indic-conformer-hi" if strict else "small",
                    language_probability=None if strict else probability, latency_ms=12,
                    device="cpu", compute_type="int8")
            monkeypatch.setattr(orch.stt, "transcribe", transcribe)
            result = asyncio.run(orch.process_audio_samples(pcm, speak=False, allow_llm_fallback=False))
            assert calls == ([False, True] if second_pass else [False])
            if second_pass:
                assert result.intent == "GetTime" and result.recognition_mode == "multilingual"
                assert result.language_probability == probability
            else:
                assert not result.action_executed
