"""Part A regression suite: code-switched EN/HI/KN routing, anaphora, and execution grounding."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Generator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from aura_edge.config import ComputeMode, ConnectivityMode, Settings
from aura_edge.data.database import create_db_engine
from aura_edge.data.models import (
    ConversationHistoryItem,
    JobExecution,
    MemoryItem,
    MemorySetting,
    NotificationPreference,
    NotificationRecord,
    ScheduledJob,
    SmartHomeDevice,
)
from aura_edge.intelligence.conversation import validate_no_unverified_action_claim
from aura_edge.intelligence.fast_router import MultilingualFastRouter
from aura_edge.intelligence.normalizer import (
    detect_language,
    extract_duration_seconds,
    normalize_utterance,
)
from aura_edge.intelligence.planner import HybridPlanner
from aura_edge.intelligence.schemas import IntentName
from aura_edge.main import create_app


def _offline_settings() -> Settings:
    return Settings(
        _env_file=None,
        connectivity_mode=ConnectivityMode.STRICT_OFFLINE,
        compute_mode=ComputeMode.CPU,
    )


def _cleanup_db() -> None:
    engine = create_db_engine(_offline_settings().database_url)
    with Session(engine) as session:
        session.execute(delete(JobExecution))
        session.execute(delete(ScheduledJob))
        session.execute(delete(NotificationRecord))
        session.execute(delete(NotificationPreference))
        session.execute(delete(ConversationHistoryItem))
        session.execute(delete(MemoryItem))
        session.execute(delete(MemorySetting))
        ac = session.get(SmartHomeDevice, "ac.bedroom")
        if ac is not None:
            ac.available = True
            ac.state = {"power": False, "temperature": 24, "mode": "cool"}
        fan = session.get(SmartHomeDevice, "fan.bedroom")
        if fan is not None:
            fan.available = True
            fan.state = {"power": False, "speed": 2}
        session.commit()


@pytest.fixture(autouse=True)
def _cleanup() -> Generator[None, None, None]:
    _cleanup_db()
    yield
    _cleanup_db()


def test_multilingual_normalization_and_script_priority() -> None:
    assert extract_duration_seconds("thirty seconds") == 30
    assert extract_duration_seconds("थर्टी सेकंड्स") == 30
    assert extract_duration_seconds("ಥರ್ಟಿ ಸೆಕೆಂಡ್ಸ್") == 30
    assert extract_duration_seconds("ಒಂದು minute") == 60
    assert extract_duration_seconds("0 seconds") is None

    n_hi = normalize_utterance("ट्वेंटी फोर degrees")
    assert n_hi.language == "hi"
    assert "24" in n_hi.normalized

    n_kn = normalize_utterance("ಬೆಡ್‌ರೂಮ್ AC twenty-four degrees ಗೆ set ಮಾಡಿ.")
    assert n_kn.language == "kn"
    assert "24" in n_kn.normalized

    # Kannada script must win over stray Devanagari character
    assert detect_language("ಬೆಡ್‌ರೂಮ್ AC 24 क") == "kn"


def test_code_switched_hindi_and_kannada_execution_and_memory_delete() -> None:
    app = create_app(_offline_settings())
    with TestClient(app) as client:
        client.put("/api/v1/notifications/preferences", json={"spoken_enabled": False})

        # 1. Hindi code-switched AC set
        r1 = client.post(
            "/api/v1/intent/process",
            json={"text": "Bedroom का AC 24 degrees पर set करो.", "language": "auto", "speak": False},
        ).json()
        assert r1["intent"] == "DeviceControl"
        assert r1["action_executed"] is True
        assert r1["response_language"] == "hi"
        assert r1["slots"]["room"] == "bedroom"
        assert r1["slots"]["device_type"] == "air_conditioner"
        assert r1["slots"]["value"] == 24

        # 2. Hindi code-switched delayed reminder
        r2 = client.post(
            "/api/v1/intent/process",
            json={"text": "मुझे thirty seconds के बाद पानी पीने का reminder देना.", "language": "auto", "speak": False},
        ).json()
        assert r2["intent"] == "CreateReminder"
        assert r2["action_executed"] is True
        assert r2["response_language"] == "hi"
        assert r2["slots"]["delay_seconds"] == 30
        assert "पानी पीने" in r2["slots"]["message"]

        # 3. Save preference then delete via SOV Hindi phrasing
        save_res = client.post(
            "/api/v1/intent/process",
            json={"text": "Remember my bedroom AC preferred temperature is 22 degrees", "speak": False},
        ).json()
        assert save_res["action_executed"] is True

        r3 = client.post(
            "/api/v1/intent/process",
            json={"text": "मेरा preferred temperature भूल जाओ.", "language": "auto", "speak": False},
        ).json()
        assert r3["intent"] == "DeleteMemory"
        assert r3["action_executed"] is True
        assert r3["response_language"] == "hi"
        assert r3["data"]["deleted_count"] >= 1

        # Verify retrieval after deletion does not resurrect stale memory
        after_del = client.post(
            "/api/v1/intent/process",
            json={"text": "What is my preferred AC temperature?", "speak": False},
        ).json()
        assert after_del["action_executed"] is False
        assert after_del["outcome"] == "clarification"

        # 4. Kannada code-switched AC set ("twenty-four")
        r4 = client.post(
            "/api/v1/intent/process",
            json={"text": "ಬೆಡ್‌ರೂಮ್ AC twenty-four degrees ಗೆ set ಮಾಡಿ.", "language": "auto", "speak": False},
        ).json()
        assert r4["intent"] == "DeviceControl"
        assert r4["action_executed"] is True
        assert r4["response_language"] == "kn"
        assert r4["slots"]["room"] == "bedroom"
        assert r4["slots"]["value"] == 24

        # 5. Kannada code-switched delayed device schedule ("ಒಂದು minute ಆದ್ಮೇಲೆ fan off ಮಾಡಿ.")
        # Note: bedroom & living room both have fans, so specify bedroom or verify schedule disambiguation cleanly
        r5 = client.post(
            "/api/v1/intent/process",
            json={"text": "ಒಂದು minute ಆದ್ಮೇಲೆ bedroom fan off ಮಾಡಿ.", "language": "auto", "speak": False},
        ).json()
        assert r5["intent"] == "ScheduleDeviceAction"
        assert r5["action_executed"] is True
        assert r5["response_language"] == "kn"
        assert r5["slots"]["delay_seconds"] == 60
        assert r5["slots"]["action"] == "power"
        assert r5["slots"]["value"] is False

        # Bare "ಒಂದು minute ಆದ್ಮೇಲೆ fan off ಮಾಡಿ." routes deterministically & asks KN clarification (multiple fans)
        r5_amb = client.post(
            "/api/v1/intent/process",
            json={"text": "ಒಂದು minute ಆದ್ಮೇಲೆ fan off ಮಾಡಿ.", "language": "auto", "speak": False},
        ).json()
        assert r5_amb["intent"] == "ScheduleDeviceAction"
        assert r5_amb["outcome"] == "clarification"
        assert r5_amb["response_language"] == "kn"

        # 6. Kannada code-switched reminder ("ನನಗೆ thirty seconds ಆದ್ಮೇಲೆ reminder ಕೊಡಿ.")
        r6 = client.post(
            "/api/v1/intent/process",
            json={"text": "ನನಗೆ thirty seconds ಆದ್ಮೇಲೆ reminder ಕೊಡಿ.", "language": "auto", "speak": False},
        ).json()
        assert r6["intent"] == "CreateReminder"
        assert r6["action_executed"] is True
        assert r6["response_language"] == "kn"
        assert r6["slots"]["delay_seconds"] == 30


def test_anaphora_resolution_and_localized_clarifications() -> None:
    app = create_app(_offline_settings())
    with TestClient(app) as client:
        client.put("/api/v1/notifications/preferences", json={"spoken_enabled": False})

        # Turn on bedroom fan, then resolve "Turn it off."
        on_res = client.post(
            "/api/v1/intent/process",
            json={"text": "Turn on the bedroom fan", "speak": False},
        ).json()
        assert on_res["action_executed"] is True

        off_res = client.post(
            "/api/v1/intent/process",
            json={"text": "Turn it off.", "speak": False},
        ).json()
        assert off_res["intent"] == "DeviceControl"
        assert off_res["action_executed"] is True
        assert off_res["slots"]["device_type"] == "fan"
        assert off_res["slots"]["room"] == "bedroom"
        assert off_res["slots"]["value"] is False

        # Save bedroom AC preference, then follow up with "Make it 25 instead." and "Set it to my preferred temperature."
        client.post(
            "/api/v1/intent/process",
            json={"text": "Remember my bedroom AC preferred temperature is 21 degrees", "speak": False},
        )
        set_ac = client.post(
            "/api/v1/intent/process",
            json={"text": "Set the bedroom AC to 22 degrees", "speak": False},
        ).json()
        assert set_ac["action_executed"] is True

        make_25 = client.post(
            "/api/v1/intent/process",
            json={"text": "Make it 25 instead.", "speak": False},
        ).json()
        assert make_25["intent"] == "DeviceControl"
        assert make_25["action_executed"] is True
        assert make_25["slots"]["value"] == 25

        pref_set = client.post(
            "/api/v1/intent/process",
            json={"text": "Set it to my preferred temperature.", "speak": False},
        ).json()
        assert pref_set["intent"] == "DeviceControl"
        assert pref_set["action_executed"] is True
        assert pref_set["data"]["device"]["state"]["temperature"] == 21

        # Create reminder, then cancel via "Cancel that reminder."
        rem = client.post(
            "/api/v1/intent/process",
            json={"text": "Remind me to stretch in 3 minutes", "speak": False},
        ).json()
        assert rem["action_executed"] is True

        cancel_rem = client.post(
            "/api/v1/intent/process",
            json={"text": "Cancel that reminder.", "speak": False},
        ).json()
        assert cancel_rem["intent"] == "CancelReminder"
        assert cancel_rem["action_executed"] is True

        # HI & KN vague reminders clarify strictly in HI & KN (never switch to English)
        hi_vague = client.post(
            "/api/v1/intent/process",
            json={"text": "मुझे याद दिलाओ", "language": "auto", "speak": False},
        ).json()
        assert hi_vague["outcome"] == "clarification"
        assert hi_vague["response_language"] == "hi"

        kn_vague = client.post(
            "/api/v1/intent/process",
            json={"text": "ನನಗೆ ನೆನಪಿಸು", "language": "auto", "speak": False},
        ).json()
        assert kn_vague["outcome"] == "clarification"
        assert kn_vague["response_language"] == "kn"


def test_boolean_power_coercion_and_execution_grounding() -> None:
    router = MultilingualFastRouter()
    planner = HybridPlanner(router, ollama_client=None)

    # Power string booleans coerced cleanly; invalid strings clarify
    good_off = planner._validate_llm_json(
        {"intent_name": "DeviceControl", "room": "bedroom", "device_type": "fan", "action": "power", "value": "off"},
        language="en",
        script="latin",
        raw_text="turn bedroom fan off",
    )
    assert good_off.intent_name == IntentName.DEVICE_CONTROL
    assert good_off.slots["value"] is False

    bad_power = planner._validate_llm_json(
        {"intent_name": "DeviceControl", "room": "bedroom", "device_type": "fan", "action": "power", "value": "maybe"},
        language="kn",
        script="kannada",
        raw_text="fan maybe",
    )
    assert bad_power.intent_name == IntentName.CLARIFY
    assert bad_power.language == "kn"

    # Operational grounding gate: mocked LLM returning CONVERSATION on operational text becomes CLARIFY
    fake_ollama = SimpleNamespace()

    async def _fake_chat_json(_msgs: Any) -> Any:
        return SimpleNamespace(
            text='{"intent_name": "Conversation"}',
            parsed_json={"intent_name": "Conversation"},
            latency_ms=5.0,
        )

    fake_ollama.chat_json = _fake_chat_json
    grounded_planner = HybridPlanner(router, ollama_client=fake_ollama)
    gated, *_ = asyncio.run(
        grounded_planner.plan("turn off the purple quantum toaster", allow_llm_fallback=True)
    )
    assert gated.intent_name == IntentName.CLARIFY

    # Unverified action-claim detector blocks hallucinated confirmations
    ok, reason = validate_no_unverified_action_claim("I've turned off the bedroom light for you.", "en")
    assert ok is False
    assert reason == "unverified_action_claim"

    # Orchestrator rejects hallucinated LLM execution claims in Conversation path
    app = create_app(_offline_settings())
    with TestClient(app) as client:
        orch = app.state.orchestrator

        async def _fake_stream(*_args: Any, **_kwargs: Any) -> AsyncIterator[tuple[str, dict[str, Any] | None]]:
            yield ("Done, I've turned off your bedroom AC and deleted your preference.", None)
            yield ("", {"ttft_ms": 2.0, "latency_ms": 5.0})

        orch.ollama_client.stream_conversation = _fake_stream
        out = client.portal.call(lambda: orch.process_text("hello, did anything change?", speak=False))
        assert out.action_executed is False
        assert "I've turned off" not in out.response_text
        assert "did not run that action" in out.response_text


def test_unverified_conversation_never_reaches_dashboard_stream(monkeypatch):
    app = create_app(_offline_settings())
    orch = app.state.orchestrator

    async def generate(*_args, **_kwargs):
        yield "I turned your bedroom ", None
        yield "light off.", None
        yield "", {"ttft_ms": 2.0, "latency_ms": 5.0}

    monkeypatch.setattr(orch.ollama_client, "stream_conversation", generate)
    with TestClient(app) as client, client.websocket_connect("/api/v1/ws/live") as ws:
        result = client.post("/api/v1/intent/process", json={"text": "Why is the sky blue?", "speak": False}).json()
        displayed = []
        while True:
            message = ws.receive_json()
            if message["payload"].get("turn_id") != result["turn_id"]:
                continue
            if message["type"] == "response_text_delta":
                displayed.append(message["payload"]["token"])
            if message["type"] == "response_completed":
                break
        assert "did not run" in result["response_text"]
        assert "".join(displayed) == result["response_text"]


@pytest.mark.parametrize("answer", [
    "I set your bedroom AC to 24 degrees.", "Your study fan is now off.",
    '{"intent_name": "DeviceControl", "device_id": "ac.bedroom"}',
])
def test_tool_claims_and_serialized_actions_are_rejected(answer):
    assert validate_no_unverified_action_claim(answer, "en") == (False, "unverified_action_claim")
