"""Tests for Phase 1.5 natural conversation, memory, streaming, continuous VAD, and endpoints."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient

from aura_edge.config import ComputeMode, ConnectivityMode, Settings
from aura_edge.intelligence.conversation import ConversationSession, strip_markdown_for_speech
from aura_edge.intelligence.fast_router import MultilingualFastRouter
from aura_edge.intelligence.ollama_client import OllamaUnavailableError
from aura_edge.intelligence.planner import HybridPlanner
from aura_edge.intelligence.schemas import IntentName
from aura_edge.main import create_app
from aura_edge.speech.playback import AudioPlayback
from aura_edge.speech.tts_router import SentenceStreamBuffer, TTSRouter


def test_six_way_routing_greetings_conversation_tools_and_refusals() -> None:
    router = MultilingualFastRouter()
    planner = HybridPlanner(router, ollama_client=object())  # type: ignore[arg-type]

    # Greetings & Acknowledgments (deterministic, fast)
    for txt, exp_lang, exp_intent in [
        ("Hello", "en", IntentName.GREETING),
        ("नमस्ते", "hi", IntentName.GREETING),
        ("नमस्ती", "hi", IntentName.GREETING),
        ("ನಮಸ್ಕಾರ", "kn", IntentName.GREETING),
        ("Thank you very much", "en", IntentName.ACKNOWLEDGMENT),
        ("धन्यवाद", "hi", IntentName.ACKNOWLEDGMENT),
        ("ಧನ್ಯವಾದಗಳು", "kn", IntentName.ACKNOWLEDGMENT),
        ("Goodbye", "en", IntentName.FAREWELL),
    ]:
        intent, _ms, llm_ms, _ = asyncio.run(planner.plan(txt, allow_llm_fallback=True))
        assert intent.intent_name == exp_intent
        assert intent.language == exp_lang
        assert intent.route_category == "greeting"
        assert llm_ms is None

    # Language switching
    sw, _, _, _ = asyncio.run(planner.plan("Switch to Kannada", allow_llm_fallback=True))
    assert sw.intent_name == IntentName.SWITCH_LANGUAGE
    assert sw.route_category == "language_switch"
    assert sw.target_language == "kn"

    # Cross-language conversational follow-up ("Repeat in Kannada")
    rep, _, _, _ = asyncio.run(planner.plan("Repeat that in Kannada", allow_llm_fallback=True))
    assert rep.intent_name == IntentName.CONVERSATION
    assert rep.route_category == "conversation"
    assert rep.target_language == "kn"

    # General knowledge & follow-up conversation (no extra tool classification call)
    q, _, llm_ms, _ = asyncio.run(planner.plan("Why is the sky blue?", allow_llm_fallback=True))
    assert q.intent_name == IntentName.CONVERSATION
    assert q.source == "llm_conversation"
    assert llm_ms is None

    # Restricted action strictly refused even with LLM enabled
    ref, _, _, _ = asyncio.run(planner.plan("Order pizza online", allow_llm_fallback=True))
    assert ref.intent_name == IntentName.UNSUPPORTED
    assert ref.route_category == "refusal"


def test_clock_requests_and_misspelled_timer_never_bypass_tool_routing() -> None:
    router = MultilingualFastRouter()

    for text, expected in [
        ("What is the day today?", IntentName.GET_DATE),
        ("ಇವತ್ತಿಂದ ಡೇಟ್ ಏನು", IntentName.GET_DATE),
    ]:
        intent, _ = router.route(text)
        assert intent is not None and intent.intent_name == expected

    class _FakeClassifier:
        called = False

        async def chat_json(self, _messages):
            from aura_edge.intelligence.ollama_client import OllamaStructuredResult

            self.called = True
            return OllamaStructuredResult(
                content='{"intent_name":"Clarify","clarification_needed":true}',
                parsed_json={"intent_name": "Clarify", "clarification_needed": True},
                model="qwen3.5:4b",
                latency_ms=1.0,
                llm_device="cpu",
                num_gpu=0,
            )

    classifier = _FakeClassifier()
    planner = HybridPlanner(router, ollama_client=classifier)  # type: ignore[arg-type]
    intent, _, _, _ = asyncio.run(planner.plan("manage timmer"))
    assert classifier.called is True
    assert intent.intent_name == IntentName.CLARIFY


def test_conversation_session_bounded_history_and_atomic_commit() -> None:
    session = ConversationSession(max_turns=2)
    session.record_turn(
        user_text="Why is the sky blue?",
        assistant_text="Because of Rayleigh scattering.",
        language="en",
        intent="Conversation",
        route_category="conversation",
    )
    session.record_turn(
        user_text="What time is it?",
        assistant_text="The time is 5:30 PM.",
        language="en",
        intent="GetTime",
        route_category="tool",
    )
    session.record_turn(
        user_text="Explain simply",
        assistant_text="Air scatters blue light more than red light.",
        language="en",
        intent="Conversation",
        route_category="conversation",
    )
    hist = session.get_history()
    assert len(hist) == 4  # Bounded to 2 turns * 2 messages
    assert hist[0].content == "What time is it?"
    assert "Rayleigh scattering" not in strip_markdown_for_speech("```py\nx=1\n```")
    assert strip_markdown_for_speech("**Hello** in ಕನ್ನಡ and हिंदी") == "Hello in ಕನ್ನಡ and हिंदी"


def test_sentence_stream_buffer_and_separated_playback_metrics() -> None:
    buf = SentenceStreamBuffer(min_chars=12)
    tokens = ["The sky looks blue ", "because sunlight scatters in ", "air. ", "पहला वाक्य पूरा। ", "ಅಂತ್ಯದ ಭಾಗ."]
    emitted: list[str] = []
    for tok in tokens:
        emitted.extend(buf.push(tok))
    tail = buf.flush()
    if tail:
        emitted.append(tail)
    assert emitted == [
        "The sky looks blue because sunlight scatters in air.",
        "पहला वाक्य पूरा।",
        "ಅಂತ್ಯದ ಭಾಗ.",
    ]

    # Verify synthesis latency, playback latency, TTFA, and cache hits are cleanly separated
    cfg = Settings(_env_file=None)
    tts = TTSRouter(cfg)
    prewarm = tts.prewarm_common_phrases()
    assert prewarm["phrases_warmed"] >= 6

    playback = AudioPlayback(tts)
    q: asyncio.Queue[str | None] = asyncio.Queue()
    greeting_en = tts.synthesize("Hello!", language="en")
    assert greeting_en.cached is False
    q.put_nowait("Hello!")
    q.put_nowait("How can I help today?")
    q.put_nowait(None)

    audios, metrics = asyncio.run(
        playback.speak_sentence_stream(q, language="en", play_to_speaker=False)
    )
    assert len(audios) == 2
    assert metrics.cache_hits == 1
    assert metrics.audio_playback_ms == 0.0  # play_to_speaker=False separates playback time
    assert metrics.tts_synthesis_ms >= 0.0
    assert metrics.first_audio_ready_ms is not None
    playback.close()


def test_streaming_conversation_endpoints_mute_and_history(monkeypatch: Any) -> None:
    cfg = Settings(_env_file=None, connectivity_mode=ConnectivityMode.STRICT_OFFLINE, compute_mode=ComputeMode.CPU)
    app = create_app(cfg)

    async def _fake_stream_conversation(self, messages, **kwargs):
        # Verify conversation context includes previous user turn on follow-up
        joined = " | ".join(m["content"] for m in messages)
        if "Why is the sky blue?" in joined and "Make it shorter" in joined:
            tokens = ["Air scatters ", "blue sunlight. ", "Simple."]
        else:
            tokens = ["Sunlight scatters in Earth's atmosphere. ", "Blue light scatters most."]
        for tok in tokens:
            yield tok, None
        yield "", {"ttft_ms": 42.0, "latency_ms": 115.0, "eval_count": 12, "tokens_per_sec": 40.0}

    async def _fake_prewarm(self, **kwargs):
        return 12.5

    with TestClient(app) as client:
        orch = app.state.orchestrator
        monkeypatch.setattr(orch.ollama_client, "stream_conversation", _fake_stream_conversation.__get__(orch.ollama_client, type(orch.ollama_client)))
        monkeypatch.setattr(orch.ollama_client, "prewarm", _fake_prewarm.__get__(orch.ollama_client, type(orch.ollama_client)))

        with client.websocket_connect("/api/v1/ws/live") as ws:
            _hello = ws.receive_json()

            # Prewarm
            pw = client.post("/api/v1/voice/prewarm", json={"prewarm_ollama": True})
            assert pw.status_code == 200
            assert pw.json()["ollama_prewarmed"] is True

            # Turn 1: Knowledge question streamed via WS
            r1 = client.post(
                "/api/v1/intent/process",
                json={"text": "Why is the sky blue?", "speak": False},
            )
            assert r1.status_code == 200
            b1 = r1.json()
            assert b1["intent"] == "Conversation"
            assert b1["route_category"] == "conversation"
            assert b1["llm_ttft_ms"] == 42.0
            assert b1["llm_ms"] == 115.0

            # Turn 2: Follow-up remembering Turn 1
            r2 = client.post(
                "/api/v1/intent/process",
                json={"text": "Make it shorter", "speak": False},
            )
            assert r2.status_code == 200
            assert "Simple." in r2.json()["response_text"]

            # Drain WS events and confirm token streaming deltas fired
            seen_types: set[str] = set()
            for _ in range(15):
                ev = ws.receive_json()
                seen_types.add(ev["type"])
                if ev["type"] == "conversation_history_updated":
                    break
            assert "response_text_delta" in seen_types
            assert "response_completed" in seen_types

        # Conversation history inspection & clear
        hist = client.get("/api/v1/conversation/history").json()
        assert hist["count"] == 4
        assert client.delete("/api/v1/conversation/history").json()["cleared"] == 4

        # Mic mute & continuous mode stop safety
        m_on = client.put("/api/v1/voice/mic/mute", json={"muted": True}).json()
        assert m_on["muted"] is True
        m_off = client.put("/api/v1/voice/mic/mute", json={"muted": False}).json()
        assert m_off["muted"] is False
        st = client.post("/api/v1/voice/conversation/stop").json()
        assert st["stopped"] is True


def test_continuous_conversation_loop_silence_and_auto_resume() -> None:
    cfg = Settings(_env_file=None, compute_mode=ComputeMode.CPU)
    app = create_app(cfg)

    async def _run_loop_check() -> None:
        orch = app.state.orchestrator
        # Use fake frames into queue: first cycle = continuous farewell stops loop cleanly
        res = await orch.start_conversation(
            language="en",
            speak=False,
            mode="continuous",
            open_hardware_stream=False,
            silence_timeout_sec=5.0,
        )
        assert res["active"] is True
        await asyncio.sleep(0.1)
        # Toggle mute in active loop
        await orch.set_mic_mute(True)
        assert orch.audio_capture.is_muted is True
        await orch.set_mic_mute(False)
        stopped = await orch.stop_conversation(reason="test_done")
        assert stopped["was_active"] is True
        assert orch.get_status()["conversation_active"] is False

    with TestClient(app):
        asyncio.run(_run_loop_check())


def test_ollama_offline_conversation_graceful_error(monkeypatch: Any) -> None:
    cfg = Settings(_env_file=None, compute_mode=ComputeMode.CPU)
    app = create_app(cfg)

    async def _down_stream(self, messages, **kwargs):
        raise OllamaUnavailableError("Ollama stopped")
        yield "", None

    with TestClient(app) as client:
        orch = app.state.orchestrator
        monkeypatch.setattr(orch.ollama_client, "stream_conversation", _down_stream.__get__(orch.ollama_client, type(orch.ollama_client)))
        res = client.post("/api/v1/intent/process", json={"text": "Tell me about stars", "speak": False})
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "failed"
        assert body["error"] is not None
        # History must remain empty (no orphaned user turn!)
        assert client.get("/api/v1/conversation/history").json()["count"] == 0
        # Greetings & deterministic clock still work offline while Ollama is down!
        hi = client.post("/api/v1/intent/process", json={"text": "Hello", "speak": False}).json()
        assert hi["intent"] == "Greeting"
        assert hi["action_executed"] is True
