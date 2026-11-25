"""Focused acceptance tests for Phase 0-2 real-world voice, concurrency, language, and CLI bug fixes."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
from fastapi.testclient import TestClient

from aura_edge.config import ComputeMode, Settings
from aura_edge.intelligence.conversation import (
    _CONVERSATION_SYSTEM_PROMPT,
    validate_response_language,
)
from aura_edge.intelligence.schemas import LanguageCode
from aura_edge.main import create_app
from aura_edge.speech.audio_capture import AudioRouteMode
from aura_edge.speech.playback import AudioPlayback, prepare_pcm_for_playback
from aura_edge.speech.stt import TranscriptionResult
from aura_edge.speech.tts_backends.base import SynthesizedAudio
from aura_edge.speech.tts_router import TTSRouter

_CLI_PATH = Path(__file__).resolve().parents[1] / "scripts" / "voice_cli.py"
_SPEC = importlib.util.spec_from_file_location("aura_voice_cli_mod", _CLI_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_CLI_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["aura_voice_cli_mod"] = _CLI_MOD
_SPEC.loader.exec_module(_CLI_MOD)


class _DummyTtsBackend:
    name = "dummy_tts"

    def supports_language(self, _lang: LanguageCode) -> bool:
        return True

    def model_info(self, language: LanguageCode) -> tuple[str, str]:
        return (f"dummy-{language}", f"voice-{language}")

    def synthesize(self, text: str, *, language: LanguageCode, speed: float = 1.0) -> SynthesizedAudio:
        sr = 16000 if language == "kn" else 22050
        samples = np.sin(np.linspace(0, 8 * np.pi, int(sr * 0.12), dtype=np.float32)) * 0.2
        return SynthesizedAudio(
            samples=samples,
            sample_rate=sr,
            language=language,
            backend=self.name,
            model_name=f"dummy-{language}",
            voice_name=f"voice-{language}",
            latency_ms=2.5,
        )


def test_outdated_prompt_removed_and_short_deterministic_replies() -> None:
    assert "never pretend you can control smart-home lights" not in _CONVERSATION_SYSTEM_PROMPT.lower()
    assert "English, Hindi, and Kannada ONLY" in _CONVERSATION_SYSTEM_PROMPT

    cfg = Settings(_env_file=None, compute_mode=ComputeMode.CPU)
    app = create_app(cfg)
    with TestClient(app) as client:
        orch = app.state.orchestrator
        caps = orch.tool_registry.describe_capabilities("en")
        assert "SmartHomeService" in caps and "DateTimeTool" in caps and "TimerManager" in caps

        msgs = orch.conversation.build_llm_messages("Hi", reply_language="en", capability_summary=caps)
        assert "SmartHomeService" in msgs[0]["content"]

        # Short natural greetings, acknowledgments, identity, and supported languages (0 LLM calls)
        g = client.post("/api/v1/intent/process", json={"text": "Hello", "speak": False}).json()
        assert g["intent"] == "Greeting" and g["intent_source"] == "hassil_fast"
        assert len(g["response_text"]) <= 40

        ack = client.post("/api/v1/intent/process", json={"text": "Thank you", "speak": False}).json()
        assert ack["intent"] == "Acknowledgment" and len(ack["response_text"]) <= 30

        ident = client.post("/api/v1/intent/process", json={"text": "What is your name?", "speak": False}).json()
        assert ident["intent"] == "Identity" and "AURA EDGE" in ident["response_text"]
        assert len(ident["response_text"]) <= 60

        langs = client.post(
            "/api/v1/intent/process", json={"text": "What languages do you support?", "speak": False}
        ).json()
        assert langs["intent"] == "SupportedLanguages"
        assert "English, Hindi, and Kannada" in langs["response_text"]
        assert "Tagalog" not in langs["response_text"]


def test_explicit_language_enforcement_script_guard_and_switching(monkeypatch: Any) -> None:
    # Validator rejects Latin-as-Kannada and unsupported-language hallucinations like Tagalog
    ok_kn, _ = validate_response_language("ನಮಸ್ಕಾರ, ನಾನು ಸಹಾಯ ಮಾಡಬಲ್ಲೆ.", "kn")
    bad_kn, _ = validate_response_language("Hello, I support Tagalog too.", "kn")
    bad_en, reason_en = validate_response_language("I support Kannada, English, and Tagalog.", "en")
    assert ok_kn is True
    assert bad_kn is False
    assert bad_en is False and reason_en == "unsupported_language_claim"

    cfg = Settings(_env_file=None, compute_mode=ComputeMode.CPU)
    app = create_app(cfg)

    async def _fake_stream_bad_kn(self, _messages, **_kwargs):
        # Simulate Qwen slipping into English while session language is explicitly 'kn'
        yield "I only speak Tagalog and English.", None
        yield "", {"ttft_ms": 10.0, "latency_ms": 20.0, "eval_count": 5, "tokens_per_sec": 30.0}

    with TestClient(app) as client:
        orch = app.state.orchestrator
        monkeypatch.setattr(
            orch.ollama_client,
            "stream_conversation",
            _fake_stream_bad_kn.__get__(orch.ollama_client, type(orch.ollama_client)),
        )

        # STT must never leak unsupported Whisper code like 'tl'
        class _FakeWhisperTagalog:
            def transcribe(self, *_args, **_kwargs):
                return ([SimpleNamespace(text="hello friend")], SimpleNamespace(language="tl", language_probability=0.9))

        monkeypatch.setattr(orch.stt, "_load_model_locked", lambda _s: (_FakeWhisperTagalog(), "cpu", "int8", None))
        stt_res = orch.stt.transcribe(np.ones(1600, dtype=np.float32) * 0.2, language="auto")
        assert stt_res.detected_language == "en"

        # Switch language to Kannada via /voice/language and omit language in /intent/process
        assert client.put("/api/v1/voice/language", json={"language": "kn"}).status_code == 200
        kn_res = client.post(
            "/api/v1/intent/process",
            json={"text": "Tell me about space", "speak": False},
        ).json()
        assert kn_res["response_language"] == "kn"
        # Guardrail replaced wrong-script English reply with real Kannada text (not marked English, no Latin)
        assert any("\u0C80" <= ch <= "\u0CFF" for ch in kn_res["response_text"])

        # Switch -> Hindi -> English -> Auto
        assert client.put("/api/v1/voice/language", json={"language": "hi"}).json()["resolved_language"] == "hi"
        hi_hi = client.post("/api/v1/intent/process", json={"text": "नमस्ते", "speak": False}).json()
        assert hi_hi["response_language"] == "hi"
        assert client.put("/api/v1/voice/language", json={"language": "auto"}).json()["session_language"] == "auto"


def test_pcm_conditioning_exclusive_playback_and_feedback_suspension() -> None:
    # 1. PCM conditioning removes DC offset, avoids clipping, preserves fade & resamples cleanly
    raw = np.ones(4000, dtype=np.float32) * 0.1 + np.sin(np.linspace(0, 40 * np.pi, 4000, dtype=np.float32)) * 1.4
    pcm, sr = prepare_pcm_for_playback(raw, 22050, target_rate=48000, add_pause_ms=40.0)
    assert sr == 48000
    assert float(np.max(np.abs(pcm))) <= 0.95
    assert pcm[0] == 0.0 and abs(float(pcm[-1])) < 1e-4

    # 2. Exclusive playback owner prevents overlapping playback & blocks stale cancelled turns
    cfg = Settings(_env_file=None, audio_post_playback_cooldown_sec=0.05)
    tts = TTSRouter(cfg, primary_backend=_DummyTtsBackend())
    playback = AudioPlayback(tts)
    overlap_log: list[str] = []

    def _tracked_open(preferred_rate: int, buf: Any) -> tuple[Any, int]:
        overlap_log.append(f"start:{preferred_rate}")
        time.sleep(0.04)
        overlap_log.append(f"end:{preferred_rate}")
        raise RuntimeError("headless test stream")

    playback._open_stream_blocking = _tracked_open  # type: ignore[method-assign]

    async def _run_exclusive() -> None:
        t1 = asyncio.create_task(playback.speak_text("First sentence.", language="en", play_to_speaker=True))
        t2 = asyncio.create_task(playback.speak_text("Second sentence.", language="kn", play_to_speaker=True))
        await asyncio.gather(t1, t2)
        cancel_ev = asyncio.Event()
        cancel_ev.set()
        cancelled = await playback.speak_text("Should not play.", language="en", play_to_speaker=True, external_cancel=cancel_ev)
        assert cancelled == []

    asyncio.run(_run_exclusive())
    assert overlap_log in (
        ["start:22050", "end:22050", "start:16000", "end:16000"],
        ["start:16000", "end:16000", "start:22050", "end:22050"],
    )
    playback.close()

    # 3. Half-duplex microphone suspension drops echo frames & stays muted if user muted
    app = create_app(cfg)
    with TestClient(app):
        orch = app.state.orchestrator
        orch.audio_capture.suspend_for_playback()
        assert orch.audio_capture.is_suspended is True
        assert orch.audio_capture.is_muted is True
        orch.audio_capture.push_test_frame(np.ones(512, dtype=np.int16) * 2000)
        assert orch.audio_capture._frame_queue.empty()
        asyncio.run(orch._resume_mic_after_speaking(did_speak=True))
        assert orch.audio_capture.is_suspended is False
        assert orch.audio_capture.route_mode == AudioRouteMode.NORMAL

        # Sticky user mute survives playback cycle
        asyncio.run(orch.set_mic_mute(True))
        orch.audio_capture.suspend_for_playback()
        asyncio.run(orch._resume_mic_after_speaking(did_speak=False))
        assert orch.audio_capture.is_muted is True


def test_ten_consecutive_hands_free_turns_timers_smart_home_and_concurrency(monkeypatch: Any) -> None:
    cfg = Settings(_env_file=None, compute_mode=ComputeMode.CPU, audio_post_playback_cooldown_sec=0.0)
    app = create_app(cfg)

    with TestClient(app) as client:
        # Ensure smart-home voice commands & timers work together cleanly
        sh = client.post("/api/v1/intent/process", json={"text": "Turn on the living room light", "speak": False}).json()
        assert sh["intent"] == "DeviceControl" and sh["action_executed"] is True
        assert sh["data"]["device"]["state"]["power"] is True

        tm = client.post("/api/v1/intent/process", json={"text": "Set a 2 minute timer", "speak": False}).json()
        assert tm["intent"] == "SetTimer" and tm["action_executed"] is True
        tid = tm["data"]["timer"]["id"]
        assert client.delete(f"/api/v1/timers/{tid}").status_code == 200

        # 10 consecutive hands-free turns over continuous loop with synthetic capture on a single loop
    app2 = create_app(cfg)
    orch2 = app2.state.orchestrator
    utterances = [
        ("Hello", "en"),
        ("What is your name?", "en"),
        ("Turn on the bedroom fan", "en"),
        ("बेडरूम का पंखा बंद करो", "hi"),
        ("Switch to Kannada", "en"),
        ("ಈಗ ಎಷ್ಟು ಗಂಟೆ ಆಗಿದೆ?", "kn"),
        ("What languages do you support?", "en"),
        ("Set a 1 minute timer", "en"),
        ("Cancel all timers", "en"),
        ("Goodbye", "en"),
    ]
    idx = {"i": 0}
    completed_intents: list[str] = []

    async def _fake_ptt_capture(**_kwargs) -> None:
        i = idx["i"]
        if i < len(utterances):
            orch2.audio_capture._recorded_frames = [np.ones(1600, dtype=np.int16) * 500]
            orch2.audio_capture._last_capture_reason = "speech_end"
        else:
            orch2.audio_capture._recorded_frames = []
            orch2.audio_capture._last_capture_reason = "no_speech_timeout"
        await asyncio.sleep(0.01)

    def _fake_transcribe(_audio, **_kwargs) -> TranscriptionResult:
        i = idx["i"]
        idx["i"] += 1
        text, lang = utterances[i]
        return TranscriptionResult(
            text=text,
            detected_language=lang,  # type: ignore[arg-type]
            language_probability=0.99,
            model_size="fake",
            device="cpu",
            compute_type="int8",
            latency_ms=1.0,
            is_empty=False,
        )

    monkeypatch.setattr(orch2.audio_capture, "start_ptt_capture", _fake_ptt_capture)
    monkeypatch.setattr(orch2.stt, "transcribe", _fake_transcribe)

    orig_process = orch2._process_audio_with_turn

    async def _spy_process(*args, **kwargs):
        res = await orig_process(*args, **kwargs)
        completed_intents.append(res.intent)
        return res

    monkeypatch.setattr(orch2, "_process_audio_with_turn", _spy_process)

    async def _run_10_turns() -> None:
        await app2.state.smart_home.startup()
        await orch2.startup()
        try:
            started = await orch2.start_conversation(
                language="auto",
                speak=False,
                mode="continuous",
                open_hardware_stream=False,
                silence_timeout_sec=10.0,
            )
            assert started["active"] is True
            deadline = time.monotonic() + 8.0
            while orch2._conversation_active and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert orch2._conversation_active is False

            # Concurrent text requests serialize cleanly via _turn_lock without raising StaleTurnError
            r_a, r_b = await asyncio.gather(
                orch2.process_text("Hello", speak=False),
                orch2.process_text("What is your name?", speak=False),
            )
            assert {r_a.intent, r_b.intent} == {"Greeting", "Identity"}
        finally:
            await orch2.shutdown()
            await app2.state.smart_home.shutdown()

    asyncio.run(_run_10_turns())
    assert len(completed_intents) == 10
    assert completed_intents[-1] == "Farewell"


def test_cli_validation_and_turn_output_deduplication() -> None:
    parse = _CLI_MOD.parse_cli_command
    assert parse("/")["kind"] == "error"
    assert parse("/lang")["kind"] == "error" and "Usage:" in parse("/lang")["message"]
    assert parse("/lang xx")["kind"] == "error"
    assert parse("/mode")["kind"] == "error"
    assert parse("/unknowncmd")["kind"] == "error"
    assert parse("...")["kind"] == "error"
    assert parse("???!")["kind"] == "error"
    assert parse("")["kind"] == "empty"
    assert parse("/lang kn") == {"kind": "lang", "value": "kn"}
    assert parse("/mode gpu") == {"kind": "mode", "value": "gpu"}
    assert parse("ಈಗ ಎಷ್ಟು ಗಂಟೆ") == {"kind": "text", "value": "ಈಗ ಎಷ್ಟು ಗಂಟೆ"}

    dedup = _CLI_MOD.TurnOutputDeduplicator()
    assert dedup.claim_render("t1") is True
    assert dedup.claim_render("t1") is False  # Duplicate WS/HTTP turn suppressed

    dedup.begin_http_turn()
    assert dedup.should_emit_ws_prompt() is False  # No duplicate AURA> prompt during HTTP turn
    dedup.end_http_turn()
    assert dedup.should_emit_ws_prompt() is True
