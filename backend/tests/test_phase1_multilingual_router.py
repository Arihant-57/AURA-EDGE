"""Tests for Unicode-safe EN/HI/KN normalization, Hassil routing, compound guards, and refusal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aura_edge.intelligence.fast_router import MultilingualFastRouter
from aura_edge.intelligence.normalizer import normalize_utterance
from aura_edge.intelligence.planner import HybridPlanner
from aura_edge.intelligence.schemas import IntentName

_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "multilingual_utterances.json"
_UTTERANCES = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def test_unicode_preservation_for_hindi_and_kannada() -> None:
    hi = normalize_utterance("कृपया ५ मिनट का चाय टाइमर लगाओ")
    assert hi.language == "hi"
    assert "चाय" in hi.normalized
    assert "5" in hi.normalized
    assert any("चाय" == t for t in hi.tokens)

    kn = normalize_utterance("ದಯವಿಟ್ಟು ೫ ನಿಮಿಷದ ಚಹಾ ಟೈಮರ್ ಇಡು")
    assert kn.language == "kn"
    assert "ಚಹಾ" in kn.normalized
    assert "5" in kn.normalized
    assert any("ಚಹಾ" == t for t in kn.tokens)


@pytest.mark.parametrize("case", _UTTERANCES, ids=[c["id"] for c in _UTTERANCES])
async def test_multilingual_96_utterances_deterministic_accuracy(case: dict[str, object]) -> None:
    router = MultilingualFastRouter()
    planner = HybridPlanner(router, ollama_client=None)

    text = str(case["text"])
    expected_lang = str(case["lang"])
    expected_intent = IntentName(str(case["expected_intent"]))

    intent, router_ms, llm_ms, _ = await planner.plan(
        text,
        requested_language="auto",
        allow_llm_fallback=False,
    )

    assert llm_ms is None
    assert router_ms >= 0.0
    assert intent.language == expected_lang, f"{case['id']}: wrong lang {intent.language} for {text!r}"
    assert intent.intent_name == expected_intent, (
        f"{case['id']}: expected {expected_intent.value}, got {intent.intent_name.value} for {text!r}"
    )

    if "expected_duration" in case:
        assert intent.slots.get("duration_seconds") == case["expected_duration"]
    if "expected_label" in case:
        assert intent.slots.get("label") == case["expected_label"]


async def test_compound_commands_validate_all_subactions_and_never_partially_execute() -> None:
    router = MultilingualFastRouter()
    planner = HybridPlanner(router, ollama_client=None)

    valid_compound, _, _, _ = await planner.plan(
        "What time is it; set a timer for 2 minutes",
        allow_llm_fallback=False,
    )
    assert len(valid_compound.sub_actions) == 2
    assert valid_compound.sub_actions[0].intent_name == IntentName.GET_TIME
    assert valid_compound.sub_actions[1].intent_name == IntentName.SET_TIMER
    assert valid_compound.sub_actions[1].slots["duration_seconds"] == 120

    # Mixed valid + unsupported sub-action MUST refuse instead of running partial actions
    unsafe_compound, _, _, _ = await planner.plan(
        "What time is it; order pizza online",
        allow_llm_fallback=False,
    )
    assert unsafe_compound.intent_name == IntentName.UNSUPPORTED
    assert unsafe_compound.sub_actions == []


async def test_llm_fallback_cannot_invent_unknown_tools_or_devices() -> None:
    class _FakeHallucinatingOllama:
        async def chat_json(self, _messages):
            from aura_edge.intelligence.ollama_client import OllamaStructuredResult

            return OllamaStructuredResult(
                content='{"intent_name": "UnlockFrontDoor", "device": "door"}',
                parsed_json={"intent_name": "UnlockFrontDoor", "device": "door"},
                model="qwen3.5:4b",
                latency_ms=12.0,
                llm_device="cpu",
                num_gpu=0,
            )

    planner = HybridPlanner(MultilingualFastRouter(), ollama_client=_FakeHallucinatingOllama())  # type: ignore[arg-type]
    intent, _, llm_ms, _ = await planner.plan("Open my front door", allow_llm_fallback=True)
    assert llm_ms == 12.0
    assert intent.intent_name == IntentName.UNSUPPORTED
    assert intent.source == "refusal"


async def test_r5_colloquial_time_date_smart_home_and_planner_device_control() -> None:
    router = MultilingualFastRouter()
    planner = HybridPlanner(router, ollama_client=None)

    # All required EN/HI/KN time & date phrasings
    clock_cases = [
        ("What's the time?", "en", IntentName.GET_TIME),
        ("Today's date?", "en", IntentName.GET_DATE),
        ("Date today?", "en", IntentName.GET_DATE),
        ("ಇವತ್ತಿನ ಡೇಟ್ ಏನು?", "kn", IntentName.GET_DATE),
        ("ಇವತ್ತು ತಾರೀಖು ಎಷ್ಟು?", "kn", IntentName.GET_DATE),
        ("ಈಗ ಎಷ್ಟು ಗಂಟೆ?", "kn", IntentName.GET_TIME),
        ("आज तारीख क्या है?", "hi", IntentName.GET_DATE),
        ("अभी कितने बजे हैं?", "hi", IntentName.GET_TIME),
        ("aaj ki date kya hai", "hi", IntentName.GET_DATE),
        ("ivattu tareekhu eshtu", "kn", IntentName.GET_DATE),
    ]
    for text, exp_lang, exp_intent in clock_cases:
        intent, _, _, _ = await planner.plan(text, requested_language="auto", allow_llm_fallback=False)
        assert intent.intent_name == exp_intent, f"Failed routing for {text!r}: got {intent.intent_name}"
        assert intent.language == exp_lang

    # Smart-home: Kannada ಠ fix ("ಪಠಣ"), ZWNJ & non-ZWNJ variants, status queries, routines, climate, compound
    sh_cases = [
        ("ಪಠಣ ಕೋಣೆಯ ಲೈಟ್ ಆನ್ ಮಾಡು", "study", "light", "power", True),
        ("ಬೆಡ್‌ರೂಮ್ ಲೈಟ್ ಆನ್ ಮಾಡು", "bedroom", "light", "power", True),
        ("ಬೆಡ್ರೂಮ್ ಫ್ಯಾನ್ ಆಫ್ ಮಾಡು", "bedroom", "fan", "power", False),
        ("ಕಿಚನ್ ಲೈಟ್ ಆನ್ ಮಾಡು", "kitchen", "light", "power", True),
        ("किचन की लाइट चालू करो", "kitchen", "light", "power", True),
        ("ಲಿವಿಂಗ್ ರೂಮ್ ಟಿವಿ ಆನ್ ಮಾಡು", "living_room", "television", "power", True),
        ("Is the bedroom light on?", "bedroom", "light", "status", None),
        ("What is the AC temperature?", None, "air_conditioner", "status", None),
        ("Is the washing machine running?", None, "washing_machine", "status", None),
        ("ಬೆಡ್‌ರೂಮ್ ಲೈಟ್ ಆನ್ ಇದೆಯಾ?", "bedroom", "light", "status", None),
        ("बेडरूम की लाइट चालू है क्या?", "bedroom", "light", "status", None),
        ("What is the bedroom temperature?", "bedroom", "climate_sensor", "status", None),
    ]
    for text, exp_room, exp_type, exp_action, exp_val in sh_cases:
        intent, _ = router.route(text)
        assert intent is not None, f"Smart-home route returned None for {text!r}"
        assert intent.intent_name == IntentName.DEVICE_CONTROL
        assert intent.slots.get("room") == exp_room, f"{text!r} room={intent.slots.get('room')}"
        assert intent.slots.get("device_type") == exp_type, f"{text!r} type={intent.slots.get('device_type')}"
        assert intent.slots.get("action") == exp_action, f"{text!r} action={intent.slots.get('action')}"
        assert intent.slots.get("value") == exp_val

    # Ensure "ac" does NOT falsely match inside "vacuum"
    vac, _ = router.route("Start the robot vacuum")
    assert vac is not None and vac.slots.get("device_type") == "robot_vacuum"

    # Routine triggers
    for text, exp_routine in [
        ("Run the morning routine", "morning"),
        ("Activate night mode", "night"),
        ("Start study routine", "study"),
        ("ಮಾರ್ನಿಂಗ್ ರೂಟೀನ್ ಚಲಾಯಿಸು", "morning"),
    ]:
        r_intent, _ = router.route(text)
        assert r_intent is not None and r_intent.intent_name == IntentName.DEVICE_CONTROL
        assert r_intent.slots.get("action") == "routine"
        assert r_intent.slots.get("routine_id") == exp_routine

    # Compound clause including smart-home + timer
    comp, _ = router.route("Turn on the bedroom light and set a 2 minute timer")
    assert comp is not None and len(comp.sub_actions) == 2
    assert comp.sub_actions[0].intent_name == IntentName.DEVICE_CONTROL
    assert comp.sub_actions[1].intent_name == IntentName.SET_TIMER

    # Planner LLM DeviceControl validation & clarification on uncertain slots
    class _FakeDevicePlannerOllama:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        async def chat_json(self, _messages):
            from aura_edge.intelligence.ollama_client import OllamaStructuredResult

            return OllamaStructuredResult(
                content=json.dumps(self._payload),
                parsed_json=self._payload,
                model="qwen3.5:4b",
                latency_ms=5.0,
                llm_device="cpu",
                num_gpu=0,
            )

    p_valid = HybridPlanner(
        router,
        ollama_client=_FakeDevicePlannerOllama(
            {
                "intent_name": "DeviceControl",
                "room": "bedroom",
                "device_type": "light",
                "action": "brightness",
                "value": 55,
            }
        ),  # type: ignore[arg-type]
    )
    dc_intent, _, _, _ = await p_valid.plan("make the bedroom light dimmer to 55", allow_llm_fallback=True)
    assert dc_intent.intent_name == IntentName.DEVICE_CONTROL
    assert dc_intent.slots["value"] == 55
    assert dc_intent.slots["action"] == "brightness"

    p_uncertain = HybridPlanner(
        router,
        ollama_client=_FakeDevicePlannerOllama(
            {"intent_name": "DeviceControl", "room": "bedroom", "device_type": "light", "action": "power", "value": None}
        ),  # type: ignore[arg-type]
    )
    clar_intent, _, _, _ = await p_uncertain.plan("bedroom light something", allow_llm_fallback=True)
    assert clar_intent.intent_name == IntentName.CLARIFY
    assert clar_intent.route_category == "clarification"

def test_joined_kannada_clock_words():
    intent, _ = MultilingualFastRouter().route("ಈಗ ಎಷ್ಟುಗಂತೆ ಆಗಿದೆ", requested_language="kn")
    assert intent is not None and intent.intent_name == IntentName.GET_TIME

@pytest.mark.parametrize("amount", ["3 months", "30 milliseconds", "5 sandwiches", "-5 minutes", "2.5 minutes"])
def test_invalid_timer_quantity_cannot_reach_execution(amount):
    result, _ = MultilingualFastRouter().route(f"Set a timer for {amount}")
    assert result is not None and result.intent_name == IntentName.CLARIFY

def test_compound_device_reminder_preserves_both_steps_and_limits_plans():
    router = MultilingualFastRouter()
    result, _ = router.route("Set the bedroom AC to 24 degrees and remind me in ten minutes to turn it off.")
    assert result and [step.intent_name for step in result.sub_actions] == [IntentName.DEVICE_CONTROL, IntentName.CREATE_REMINDER]
    assert result.sub_actions[0].slots["value"] == 24
    assert result.sub_actions[1].slots["delay_seconds"] == 600
    result, _ = router.route("Summarize my day and tell me what tasks are still pending.")
    assert result and [s.intent_name for s in result.sub_actions] == [IntentName.DAILY_BRIEFING, IntentName.LIST_TASKS]
    result, _ = router.route("; ".join(["what time is it"] * 5))
    assert result and result.intent_name == IntentName.CLARIFY


@pytest.mark.parametrize(("text", "seconds"), [
    ("पाज मिन्त कर ताीमर लगाओ, और अभी क्या समें हुए है? बताव!", 300),
    ("10 सेकंद का तैमर लगाो", 10),
    ("बीस सेकंद का टायमर लगाओ", 20),
])
def test_hindi_recognition_variants_preserve_duration(text, seconds):
    intent, _ = MultilingualFastRouter().route(text)
    assert intent and intent.intent_name == IntentName.SET_TIMER
    assert intent.slots["duration_seconds"] == seconds


@pytest.mark.parametrize(("question", "answer"), [
    ("What is seventeen multiplied by six?", 102), ("calculate 12 minus 17", -5), ("9 + 6", 15),
])
async def test_exact_arithmetic_uses_registered_tool(question, answer):
    from aura_edge.config import Settings
    from aura_edge.main import create_app

    intent, _ = MultilingualFastRouter().route(question)
    assert intent and intent.intent_name == IntentName.CALCULATE
    registry = create_app(Settings(_env_file=None)).state.orchestrator.tool_registry
    result = await registry.execute_intent(intent)
    assert result.success and result.data["value"] == answer


@pytest.mark.parametrize(("phrase", "expected"), [
    ("Don't turn on the bedroom light", IntentName.CLARIFY),
    ("Do not switch off the study fan", IntentName.CLARIFY),
    ("बेडरूम की लाइट बंद मत करो", IntentName.CLARIFY),
    ("ಬೆಡ್‌ರೂಮ್ ಲೈಟ್ ಆಫ್ ಮಾಡಬೇಡಿ", IntentName.CLARIFY),
    ("If I turn on the bedroom AC, what happens?", IntentName.CONVERSATION),
    ("What happens if I switch off the study fan?", IntentName.CONVERSATION),
])
def test_discussion_and_negation_cannot_select_tools(phrase, expected):
    intent, _ = MultilingualFastRouter().route(phrase)
    assert intent and intent.intent_name == expected and not intent.sub_actions
