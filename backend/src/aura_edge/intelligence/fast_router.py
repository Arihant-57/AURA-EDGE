"""Deterministic multilingual Hassil intent router for English, Hindi, and Kannada."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from hassil import Intents, recognize_best

from aura_edge.intelligence.normalizer import (
    NormalizedText,
    extract_duration_seconds,
    normalize_utterance,
)
from aura_edge.intelligence.response_catalog import get_phrase
from aura_edge.intelligence.schemas import (
    IntentName,
    LanguageCode,
    RequestedLanguage,
    TypedAction,
    TypedIntent,
)

_INTENTS_DIR = Path(__file__).resolve().parent / "intents"

# Ambiguous demonstrative / underspecified commands that require clarification (zero side effects)
_AMBIGUOUS_PATTERNS = [
    re.compile(r"\b(?:do\s+the\s+thing|that\s+thing|something|over\s+there|fix\s+it|do\s+it)\b", re.IGNORECASE),
    re.compile(r"(?:वो\s+वाला|वह\s+काम|कुछ\s+भी|वहाँ\s+वाला|ये\s+कर\s+दो)", re.UNICODE),
    re.compile(r"(?:ಅದನ್ನು\s+ಅಲ್ಲಿ|ಆ\s+ಕೆಲಸ|ಏನಾದರೂ|ಇದನ್ನು\s+ಮಾಡು)", re.UNICODE),
    re.compile(r"^(?:please\s+)?(?:turn|switch)\s+it\s+(?:on|off)\b", re.IGNORECASE),
]

_HYPOTHETICAL = re.compile(
    r"^(?:what (?:would|will|could) happen|what happens|what if|suppose|imagine|if (?:i|we|you)|"
    r"how (?:would|does)|would it|explain (?:how|what)|अगर|यदि|ಒಂದು ವೇಳೆ)", re.IGNORECASE,
)
_NEGATED_REQUEST = re.compile(
    r"\b(?:do not|don['’]t|never|without|not to|didn['’]t|did not)\b|"
    r"(?<![\w\u0900-\u097F])(?:मत|नहीं|नही)(?![\w\u0900-\u097F])|ಬೇಡ|ಬೇಡಿ|ಮಾಡಬಾರದು",
    re.IGNORECASE,
)

# Compound clause separator that avoids splitting "1 minute and 30 seconds"
_COMPOUND_SPLIT_RE = re.compile(
    r"\s*(?:;|,\s*and\s+|\s+and\s+then\s+|\s+and\s+|\s+aur\s+phir\s+|\s+और\s+फिर\s+|\s+और\s+|\s+ಮತ್ತು\s+ನಂತರ\s+|\s+ಮತ್ತು\s+)\s*",
    flags=re.IGNORECASE | re.UNICODE,
)


# Standalone greetings, acknowledgments, farewells, and language switches (Phase 1.5)
_GREETING_RE = re.compile(
    r"^(?:hi|hello|hey(?:\s+there)?|good\s+(?:morning|afternoon|evening)|"
    r"namaste|namaskara|namaskar|नमस्ते|नमस्ती|नमस्टे|नमस्कार|हैलो|हेलो|"
    r"ನಮಸ್ಕಾರ|ನಮಸ್ಕಾ|ನಮಸ್ಕ|ನಮಸ್ತೆ|ನಮಸ್ತಾ|ನಮಸ್ತ|ಹಲೋ)(?:\s+(?:aura|aura\s+edge))?$",
    flags=re.IGNORECASE | re.UNICODE,
)
_ACKNOWLEDGMENT_RE = re.compile(
    r"^(?:thanks?(?:\s+you)?(?:\s+(?:very\s+much|so\s+much|a\s+lot))?|thank\s+you(?:\s+(?:very\s+much|so\s+much))?|"
    r"ok|okay|got\s+it|alright|cool|great|awesome|nice|"
    r"धन्यवाद|शुक्रिया|ठीक\s+है|बढ़िया|बहुत\s+बढ़िया|shukriya|dhanyavad|"
    r"ಧನ್ಯವಾದ|ಧನ್ಯವಾದಗಳು|ಸರಿ|ತುಂಬಾ\s+ಚೆನ್ನಾಗಿದೆ|dhanyavada)$",
    flags=re.IGNORECASE | re.UNICODE,
)
_FAREWELL_RE = re.compile(
    r"^(?:bye|goodbye|see\s+you(?:\s+later)?|talk\s+to\s+you\s+later|"
    r"stop\s+(?:the\s+)?(?:conversation|chat|session|listening)|end\s+(?:the\s+)?(?:conversation|session)|"
    r"अलविदा|बाय|बातचीत\s+(?:बंद|खत्म)\s+करो|alvida|"
    r"ವಿದಾಯ|ಬಾಯ್|ಸಂಭಾಷಣೆ\s+ನಿಲ್ಲಿಸು|ಸಾಕು\s+ಮಾತನಾಡೋದು)$",
    flags=re.IGNORECASE | re.UNICODE,
)
_IDENTITY_RE = re.compile(
    r"^(?:what(?:'s|\s+is)\s+your\s+name|who\s+are\s+you|tell\s+me\s+your\s+name|"
    r"तुम्हारा\s+नाम\s+क्या\s+है|आपका\s+नाम\s+क्या\s+है|तुम\s+कौन\s+हो|आप\s+कौन\s+हैं|"
    r"ನಿಮ್ಮ\s+ಹೆಸರು\s+ಏನು|ನೀವು\s+ಯಾರು|ನಿನ್ನ\s+ಹೆಸರೇನು)(?:\s*[?!.।॥]*)?$",
    flags=re.IGNORECASE | re.UNICODE,
)
_SUPPORTED_LANGUAGES_RE = re.compile(
    r"^(?:(?:which|what)\s+languages?\s+(?:do\s+you|can\s+you)\s+(?:support|speak|understand)|"
    r"what\s+languages?\s+are\s+supported|list\s+supported\s+languages|"
    r"तुम\s+कौन\s*सी\s+भाषाएं?\s*(?:बोल|समझ)\s*सकते\s+हो|आप\s+किन\s+भाषाओं\s+को\s+समर्थन\s+करते\s+हैं|"
    r"ನೀವು\s+ಯಾವ\s+ಭಾಷೆಗಳ?ನ್ನು\s+(?:ಬೆಂಬಲಿಸುತ್ತೀರಿ|ಮಾತನಾಡುತ್ತೀರಿ|ಅರ್ಥಮಾಡಿಕೊಳ್ಳುತ್ತೀರಿ))(?:\s*[?!.।॥]*)?$",
    flags=re.IGNORECASE | re.UNICODE,
)
_PURE_LANG_SWITCH_RE = re.compile(
    r"^(?:(?:please\s+)?(?:switch|change(?:\s+language)?|speak|talk|respond|reply)\s+(?:to|in)\s+|"
    r"(?:अपनी\s+)?भाषा\s+(?:बदलकर\s+)?)?"
    r"(english|hindi|kannada|अंग्रेज़ी|अंग्रेजी|हिंदी|हिन्दी|कन्नड़|ಇಂಗ್ಲಿಷ್|ಹಿಂದಿ|ಕನ್ನಡ)"
    r"(?:\s+(?:please|mein\s+(?:baat\s+karo|bolo)|में\s+(?:बात\s+करो|बोलो)|ಲ್ಲಿ\s+(?:ಮಾತಾಡು|ಮಾತನಾಡಿ|ಹೇಳು)|ದಲ್ಲ?ಿ\s+(?:ಮಾತಾಡು|ಹೇಳು)))?$",
    flags=re.IGNORECASE | re.UNICODE,
)
_EXPLICIT_LANG_REQUEST_RE = re.compile(
    r"(?:\b(?:in|into|to)\s+(english|hindi|kannada)\b|"
    r"(अंग्रेज़ी|अंग्रेजी|हिंदी|हिन्दी|कन्नड़)\s*(?:में|मे)|"
    r"(?:in|into)\s+(english|hindi|kannada)|"
    r"(ಇಂಗ್ಲಿಷ್|ಹಿಂದಿ|ಕನ್ನಡ)(?:ನಲ್ಲಿ|ದಲ್ಲಿ))",
    flags=re.IGNORECASE | re.UNICODE,
)
_LANG_NAME_MAP: dict[str, LanguageCode] = {
    "english": "en",
    "अंग्रेज़ी": "en",
    "अंग्रेजी": "en",
    "ಇಂಗ್ಲಿಷ್": "en",
    "hindi": "hi",
    "हिंदी": "hi",
    "हिन्दी": "hi",
    "ಹಿಂದಿ": "hi",
    "kannada": "kn",
    "कन्नड़": "kn",
    "ಕನ್ನಡ": "kn",
}
_RESTRICTED_ACTION_RE = re.compile(
    r"(?:\b(?:zomato|swiggy|amazon|flipkart|uber|ola)\b|"
    r"^(?:book|reserve|purchase)\b[^.!?]*\b(?:flights?|tickets?|hotels?)\b|"
    r"\b(?:order|buy|purchase)\b.*\b(?:food|pizza|biryani|groceries|online)\b|"
    r"\btransfer\b.*\b(?:rupees|dollars|money|funds|bank|friend)\b|"
    r"\b(?:send|compose)\b.*\b(?:email|gmail|sms|whatsapp|message)\b|"
    r"(?:ऑर्डर|खरीदो|पैसे\s+भेजो|बैंक\s+से|ईमेल\s+.*भेजो)|"
    r"(?:ಆರ್ಡರ್|ಹಣ\s+ಕಳುಹಿಸು|ಬ್ಯಾಂಕ್\s+ಖಾತೆ|ಇಮೇಲ್\s+ಕಳುಹಿಸು))",
    flags=re.IGNORECASE | re.UNICODE,
)

_SMART_HOME_ROOMS = {
    "living_room": (
        "living room",
        "living",
        "hall",
        "लिविंग रूम",
        "बैठक",
        "ಹಾಲ್",
        "ಲಿವಿಂಗ್ ರೂಮ್",
        "ಲಿವಿಂಗ್‌ರೂಮ್",
    ),
    "bedroom": (
        "bedroom",
        "bed room",
        "बेडरूम",
        "बेड रूम",
        "ಮಲಗುವ ಕೋಣೆ",
        "ಬೆಡ್‌ರೂಮ್",
        "ಬೆಡ್ ರೂಮ್",
        "ಬೆಡ್ರೂಮ್",
    ),
    "kitchen": ("kitchen", "रसोई", "किचन", "ಅಡುಗೆಮನೆ", "ಅಡುಗೆ ಮನೆ", "ಕಿಚನ್"),
    "study": (
        "study room",
        "study",
        "अध्ययन कक्ष",
        "अध्ययन",
        "स्टडी रूम",
        "स्टडी",
        "ಪಠಣ",
        "ಅಧ್ಯಯನ ಕೊಠಡಿ",
        "ಅಧ್ಯಯನ",
        "ಸ್ಟಡಿ ರೂಮ್",
        "ಸ್ಟಡಿ",
    ),
    "utility_room": (
        "utility room",
        "utility",
        "यूटिलिटी रूम",
        "यूटिलिटी",
        "ಯುಟಿಲಿಟಿ ಕೊಠಡಿ",
        "ಯುಟಿಲಿಟಿ",
    ),
}
_SMART_HOME_TYPES = {
    "light": ("light", "lights", "lamp", "लाइट", "बत्ती", "ಲೈಟ್", "ದೀಪ", "ಬಲ್ಬ್"),
    "fan": ("fan", "fans", "पंखा", "फैन", "ಫ್ಯಾನ್"),
    "air_conditioner": ("air conditioner", "ac", "एसी", "ऐसी", "ಏಸಿ", "ಎಸಿ"),
    "television": ("television", "tv", "टीवी", "ಟಿವಿ"),
    "curtain": ("curtain", "curtains", "पर्दा", "पर्दे", "ಪರದೆ"),
    "washing_machine": (
        "washing machine",
        "washer",
        "वॉशिंग मशीन",
        "ವಾಷಿಂಗ್ ಮೆಷಿನ್",
        "ವಾಷಿಂಗ್ ಮಷೀನ್",
    ),
    "robot_vacuum": ("robot vacuum", "vacuum", "robot", "रोबोट वैक्यूम", "वैक्यूम", "ವ್ಯಾಕ್ಯೂಮ್"),
    "air_purifier": ("air purifier", "purifier", "प्यूरिफायर", "ಪ್ಯೂರಿಫೈಯರ್"),
    "water_heater": ("water heater", "geyser", "गीजर", "ಗೀಸರ್", "ವಾಟರ್ ಹೀಟರ್"),
    "water_pump": ("water pump", "pump", "पंप", "ಪಂಪ್", "ವಾಟರ್ ಪಂಪ್"),
    "refrigerator": ("refrigerator", "fridge", "फ्रिज", "ರೆಫ್ರಿಜರೇಟರ್", "ಫ್ರಿಜ್"),
    "climate_sensor": (
        "climate sensor",
        "temperature sensor",
        "humidity sensor",
        "climate",
        "thermometer",
        "हवामान",
        "ಕ್ಲೈಮೇಟ್",
    ),
}
_ROUTINE_ALIASES = {
    "morning": ("morning", "सुबह", "मॉर्निंग", "ಬೆಳಗಿನ", "ಬೆಳಗ್ಗೆ", "ಮಾರ್ನಿಂಗ್"),
    "night": ("night", "रात", "नाइट", "ರಾತ್ರಿ", "ನೈಟ್"),
    "study": ("study", "पढ़ाई", "स्टडी", "ಅಧ್ಯಯನ", "ಸ್ಟಡಿ"),
    "away": ("away", "बाहर", "ಹೊರಗೆ", "ಅವೇ"),
}
_ROUTINE_TRIGGER_RE = re.compile(
    r"(?:\b(?:routine|mode)\b|रूटीन|मोड|ದಿನಚರಿ|ರೂಟೀನ್|ಮೋಡ್)",
    flags=re.IGNORECASE | re.UNICODE,
)
_STATUS_QUERY_RE = re.compile(
    r"(?:^\s*(?:is|are|what(?:'s|\s+is)|check|tell\s+me)\b.*\b"
    r"(?:on|off|open|closed|running|cleaning|paused|docked|idle|temperature|humidity|brightness|speed|volume|status|state)\b|"
    r"\b(?:status|state|how\s+long|remaining)\b|"
    r"क्या\b|है\s+क्या|चल\s+रही\s+है|कितना\s+(?:है|हुआ)|स्थिति|कितना\s+समय|"
    r"ಇದೆಯಾ|ಇದೆಯೇ|ಆಗಿದೆಯಾ|ನಡೀತಿದೆಯಾ|ನಡೆಯುತ್ತಿದೆಯಾ|ಸ್ಥಿತಿ|ಎಷ್ಟು\s+ಸಮಯ|"
    r"(?:ತಾಪಮಾನ|ಆರ್ದ್ರತೆ|ಪ್ರಕಾಶ|ವೇಗ|ವಾಲ್ಯೂಮ್)\s*(?:ಎಷ್ಟು|ಏನು))",
    flags=re.IGNORECASE | re.UNICODE,
)
_CLIMATE_QUERY_RE = re.compile(
    r"(?:\b(?:temperature|humidity|climate)\b|तापमान|नमी|ತಾಪಮಾನ|ಆರ್ದ್ರತೆ)",
    flags=re.IGNORECASE | re.UNICODE,
)


def _contains_alias(text: str, aliases: tuple[str, ...]) -> bool:
    for alias in aliases:
        if alias.isascii() and " " not in alias:
            if re.search(rf"\b{re.escape(alias)}\b", text):
                return True
        elif alias in text:
            return True
    return False


def _try_smart_home_action(text: str) -> TypedAction | None:
    low = text.lower()
    # Guard against physics / general knowledge idioms like "speed of light"
    if re.search(r"\bspeed\s+of\s+light\b", low):
        return None
    room = next((key for key, aliases in _SMART_HOME_ROOMS.items() if _contains_alias(low, aliases)), None)
    kind = next((key for key, aliases in _SMART_HOME_TYPES.items() if _contains_alias(low, aliases)), None)

    if kind is None and _ROUTINE_TRIGGER_RE.search(low):
        routine_id = next(
            (key for key, aliases in _ROUTINE_ALIASES.items() if _contains_alias(low, aliases)),
            None,
        )
        if routine_id is not None:
            return TypedAction(
                intent_name=IntentName.DEVICE_CONTROL,
                slots={"action": "routine", "routine_id": routine_id},
                confidence=0.94,
            )

    if kind is None and _CLIMATE_QUERY_RE.search(low):
        kind = "climate_sensor"

    if kind is None:
        return None
    slots: dict[str, Any] = {"room": room, "device_type": kind}

    # Personalized smart-home command: "set the bedroom AC to my preferred temperature"
    if re.search(
        r"\b(?:preferred|preference|favorite|usual)\b|पसंदीदा|मनपसंद|ಇಷ್ಟದ|ಆದ್ಯತೆಯ",
        low,
        flags=re.UNICODE,
    ):
        prop = "temperature"
        if kind == "fan" or re.search(r"speed|स्पीड|ವೇಗ", low):
            prop = "speed"
        elif kind == "light" or re.search(r"brightness|रोशनी|ಪ್ರಕಾಶ", low):
            prop = "brightness"
        elif kind == "curtain":
            prop = "position"
        elif kind == "television":
            prop = "volume"
        slots.update(action=prop, value=None, use_preferred=True)
        return TypedAction(intent_name=IntentName.DEVICE_CONTROL, slots=slots, confidence=0.95)

    number = re.search(r"(?<!\d)(\d{1,3})(?:\s*(?:degrees?|percent|%|डिग्री|ಡಿಗ್ರಿ))?", low)
    if _STATUS_QUERY_RE.search(low) or (
        kind == "climate_sensor" and _CLIMATE_QUERY_RE.search(low) and number is None
    ):
        slots.update(action="status", value=None)
    elif re.search(
        r"\b(?:turn|switch)\s+on\b|\bon\s+(?:karo|kijiye|maad[iu])\b|"
        r"\bon\s+(?:करो|कर\s+दो|कीजिए|ಮಾಡು|ಮಾಡಿ)(?![\w\u0900-\u097F\u0C80-\u0CFF])|"
        r"चालू\s*(?:करो|कर\s+दो|कीजिए)|ಚಾಲು\s*(?:ಮಾಡು|ಮಾಡಿ)|ಆನ್\s+ಮಾಡು|ಆನ್\s+ಮಾಡಿ|"
        r"(?<![\w\u0C80-\u0CFF])ಆನ್(?![\w\u0C80-\u0CFF])",
        low,
    ) or (room is not None and kind is not None and re.search(
        r"(?<![\w\u0C80-\u0CFF])ಅನ್\s+ಮಾಡ[ುಿ](?![\w\u0C80-\u0CFF])", low
    )):
        slots.update(action="power", value=True)
    elif re.search(
        r"\b(?:turn|switch)\s+off\b|\boff\s+(?:karo|kijiye|maad[iu])\b|"
        r"\boff\s+(?:करो|कर\s+दो|कीजिए|ಮಾಡು|ಮಾಡಿ)(?![\w\u0900-\u097F\u0C80-\u0CFF])|"
        r"बंद\s*(?:करो|कर\s+दो|कीजिए)|ಬಂದ್\s*(?:ಮಾಡು|ಮಾಡಿ)|ಆಫ್\s+ಮಾಡು|ಆಫ್\s+ಮಾಡಿ|"
        r"(?<![\w\u0C80-\u0CFF])ಆಫ್(?![\w\u0C80-\u0CFF])",
        low,
    ):
        slots.update(action="power", value=False)
    elif kind == "curtain" and re.search(r"\bopen\b|खोलो|ತೆರೆ", low):
        slots.update(action="position", value=100)
    elif kind == "curtain" and re.search(r"\bclose\b|बंद|ಮುಚ್ಚು", low):
        slots.update(action="position", value=0)
    elif kind in {"washing_machine", "robot_vacuum"} and re.search(r"\bstart\b|शुरू|ಪ್ರಾರಂಭ", low):
        slots.update(action="activity", value="start")
    elif kind in {"washing_machine", "robot_vacuum"} and re.search(r"\bpause\b|रोको|ವಿರಾಮ", low):
        slots.update(action="activity", value="pause")
    elif kind in {"washing_machine", "robot_vacuum"} and re.search(r"\bresume\b|जारी|ಮುಂದುವರಿಸು", low):
        slots.update(action="activity", value="resume")
    elif kind == "robot_vacuum" and re.search(r"\bdock\b|चार्जिंग|ಡಾಕ್", low):
        slots.update(action="activity", value="dock")
    elif kind in {"washing_machine", "robot_vacuum"} and re.search(r"\bcancel\b|रद्द|ರದ್ದು", low):
        slots.update(action="activity", value="cancel")
    elif number and (
        re.search(r"brightness|bright|रोशनी|ब्राइटनेस|ಪ್ರಕಾಶ", low)
        or (kind == "light" and re.search(r"^\s*(?:please\s+)?dim\b.*(?:%|\bpercent\b)", low))
    ):
        slots.update(action="brightness", value=int(number.group(1)))
    elif number and re.search(r"speed|गति|स्पीड|ವೇಗ", low):
        slots.update(action="speed", value=int(number.group(1)))
    elif number and kind == "air_conditioner":
        slots.update(action="temperature", value=int(number.group(1)))
    elif number and kind == "television" and re.search(r"volume|आवाज़|ವಾಲ್ಯೂಮ್", low):
        slots.update(action="volume", value=int(number.group(1)))
    else:
        return None
    return TypedAction(intent_name=IntentName.DEVICE_CONTROL, slots=slots, confidence=0.94)


def extract_requested_target_language(text: str) -> LanguageCode | None:
    """Detect explicit request to reply in English, Hindi, or Kannada."""
    m = _EXPLICIT_LANG_REQUEST_RE.search(text or "")
    if not m:
        return None
    for grp in m.groups():
        if grp:
            mapped = _LANG_NAME_MAP.get(grp.strip().lower())
            if mapped is not None:
                return mapped
    return None

_LABEL_STOPWORDS = {
    "a",
    "an",
    "the",
    "new",
    "my",
    "for",
    "named",
    "called",
    "ka",
    "ki",
    "ke",
    "liye",
    "का",
    "की",
    "के",
    "लिए",
    "ದ",
    "ಗೆ",
    "ಕ್ಕಾಗಿ",
    "ಗಾಗಿ",
    "timer",
    "timers",
    "टाइमर",
    "ಟೈಮರ್",
}

_U_B_START = r"(?<![\w\u0900-\u097F\u0C80-\u0CFF])"
_U_B_END = r"(?![\w\u0900-\u097F\u0C80-\u0CFF])"

_TIMER_ACTION_KEYWORDS = {
    "set": re.compile(rf"{_U_B_START}(?:set|start|create|add|remind|give\s+me|लगाओ|लगा|सेट|शुरू|रखो|lagao|ಇಡು|ಹಾಕು|ಸೆಟ್|idu|haaku){_U_B_END}", re.IGNORECASE | re.UNICODE),
    "cancel_all": re.compile(rf"{_U_B_START}(?:all|सभी|सारे|sabhi|saare|ಎಲ್ಲಾ|ಎಲ್ಲ|ಸರ್ವ|ella){_U_B_END}", re.IGNORECASE | re.UNICODE),
    "cancel": re.compile(rf"{_U_B_START}(?:cancell?ed|cancel|stop|delete|remove|clear|रद्द|हटाओ|हटा|कैंसिल|बंद|band|hatao|radd|ರದ್ದು|ರದ್ದುಗೊಳಿಸು|ತೆಗೆದುಹಾಕು|raddu){_U_B_END}", re.IGNORECASE | re.UNICODE),
    "pause": re.compile(rf"{_U_B_START}(?:pause|hold|रोको|रोक|पॉज़|roko|ನಿಲ್ಲಿಸು|ಪಾಸ್|nillisu){_U_B_END}", re.IGNORECASE | re.UNICODE),
    "resume": re.compile(rf"{_U_B_START}(?:resume|unpause|continue|restart|रिज़्यूम|चलाओ|ಮತ್ತೆ\s+ಪ್ರಾರಂಭಿಸು|ಮುಂದುವರಿಸು){_U_B_END}", re.IGNORECASE | re.UNICODE),
    "remaining": re.compile(rf"{_U_B_START}(?:left|remaining|status|बाकी|बचा|baki|baaki|ಬಾಕಿ|ಎಷ್ಟು\s+ಸಮಯ){_U_B_END}", re.IGNORECASE | re.UNICODE),
    "list": re.compile(rf"{_U_B_START}(?:list|show|check|दिखाओ|लिस्ट|बताओ|dikhao|ತೋರಿಸು|ಪಟ್ಟಿ|torisu){_U_B_END}", re.IGNORECASE | re.UNICODE),
}

_INTENT_MAP: dict[str, IntentName] = {
    "GetTime": IntentName.GET_TIME,
    "GetDate": IntentName.GET_DATE,
    "SetTimer": IntentName.SET_TIMER,
    "ListTimers": IntentName.LIST_TIMERS,
    "GetTimerRemaining": IntentName.GET_TIMER_REMAINING,
    "PauseTimer": IntentName.PAUSE_TIMER,
    "ResumeTimer": IntentName.RESUME_TIMER,
    "CancelTimer": IntentName.CANCEL_TIMER,
    "CancelAllTimers": IntentName.CANCEL_ALL_TIMERS,
    "Help": IntentName.HELP,
    "StopAssistant": IntentName.STOP_ASSISTANT,
}


def _clean_label(raw: str | None) -> str | None:
    if not raw:
        return None
    words = [w for w in raw.strip().split() if w.lower() not in _LABEL_STOPWORDS]
    cleaned = " ".join(words).strip(" ,.-")
    return cleaned or None


def _extract_label_from_timer_text(text: str) -> str | None:
    # Look for explicit "for <label>" or "<label> timer" after removing duration phrase
    no_dur = re.sub(
        r"\b\d+\s*(?:hours?|hrs?|hr|h|minutes?|mins?|min|m|seconds?|secs?|sec|s|"
        r"घंट[ाेों]|मिनट(?:ों)?|सेक[ंडण्ड]+|ghant[ae]|minat|"
        r"ಗಂಟೆ(?:ಗಳು)?|ನಿಮಿಷ(?:ಗಳು|ದ)?|ಸೆಕೆಂಡ್(?:‌ಗಳು|ಗಳು)?|gante|nimisha)\b",
        " ",
        text,
        flags=re.IGNORECASE | re.UNICODE,
    )
    for pat in (
        r"(?:named|called)\s+([\w\u0900-\u097F\u0C80-\u0CFF]+)",
        r"(?:for|on|the)\s+([\w\u0900-\u097F\u0C80-\u0CFF]+)\s+(?:timer|टाइमर|ಟೈಮರ್)",
        r"([\w\u0900-\u097F\u0C80-\u0CFF]+)\s+(?:ka|ki|ke|का|की|के|ದ|ಗಾಗಿ|ಕ್ಕೆ)?\s*(?:timer|टाइमर|ಟೈಮರ್)",
    ):
        m = re.search(pat, no_dur, flags=re.IGNORECASE | re.UNICODE)
        if m:
            candidate = _clean_label(m.group(1))
            if candidate and not candidate.isdigit() and candidate.lower() not in {
                "set",
                "start",
                "create",
                "add",
                "pause",
                "resume",
                "cancel",
                "stop",
                "delete",
                "check",
                "my",
                "new",
                "एक",
                "ಒಂದು",
            }:
                return candidate
    return None


_SAVE_MEMORY_PREFIX_RE = re.compile(
    r"^(?:please\s+)?(?:remember(?:\s+that)?|save\s+to\s+memory(?:\s+that)?|note\s+that|don'?t\s+forget(?:\s+that)?|"
    r"(?:कृपया\s+)?याद\s+(?:रखो|रखना|रखें)(?:\s+कि)?|"
    r"(?:ದಯವಿಟ್ಟು\s+)?ನೆನಪಿ(?:ಡು|ಟ್ಟುಕೋ|ಡಿ|ಟ್ಟುಕೊಳ್ಳಿ)(?:\s+ಅಂತ)?)\s+(.+)$",
    flags=re.IGNORECASE | re.UNICODE,
)
_LIST_MEMORY_RE = re.compile(
    r"^(?:list|show|what\s+are)\s+(?:all\s+)?(?:my\s+)?memor(?:y|ies)|"
    r"^what\s+do\s+you\s+remember(?:\s+about\s+me)?\s*[?]?$|"
    r"(?:तुम्हें|आपको)\s+(?:मेरे\s+बारे\s+में\s+)?क्या\s+याद\s+है|"
    r"मेरी\s+यादें\s+(?:दिखाओ|बताओ)|"
    r"(?:ನನ್ನ\s+ಬಗ್ಗೆ\s+)?ನಿಮಗೆ\s+ಏನು\s+ನೆನಪಿದೆ|"
    r"ನನ್ನ\s+ನೆನಪುಗಳ?ನ್ನು\s+(?:ತೋರಿಸು|ಹೇಳು)",
    flags=re.IGNORECASE | re.UNICODE,
)
_RETRIEVE_PREF_RE = re.compile(
    r"(?:what\s+(?:temperature|temp|speed|brightness|volume|position|[a-z\s]+)?\s*(?:do\s+i|is\s+my)\s*(?:prefer(?:red)?|favorite)|"
    r"what\s+do\s+you\s+remember\s+about\s+(.+)|"
    r"मेरा\s+(?:पसंदीदा|मनपसंद)\s+(.+?)\s*(?:क्या|कितना)\s+है|"
    r"मुझे\s+कौन\s*सा\s+(.+?)\s+पसंद\s+है|"
    r"(?:ನನ್ನ\s+)?(?:ಇಷ್ಟದ|ಆದ್ಯತೆಯ)\s+(.+?)\s*(?:ಎಷ್ಟು|ಏನು))",
    flags=re.IGNORECASE | re.UNICODE,
)
_UPDATE_PREF_RE = re.compile(
    r"(?:change|update|modify)\s+(?:my\s+)?(?:preferred|preference|favorite)\s+(.+?)\s+to\s+(.+)|"
    r"मेरा\s+(?:पसंदीदा|मनपसंद)\s+(.+?)\s+(?:बदलकर\s+)?(.+?)\s+(?:करो|कर\s+दो|रखो)|"
    r"(?:ನನ್ನ\s+)?(?:ಇಷ್ಟದ|ಆದ್ಯತೆಯ)\s+(.+?)\s+(?:ಅನ್ನು\s+)?(.+?)\s*(?:ಗೆ|ಕ್ಕೆ)\s*(?:ಬದಲಾಯಿಸು|ಮಾಡು|ಬದಲಿಸು)",
    flags=re.IGNORECASE | re.UNICODE,
)
_DELETE_MEMORY_RE = re.compile(
    r"^(?:please\s+)?(?:forget(?:\s+that|\s+about|\s+my)?|delete\s+(?:my\s+)?memory(?:\s+about)?|"
    r"(?:कृपया\s+)?भूल\s+जाओ(?:\s+कि)?|"
    r"(?:ದಯವಿಟ್ಟು\s+)?ಮರೆತುಬಿಡು(?:\s+ಅಂತ)?)\s+(.+)$",
    flags=re.IGNORECASE | re.UNICODE,
)
_DELETE_MEMORY_SUFFIX_RE = re.compile(
    r"^(?:please\s+|कृपया\s+|ದಯವಿಟ್ಟು\s+)?(?:my\s+|मेरा\s+|मेरी\s+|मेरे\s+|ನನ್ನ\s+)?(.+?)"
    r"(?:\s+(?:को|की|का|ಅನ್ನು|ಗೆ))?\s*(?:forget(?:\s+karo|\s+maad[iu])?|भूल\s+जाओ|हटा\s+दो|हटाओ|डिलीट\s+करो|"
    r"ಮರೆತುಬಿಡು|ಮರೆತುಬಿಡಿ|ಅಳಿಸು|ಅಳಿಸಿ)$",
    flags=re.IGNORECASE | re.UNICODE,
)

_WEEKDAY_MAP: dict[str, int] = {
    "monday": 0, "mon": 0, "सोमवार": 0, "ಸೋಮವಾರ": 0,
    "tuesday": 1, "tue": 1, "मंगलवार": 1, "ಮಂಗಳವಾರ": 1,
    "wednesday": 2, "wed": 2, "बुधवार": 2, "ಬುಧವಾರ": 2,
    "thursday": 3, "thu": 3, "गुरुवार": 3, "ಗುರುವಾರ": 3,
    "friday": 4, "fri": 4, "शुक्रवार": 4, "ಶುಕ್ರವಾರ": 4,
    "saturday": 5, "sat": 5, "शनिवार": 5, "ಶನಿವಾರ": 5,
    "sunday": 6, "sun": 6, "रविवार": 6, "ರವಿವಾರ": 6,
}

_DELAY_UNIT_GROUP = (
    r"(?:hours?|hrs?|hr|h|minutes?|mins?|min|m|seconds?|secs?|sec|s|"
    r"घंट[ाेों]|मिनट(?:ों|्स)?|सेक[ंडण्ड]+(?:्स)?|ghant[ae]|minat|"
    r"ಗಂಟೆ(?:ಗಳು)?|ನಿಮಿಷ(?:ಗಳು|ದ)?|ಮಿನಿಟ್(?:ಸ್)?|ಸೆಕೆಂಡ್(?:‌ಗಳು|ಗಳು|ಸ್)?|ಸೆಕೆಂಡು(?:ಗಳು)?|gante|nimisha)"
)
_POWER_REQUEST_RE = re.compile(r"\b(?:turn|switch)\b[^.!?;]*?\b(?:on|off)\b", re.IGNORECASE)
_TEMPORARY_POWER_RE = re.compile(rf"\bfor\s+(?:\d+|an?)\s*(?:[-‑–]\s*)?{_DELAY_UNIT_GROUP}\b", re.IGNORECASE)
_AFTER_RE = re.compile(r"\bafter\b", re.IGNORECASE)


def _extract_delay_from_phrase(text: str) -> tuple[int | None, str]:
    """Match relative delays across EN/HI/KN and code-switched phrases."""
    patterns = (
        rf"\b(?:in|after)\s+(\d+\s*{_DELAY_UNIT_GROUP}(?:\s+(?:and\s+)?\d+\s*{_DELAY_UNIT_GROUP})?)\b",
        (
            rf"(\d+\s*{_DELAY_UNIT_GROUP}(?:\s+(?:and\s+|और\s+|ಮತ್ತು\s+)?\d+\s*{_DELAY_UNIT_GROUP})?)\s*"
            r"(?:में|के\s+बाद|बाद|baad|mein|ದಲ್ಲಿ|ನಂತರ|ಆದ್ಮೇಲೆ|ಆದ\s*ಮೇಲೆ|ಒಳಗೆ)(?![\w\u0900-\u097F\u0C80-\u0CFF])"
        ),
    )
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE | re.UNICODE)
        if m:
            dur = extract_duration_seconds(m.group(1))
            if dur is not None and dur > 0:
                remainder = (text[: m.start()] + " " + text[m.end() :]).strip()
                return dur, re.sub(r"\s+", " ", remainder)
    return None, text


def _parse_clock_and_recurrence(text: str) -> dict[str, Any] | None:
    """Parse local clock times, tomorrow/today, and daily/weekly recurrence specs."""
    low = text.lower()
    is_daily = bool(
        re.search(
            r"\bevery\s+(?:day|morning|evening|night|afternoon)\b|\bdaily\b|रोज़|हर\s+(?:दिन|सुबह|शाम|रात)|ಪ್ರತಿದಿನ|ಪ್ರತಿ\s+(?:ಬೆಳಗ್ಗೆ|ಸಂಜೆ|ರಾತ್ರಿ)",
            low,
            flags=re.UNICODE,
        )
    )
    weekdays: list[int] = []
    if re.search(r"\bevery\b|हर|ಪ್ರತಿ", low):
        for name, idx in _WEEKDAY_MAP.items():
            if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", low, flags=re.UNICODE) and idx not in weekdays:
                weekdays.append(idx)

    day_offset = 1 if re.search(r"\btomorrow\b|कल|ನಾಳೆ", low) else 0

    # Require an explicit time indicator word/marker so device numbers ("24 degrees") don't match!
    has_time_marker = bool(
        re.search(
            r"\bat\s+\d{1,2}\b|\d{1,2}:\d{2}|\d{1,2}\s*(?:a\.?m\.?|p\.?m\.?|बजे|ಗಂಟೆಗೆ)",
            low,
            flags=re.UNICODE,
        )
    )
    if not has_time_marker and not is_daily and not weekdays and day_offset == 0:
        return None

    if not has_time_marker:
        # E.g. "remind me tomorrow" with NO clock time -> ambiguous!
        return {"ambiguous": True, "reason": "missing_time"}

    # Re-search specifically at the time marker
    tm = re.search(
        r"(?:\bat\s+)?(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?|बजे|ಗಂಟೆಗೆ)?",
        re.search(
            r"(?:\bat\s+\d{1,2}(?::\d{2})?(?:\s*(?:a\.?m\.?|p\.?m\.?))?|"
            r"\d{1,2}:\d{2}(?:\s*(?:a\.?m\.?|p\.?m\.?))?|"
            r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|बजे|ಗಂಟೆಗೆ))",
            low,
            flags=re.UNICODE,
        ).group(0),  # type: ignore[union-attr]
        flags=re.UNICODE,
    )
    if tm is None:
        return {"ambiguous": True, "reason": "missing_time"}

    hour = int(tm.group(1))
    minute = int(tm.group(2) or 0)
    if hour > 23 or minute > 59:
        return {"ambiguous": True, "reason": "invalid_time"}

    meridiem = (tm.group(3) or "").replace(".", "").lower()
    if re.search(r"\bmorning\b|सुबह|ಬೆಳಗ್ಗೆ|ಬೆಳಿಗ್ಗೆ", low) and not meridiem:
        meridiem = "am"
    elif re.search(r"\b(?:evening|night|afternoon)\b|शाम|रात|दोपहर|ಸಂಜೆ|ರಾತ್ರಿ|ಮಧ್ಯಾಹ್ನ", low) and not meridiem:
        meridiem = "pm"

    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif not meridiem and hour <= 12 and ":" not in tm.group(0) and hour < 24:
        # Bare "at 5" without AM/PM when no morning/evening cue
        return {"ambiguous": True, "reason": "ambiguous_ampm"}

    recurrence_kind = "weekly" if weekdays else ("daily" if is_daily else "once")
    return {
        "ambiguous": False,
        "hour": hour,
        "minute": minute,
        "day_offset": day_offset,
        "recurrence": {
            "kind": recurrence_kind,
            "hour": hour,
            "minute": minute,
            **({"weekdays": weekdays} if weekdays else {}),
        },
    }


def _try_memory_action(norm: NormalizedText) -> TypedIntent | None:
    text = norm.normalized.strip()
    if not text:
        return None

    m_save = _SAVE_MEMORY_PREFIX_RE.match(text)
    if m_save:
        body = m_save.group(1).strip().rstrip(".")
        return TypedIntent(
            intent_name=IntentName.SAVE_MEMORY,
            confidence=0.96,
            source="hassil_fast",
            route_category="tool",
            language=norm.language,
            input_script=norm.script,
            slots={"text": body, "raw_text": text},
            raw_text=norm.raw,
        )

    m_del = _DELETE_MEMORY_RE.match(text)
    if m_del:
        query = m_del.group(1).strip().rstrip(".")
        return TypedIntent(
            intent_name=IntentName.DELETE_MEMORY,
            confidence=0.95,
            source="hassil_fast",
            route_category="tool",
            language=norm.language,
            input_script=norm.script,
            slots={"query": query},
            raw_text=norm.raw,
        )

    if not re.search(
        r"\b(?:timer|timers|reminder|reminders|schedule|schedules|task|tasks|event|events|alarm)\b|"
        r"टाइमर|रिमाइंडर|शेड्यूल|टास्क|ಟೈಮರ್|ರಿಮೈಂಡರ್|ಜ್ಞಾಪನೆ|ವೇಳಾಪಟ್ಟಿ|ಟಾಸ್ಕ್",
        text.lower(),
        flags=re.UNICODE,
    ):
        m_del_sfx = _DELETE_MEMORY_SUFFIX_RE.match(text)
        if m_del_sfx:
            query = re.sub(
                r"^(?:my|मेरा|मेरी|मेरे|ನನ್ನ)\s+",
                "",
                m_del_sfx.group(1).strip().rstrip("."),
                flags=re.IGNORECASE | re.UNICODE,
            ).strip()
            if query:
                return TypedIntent(
                    intent_name=IntentName.DELETE_MEMORY,
                    confidence=0.95,
                    source="hassil_fast",
                    route_category="tool",
                    language=norm.language,
                    input_script=norm.script,
                    slots={"query": query},
                    raw_text=norm.raw,
                )

    if _LIST_MEMORY_RE.match(text.lower()):
        return TypedIntent(
            intent_name=IntentName.LIST_MEMORIES,
            confidence=0.96,
            source="hassil_fast",
            route_category="tool",
            language=norm.language,
            input_script=norm.script,
            slots={},
            raw_text=norm.raw,
        )

    m_upd = _UPDATE_PREF_RE.search(text)
    if m_upd:
        return TypedIntent(
            intent_name=IntentName.UPDATE_MEMORY,
            confidence=0.94,
            source="hassil_fast",
            route_category="tool",
            language=norm.language,
            input_script=norm.script,
            slots={"text": text},
            raw_text=norm.raw,
        )

    m_ret = _RETRIEVE_PREF_RE.search(text)
    if m_ret:
        low = text.lower()
        room = next((k for k, al in _SMART_HOME_ROOMS.items() if _contains_alias(low, al)), None)
        dev_type = next((k for k, al in _SMART_HOME_TYPES.items() if _contains_alias(low, al)), None)
        return TypedIntent(
            intent_name=IntentName.RETRIEVE_MEMORY,
            confidence=0.95,
            source="hassil_fast",
            route_category="tool",
            language=norm.language,
            input_script=norm.script,
            slots={"query": text, "room": room, "device_type": dev_type},
            raw_text=norm.raw,
        )
    return None


def _try_schedule_or_reminder_action(norm: NormalizedText) -> TypedIntent | None:
    text = norm.normalized.strip()
    low = text.lower()
    if not text:
        return None

    # List / Cancel reminders & schedules
    if re.search(
        r"\b(?:list|show|what\s+are)\b.*\breminders?\b|रिमाइंडर\s*(?:दिखाओ|बताओ|लिस्ट)|ಜ್ಞಾಪನೆಗಳ?ನ್ನು\s*(?:ತೋರಿಸು|ಹೇಳು)",
        low,
        flags=re.UNICODE,
    ):
        return TypedIntent(
            intent_name=IntentName.LIST_REMINDERS,
            confidence=0.95,
            source="hassil_fast",
            route_category="tool",
            language=norm.language,
            input_script=norm.script,
            slots={},
            raw_text=norm.raw,
        )

    if re.search(
        r"\b(?:list|show|what\s+are)\b.*\bschedules?\b|शेड्यूल\s*(?:दिखाओ|बताओ)|ವೇಳಾಪಟ್ಟಿ\s*(?:ತೋರಿಸು|ಹೇಳು)",
        low,
        flags=re.UNICODE,
    ):
        return TypedIntent(
            intent_name=IntentName.LIST_SCHEDULES,
            confidence=0.95,
            source="hassil_fast",
            route_category="tool",
            language=norm.language,
            input_script=norm.script,
            slots={},
            raw_text=norm.raw,
        )

    is_cancel = bool(re.search(r"\b(?:cancel|delete|remove)\b|रद्द|हटाओ|ರದ್ದು|ತೆಗೆ", low, flags=re.UNICODE))
    if is_cancel and re.search(r"\breminder\b|रिमाइंडर|ಜ್ಞಾಪನೆ|ರಿಮೈಂಡರ್", low, flags=re.UNICODE):
        return TypedIntent(
            intent_name=IntentName.CANCEL_REMINDER,
            confidence=0.92,
            source="hassil_fast",
            route_category="tool",
            language=norm.language,
            input_script=norm.script,
            slots={"query": text},
            raw_text=norm.raw,
        )
    if is_cancel and re.search(r"\bschedule\b|शेड्यूल|ವೇಳಾಪಟ್ಟಿ", low, flags=re.UNICODE):
        return TypedIntent(
            intent_name=IntentName.CANCEL_SCHEDULE,
            confidence=0.92,
            source="hassil_fast",
            route_category="tool",
            language=norm.language,
            input_script=norm.script,
            slots={"query": text},
            raw_text=norm.raw,
        )

    delay_sec, rem_text = _extract_delay_from_phrase(text)
    clock_spec = None if delay_sec is not None else _parse_clock_and_recurrence(text)

    # Do not intercept plain timers ("set a timer in/for 5 minutes")
    if re.search(r"\btimers?\b|टाइमर|ಟೈಮರ್", low, flags=re.UNICODE):
        return None

    # A. Explicit reminder intent
    is_reminder = bool(
        re.search(
            r"\bremind\s+me\b|\bset\s+(?:a\s+)?reminder\b|\breminders?\b|"
            r"याद\s+दिला(?:ओ|ना)|रिमाइंडर|ನೆನಪಿಸು|ರಿಮೈಂಡರ್|ಜ್ಞಾಪನೆ",
            low,
            flags=re.UNICODE,
        )
    )
    if is_reminder:
        if delay_sec is None and (clock_spec is None or clock_spec.get("ambiguous")):
            q_msg = {
                "en": "When should I remind you, and what should I remind you about?",
                "hi": "मैं आपको कब और किस बारे में याद दिलाऊँ?",
                "kn": "ನಾನು ನಿಮಗೆ ಯಾವಾಗ ಮತ್ತು ಯಾವುದರ ಬಗ್ಗೆ ನೆನಪಿಸಲಿ?",
            }[norm.language]
            if clock_spec and clock_spec.get("ambiguous"):
                q_msg = {
                    "en": "Please specify the exact time with AM or PM for this reminder.",
                    "hi": "कृपया रिमाइंडर के लिए सुबह या शाम का सही समय बताएं।",
                    "kn": "ದಯವಿಟ್ಟು ಜ್ಞಾಪನೆಗಾಗಿ ಬೆಳಗ್ಗೆ ಅಥವಾ ಸಂಜೆ ಎಂಬ ನಿಖರ ಸಮಯವನ್ನು ತಿಳಿಸಿ.",
                }[norm.language]
            return TypedIntent(
                intent_name=IntentName.CLARIFY,
                confidence=0.92,
                source="clarification",
                route_category="clarification",
                language=norm.language,
                input_script=norm.script,
                clarification_question=q_msg,
                raw_text=norm.raw,
            )

        # Strip reminder boilerplate & time clause to extract reminder content
        body = rem_text if delay_sec is not None else text
        body = re.sub(
            r"^(?:please\s+)?(?:remind\s+me(?:\s+to)?|set\s+(?:a\s+)?reminder(?:\s+to|\s+for)?)|"
            r"(?:कल\s+|आज\s+)?(?:सुबह|शाम|दोपहर|रात)?\s*\d{1,2}(?::\d{2})?\s*(?:बजे)?\s*(?:याद\s+दिलाना|याद\s+दिलाओ)?|"
            r"(?:ನಾಳೆ\s+|ಇಂದು\s+)?(?:ಬೆಳಗ್ಗೆ|ಸಂಜೆ|ಮಧ್ಯಾಹ್ನ|ರಾತ್ರಿ)?\s*\d{1,2}(?::\d{2})?\s*(?:ಗಂಟೆಗೆ)?\s*(?:ನೆನಪಿಸು)?",
            " ",
            body,
            flags=re.IGNORECASE | re.UNICODE,
        )
        body = re.sub(
            r"\b(?:every\s+(?:day|morning|evening|night)|daily|tomorrow|today|at\s+\d{1,2}(?::\d{2})?(?:\s*(?:a\.?m\.?|p\.?m\.?))?)\b",
            " ",
            body,
            flags=re.IGNORECASE,
        )
        body = re.sub(
            r"(?:\b(?:a|an)\s+)?\breminders?\b\s*(?:देना|दो|दीजिए|दे|लगाओ|रखो|ಕೊಡಿ|ಕೊಡು|ಹಾಕು|ಇಡು|ಮಾಡು|ಮಾಡಿ)?|"
            r"(?:रिमाइंडर|ರಿಮೈಂಡರ್|ಜ್ಞಾಪನೆ)\s*(?:देना|दो|दीजिए|दे|लगाओ|रखो|ಕೊಡಿ|ಕೊಡು|ಹಾಕು|ಇಡು|ಮಾಡು|ಮಾಡಿ)?",
            " ",
            body,
            flags=re.IGNORECASE | re.UNICODE,
        )
        body = re.sub(
            r"(?:मुझे\s+|ನನಗೆ\s+|\s+(?:का|की|के|को|ಗೆ|ಕ್ಕೆ|ನ್ನು|ಅನ್ನು)\s+|\s+(?:का|की|के|को)$)",
            " ",
            body,
            flags=re.UNICODE,
        )
        body = re.sub(r"\s+", " ", body).strip(" ,.-")
        if len(body) < 2:
            if delay_sec is not None:
                body = {"en": "Reminder", "hi": "रिमाइंडर", "kn": "ಜ್ಞಾಪನೆ"}[norm.language]
            else:
                q_msg = {
                    "en": "What should I remind you about?",
                    "hi": "मैं आपको किस बारे में याद दिलाऊँ?",
                    "kn": "ನಾನು ನಿಮಗೆ ಯಾವುದರ ಬಗ್ಗೆ ನೆನಪಿಸಲಿ?",
                }[norm.language]
                return TypedIntent(
                    intent_name=IntentName.CLARIFY,
                    confidence=0.92,
                    source="clarification",
                    route_category="clarification",
                    language=norm.language,
                    input_script=norm.script,
                    clarification_question=q_msg,
                    raw_text=norm.raw,
                )
        return TypedIntent(
            intent_name=IntentName.CREATE_REMINDER,
            confidence=0.95,
            source="hassil_fast",
            route_category="tool",
            language=norm.language,
            input_script=norm.script,
            slots={
                "message": body,
                "delay_seconds": delay_sec,
                "time_spec": clock_spec,
            },
            raw_text=norm.raw,
        )

    # B. Scheduled smart-home action or routine (requires delay_sec OR valid clock_spec)
    if delay_sec is None and clock_spec is None:
        return None
    if clock_spec is not None and clock_spec.get("ambiguous"):
        # Only intercept if it actually references a smart-home device or routine
        has_device_or_routine = bool(
            _try_smart_home_action(text)
        )
        if not has_device_or_routine:
            return None
        q_msg = {
            "en": "Please specify the exact time (AM or PM) to schedule that device action.",
            "hi": "कृपया उस उपकरण को शेड्यूल करने के लिए सुबह या शाम का सही समय बताएं।",
            "kn": "ಆ ಸಾಧನವನ್ನು ನಿಗದಿಪಡಿಸಲು ದಯವಿಟ್ಟು ನಿಖರ ಸಮಯವನ್ನು (ಬೆಳಗ್ಗೆ ಅಥವಾ ಸಂಜೆ) ತಿಳಿಸಿ.",
        }[norm.language]
        return TypedIntent(
            intent_name=IntentName.CLARIFY,
            confidence=0.92,
            source="clarification",
            route_category="clarification",
            language=norm.language,
            input_script=norm.script,
            clarification_question=q_msg,
            raw_text=norm.raw,
        )

    # Try stripping the time expression and parsing the smart-home command
    clean_cmd = rem_text if delay_sec is not None else re.sub(
        r"\b(?:every\s+(?:day|morning|evening|night)|daily|tomorrow|today|at\s+\d{1,2}(?::\d{2})?(?:\s*(?:a\.?m\.?|p\.?m\.?))?)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    clean_cmd = re.sub(
        r"(?:रोज़|कल|आज|हर\s+(?:दिन|सुबह|शाम))?\s*(?:\d{1,2}(?::\d{2})?\s*बजे)|"
        r"(?:ಪ್ರತಿದಿನ|ನಾಳೆ|ಇಂದು)?\s*(?:\d{1,2}(?::\d{2})?\s*ಗಂಟೆಗೆ)",
        " ",
        clean_cmd,
        flags=re.UNICODE,
    )
    sh_action = _try_smart_home_action(re.sub(r"\s+", " ", clean_cmd).strip())
    if sh_action is None:
        return None

    if sh_action.slots.get("action") == "routine":
        return TypedIntent(
            intent_name=IntentName.SCHEDULE_ROUTINE,
            confidence=0.94,
            source="hassil_fast",
            route_category="tool",
            language=norm.language,
            input_script=norm.script,
            slots={
                "routine_id": sh_action.slots["routine_id"],
                "delay_seconds": delay_sec,
                "time_spec": clock_spec,
            },
            raw_text=norm.raw,
        )

    return TypedIntent(
        intent_name=IntentName.SCHEDULE_DEVICE_ACTION,
        confidence=0.94,
        source="hassil_fast",
        route_category="tool",
        language=norm.language,
        input_script=norm.script,
        slots={
            "room": sh_action.slots.get("room"),
            "device_type": sh_action.slots.get("device_type"),
            "action": sh_action.slots.get("action"),
            "value": sh_action.slots.get("value"),
            "delay_seconds": delay_sec,
            "time_spec": clock_spec,
        },
        raw_text=norm.raw,
    )


def _try_phase4_action(norm: NormalizedText) -> TypedIntent | None:
    """Deterministic EN/HI/KN/code-switched routing for Phase 4 Personal Secretary intents."""
    text = norm.normalized.strip()
    low = text.lower()
    if not text:
        return None

    def _intent(name: IntentName, slots: dict[str, Any] | None = None, conf: float = 0.93) -> TypedIntent:
        return TypedIntent(
            intent_name=name,
            confidence=conf,
            source="hassil_fast",
            route_category="tool",
            language=norm.language,
            input_script=norm.script,
            slots=slots or {},
            raw_text=norm.raw,
        )

    # 1. Daily briefing
    if re.search(
        r"\bsummari[sz]e my day\b|\b(?:daily|morning)\s+briefing\b|\bbrief\s+me\b|\b(?:what(?:'s|\s+is)|give\s+me)\s+my\s+(?:day|daily\s+summary)\b|"
        r"आज\s+की\s+ब्रीफिंग|दैनिक\s+सारांश|ಇಂದಿನ\s+ಬ್ರೀಫಿಂಗ್|ದೈನಂದಿನ\s+ಸಾರಾಂಶ",
        low,
        flags=re.UNICODE,
    ):
        return _intent(IntentName.DAILY_BRIEFING)

    # 2. Groceries, pantry & offline recipes
    if re.search(
        r"\b(?:suggest|show|find)\s+recipes?\b|\bwhat\s+can\s+i\s+cook\b|\brecipe\s+ideas?\b|"
        r"क्या\s+(?:बना|पका)\s*सकते\s+हैं|रेसिपी\s+(?:बताओ|सुझाओ)|"
        r"ಏನು\s+ಅಡುಗೆ\s+ಮಾಡಬಹುದು|ರೆಸಿಪಿ\s*(?:ತಿಳಿಸು|ಹೇಳು)",
        low,
        flags=re.UNICODE,
    ):
        return _intent(IntentName.SUGGEST_RECIPES, {"query": text})

    if re.search(
        r"\b(?:check|show|list|what(?:'s|\s+is)\s+in)\s+(?:the\s+|my\s+)?(?:pantry|inventory)\b|"
        r"\b(?:low[\s\-]stock|missing\s+grocer(?:y|ies))\b|"
        r"राशन\s+स्टॉक|कम\s+सामान|ದಿನಸಿ\s+ಸ್ಟಾಕ್|ಕಡಿಮೆ\s+ವಸ್ತು",
        low,
        flags=re.UNICODE,
    ):
        return _intent(IntentName.CHECK_INVENTORY)

    if re.search(
        r"\b(?:list|show|what(?:'s|\s+is)\s+on)\s+(?:my\s+|the\s+)?(?:grocery|shopping)\s+list\b|"
        r"(?:राशन|किराना|शॉपिंग)\s+(?:लिस्ट|सूची)\s*(?:दिखाओ|बताओ)|"
        r"(?:ಕಿರಾಣಿ|ದಿನಸಿ|ಶಾಪಿಂಗ್)\s+ಪಟ್ಟಿ\s*(?:ತೋರಿಸು|ಹೇಳು)",
        low,
        flags=re.UNICODE,
    ):
        return _intent(IntentName.LIST_GROCERY_LIST)

    m_groc_add = re.search(
        r"(?:add|put)\s+(.+?)\s+to\s+(?:my\s+|the\s+)?(?:grocery|shopping)\s+list|"
        r"(?:राशन|किराना|शॉपिंग)\s+(?:लिस्ट|सूची)\s+में\s+(.+?)\s+(?:जोड़ो|डालो)|"
        r"(?:ಕಿರಾಣಿ|ದಿನಸಿ|ಶಾಪಿಂಗ್)\s+ಪಟ್ಟಿಗೆ\s+(.+?)\s+(?:ಸೇರಿಸು|ಸೇರಿಸಿ|ಹಾಕು)",
        text,
        flags=re.IGNORECASE | re.UNICODE,
    )
    if m_groc_add:
        item_raw = next((g for g in m_groc_add.groups() if g), "").strip()
        if item_raw:
            return _intent(IntentName.ADD_GROCERY_ITEM, {"item_name": item_raw, "quantity": 1, "unit": "unit"})

    # 3. Expenses
    if re.search(
        r"\b(?:expense\s+summary|total\s+expenses?|how\s+much\s+did\s+i\s+spend|show\s+(?:my\s+)?(?:daily|monthly)?\s*expenses?)\b|"
        r"(?:मासिक|दैनिक|कुल)\s+खर्च|ಖರ್ಚು\s*(?:ಸಾರಾಂಶ|ತೋರಿಸು)|ತಿಂಗಳ\s+ಖರ್ಚು",
        low,
        flags=re.UNICODE,
    ):
        period = "day" if re.search(r"\btoday|daily|आज|दैनिक|ಇಂದು|ದಿನದ\b", low) else "month"
        return _intent(IntentName.EXPENSE_SUMMARY, {"period": period})

    m_exp = re.search(
        r"(?:\b(?:log|add|record|spent)\s+(?:an?\s+)?(?:expense\s+(?:of\s+)?)?(?:rs\.?|inr|₹)?\s*(\d+(?:\.\d{1,2})?)\s*(?:rupees?|rs\.?|inr|₹)?(?:\s+(?:on|for)\s+(.+))?$)|"
        r"(?:(\d+(?:\.\d{1,2})?)\s*(?:रुपये|रु)?\s*(.+?)\s*(?:पर|में)\s+खर्च\s+(?:लिखो|जोड़ो|हुए))|"
        r"(?:(\d+(?:\.\d{1,2})?)\s*(?:ರೂಪಾಯಿ)?\s*(.+?)\s*(?:ಗೆ|ಮೇಲೆ)\s+ಖರ್ಚು\s+(?:ಸೇರಿಸು|ಸೇರಿಸಿ))",
        text,
        flags=re.IGNORECASE | re.UNICODE,
    )
    if m_exp:
        amt = m_exp.group(1) or m_exp.group(3) or m_exp.group(5)
        cat = (m_exp.group(2) or m_exp.group(4) or m_exp.group(6) or "general").strip(" .")
        if amt:
            return _intent(
                IntentName.ADD_EXPENSE,
                {"amount": amt, "category": cat or "general"},
            )

    # 4. Parcels
    if re.search(
        r"\b(?:list|show|check|track)\s+(?:my\s+|saved\s+)?parcels?\b|\bmy\s+deliveries\b|"
        r"पार्सल\s*(?:दिखाओ|लिस्ट|बताओ)|ಪಾರ್ಸೆಲ್\s*(?:ತೋರಿಸು|ಪಟ್ಟಿ|ಹೇಳು)",
        low,
        flags=re.UNICODE,
    ):
        return _intent(IntentName.LIST_PARCELS)

    m_parcel = re.search(
        r"(?:add|save)\s+(?:a\s+)?parcel\s+(?:for\s+)?(.+)",
        text,
        flags=re.IGNORECASE,
    )
    if m_parcel and m_parcel.group(1).strip():
        return _intent(IntentName.ADD_PARCEL, {"order_name": m_parcel.group(1).strip(" .")})

    # 5. Tasks
    if re.search(
        r"\bwhat tasks are (?:still )?pending\b|\b(?:list|show|what\s+are)\s+(?:my\s+)?(?:pending\s+|open\s+)?tasks?\b|\bmy\s+to[\s\-]?do\s+list\b|"
        r"(?:मेरा|मेरे)\s+टास्क\s*(?:दिखाओ|लिस्ट|बताओ)|ನನ್ನ\s+ಟಾಸ್ಕ್\s*(?:ತೋರಿಸು|ಪಟ್ಟಿ|ಹೇಳು)",
        low,
        flags=re.UNICODE,
    ):
        return _intent(IntentName.LIST_TASKS, {"status": "pending"})

    m_task_done = re.search(
        r"(?:complete|finish|mark\s+done)\s+(?:the\s+|my\s+)?task(?:\s+(.+))?|"
        r"टास्क\s+(?:पूरा|खत्म)\s+करो|ಟಾಸ್ಕ್\s+(?:ಪೂರ್ಣಗೊಳಿಸು|ಮುಗಿಸು)",
        text,
        flags=re.IGNORECASE | re.UNICODE,
    )
    if m_task_done:
        return _intent(
            IntentName.COMPLETE_TASK,
            {"query": (m_task_done.group(1) or "").strip(" .")},
        )

    m_task_add = re.search(
        r"(?:add|create)\s+(?:a\s+)?(?:new\s+)?task(?:\s+to)?\s+(.+)|"
        r"टास्क\s+(?:जोड़ो|बनाओ)\s*(?::)?\s*(.+)|"
        r"ಟಾಸ್ಕ್\s+(?:ಸೇರಿಸು|ಸೇರಿಸಿ|ರಚಿಸು)\s*(?::)?\s*(.+)",
        text,
        flags=re.IGNORECASE | re.UNICODE,
    )
    if m_task_add:
        title = next((g for g in m_task_add.groups() if g), "").strip(" .")
        if title:
            return _intent(IntentName.CREATE_TASK, {"title": title})

    # 6. Calendar events
    if re.search(
        r"\b(?:show|list|check|what(?:'s|\s+is)\s+on)\s+(?:my\s+)?(?:calendar|schedule|events?|meetings?)\b|"
        r"(?:आज|कल)\s+के\s+(?:इवेंट|मीटिंग|कैलेंडर)|(?:ಇಂದಿನ|ನಾಳೆಯ)\s+(?:ಕ್ಯಾಲೆಂಡರ್|ಮೀಟಿಂಗ್)",
        low,
        flags=re.UNICODE,
    ):
        rk = "tomorrow" if re.search(r"\btomorrow|कल|ನಾಳೆ\b", low) else (
            "today" if re.search(r"\btoday|आज|ಇಂದು|ಇಂದಿನ\b", low) else "upcoming"
        )
        return _intent(IntentName.LIST_CALENDAR_EVENTS, {"range_kind": rk})

    m_cal_add = re.search(
        r"(?:add|schedule|create)\s+(?:a\s+)?(?:calendar\s+)?(?:event|meeting|appointment)\s+(.+)",
        text,
        flags=re.IGNORECASE,
    )
    if m_cal_add:
        spec = _parse_clock_and_recurrence(text)
        if spec is None or spec.get("ambiguous"):
            return TypedIntent(
                intent_name=IntentName.CLARIFY,
                confidence=0.9,
                source="clarification",
                route_category="clarification",
                language=norm.language,
                input_script=norm.script,
                clarification_question={
                    "en": "Please specify the exact time (AM or PM) for this calendar event.",
                    "hi": "कृपया इस कैलेंडर इवेंट के लिए सुबह या शाम का सही समय बताएं।",
                    "kn": "ದಯವಿಟ್ಟು ಈ ಕ್ಯಾಲೆಂಡರ್ ಕಾರ್ಯಕ್ರಮಕ್ಕೆ ನಿಖರ ಸಮಯವನ್ನು (ಬೆಳಗ್ಗೆ ಅಥವಾ ಸಂಜೆ) ತಿಳಿಸಿ.",
                }[norm.language],
                raw_text=norm.raw,
            )
        title = re.sub(
            r"\b(?:every\s+(?:day|morning|evening|night)|daily|tomorrow|today|at\s+\d{1,2}(?::\d{2})?(?:\s*(?:a\.?m\.?|p\.?m\.?))?)\b",
            " ",
            m_cal_add.group(1),
            flags=re.IGNORECASE,
        ).strip(" .")
        return _intent(
            IntentName.CREATE_CALENDAR_EVENT,
            {"title": title or "Calendar event", "time_spec": spec},
        )

    return None


class MultilingualFastRouter:
    """Deterministic Tier-0 router combining Hassil YAML grammars and Unicode guards."""

    def __init__(self, intents_dir: Path | None = None) -> None:
        base = intents_dir or _INTENTS_DIR
        self._intents: dict[LanguageCode, Intents] = {}
        for lang in ("en", "hi", "kn"):
            yaml_path = base / lang / "phase1.yaml"
            if yaml_path.is_file():
                with yaml_path.open("r", encoding="utf-8") as fh:
                    self._intents[lang] = Intents.from_yaml(fh)  # type: ignore[index]

    def _try_hassil_single(self, norm: NormalizedText) -> TypedAction | None:
        # Try detected language first, then fallback languages in case of Romanized phrasing
        search_order: list[LanguageCode] = [norm.language]
        for other in ("en", "hi", "kn"):
            typed_other: LanguageCode = other  # type: ignore[assignment]
            if typed_other not in search_order:
                search_order.append(typed_other)

        for lang in search_order:
            intents = self._intents.get(lang)
            if intents is None:
                continue
            res = recognize_best(norm.normalized.lower(), intents)
            if res is None:
                continue
            intent_enum = _INTENT_MAP.get(res.intent.name)
            if intent_enum is None:
                continue

            slots: dict[str, object] = {}
            if intent_enum == IntentName.SET_TIMER:
                dur = extract_duration_seconds(norm.normalized, require_all_quantities=True)
                if dur is None:
                    continue
                slots["duration_seconds"] = dur
                lbl_ent = res.entities.get("timer_label")
                lbl = _clean_label(str(lbl_ent.value)) if lbl_ent is not None else None
                if lbl is None:
                    lbl = _extract_label_from_timer_text(norm.normalized)
                if lbl:
                    slots["label"] = lbl
            elif intent_enum in (
                IntentName.GET_TIMER_REMAINING,
                IntentName.PAUSE_TIMER,
                IntentName.RESUME_TIMER,
                IntentName.CANCEL_TIMER,
            ):
                lbl_ent = res.entities.get("timer_label")
                lbl = _clean_label(str(lbl_ent.value)) if lbl_ent is not None else None
                if lbl is None:
                    lbl = _extract_label_from_timer_text(norm.normalized)
                if lbl:
                    slots["label"] = lbl

            return TypedAction(intent_name=intent_enum, slots=slots, confidence=0.98)

        return None

    def _try_keyword_timer_or_clock(self, norm: NormalizedText) -> TypedAction | None:
        """Catch natural EN/HI/KN phrasing variations that Hassil didn't enumerate verbatim."""
        text = norm.normalized
        low = text.lower()

        arithmetic = re.fullmatch(
            r"(?:what is |what's |calculate )?(-?\d{1,9})\s*"
            r"(times|multiplied by|plus|minus|[+*×−-])\s*(-?\d{1,9})", low
        )
        if arithmetic:
            left, operator, right = arithmetic.groups()
            return TypedAction(intent_name=IntentName.CALCULATE, slots={
                "left": int(left), "operator": operator, "right": int(right),
            })

        # Time / Date quick catches
        if re.search(
            r"(?:\bwhat(?:'s|\s+is)?\s+(?:the\s+)?time\b|\bwhat\s+time\s+is\s+it\b|\bcurrent\s+time\b|"
            r"(?:क्या\s+)?समय\s*(?:क्या|हुआ|हुए|बता)|कितने\s+बजे|टाइम\s*(?:क्या|कितना)\s*(?:हुआ|है)|"
            r"kitne\s+baje|samay\s+batao|time\s+kya\s+(?:hua|hai)|"
            r"ಎಷ್ಟು\s+ಗಂಟೆ|ಗಂಟೆ\s+ಎಷ್ಟು|ಸಮಯ\s*(?:ಎಷ್ಟು|ಏನು|ಹೇಳು)|ಟೈಮ್\s*(?:ಎಷ್ಟು|ಏನು|ಹೇಳು)|"
            r"eshtu\s+gante|samaya\s+eshtu|time\s+eshtu)",
            low,
            flags=re.UNICODE,
        ):
            return TypedAction(intent_name=IntentName.GET_TIME, confidence=0.92)

        if re.search(
            r"(?:\btoday(?:'s)?\s+date\b|\bdate\s+today\b|\bwhat(?:'s|\s+is)?\s+(?:the\s+|today(?:'s)?\s+)?(?:date|day)\b|"
            r"(?:तारी[खक]|डेट)\s*(?:क्या|कितनी|बता)|आज\s*(?:की\s+)?(?:तारी[खक]|डेट)\s*(?:क्या|बता)?|आज\s+(?:क्या|क[ौो]न\s*सा|कोंसा)\s+दिन|"
            r"aaj\s+(?:ki\s+)?(?:kya\s+)?(?:tareekh|tarikh|date)|"
            r"(?:ಇವತ್ತ\w*|ಇಂದು).*?(?:ಡೇಟ್|ದಿನಾಂಕ|ತಾರೀಖು).*?(?:ಏನು|ಎಷ್ಟು)|"
            r"(?:ಡೇಟ್|ದಿನಾಂಕ|ತಾರೀಖು)\s*(?:ಏನು|ಎಷ್ಟು|ಹೇಳು)|ಯಾವ\s*(?:ದಿನ|ತಾರೀಖು)|"
            r"dinanka\s+(?:enu|eshtu)|tareekhu\s+eshtu|(?:ivatt\w*|indu)\s+(?:date|dinanka|tareekhu)\s*(?:enu|eshtu)?)",
            low,
            flags=re.UNICODE,
        ):
            return TypedAction(intent_name=IntentName.GET_DATE, confidence=0.92)

        has_timer_word = bool(
            re.search(r"(?:\btimer\b|\btimers\b|टाइमर|ಟೈಮರ್)", low, flags=re.UNICODE)
        )
        if not has_timer_word:
            return None

        dur = extract_duration_seconds(text, require_all_quantities=True)
        if dur is not None and _TIMER_ACTION_KEYWORDS["set"].search(text):
            slots: dict[str, object] = {"duration_seconds": dur}
            lbl = _extract_label_from_timer_text(text)
            if lbl:
                slots["label"] = lbl
            return TypedAction(intent_name=IntentName.SET_TIMER, slots=slots, confidence=0.90)

        if _TIMER_ACTION_KEYWORDS["cancel"].search(text):
            if _TIMER_ACTION_KEYWORDS["cancel_all"].search(text):
                return TypedAction(intent_name=IntentName.CANCEL_ALL_TIMERS, confidence=0.90)
            lbl = _extract_label_from_timer_text(text)
            return TypedAction(
                intent_name=IntentName.CANCEL_TIMER,
                slots={"label": lbl} if lbl else {},
                confidence=0.88,
            )

        if _TIMER_ACTION_KEYWORDS["pause"].search(text):
            lbl = _extract_label_from_timer_text(text)
            return TypedAction(
                intent_name=IntentName.PAUSE_TIMER,
                slots={"label": lbl} if lbl else {},
                confidence=0.88,
            )

        if _TIMER_ACTION_KEYWORDS["resume"].search(text):
            lbl = _extract_label_from_timer_text(text)
            return TypedAction(
                intent_name=IntentName.RESUME_TIMER,
                slots={"label": lbl} if lbl else {},
                confidence=0.88,
            )

        if _TIMER_ACTION_KEYWORDS["remaining"].search(text):
            lbl = _extract_label_from_timer_text(text)
            return TypedAction(
                intent_name=IntentName.GET_TIMER_REMAINING,
                slots={"label": lbl} if lbl else {},
                confidence=0.88,
            )

        if _TIMER_ACTION_KEYWORDS["list"].search(text) or re.search(
            r"\b(?:timers|टाइमरों|ಟೈಮರ್‌ಗಳು|ಟೈಮರ್‌ಗಳನ್ನು)\b", low, flags=re.UNICODE
        ):
            return TypedAction(intent_name=IntentName.LIST_TIMERS, confidence=0.88)

        return None

    def route(
        self,
        raw_text: str,
        *,
        requested_language: RequestedLanguage = "auto",
        fallback_language: LanguageCode = "en",
    ) -> tuple[TypedIntent | None, float]:
        """Attempt deterministic routing. Returns ``(TypedIntent | None, router_ms)``."""
        t0 = time.perf_counter()
        norm = normalize_utterance(
            raw_text,
            requested_language=requested_language,
            fallback_language=fallback_language,
        )

        if not norm.normalized or not any(ch.isalnum() for ch in norm.normalized):
            return (
                TypedIntent(
                    intent_name=IntentName.CLARIFY,
                    confidence=1.0,
                    source="clarification",
                    route_category="clarification",
                    language=norm.language,
                    input_script=norm.script,
                    clarification_question=get_phrase("empty_speech", norm.language),
                    raw_text=raw_text,
                ),
                round((time.perf_counter() - t0) * 1000.0, 2),
            )

        if _HYPOTHETICAL.search(norm.normalized):
            return TypedIntent(intent_name=IntentName.CONVERSATION, confidence=1.0, source="hassil_fast",
                               route_category="conversation", language=norm.language, input_script=norm.script,
                               raw_text=raw_text), round((time.perf_counter() - t0) * 1000.0, 2)

        # Split compound commands safely (preserving "1 minute and 30 seconds")
        clauses = []
        for segment in _COMPOUND_SPLIT_RE.split(norm.normalized):
            segment = segment.strip()
            if segment and clauses and segment[0].isdigit():
                clauses[-1] += " " + segment
            elif segment:
                clauses.append(segment)

        # Keep free-form reminder messages and shopping item names intact. A leading
        # device instruction or briefing followed by another instruction is a plan.
        compound_tool = len(clauses) > 1 and (
            _try_smart_home_action(clauses[0]) is not None
            or re.search(r"\b(?:briefing|brief me|summari[sz]e my day)\b", clauses[0], re.IGNORECASE)
        )
        unsafe_power_timing = _POWER_REQUEST_RE.search(norm.normalized) and (
            _TEMPORARY_POWER_RE.search(norm.normalized)
            or (_AFTER_RE.search(norm.normalized) and _extract_delay_from_phrase(norm.normalized)[0] is None)
        )
        invalid_timer = any(
            re.search(r"\b(?:set|start|create)\b.*?\btimer\b", clause, re.IGNORECASE)
            and re.search(r"\d", clause)
            and extract_duration_seconds(clause, require_all_quantities=True) is None
            for clause in clauses
        )
        if (len(clauses) > 4 or unsafe_power_timing or invalid_timer or _NEGATED_REQUEST.search(norm.normalized)
                or any(pattern.search(norm.normalized) for pattern in _AMBIGUOUS_PATTERNS)):
            return (
                TypedIntent(
                    intent_name=IntentName.CLARIFY,
                    confidence=0.95,
                    source="clarification",
                    route_category="clarification",
                    language=norm.language,
                    input_script=norm.script,
                    clarification_question=get_phrase("ambiguous_request", norm.language),
                    raw_text=raw_text,
                ),
                round((time.perf_counter() - t0) * 1000.0, 2),
            )

        mem_intent = _try_memory_action(norm)
        if mem_intent is not None:
            return mem_intent, round((time.perf_counter() - t0) * 1000.0, 2)

        # Preserve explicit Phase 1 Hassil countdown timers ("Remind me in 45 seconds", etc.)
        if len(clauses) == 1 and "to " not in norm.normalized.lower() and not re.search(
            r"\b(?:light|fan|ac|tv|curtain|washer|vacuum|routine|reminder|शेड्यूल|रिमाइंडर|ಜ್ಞಾಪನೆ|ರಿಮೈಂಡರ್)\b",
            norm.normalized.lower(),
            flags=re.UNICODE,
        ):
            hassil_timer = self._try_hassil_single(norm)
            if hassil_timer is not None and hassil_timer.intent_name == IntentName.SET_TIMER:
                return (
                    TypedIntent(
                        intent_name=hassil_timer.intent_name,
                        confidence=hassil_timer.confidence,
                        source="hassil_fast",
                        route_category="tool",
                        language=norm.language,
                        input_script=norm.script,
                        slots=hassil_timer.slots,
                        sub_actions=[hassil_timer],
                        raw_text=raw_text,
                    ),
                    round((time.perf_counter() - t0) * 1000.0, 2),
                )

        sched_intent = None if compound_tool else _try_schedule_or_reminder_action(norm)
        if sched_intent is not None:
            return sched_intent, round((time.perf_counter() - t0) * 1000.0, 2)

        p4_intent = None if compound_tool else _try_phase4_action(norm)
        if p4_intent is not None:
            return p4_intent, round((time.perf_counter() - t0) * 1000.0, 2)

        if len(clauses) > 1:
            sub_actions: list[TypedAction] = []
            for clause in clauses:
                sub_norm = normalize_utterance(
                    clause,
                    requested_language=requested_language,
                    fallback_language=norm.language,
                )
                structured = _try_schedule_or_reminder_action(sub_norm) or _try_phase4_action(sub_norm)
                if structured is not None and structured.route_category != "tool":
                    return structured, round((time.perf_counter() - t0) * 1000.0, 2)
                act = (
                    (TypedAction(intent_name=structured.intent_name, slots=structured.slots) if structured else None)
                    or self._try_hassil_single(sub_norm)
                    or self._try_keyword_timer_or_clock(sub_norm)
                    or _try_smart_home_action(sub_norm.normalized)
                )
                if act is None:
                    # Do NOT partially execute unvalidated compound actions deterministically
                    return None, round((time.perf_counter() - t0) * 1000.0, 2)
                sub_actions.append(act)

            return (
                TypedIntent(
                    intent_name=sub_actions[0].intent_name,
                    confidence=min(a.confidence for a in sub_actions),
                    source="hassil_fast",
                    route_category="tool",
                    language=norm.language,
                    input_script=norm.script,
                    slots=sub_actions[0].slots,
                    sub_actions=sub_actions,
                    raw_text=raw_text,
                ),
                round((time.perf_counter() - t0) * 1000.0, 2),
            )

        action = (
            self._try_hassil_single(norm)
            or self._try_keyword_timer_or_clock(norm)
            or _try_smart_home_action(norm.normalized)
        )
        if action is not None:
            router_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            return (
                TypedIntent(
                    intent_name=action.intent_name,
                    confidence=action.confidence,
                    source="hassil_fast",
                    route_category="tool",
                    language=norm.language,
                    input_script=norm.script,
                    slots=action.slots,
                    sub_actions=[action],
                    raw_text=raw_text,
                ),
                router_ms,
            )

        # Standalone conversational micro-intents (greetings, thanks, farewells, pure language switches)
        clean_low = norm.normalized.strip().lower()
        if _GREETING_RE.match(clean_low):
            return (
                TypedIntent(
                    intent_name=IntentName.GREETING,
                    confidence=0.98,
                    source="hassil_fast",
                    route_category="greeting",
                    language=norm.language,
                    input_script=norm.script,
                    raw_text=raw_text,
                ),
                round((time.perf_counter() - t0) * 1000.0, 2),
            )
        if _ACKNOWLEDGMENT_RE.match(clean_low):
            return (
                TypedIntent(
                    intent_name=IntentName.ACKNOWLEDGMENT,
                    confidence=0.96,
                    source="hassil_fast",
                    route_category="greeting",
                    language=norm.language,
                    input_script=norm.script,
                    raw_text=raw_text,
                ),
                round((time.perf_counter() - t0) * 1000.0, 2),
            )
        if _FAREWELL_RE.match(clean_low):
            return (
                TypedIntent(
                    intent_name=IntentName.FAREWELL,
                    confidence=0.97,
                    source="hassil_fast",
                    route_category="greeting",
                    language=norm.language,
                    input_script=norm.script,
                    raw_text=raw_text,
                ),
                round((time.perf_counter() - t0) * 1000.0, 2),
            )
        if _IDENTITY_RE.match(clean_low):
            return (
                TypedIntent(
                    intent_name=IntentName.IDENTITY,
                    confidence=0.98,
                    source="hassil_fast",
                    route_category="greeting",
                    language=norm.language,
                    input_script=norm.script,
                    raw_text=raw_text,
                ),
                round((time.perf_counter() - t0) * 1000.0, 2),
            )
        if _SUPPORTED_LANGUAGES_RE.match(clean_low):
            return (
                TypedIntent(
                    intent_name=IntentName.SUPPORTED_LANGUAGES,
                    confidence=0.98,
                    source="hassil_fast",
                    route_category="greeting",
                    language=norm.language,
                    input_script=norm.script,
                    raw_text=raw_text,
                ),
                round((time.perf_counter() - t0) * 1000.0, 2),
            )
        lang_switch_match = _PURE_LANG_SWITCH_RE.match(norm.normalized.strip())
        if lang_switch_match and any(
            kw in clean_low
            for kw in (
                "switch",
                "change",
                "speak",
                "talk",
                "respond",
                "reply",
                "भाषा",
                "बोलो",
                "बात",
                "ಮಾತಾಡು",
                "ಮಾತನಾಡಿ",
                "ಹೇಳು",
            )
        ):
            raw_target = lang_switch_match.group(1).strip().lower()
            target_lang = _LANG_NAME_MAP.get(raw_target, norm.language)
            return (
                TypedIntent(
                    intent_name=IntentName.SWITCH_LANGUAGE,
                    confidence=0.97,
                    source="hassil_fast",
                    route_category="language_switch",
                    language=target_lang,
                    target_language=target_lang,
                    input_script=norm.script,
                    slots={"target_language": target_lang},
                    raw_text=raw_text,
                ),
                round((time.perf_counter() - t0) * 1000.0, 2),
            )
        if _RESTRICTED_ACTION_RE.search(norm.normalized):
            return (
                TypedIntent(
                    intent_name=IntentName.UNSUPPORTED,
                    confidence=0.96,
                    source="refusal",
                    route_category="refusal",
                    language=norm.language,
                    input_script=norm.script,
                    raw_text=raw_text,
                ),
                round((time.perf_counter() - t0) * 1000.0, 2),
            )

        return None, round((time.perf_counter() - t0) * 1000.0, 2)
