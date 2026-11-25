"""Base TTS backend protocol and synthesized audio container."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from aura_edge.intelligence.schemas import LanguageCode

_KANNADA_DIGITS_TRANS = str.maketrans("೦೧೨೩೪೫೬೭೮೯", "0123456789")

_KN_PHRASE_MAP: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\baura[\s\-_]+edge\b", re.IGNORECASE), "ಆರಾ ಎಡ್ಜ್"),
    (re.compile(r"\bliving[\s_]+room\b", re.IGNORECASE), "ಲಿವಿಂಗ್ ರೂಮ್"),
    (re.compile(r"\butility[\s_]+room\b", re.IGNORECASE), "ಯುಟಿಲಿಟಿ ರೂಮ್"),
    (re.compile(r"\bsmart[\s\-_]+home\b", re.IGNORECASE), "ಸ್ಮಾರ್ಟ್ ಹೋಮ್"),
    (re.compile(r"\bair[\s_]+conditioner\b", re.IGNORECASE), "ಎ ಸಿ"),
    (re.compile(r"\bair[\s_]+purifier\b", re.IGNORECASE), "ಏರ್ ಪ್ಯೂರಿಫೈಯರ್"),
    (re.compile(r"\bwashing[\s_]+machine\b", re.IGNORECASE), "ವಾಷಿಂಗ್ ಮಷೀನ್"),
    (re.compile(r"\brobot[\s_]+vacuum\b", re.IGNORECASE), "ರೋಬೋಟ್ ವ್ಯಾಕ್ಯೂಮ್"),
    (re.compile(r"\bwater[\s_]+heater\b", re.IGNORECASE), "ವಾಟರ್ ಹೀಟರ್"),
    (re.compile(r"\bwater[\s_]+pump\b", re.IGNORECASE), "ವಾಟರ್ ಪಂಪ್"),
    (re.compile(r"\bclimate[\s_]+sensor\b", re.IGNORECASE), "ಕ್ಲೈಮೇಟ್ ಸೆನ್ಸರ್"),
    (re.compile(r"\bstudy[\s_]+desk\b", re.IGNORECASE), "ಸ್ಟಡಿ ಡೆಸ್ಕ್"),
    (re.compile(r"\ba\.m\.\b", re.IGNORECASE), "ಎ ಎಂ"),
    (re.compile(r"\bp\.m\.\b", re.IGNORECASE), "ಪಿ ಎಂ"),
)

_KN_LOANWORDS: dict[str, str] = {
    "aura": "ಆರಾ",
    "edge": "ಎಡ್ಜ್",
    "light": "ಲೈಟ್",
    "lights": "ಲೈಟ್ಸ್",
    "bedroom": "ಬೆಡ್‌ರೂಮ್",
    "living": "ಲಿವಿಂಗ್",
    "room": "ರೂಮ್",
    "kitchen": "ಕಿಚನ್",
    "study": "ಸ್ಟಡಿ",
    "utility": "ಯುಟಿಲಿಟಿ",
    "fan": "ಫ್ಯಾನ್",
    "ceiling": "ಸೀಲಿಂಗ್",
    "ac": "ಎ ಸಿ",
    "tv": "ಟಿ ವಿ",
    "curtain": "ಕರ್ಟನ್",
    "curtains": "ಕರ್ಟನ್ಸ್",
    "purifier": "ಪ್ಯೂರಿಫೈಯರ್",
    "refrigerator": "ರೆಫ್ರಿಜರೇಟರ್",
    "fridge": "ಫ್ರಿಜ್",
    "washer": "ವಾಷರ್",
    "vacuum": "ವ್ಯಾಕ್ಯೂಮ್",
    "heater": "ಹೀಟರ್",
    "pump": "ಪಂಪ್",
    "sensor": "ಸೆನ್ಸರ್",
    "climate": "ಕ್ಲೈಮೇಟ್",
    "desk": "ಡೆಸ್ಕ್",
    "main": "ಮೇನ್",
    "home": "ಹೋಮ್",
    "on": "ಆನ್",
    "off": "ಆಫ್",
    "timer": "ಟೈಮರ್",
    "timers": "ಟೈಮರ್ಸ್",
    "tea": "ಟೀ",
    "coffee": "ಕಾಫಿ",
    "meeting": "ಮೀಟಿಂಗ್",
    "alarm": "ಅಲಾರ್ಮ್",
    "brightness": "ಬ್ರೈಟ್‌ನೆಸ್",
    "temperature": "ಟೆಂಪರೇಚರ್",
    "humidity": "ಹ್ಯುಮಿಡಿಟಿ",
    "speed": "ಸ್ಪೀಡ್",
    "volume": "ವಾಲ್ಯೂಮ್",
    "power": "ಪವರ್",
    "mode": "ಮೋಡ್",
    "cool": "ಕೂಲ್",
    "dry": "ಡ್ರೈ",
    "auto": "ಆಟೋ",
    "ist": "ಐ ಎಸ್ ಟಿ",
    "am": "ಎ ಎಂ",
    "pm": "ಪಿ ಎಂ",
    "ok": "ಓಕೆ",
    "okay": "ಓಕೆ",
    "yes": "ಎಸ್",
    "no": "ನೋ",
    "stop": "ಸ್ಟಾಪ್",
    "start": "ಸ್ಟಾರ್ಟ್",
    "pause": "ಪಾಸ್",
    "resume": "ರೆಸ್ಯೂಮ್",
    "cancel": "ಕ್ಯಾನ್ಸಲ್",
    "dock": "ಡಾಕ್",
    "docked": "ಡಾಕ್ಡ್",
    "idle": "ಐಡಲ್",
    "running": "ರನ್ನಿಂಗ್",
    "cleaning": "ಕ್ಲೀನಿಂಗ್",
    "completed": "ಕಂಪ್ಲೀಟೆಡ್",
    "hello": "ಹಲೋ",
    "hi": "ಹಾಯ್",
    "bye": "ಬೈ",
    "true": "ಟ್ರೂ",
    "false": "ಫಾಲ್ಸ್",
}

_KN_LETTER_NAMES: dict[str, str] = {
    "a": "ಎ",
    "b": "ಬಿ",
    "c": "ಸಿ",
    "d": "ಡಿ",
    "e": "ಇ",
    "f": "ಎಫ್",
    "g": "ಜಿ",
    "h": "ಎಚ್",
    "i": "ಐ",
    "j": "ಜೆ",
    "k": "ಕೆ",
    "l": "ಎಲ್",
    "m": "ಎಂ",
    "n": "ಎನ್",
    "o": "ಓ",
    "p": "ಪಿ",
    "q": "ಕ್ಯೂ",
    "r": "ಆರ್",
    "s": "ಎಸ್",
    "t": "ಟಿ",
    "u": "ಯು",
    "v": "ವಿ",
    "w": "ಡಬ್ಲ್ಯೂ",
    "x": "ಎಕ್ಸ್",
    "y": "ವೈ",
    "z": "ಝೆಡ್",
}

_KN_CONSONANT_DIGRAPHS: tuple[tuple[str, str], ...] = (
    ("sh", "ಶ"),
    ("ch", "ಚ"),
    ("th", "ತ"),
    ("dh", "ಧ"),
    ("ph", "ಫ"),
    ("bh", "ಭ"),
    ("kh", "ಖ"),
    ("gh", "ಘ"),
    ("jh", "ಝ"),
    ("ck", "ಕ"),
    ("qu", "ಕ್ವ"),
    ("wh", "ವ"),
)

_KN_CONSONANT_SINGLES: dict[str, str] = {
    "b": "ಬ",
    "c": "ಕ",
    "d": "ಡ",
    "f": "ಫ",
    "g": "ಗ",
    "h": "ಹ",
    "j": "ಜ",
    "k": "ಕ",
    "l": "ಲ",
    "m": "ಮ",
    "n": "ನ",
    "p": "ಪ",
    "q": "ಕ",
    "r": "ರ",
    "s": "ಸ",
    "t": "ಟ",
    "v": "ವ",
    "w": "ವ",
    "x": "ಕ್ಸ",
    "y": "ಯ",
    "z": "ಝ",
}

_KN_STANDALONE_VOWELS: tuple[tuple[str, str], ...] = (
    ("ai", "ಐ"),
    ("au", "ಔ"),
    ("ou", "ಔ"),
    ("ee", "ಈ"),
    ("ea", "ಈ"),
    ("oo", "ಊ"),
    ("aa", "ಆ"),
    ("ey", "ಏ"),
    ("ay", "ಏ"),
    ("a", "ಅ"),
    ("e", "ಎ"),
    ("i", "ಇ"),
    ("o", "ಒ"),
    ("u", "ಉ"),
)

_KN_MATRAS: tuple[tuple[str, str], ...] = (
    ("ai", "ೈ"),
    ("au", "ೌ"),
    ("ou", "ೌ"),
    ("ee", "ೀ"),
    ("ea", "ೀ"),
    ("oo", "ೂ"),
    ("aa", "ಾ"),
    ("ey", "ೇ"),
    ("ay", "ೇ"),
    ("a", ""),
    ("e", "ೆ"),
    ("i", "ಿ"),
    ("o", "ೊ"),
    ("u", "ು"),
)


def _phonetic_en_to_kn(word: str) -> str:
    """Best-effort syllable transliteration for arbitrary Latin words into Kannada script."""
    w = word.lower()
    if not w:
        return ""
    out: list[str] = []
    i = 0
    n = len(w)
    while i < n:
        # Handle nasal 'ng' naturally via anusvara when preceded by vowel/consonant
        if w[i : i + 2] == "ng" and i > 0:
            out.append("ಂಗ್" if i + 2 == n else "ಂಗ")
            i += 2
            if i < n and w[i] in "aeiou":
                for v_pat, matra in _KN_MATRAS:
                    if w.startswith(v_pat, i):
                        if matra:
                            out.append(matra)
                        i += len(v_pat)
                        break
            continue

        # Consonant match (digraph then single)
        cons = ""
        step = 0
        for dg, kn_c in _KN_CONSONANT_DIGRAPHS:
            if w.startswith(dg, i):
                cons = kn_c
                step = len(dg)
                break
        if not cons and w[i] in _KN_CONSONANT_SINGLES:
            ch = w[i]
            nxt = w[i + 1] if i + 1 < n else ""
            if ch == "c" and nxt in "eiy":
                cons = "ಸ"
            elif ch == "g" and nxt in "eiy":
                cons = "ಜ"
            else:
                cons = _KN_CONSONANT_SINGLES[ch]
            step = 1

        if cons:
            i += step
            # Check silent trailing 'e' on multi-syllable words (e.g. "home", "cake")
            if i == n - 1 and w[i] == "e" and len(w) > 2:
                out.append(cons + "್")
                i += 1
                continue
            # Check following vowel matra
            matched_vowel = False
            for v_pat, matra in _KN_MATRAS:
                if w.startswith(v_pat, i):
                    out.append(cons + matra)
                    i += len(v_pat)
                    matched_vowel = True
                    break
            if not matched_vowel:
                if i < n and w[i] == "y":
                    out.append(cons + "ಿ")
                    i += 1
                else:
                    out.append(cons + "್")
            continue

        # Standalone vowel
        matched_v = False
        for v_pat, kn_v in _KN_STANDALONE_VOWELS:
            if w.startswith(v_pat, i):
                out.append(kn_v)
                i += len(v_pat)
                matched_v = True
                break
        if matched_v:
            continue

        if w[i] == "y":
            out.append("ಯ")
        i += 1

    return "".join(out)


def _transliterate_latin_word_kn(word: str) -> str:
    low = word.lower()
    mapped = _KN_LOANWORDS.get(low)
    if mapped is not None:
        return mapped
    has_vowel = any(ch in "aeiouy" for ch in low)
    if (word.isupper() and len(word) <= 4) or (not has_vowel and len(word) <= 4):
        return " ".join(_KN_LETTER_NAMES.get(ch, ch) for ch in low)
    return _phonetic_en_to_kn(low) or " ".join(_KN_LETTER_NAMES.get(ch, ch) for ch in low)


def _kn_period_from_hour(hour: int, ampm_raw: str) -> str:
    is_pm = ampm_raw.lower().startswith("p")
    h = hour % 12
    if not is_pm:
        return "ರಾತ್ರಿ" if h in (0, 1, 2, 3, 4) else "ಬೆಳಗ್ಗೆ"
    if h in (0, 1, 2, 3):
        return "ಮಧ್ಯಾಹ್ನ"
    if h in (4, 5, 6, 7):
        return "ಸಂಜೆ"
    return "ರಾತ್ರಿ"


def normalize_kannada_for_tts(text: str) -> str:
    """Normalize numbers, dates, times, units, and English loanwords into speakable Kannada."""
    out = re.sub(r"\s+", " ", text or "").strip()
    if not out:
        return ""

    out = out.translate(_KANNADA_DIGITS_TRANS)
    out = out.replace("%", " ಶೇಕಡಾ ")
    out = re.sub(r"°\s*[cC]\b", " ಡಿಗ್ರಿ ಸೆಲ್ಸಿಯಸ್ ", out)
    out = re.sub(r"°\s*[fF]\b", " ಡಿಗ್ರಿ ಫ್ಯಾರನ್‌ಹೀಟ್ ", out)
    out = out.replace("°", " ಡಿಗ್ರಿ ")

    # Dates: YYYY-MM-DD / DD/MM/YYYY / DD-MM-YYYY -> spaced numbers
    out = re.sub(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", r"\1 \2 \3", out)
    out = re.sub(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})\b", r"\1 \2 \3", out)

    # Clock times with AM/PM -> natural spoken Kannada period + hour/minute
    def _repl_time_ampm(m: re.Match[str]) -> str:
        hour = int(m.group(1))
        minute = m.group(2)
        period = _kn_period_from_hour(hour, m.group(3))
        if minute is None or minute == "00":
            return f"{period} {hour}"
        return f"{period} {hour} {minute}"

    out = re.sub(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\b",
        _repl_time_ampm,
        out,
        flags=re.IGNORECASE,
    )

    # Remaining 24h/12h colons
    out = re.sub(r"\b(\d{1,2}):00\b", r"\1", out)
    out = re.sub(r"\b(\d{1,2}):(\d{2})\b", r"\1 \2", out)

    # Decimals (e.g., 3.5 -> 3 ಪಾಯಿಂಟ್ 5)
    out = re.sub(r"(\d+)\.(\d+)", r"\1 ಪಾಯಿಂಟ್ \2", out)

    # Multi-word English domain phrases first
    for pat, repl in _KN_PHRASE_MAP:
        out = pat.sub(repl, out)

    # Transliterate all remaining ASCII Latin words so device names/labels never vanish
    out = re.sub(r"[A-Za-z]+", lambda m: _transliterate_latin_word_kn(m.group(0)), out)

    # Keep Kannada script (\u0C80-\u0CFF), ZWNJ/ZWJ, ASCII 0-9, space, hyphen, apostrophe, underscore
    out = re.sub(r"[^\u0C80-\u0CFF\u200c\u200d0-9\s'\-_]+", " ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


@dataclass
class SynthesizedAudio:
    """In-memory mono float32 PCM audio produced by an offline TTS backend."""

    samples: np.ndarray
    sample_rate: int
    language: LanguageCode
    backend: str
    model_name: str
    voice_name: str
    latency_ms: float
    cached: bool = False


class TTSBackend(Protocol):
    """Protocol implemented by every offline TTS backend."""

    name: str

    def supports_language(self, language: LanguageCode) -> bool: ...

    def model_info(self, language: LanguageCode) -> tuple[str, str]: ...

    def synthesize(
        self,
        text: str,
        *,
        language: LanguageCode,
        speed: float = 1.0,
    ) -> SynthesizedAudio: ...
