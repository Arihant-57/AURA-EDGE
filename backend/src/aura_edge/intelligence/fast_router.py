"""Deterministic multilingual Hassil intent router for English, Hindi, and Kannada."""

from __future__ import annotations

import re
import time
from pathlib import Path

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
            r"(?:\btoday(?:'s)?\s+date\b|\bwhat\s+date\b|\bwhat\s+day\b|तारीख\s+(?:क्या|बता)|आज\s+क्या\s+दिन|aaj\s+kya\s+tareekh|ದಿನಾಂಕ\s+(?:ಏನು|ಎಷ್ಟು|ಹೇಳು)|ಯಾವ\s+ದಿನ|dinanka\s+enu)",
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

        if not norm.normalized:
            return (
                TypedIntent(
                    intent_name=IntentName.CLARIFY,
                    confidence=1.0,
                    source="clarification",
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

        action = self._try_hassil_single(norm) or self._try_keyword_timer_or_clock(norm)
        router_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        if action is None:
            return None, router_ms

        return (
            TypedIntent(
                intent_name=action.intent_name,
                confidence=action.confidence,
                source="hassil_fast",
                language=norm.language,
                input_script=norm.script,
                slots=action.slots,
                sub_actions=[action],
                raw_text=raw_text,
            ),
            router_ms,
        )
