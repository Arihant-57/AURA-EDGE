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
]

# Compound clause separator that avoids splitting "1 minute and 30 seconds"
_COMPOUND_SPLIT_RE = re.compile(
    r"\s*(?:;|,\s*and\s+|\s+and\s+then\s+|\s+and\s+|\s+aur\s+phir\s+|\s+और\s+फिर\s+|\s+और\s+|\s+ಮತ್ತು\s+ನಂತರ\s+|\s+ಮತ್ತು\s+)\s*",
    flags=re.IGNORECASE | re.UNICODE,
)

_TIME_UNIT_START_RE = re.compile(
    r"^\s*\d+\s*(?:hours?|hrs?|hr|h|minutes?|mins?|min|m|seconds?|secs?|sec|s|"
    r"घंट[ाे]|मिनट|सेकंड|ನಿಮಿಷ|ಸೆಕೆಂಡ್|ಗಂಟೆ)",
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
    r"\b(?:order|buy|purchase)\b.*\b(?:food|pizza|biryani|groceries|online)\b|"
    r"\btransfer\b.*\b(?:rupees|dollars|money|funds|bank|friend)\b|"
    r"\b(?:send|compose)\b.*\b(?:email|gmail|sms|whatsapp|message)\b|"
    r"(?:ऑर्डर|खरीदो|पैसे\s+भेजो|बैंक\s+से|ईमेल\s+.*भेजो)|"
    r"(?:ಆರ್ಡರ್|ಹಣ\s+ಕಳುಹಿಸು|ಬ್ಯಾಂಕ್\s+ಖಾತೆ|ಇಮೇಲ್\s+ಕಳುಹಿಸು))",
    flags=re.IGNORECASE | re.UNICODE,
)

_SMART_HOME_ROOMS = {
    "living_room": ("living room", "hall", "बैठक", "ಹಾಲ್"),
    "bedroom": ("bedroom", "बेडरूम", "ಮಲಗುವ ಕೋಣೆ", "ಬೆಡ್ ರೂಮ್"),
    "kitchen": ("kitchen", "रसोई", "ಅಡುಗೆಮನೆ"),
    "study": ("study", "अध्ययन", "ಪഠಣ", "ಅಧ್ಯಯನ"),
    "utility_room": ("utility", "यूटिलिटी", "ಯುಟಿಲಿಟಿ"),
}
_SMART_HOME_TYPES = {
    "light": ("light", "lights", "lamp", "लाइट", "बत्ती", "ಲೈಟ್", "ದೀಪ"),
    "fan": ("fan", "पंखा", "फैन", "ಫ್ಯಾನ್"),
    "air_conditioner": ("ac", "air conditioner", "एसी", "ಏಸಿ"),
    "television": ("tv", "television", "टीवी", "ಟಿವಿ"),
    "curtain": ("curtain", "curtains", "पर्दा", "पर्दे", "ಪರದೆ"),
    "washing_machine": ("washing machine", "washer", "वॉशिंग मशीन", "ವಾಷಿಂಗ್ ಮೆಷಿನ್"),
    "robot_vacuum": ("vacuum", "robot", "वैक्यूम", "ವ್ಯಾಕ್ಯೂಮ್"),
    "air_purifier": ("purifier", "प्यूरिफायर", "ಪ್ಯೂರಿಫೈಯರ್"),
    "water_heater": ("water heater", "geyser", "गीजर", "ಗೀಸರ್"),
    "water_pump": ("water pump", "pump", "पंप", "ಪಂಪ್"),
    "refrigerator": ("refrigerator", "fridge", "फ्रिज", "ರೆಫ್ರಿಜರೇಟರ್"),
}


def _contains_alias(text: str, aliases: tuple[str, ...]) -> bool:
    return any(alias in text for alias in aliases)


def _try_smart_home_action(text: str) -> TypedAction | None:
    low = text.lower()
    room = next((key for key, aliases in _SMART_HOME_ROOMS.items() if _contains_alias(low, aliases)), None)
    kind = next((key for key, aliases in _SMART_HOME_TYPES.items() if _contains_alias(low, aliases)), None)
    if kind is None:
        return None
    slots: dict[str, Any] = {"room": room, "device_type": kind}
    number = re.search(r"(?<!\d)(\d{1,3})(?:\s*(?:degrees?|percent|%|डिग्री|ಡಿಗ್ರಿ))?", low)
    if re.search(r"\b(?:turn|switch)\s+on\b|\bon\s+करो|चालू|ಆನ್\s+ಮಾಡು|ಆನ್", low):
        slots.update(action="power", value=True)
    elif re.search(r"\b(?:turn|switch)\s+off\b|\boff\s+करो|बंद\s+करो|ಆಫ್\s+ಮಾಡು|ಆಫ್", low):
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
    elif number and re.search(r"brightness|bright|रोशनी|ब्राइटनेस|ಪ್ರಕಾಶ", low):
        slots.update(action="brightness", value=int(number.group(1)))
    elif number and re.search(r"speed|गति|स्पीड|ವೇಗ", low):
        slots.update(action="speed", value=int(number.group(1)))
    elif number and kind == "air_conditioner":
        slots.update(action="temperature", value=int(number.group(1)))
    elif number and kind == "television" and re.search(r"volume|आवाज़|ವಾಲ್ಯೂಮ್", low):
        slots.update(action="volume", value=int(number.group(1)))
    elif re.search(r"status|state|how long|remaining|स्थिति|कितना समय|ಸ್ಥಿತಿ|ಎಷ್ಟು ಸಮಯ", low):
        slots.update(action="status", value=None)
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
    "set": re.compile(rf"{_U_B_START}(?:set|start|create|add|remind|लगाओ|लगा|सेट|शुरू|रखो|lagao|ಇಡು|ಹಾಕು|ಸೆಟ್|idu|haaku){_U_B_END}", re.IGNORECASE | re.UNICODE),
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
                dur = extract_duration_seconds(norm.normalized)
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

        # Time / Date quick catches
        if re.search(
            r"(?:\bwhat(?:'s|\s+is)?\s+(?:the\s+)?time\b|\bcurrent\s+time\b|(?:क्या\s+)?समय\s*(?:क्या|हुआ|हुए|बता)|कितने\s+बजे|kitne\s+baje|samay\s+batao|ಎಷ್ಟು\s+ಗಂಟೆ|ಸಮಯ\s+ಎಷ್ಟು|eshtu\s+gante)",
            low,
            flags=re.UNICODE,
        ):
            return TypedAction(intent_name=IntentName.GET_TIME, confidence=0.92)

        if re.search(
            r"(?:\btoday(?:'s)?\s+date\b|\bwhat(?:'s|\s+is)?\s+(?:the\s+)?(?:date|day)\b|तारीख\s+(?:क्या|बता)|आज\s+क्या\s+दिन|aaj\s+kya\s+tareekh|(?:ಇವತ್ತ\w*|ಇಂದು).*?(?:ಡೇಟ್|ದಿನಾಂಕ).*?ಏನು|ದಿನಾಂಕ\s+(?:ಏನು|ಎಷ್ಟು|ಹೇಳು)|ಯಾವ\s+ದಿನ|dinanka\s+enu)",
            low,
            flags=re.UNICODE,
        ):
            return TypedAction(intent_name=IntentName.GET_DATE, confidence=0.92)

        has_timer_word = bool(
            re.search(r"(?:\btimer\b|\btimers\b|टाइमर|ಟೈಮರ್)", low, flags=re.UNICODE)
        )
        if not has_timer_word:
            return None

        dur = extract_duration_seconds(text)
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

        for amb_re in _AMBIGUOUS_PATTERNS:
            if amb_re.search(norm.normalized):
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

        # Split compound commands safely (preserving "1 minute and 30 seconds")
        clauses = [c.strip() for c in _COMPOUND_SPLIT_RE.split(norm.normalized) if c.strip()]
        if len(clauses) > 1 and any(_TIME_UNIT_START_RE.match(c) for c in clauses[1:]):
            clauses = [norm.normalized]

        if len(clauses) > 1:
            sub_actions: list[TypedAction] = []
            for clause in clauses:
                sub_norm = normalize_utterance(
                    clause,
                    requested_language=requested_language,
                    fallback_language=norm.language,
                )
                act = self._try_hassil_single(sub_norm) or self._try_keyword_timer_or_clock(
                    sub_norm
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
