"""Localized deterministic response templates for English, Hindi, and Kannada."""

from __future__ import annotations

from aura_edge.intelligence.schemas import LanguageCode


def format_duration_spoken(seconds: int, lang: LanguageCode) -> str:
    """Format a duration in seconds as natural spoken units in EN/HI/KN."""
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)

    parts: list[str] = []
    if lang == "hi":
        if hours:
            parts.append(f"{hours} घंटे" if hours > 1 else "1 घंटा")
        if minutes:
            parts.append(f"{minutes} मिनट")
        if secs or not parts:
            parts.append(f"{secs} सेकंड")
        return " ".join(parts)

    if lang == "kn":
        if hours:
            parts.append(f"{hours} ಗಂಟೆ")
        if minutes:
            parts.append(f"{minutes} ನಿಮಿಷ")
        if secs or not parts:
            parts.append(f"{secs} ಸೆಕೆಂಡ್")
        return " ".join(parts)

    if hours:
        parts.append(f"{hours} hour" if hours == 1 else f"{hours} hours")
    if minutes:
        parts.append(f"{minutes} minute" if minutes == 1 else f"{minutes} minutes")
    if secs or not parts:
        parts.append(f"{secs} second" if secs == 1 else f"{secs} seconds")
    return " and ".join(parts)


_TEMPLATES: dict[str, dict[LanguageCode, str]] = {
    "unsupported": {
        "en": "Sorry, I cannot perform that request. I can tell the time or date and manage local timers.",
        "hi": "माफ कीजिए, मैं यह कार्य नहीं कर सकता। मैं समय, तारीख और टाइमर संभाल सकता हूँ।",
        "kn": "ಕ್ಷಮಿಸಿ, ನಾನು ಆ ಕೆಲಸವನ್ನು ಮಾಡಲು ಸಾಧ್ಯವಿಲ್ಲ. ನಾನು ಸಮಯ, ದಿನಾಂಕ ಮತ್ತು ಟೈಮರ್‌ಗಳನ್ನು ನಿರ್ವಹಿಸಬಲ್ಲೆ.",
    },
    "ambiguous_request": {
        "en": "Could you clarify what you would like me to do?",
        "hi": "कृपया स्पष्ट बताएं कि आप क्या करवाना चाहते हैं?",
        "kn": "ದಯವಿಟ್ಟು ನೀವು ಏನು ಮಾಡಬೇಕೆಂದು ಸ್ಪಷ್ಟವಾಗಿ ಹೇಳಿ?",
    },
    "ambiguous_timer": {
        "en": "You have multiple timers ({labels}). Which timer do you mean?",
        "hi": "आपके पास कई टाइमर हैं ({labels})। आप किस टाइमर की बात कर रहे हैं?",
        "kn": "ನಿಮ್ಮಲ್ಲಿ ಹಲವು ಟೈಮರ್‌ಗಳಿವೆ ({labels}). ನೀವು ಯಾವ ಟೈಮರ್ ಬಗ್ಗೆ ಹೇಳುತ್ತಿದ್ದೀರಿ?",
    },
    "no_active_timers": {
        "en": "You have no active timers right now.",
        "hi": "अभी कोई सक्रिय टाइमर नहीं है।",
        "kn": "ಸದ್ಯಕ್ಕೆ ಯಾವುದೇ ಸಕ್ರಿಯ ಟೈಮರ್ ಇಲ್ಲ.",
    },
    "timer_not_found": {
        "en": "I could not find a matching timer.",
        "hi": "मुझे ऐसा कोई टाइमर नहीं मिला।",
        "kn": "ಅಂತಹ ಯಾವುದೇ ಟೈಮರ್ ಕಂಡುಬಂದಿಲ್ಲ.",
    },
    "timer_set": {
        "en": "Timer{label_part} set for {duration}.",
        "hi": "{label_part} {duration} का टाइमर सेट कर दिया गया है।",
        "kn": "{label_part} {duration} ಟೈಮರ್ ಸೆಟ್ ಮಾಡಲಾಗಿದೆ.",
    },
    "timer_paused": {
        "en": "Paused timer{label_part} with {duration} remaining.",
        "hi": "{label_part} टाइमर रोक दिया गया है, {duration} बाकी हैं।",
        "kn": "{label_part} ಟೈಮರ್ ನಿಲ್ಲಿಸಲಾಗಿದೆ, {duration} ಬಾಕಿ ಇದೆ.",
    },
    "timer_resumed": {
        "en": "Resumed timer{label_part} with {duration} remaining.",
        "hi": "{label_part} टाइमर फिर से शुरू किया गया है, {duration} बाकी हैं।",
        "kn": "{label_part} ಟೈಮರ್ ಮತ್ತೆ ಪ್ರಾರಂಭಿಸಲಾಗಿದೆ, {duration} ಬಾಕಿ ಇದೆ.",
    },
    "timer_cancelled": {
        "en": "Cancelled timer{label_part}.",
        "hi": "{label_part} टाइमर रद्द कर दिया गया है।",
        "kn": "{label_part} ಟೈಮರ್ ರದ್ದುಗೊಳಿಸಲಾಗಿದೆ.",
    },
    "timers_cancelled_all": {
        "en": "Cancelled {count} timer(s).",
        "hi": "{count} टाइमर रद्द कर दिए गए हैं।",
        "kn": "{count} ಟೈಮರ್‌ಗಳನ್ನು ರದ್ದುಗೊಳಿಸಲಾಗಿದೆ.",
    },
    "timer_remaining": {
        "en": "Timer{label_part} has {duration} remaining.",
        "hi": "{label_part} टाइमर में {duration} बाकी हैं।",
        "kn": "{label_part} ಟೈಮರ್‌ನಲ್ಲಿ {duration} ಬಾಕಿ ಇದೆ.",
    },
    "timer_completed": {
        "en": "Time is up! Your{label_part} timer has finished.",
        "hi": "समय पूरा हो गया है! आपका {label_part} टाइमर समाप्त हो गया है।",
        "kn": "ಸಮಯ ಮುಗಿದಿದೆ! ನಿಮ್ಮ {label_part} ಟೈಮರ್ ಪೂರ್ಣಗೊಂಡಿದೆ.",
    },
    "help": {
        "en": "You can ask for the time, today's date, or set, check, pause, resume, and cancel timers in English, Hindi, or Kannada.",
        "hi": "आप अंग्रेज़ी, हिंदी या कन्नड़ में समय, तारीख पूछ सकते हैं, या टाइमर सेट, चेक, पॉज़ और रद्द कर सकते हैं।",
        "kn": "ನೀವು ಇಂಗ್ಲಿಷ್, ಹಿಂದಿ ಅಥವಾ ಕನ್ನಡದಲ್ಲಿ ಸಮಯ, ದಿನಾಂಕ ಕೇಳಬಹುದು ಅಥವಾ ಟೈಮರ್‌ಗಳನ್ನು ಸೆಟ್, ಪರಿಶೀಲನೆ, ಪಾಸ್ ಮತ್ತು ರದ್ದು ಮಾಡಬಹುದು.",
    },
    "stopped": {
        "en": "Stopped.",
        "hi": "ठीक है, रोक दिया गया।",
        "kn": "ಸರಿ, ನಿಲ್ಲಿಸಲಾಗಿದೆ.",
    },
    "empty_speech": {
        "en": "I did not hear anything. Please try again.",
        "hi": "मुझे कुछ सुनाई नहीं दिया। कृपया फिर से बोलें।",
        "kn": "ನನಗೆ ಏನೂ ಕೇಳಿಸಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೊಮ್ಮೆ ಹೇಳಿ.",
    },
    "ollama_unavailable": {
        "en": "Local reasoning service is unavailable, and I could not match that to a local command.",
        "hi": "स्थानीय रीज़निंग सेवा उपलब्ध नहीं है, और यह स्थानीय कमांड से मेल नहीं खाता।",
        "kn": "ಸ್ಥಳೀಯ ಬುದ್ಧಿಮತ್ತೆ ಸೇವೆ ಲಭ್ಯವಿಲ್ಲ, ಮತ್ತು ಇದು ಸ್ಥಳೀಯ ಆಜ್ಞೆಗೆ ಹೊಂದಿಕೆಯಾಗಲಿಲ್ಲ.",
    },
    "invalid_duration": {
        "en": "Please specify a valid timer duration between 1 second and 24 hours.",
        "hi": "कृपया 1 सेकंड से 24 घंटे के बीच एक सही टाइमर समय बताएं।",
        "kn": "ದಯವಿಟ್ಟು 1 ಸೆಕೆಂಡ್‌ನಿಂದ 24 ಗಂಟೆಗಳ ಒಳಗಿನ ಸರಿಯಾದ ಟೈಮರ್ ಸಮಯವನ್ನು ಹೇಳಿ.",
    },
}


def get_phrase(key: str, lang: LanguageCode, **kwargs: object) -> str:
    template = _TEMPLATES[key].get(lang) or _TEMPLATES[key]["en"]
    return template.format(**kwargs).replace("  ", " ").strip()
