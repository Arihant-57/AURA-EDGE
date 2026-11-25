"""Routing regressions for timed requests and verified follow-up references."""

import pytest

from aura_edge.intelligence.conversation import ConversationSession
from aura_edge.intelligence.fast_router import MultilingualFastRouter
from aura_edge.intelligence.schemas import IntentName


@pytest.mark.parametrize(("text", "language", "intent", "slot", "value"), [
    ("Start a thirty-second timer.", "en", IntentName.SET_TIMER, "duration_seconds", 30),
    ("Give me a one-minute timer.", "en", IntentName.SET_TIMER, "duration_seconds", 60),
    ("ಹತ್ತು ಸೆಕೆಂಡಿನ ಟೈಮರ್ ಇಡು.", "kn", IntentName.SET_TIMER, "duration_seconds", 10),
    ("Dim the bedroom light to 40 percent.", "en", IntentName.DEVICE_CONTROL, "value", 40),
    ("आज तारीक क्या है?", "hi", IntentName.GET_DATE, None, None),
])
def test_common_variants_preserve_intent_and_slots(text, language, intent, slot, value):
    result, _ = MultilingualFastRouter().route(text, requested_language=language)
    assert result is not None and result.intent_name == intent
    if slot:
        assert result.slots[slot] == value


@pytest.mark.parametrize("text", [
    "Turn off the bedroom AC for one minute.",
    "Tell me the time and turn off the bedroom AC for a minute.",
    "Turn off the bedroom AC after dinner.",
    "Turn it on.",
])
def test_under_specified_power_requests_never_become_an_immediate_action(text):
    result, _ = MultilingualFastRouter().route(text)
    assert result is not None and result.intent_name == IntentName.CLARIFY
    assert result.sub_actions == []


def test_delayed_power_resolves_only_from_verified_context():
    session = ConversationSession()
    session.record_verified_entity(action="DeviceControl", slots={"action": "power"}, data={
        "device": {"id": "ac.bedroom", "room": "bedroom", "device_type": "air_conditioner"},
    })
    router = MultilingualFastRouter()
    intent, _ = router.route(session.resolve_anaphora("Turn it off after one minute."))
    assert intent is not None and intent.intent_name == IntentName.SCHEDULE_DEVICE_ACTION
    assert intent.slots["delay_seconds"] == 60
    assert intent.slots["room"] == "bedroom" and intent.slots["value"] is False
