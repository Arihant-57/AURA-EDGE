"""Unicode-safe multilingual text normalization, script/language detection, and duration parsing."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from aura_edge.intelligence.schemas import InputScript, LanguageCode, RequestedLanguage

_DIGIT_TRANSLATION = str.maketrans(
    "०१२३४५६७८९೦೧೨೩೪೫೬೭೮೯",
    "01234567890123456789",
)

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_KANNADA_RE = re.compile(r"[\u0C80-\u0CFF]")
_LATIN_RE = re.compile(r"[A-Za-z]")

_POLITE_PREFIX_RE = re.compile(
    r"^(?:hey\s+aura|aura|ok\s+aura|please|kindly|can\s+you(?:\s+please)?|"
    r"could\s+you(?:\s+please)?|कृपया|ज़रा|जरा|प्लीज|ದಯವಿಟ್ಟು|ಪ್ಲೀಸ್)\s*[,:\-]?\s*",
    flags=re.IGNORECASE | re.UNICODE,
)
_POLITE_SUFFIX_RE = re.compile(
    r"\s+(?:please|for\s+me|right\s+now|कृपया|प्लीज|ದಯವಿಟ್ಟು)$",
    flags=re.IGNORECASE | re.UNICODE,
)

# Spoken number words -> digits separated by English vs Indic/Romanized Indic
_EN_NUMBER_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
    "twenty five": 25,
    "twenty-five": 25,
    "thirty": 30,
    "forty": 40,
    "forty five": 45,
    "forty-five": 45,
    "fifty": 50,
    "sixty": 60,
}

_INDIC_SCRIPT_NUMBER_WORDS: dict[str, int] = {
    "एक": 1,
    "दो": 2,
    "तीन": 3,
    "चार": 4,
    "पांच": 5,
    "पाँच": 5,
    "छह": 6,
    "छे": 6,
    "सात": 7,
    "आठ": 8,
    "नौ": 9,
    "दस": 10,
    "पंद्रह": 15,
    "बीस": 20,
    "पच्चीस": 25,
    "तीस": 30,
    "पैंतालीस": 45,
    "साठ": 60,
    "ಒಂದು": 1,
    "ಎರಡು": 2,
    "ಮೂರು": 3,
    "ನಾಲ್ಕು": 4,
    "ಐದು": 5,
    "ಆರು": 6,
    "ಏಳು": 7,
    "ಎಂಟು": 8,
    "ಒಂಬತ್ತು": 9,
    "ಹತ್ತು": 10,
    "ಹದಿನೈದು": 15,
    "ಇಪ್ಪತ್ತು": 20,
    "ಮೂವತ್ತು": 30,
}

_ROMAN_INDIC_NUMBER_WORDS: dict[str, int] = {
    "ek": 1,
    "do": 2,
    "teen": 3,
    "char": 4,
    "chaar": 4,
    "paanch": 5,
    "panch": 5,
    "chhah": 6,
    "che": 6,
    "saat": 7,
    "aath": 8,
    "nau": 9,
    "das": 10,
    "pandrah": 15,
    "bees": 20,
    "pachees": 25,
    "tees": 30,
    "ondu": 1,
    "eradu": 2,
    "mooru": 3,
    "naalku": 4,
    "aidu": 5,
    "aaru": 6,
    "elu": 7,
    "entu": 8,
    "ombattu": 9,
    "hattu": 10,
    "hadinaidu": 15,
    "ippattu": 20,
    "muvattu": 30,
}

_HALF_HOUR_RE = re.compile(
    r"(?<![\w\u0900-\u097F\u0C80-\u0CFF])"
    r"(?:half\s+an?\s+hour|aadha\s+ghant[ae]|आधा\s+घंट[ाे]|ardha\s+gante|ಅರ್ಧ\s+ಗಂಟೆ)"
    r"(?![\w\u0900-\u097F\u0C80-\u0CFF])",
    flags=re.IGNORECASE | re.UNICODE,
)
_ARTICLE_TIME_UNIT_RE = re.compile(
    r"\b(?:a|an)\s+(second|minute|hour)\b",
    flags=re.IGNORECASE,
)

_UNIT_MULT = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "सेकंड": 1,
    "सेकण्ड": 1,
    "ಸೆಕೆಂಡ್": 1,
    "ಸೆಕೆಂಡು": 1,
    "ಸೆಕೆಂಡ್‌ಗಳು": 1,
    "ಸೆಕೆಂಡುಗಳು": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "मिनट": 60,
    "मिनटों": 60,
    "minat": 60,
    "ನಿಮಿಷ": 60,
    "ನಿಮಿಷಗಳು": 60,
    "ನಿಮಿಷದ": 60,
    "nimisha": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "घंटा": 3600,
    "घंटे": 3600,
    "घंटों": 3600,
    "ghanta": 3600,
    "ghante": 3600,
    "ಗಂಟೆ": 3600,
    "ಗಂಟೆಗಳು": 3600,
    "gante": 3600,
}

_DURATION_CHUNK_RE = re.compile(
    r"(\d+)\s*(hours?|hrs?|hr|h|minutes?|mins?|min|m|seconds?|secs?|sec|s|"
    r"घंट[ाेों]|मिनट(?:ों)?|सेक[ंडण्ड]+|ghant[ae]|minat|"
    r"ಗಂಟೆ(?:ಗಳು)?|ನಿಮಿಷ(?:ಗಳು|ದ)?|ಸೆಕೆಂಡ್(?:‌ಗಳು|ಗಳು)?|ಸೆಕೆಂಡು(?:ಗಳು)?|"
    r"gante|nimisha)",
    flags=re.IGNORECASE | re.UNICODE,
)

_HINGLISH_HINTS = {
    "samay",
    "waqt",
    "tareekh",
    "tarikh",
    "baje",
    "baja",
    "kitna",
    "kitne",
    "lagao",
    "laga",
    "batao",
    "bataye",
    "chalu",
    "band",
    "ruko",
    "roko",
    "hatao",
    "kya",
    "aaj",
    "kaun",
    "din",
    "kitni",
    "der",
    "baki",
    "karo",
    "dijiye",
}

_KANGLISH_HINTS = {
    "samaya",
    "dinanka",
    "eshtu",
    "gante",
    "nimisha",
    "idu",
    "maadu",
    "maadi",
    "haaku",
    "nillisu",
    "nillisi",
    "raddu",
    "yavattu",
    "ivattu",
    "enu",
    "hege",
    "aagide",
    "baaki",
    "kodu",
    "torisu",
}


@dataclass(frozen=True)
class NormalizedText:
    raw: str
    normalized: str
    tokens: list[str]
    script: InputScript
    language: LanguageCode


def unicode_tokens(text: str) -> list[str]:
    """Tokenize preserving Devanagari and Kannada combining vowel signs/virama."""
    return re.findall(r"[\w\u0900-\u097F\u0C80-\u0CFF]+", text, flags=re.UNICODE)


def detect_script(text: str) -> InputScript:
    has_dev = bool(_DEVANAGARI_RE.search(text))
    has_kan = bool(_KANNADA_RE.search(text))
    has_lat = bool(_LATIN_RE.search(text))
    count = int(has_dev) + int(has_kan) + int(has_lat)
    if count > 1 and (has_dev or has_kan):
        return "mixed"
    if has_dev:
        return "devanagari"
    if has_kan:
        return "kannada"
    return "latin"


def detect_language(
    text: str,
    *,
    requested: RequestedLanguage = "auto",
    fallback_language: LanguageCode = "en",
) -> LanguageCode:
    if requested != "auto":
        return requested
    if _DEVANAGARI_RE.search(text):
        return "hi"
    if _KANNADA_RE.search(text):
        return "kn"
    tokens = {t.lower() for t in unicode_tokens(text)}
    if tokens & _HINGLISH_HINTS:
        return "hi"
    if tokens & _KANGLISH_HINTS:
        return "kn"
    if tokens:
        return "en"
    return fallback_language


_UNIT_FOLLOWUP_RE = r"(?=\s+(?:seconds?|secs?|sec|minutes?|mins?|min|minat|nimisha|hours?|hrs?|hr|ghant[ae]|gante)\b)"


def _replace_number_words(text: str, *, language: LanguageCode = "en") -> str:
    out = _HALF_HOUR_RE.sub("30 minutes", text)
    out = _ARTICLE_TIME_UNIT_RE.sub(r"1 \1", out)

    active_maps: list[tuple[dict[str, int], bool]] = [
        (_INDIC_SCRIPT_NUMBER_WORDS, False),
        (_EN_NUMBER_WORDS, False),
        (_ROMAN_INDIC_NUMBER_WORDS, language == "en"),
    ]
    for word_map, require_unit_followup in active_maps:
        for phrase in sorted(word_map.keys(), key=len, reverse=True):
            val = str(word_map[phrase])
            suffix = (
                _UNIT_FOLLOWUP_RE
                if require_unit_followup
                else r"(?![\w\u0900-\u097F\u0C80-\u0CFF])"
            )
            pattern = rf"(?<![\w\u0900-\u097F\u0C80-\u0CFF]){re.escape(phrase)}{suffix}"
            out = re.sub(pattern, val, out, flags=re.IGNORECASE | re.UNICODE)
    return out


def extract_duration_seconds(text: str) -> int | None:
    """Extract total duration in seconds from EN/HI/KN text, or None if absent."""
    prepared = _replace_number_words(text.translate(_DIGIT_TRANSLATION))
    total = 0
    found = False
    for match in _DURATION_CHUNK_RE.finditer(prepared):
        num = int(match.group(1))
        unit = match.group(2).lower()
        mult = _UNIT_MULT.get(unit)
        if mult is None:
            # Handle Devanagari/Kannada suffix inflections cleanly
            if unit.startswith(("घंट", "ghant", "ಗಂಟೆ", "gant")):
                mult = 3600
            elif unit.startswith(("मिनट", "minat", "ನಿಮಿಷ", "nimish")):
                mult = 60
            elif unit.startswith(("सेक", "ಸೆಕೆಂಡ")):
                mult = 1
        if mult is not None:
            total += num * mult
            found = True
    return total if found else None


def normalize_utterance(
    text: str,
    *,
    requested_language: RequestedLanguage = "auto",
    fallback_language: LanguageCode = "en",
) -> NormalizedText:
    """Unicode-NFC normalize, translate Indic digits, strip polite fillers, keep scripts intact."""
    nfc = unicodedata.normalize("NFC", text or "").strip()
    digits_norm = nfc.translate(_DIGIT_TRANSLATION)
    collapsed = re.sub(r"\s+", " ", digits_norm).strip()
    stripped = _POLITE_PREFIX_RE.sub("", collapsed).strip()
    stripped = _POLITE_SUFFIX_RE.sub("", stripped).strip()
    # Strip trailing punctuation without altering Indic vowel marks
    cleaned = stripped.rstrip("?!.।॥,").strip()
    script = detect_script(nfc)
    lang = detect_language(
        cleaned or nfc,
        requested=requested_language,
        fallback_language=fallback_language,
    )
    with_nums = _replace_number_words(cleaned, language=lang)
    return NormalizedText(
        raw=text,
        normalized=with_nums,
        tokens=unicode_tokens(with_nums),
        script=script,
        language=lang,
    )
