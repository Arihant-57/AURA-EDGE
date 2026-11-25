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
