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
    "zero": 0,
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
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "twenty one": 21,
    "twenty-one": 21,
    "twenty two": 22,
    "twenty-two": 22,
    "twenty three": 23,
    "twenty-three": 23,
    "twenty four": 24,
    "twenty-four": 24,
    "twenty five": 25,
    "twenty-five": 25,
    "twenty six": 26,
    "twenty-six": 26,
    "twenty seven": 27,
    "twenty-seven": 27,
    "twenty eight": 28,
    "twenty-eight": 28,
    "twenty nine": 29,
    "twenty-nine": 29,
    "thirty": 30,
    "thirty five": 35,
    "thirty-five": 35,
    "forty": 40,
    "forty five": 45,
    "forty-five": 45,
    "fifty": 50,
    "sixty": 60,
    "ninety": 90,
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
    "चौबीस": 24,
    "पच्चीस": 25,
    "तीस": 30,
    "पैंतालीस": 45,
    "साठ": 60,
    # Devanagari transliterations of English numbers
    "वन": 1,
    "टू": 2,
    "थ्री": 3,
    "फोर": 4,
    "फाइव": 5,
    "सिक्स": 6,
    "सेवन": 7,
    "एट": 8,
    "नाइन": 9,
    "टेन": 10,
    "ट्वेंटी": 20,
    "ट्वेंटी वन": 21,
    "ट्वेंटी टू": 22,
    "ट्वेंटी थ्री": 23,
    "ट्वेंटी फोर": 24,
    "ट्वेंटी फाइव": 25,
    "थर्टी": 30,
    "फोर्टी": 40,
    "फोर्टी फाइव": 45,
    "फिफ्टी": 50,
    "सिक्स्टी": 60,
    # Kannada native numbers
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
    "ಇಪ್ಪತ್ತನಾಲ್ಕು": 24,
    "ಇಪ್ಪತ್ತೈದು": 25,
    "ಮೂವತ್ತು": 30,
    # Kannada transliterations of English numbers
    "ಒನ್": 1,
    "ಟೂ": 2,
    "ಥ್ರೀ": 3,
    "ಫೋರ್": 4,
    "ಫೈವ್": 5,
    "ಸಿಕ್ಸ್": 6,
    "ಸೆವೆನ್": 7,
    "ಏಟ್": 8,
    "ನೈನ್": 9,
    "ಟೆನ್": 10,
    "ಟ್ವೆಂಟಿ": 20,
    "ಟ್ವೆಂಟಿ ಒನ್": 21,
    "ಟ್ವೆಂಟಿ ಟೂ": 22,
    "ಟ್ವೆಂಟಿ ಥ್ರೀ": 23,
    "ಟ್ವೆಂಟಿ ಫೋರ್": 24,
    "ಟ್ವೆಂಟಿ ಫೈವ್": 25,
    "ಥರ್ಟಿ": 30,
    "ಫೋರ್ಟಿ": 40,
    "ಫೋರ್ಟಿ ಫೈವ್": 45,
    "ಫಿಫ್ಟಿ": 50,
    "ಸಿಕ್ಸ್ಟಿ": 60,
}

# English loanword units/actions written in Devanagari/Kannada -> canonical ASCII tokens
_CODE_SWITCHED_LOANWORDS: tuple[tuple[str, str], ...] = (
    ("सेकंड्स", "seconds"),
    ("सेकण्ड्स", "seconds"),
    ("मिनट्स", "minutes"),
    ("डिग्रीज", "degrees"),
    ("डिग्रीज़", "degrees"),
    ("सेट", "set"),
    ("ಸೆಕೆಂಡ್ಸ್", "seconds"),
    ("ಸೆಕೆಂಡುಗಳು", "seconds"),
    ("ಮಿನಿಟ್ಸ್", "minutes"),
    ("ಮಿನಿಟ್", "minute"),
    ("ಡಿಗ್ರೀಸ್", "degrees"),
    ("ಸೆಟ್", "set"),
)

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
    "सेकंड्स": 1,
    "सेकण्ड्स": 1,
    "ಸೆಕೆಂಡ್": 1,
    "ಸೆಕೆಂಡು": 1,
    "ಸೆಕೆಂಡ್ಸ್": 1,
    "ಸೆಕೆಂಡ್‌ಗಳು": 1,
    "ಸೆಕೆಂಡುಗಳು": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "मिनट": 60,
    "मिनटों": 60,
    "मिनट्स": 60,
    "minat": 60,
    "ನಿಮಿಷ": 60,
    "ನಿಮಿಷಗಳು": 60,
    "ನಿಮಿಷದ": 60,
    "ಮಿನಿಟ್": 60,
    "ಮಿನಿಟ್ಸ್": 60,
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
    r"(?<![\d.,−-])(\d+)\s*(?:[-‑–]\s*)?(hours?|hrs?|hr|h|minutes?|mins?|min|m|seconds?|secs?|sec|s|"
    r"घंट(?:ों|[ाे])|मिनट(?:ों|्स)?|सेक[ंडण्ड]+(?:्स)?|ghant[ae]|minat|"
    r"ಗಂಟೆ(?:ಗಳು)?|ನಿಮಿಷ(?:ಗಳು|ದ)?|ಮಿನಿಟ್(?:ಸ್)?|ಸೆಕೆಂಡ್(?:‌ಗಳು|ಗಳು|ಸ್)?|ಸೆಕೆಂಡ(?:ಿನ|ು(?:ಗಳು)?)|"
    r"gante|nimisha)(?![A-Za-z\u0900-\u097F\u0C80-\u0CFF])",
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
    "baad",
    "bhool",
    "jao",
    "dena",
    "mera",
    "meri",
    "mujhe",
    "paani",
    "peene",
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
    "kodi",
    "torisu",
    "aadmele",
    "nantara",
    "maretubidu",
    "nanna",
    "nanage",
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
    dev_count = len(_DEVANAGARI_RE.findall(text))
    kan_count = len(_KANNADA_RE.findall(text))
    if kan_count > 0 and kan_count >= dev_count:
        return "kn"
    if dev_count > 0:
        return "hi"
    tokens = {t.lower() for t in unicode_tokens(text)}
    if tokens & _HINGLISH_HINTS:
        return "hi"
    if tokens & _KANGLISH_HINTS:
        return "kn"
    if tokens:
        return "en"
    return fallback_language


_UNIT_FOLLOWUP_RE = (
    r"(?=\s+(?:seconds?|secs?|sec|minutes?|mins?|min|minat|nimisha|hours?|hrs?|hr|ghant[ae]|gante|"
    r"सेक[ंडण्ड]+(?:्स)?|मिनट(?:ों|्स)?|घंट[ाेों]|ಸೆಕೆಂಡ್(?:‌ಗಳು|ಗಳು|ಸ್)?|ನಿಮಿಷ(?:ಗಳು|ದ)?|ಮಿನಿಟ್(?:ಸ್)?|ಗಂಟೆ(?:ಗಳು)?)(?![\w\u0900-\u097F\u0C80-\u0CFF]))"
)


def _replace_code_switched_loanwords(text: str) -> str:
    out = text
    for indic_word, ascii_word in _CODE_SWITCHED_LOANWORDS:
        out = re.sub(
            rf"(?<![\w\u0900-\u097F\u0C80-\u0CFF]){re.escape(indic_word)}(?![\w\u0900-\u097F\u0C80-\u0CFF])",
            ascii_word,
            out,
            flags=re.UNICODE,
        )
    return out


def _replace_number_words(text: str, *, language: LanguageCode = "en") -> str:
    out = _HALF_HOUR_RE.sub("30 minutes", text)
    out = _ARTICLE_TIME_UNIT_RE.sub(r"1 \1", out)
    out = _replace_code_switched_loanwords(out)

    active_maps: list[tuple[dict[str, int], bool]] = [
        (_INDIC_SCRIPT_NUMBER_WORDS, False),
        (_EN_NUMBER_WORDS, False),
        (_ROMAN_INDIC_NUMBER_WORDS, True),
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
    """Extract positive total duration in seconds from EN/HI/KN text, or None if absent."""
    prepared = _replace_number_words(text.translate(_DIGIT_TRANSLATION))
    if re.search(r"(?:[-−]\s*\d|\d[.,]\d)", prepared):
        return None
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
            elif unit.startswith(("मिनट", "minat", "ನಿಮಿಷ", "ಮಿನಿಟ್", "nimish")):
                mult = 60
            elif unit.startswith(("सेक", "ಸೆಕೆಂಡ")):
                mult = 1
        if mult is not None:
            total += num * mult
            found = True
    return total if (found and total > 0) else None


def _normalize_kannada_asr_variants(text: str) -> str:
    """Normalize known IndicConformer Kannada CTC character slips on timer/time commands."""
    out = re.sub(r"(?:ತೈಮರಿ+|ಟೈಮರಿ+)ಡು", "ಟೈಮರ್ ಇಡು", text)
    # Preserve ಟೈಮ್ ("time", followed by Kannada virama \u0CCD) while normalizing slipped ಟೈಮ / ತೈಮರ್.
    out = re.sub(
        r"(?:ತ?ತೈಮರ್|ಟೈಮ(?!\u0CCD|ರ್|[\w\u0C80-\u0CFF])|ತೈಮ(?!\u0CCD|ರ್|[\w\u0C80-\u0CFF]))",
        "ಟೈಮರ್",
        out,
    )
    out = re.sub(r"(?<![\w\u0C80-\u0CFF])ಎಷ್ಟು(?=ಗಂ[ಟತ]ೆ(?![\w\u0C80-\u0CFF]))", "ಎಷ್ಟು ", out)
    out = re.sub(r"ಗಂತೆ(?![\w\u0C80-\u0CFF])", "ಗಂಟೆ", out)
    out = re.sub(r"ನಿಮಿಸ(?![\w\u0C80-\u0CFF])", "ನಿಮಿಷ", out)
    out = re.sub(r"ಗದ್ದು(?=\s+(?:ಮಾಡು|ಗೊಳಿಸು))", "ರದ್ದು", out)
    return re.sub(r"\s+", " ", out).strip()


def _normalize_hindi_asr_variants(text: str) -> str:
    """Normalize known Whisper Hindi Devanagari phonetic variations on timer/clock commands."""
    out = re.sub(r"(?:टाईमर|ताईमर|ताम्यर|टामर|तामर|ताीमर|तैमर|टायमर)", "टाइमर", text)
    out = re.sub(r"(?<![\w\u0900-\u097F])सेकंद(?![\w\u0900-\u097F])", "सेकंड", out)
    out = re.sub(r"(?:मिन्त|मिनद|मिनत|मिंट)", "मिनट", out)
    out = re.sub(r"(?:समें|समे|समै)(?![\w\u0900-\u097F])", "समय", out)
    out = re.sub(r"लगा[ोूॉँ]+", "लगाओ", out)
    out = re.sub(r"बता[ोूॉँ]+", "बताओ", out)
    out = re.sub(r"रत्द", "रद्द", out)
    out = re.sub(r"(?<![\w\u0900-\u097F])सबी(?![\w\u0900-\u097F])", "सभी", out)
    out = re.sub(r"(?<![\w\u0900-\u097F])आभी(?![\w\u0900-\u097F])", "अभी", out)
    out = re.sub(
        r"(?<![\w\u0900-\u097F])तो(?=\s+(?:मिनट|सेकंड|घंटे|घंटा))",
        "दो",
        out,
    )
    out = re.sub(
        r"(?<![\w\u0900-\u097F])पाज(?=\s+(?:मिनट|सेकंड|घंटे|घंटा))",
        "पांच",
        out,
    )
    return re.sub(r"\s+", " ", out).strip()


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
    if lang == "kn" or script == "kannada":
        cleaned = _normalize_kannada_asr_variants(cleaned)
    elif lang == "hi" or script == "devanagari":
        cleaned = _normalize_hindi_asr_variants(cleaned)
    elif lang == "en":
        cleaned = re.sub(r"\btiner\b", "timer", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(?:teat|T)(?=\s+timer\b)", "tea", cleaned)
    with_nums = _replace_number_words(cleaned, language=lang)
    return NormalizedText(
        raw=text,
        normalized=with_nums,
        tokens=unicode_tokens(with_nums),
        script=script,
        language=lang,
    )
