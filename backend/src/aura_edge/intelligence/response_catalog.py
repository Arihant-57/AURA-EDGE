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
        "en": "Sorry, I cannot do that. I can handle time, date, timers, local smart-home devices, and offline conversation.",
        "hi": "माफ कीजिए, मैं यह नहीं कर सकता। मैं समय, तारीख, टाइमर, स्मार्ट होम डिवाइस और ऑफ़लाइन बातचीत कर सकता हूँ।",
        "kn": "ಕ್ಷಮಿಸಿ, ನನಗೆ ಅದನ್ನು ಮಾಡಲು ಸಾಧ್ಯವಿಲ್ಲ. ನಾನು ಸಮಯ, ದಿನಾಂಕ, ಟೈಮರ್, ಸ್ಮಾರ್ಟ್ ಹೋಮ್ ಸಾಧನಗಳು ಮತ್ತು ಮಾತುಕತೆಗೆ ಸಹಾಯ ಮಾಡಬಲ್ಲೆ.",
    },
    "ambiguous_request": {
        "en": "Could you clarify what you would like me to do?",
        "hi": "कृपया स्पष्ट बताएं कि आप क्या करवाना चाहते हैं?",
        "kn": "ದಯವಿಟ್ಟು ಏನು ಮಾಡಬೇಕೆಂದು ಸ್ಪಷ್ಟವಾಗಿ ಹೇಳಿ.",
    },
    "ambiguous_timer": {
        "en": "You have multiple timers ({labels}). Which timer do you mean?",
        "hi": "आपके पास कई टाइमर हैं ({labels})। आप किस टाइमर की बात कर रहे हैं?",
        "kn": "ನಿಮ್ಮಲ್ಲಿ ಹಲವು ಟೈಮರ್‌ಗಳಿವೆ ({labels}). ಯಾವ ಟೈಮರ್ ಬೇಕು?",
    },
    "no_active_timers": {
        "en": "You have no active timers right now.",
        "hi": "अभी कोई सक्रिय टाइमर नहीं है।",
        "kn": "ಸದ್ಯಕ್ಕೆ ಯಾವುದೇ ಟೈಮರ್ ಚಾಲ್ತಿಯಲ್ಲಿಲ್ಲ.",
    },
    "timer_not_found": {
        "en": "I could not find a matching timer.",
        "hi": "मुझे ऐसा कोई टाइमर नहीं मिला।",
        "kn": "ಅಂತಹ ಯಾವುದೇ ಟೈಮರ್ ಸಿಗಲಿಲ್ಲ.",
    },
    "timer_set": {
        "en": "Timer{label_part} set for {duration}.",
        "hi": "{label_part} {duration} का टाइमर सेट कर दिया गया है।",
        "kn": "{label_part} {duration} ಟೈಮರ್ ಇಡಲಾಗಿದೆ.",
    },
    "timer_paused": {
        "en": "Paused timer{label_part} with {duration} remaining.",
        "hi": "{label_part} टाइमर रोक दिया गया है, {duration} बाकी हैं।",
        "kn": "{label_part} ಟೈಮರ್ ನಿಲ್ಲಿಸಲಾಗಿದೆ, ಇನ್ನೂ {duration} ಬಾಕಿ ಇದೆ.",
    },
    "timer_resumed": {
        "en": "Resumed timer{label_part} with {duration} remaining.",
        "hi": "{label_part} टाइमर फिर से शुरू किया गया है, {duration} बाकी हैं।",
        "kn": "{label_part} ಟೈಮರ್ ಮತ್ತೆ ಶುರು ಮಾಡಲಾಗಿದೆ, ಇನ್ನೂ {duration} ಬಾಕಿ ಇದೆ.",
    },
    "timer_cancelled": {
        "en": "Cancelled timer{label_part}.",
        "hi": "{label_part} टाइमर रद्द कर दिया गया है।",
        "kn": "{label_part} ಟೈಮರ್ ರದ್ದು ಮಾಡಲಾಗಿದೆ.",
    },
    "timers_cancelled_all": {
        "en": "Cancelled {count} timer(s).",
        "hi": "{count} टाइमर रद्द कर दिए गए हैं।",
        "kn": "{count} ಟೈಮರ್‌ಗಳನ್ನು ರದ್ದು ಮಾಡಲಾಗಿದೆ.",
    },
    "timer_remaining": {
        "en": "Timer{label_part} has {duration} remaining.",
        "hi": "{label_part} टाइमर में {duration} बाकी हैं।",
        "kn": "{label_part} ಟೈಮರ್‌ನಲ್ಲಿ ಇನ್ನೂ {duration} ಬಾಕಿ ಇದೆ.",
    },
    "timer_completed": {
        "en": "Time is up! Your{label_part} timer has finished.",
        "hi": "समय पूरा हो गया है! आपका {label_part} टाइमर समाप्त हो गया है।",
        "kn": "ಸಮಯ ಮುಗಿದಿದೆ! ನಿಮ್ಮ {label_part} ಟೈಮರ್ ಪೂರ್ಣಗೊಂಡಿದೆ.",
    },
    "help": {
        "en": "You can ask for the time, date, manage timers, control simulated smart-home lights, fans, AC, TV, curtains, or appliances, or chat in English, Hindi, or Kannada.",
        "hi": "आप अंग्रेज़ी, हिंदी या कन्नड़ में समय, तारीख पूछ सकते हैं, टाइमर चला सकते हैं, स्मार्ट होम डिवाइस और रूटीन नियंत्रित कर सकते हैं, या बातचीत कर सकते हैं।",
        "kn": "ನೀವು ಕನ್ನಡ, ಹಿಂದಿ ಅಥವಾ ಇಂಗ್ಲಿಷ್‌ನಲ್ಲಿ ಸಮಯ, ದಿನಾಂಕ ಕೇಳಬಹುದು, ಟೈಮರ್ ಇಡಬಹುದು, ಮನೆಯ ದೀಪ, ಫ್ಯಾನ್, ಎಸಿ ನಿಯಂತ್ರಿಸಬಹುದು ಅಥವಾ ಮಾತನಾಡಬಹುದು.",
    },
    "stopped": {
        "en": "Stopped.",
        "hi": "ठीक है, रोक दिया गया।",
        "kn": "ಸರಿ.",
    },
    "empty_speech": {
        "en": "I did not hear anything. Please try again.",
        "hi": "मुझे कुछ सुनाई नहीं दिया। कृपया फिर से बोलें।",
        "kn": "ನನಗೆ ಕೇಳಿಸಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಮತ್ತೊಮ್ಮೆ ಹೇಳಿ.",
    },
    "ollama_unavailable": {
        "en": "Local reasoning service is unavailable, and I could not match that to a local command.",
        "hi": "स्थानीय रीज़निंग सेवा उपलब्ध नहीं है, और यह स्थानीय कमांड से मेल नहीं खाता।",
        "kn": "ಸ್ಥಳೀಯ ಬುದ್ಧಿಮತ್ತೆ ಸೇವೆ ಲಭ್ಯವಿಲ್ಲ, ಮತ್ತು ಇದು ಸ್ಥಳೀಯ ಆಜ್ಞೆಗೆ ಹೊಂದಿಕೆಯಾಗಲಿಲ್ಲ.",
    },
    "invalid_duration": {
        "en": "Please specify a valid timer duration between 1 second and 24 hours.",
        "hi": "कृपया 1 सेकंड से 24 घंटे के बीच एक सही टाइमर समय बताएं।",
        "kn": "ದಯವಿಟ್ಟು 1 ಸೆಕೆಂಡ್‌ನಿಂದ 24 ಗಂಟೆಯ ಒಳಗಿನ ಸರಿಯಾದ ಟೈಮರ್ ಸಮಯ ಹೇಳಿ.",
    },
    "greeting": {
        "en": "Hello! How can I help?",
        "hi": "नमस्ते! बताइए, क्या मदद करूँ?",
        "kn": "ನಮಸ್ಕಾರ! ನಿಮಗೆ ಹೇಗೆ ಸಹಾಯ ಮಾಡಲಿ?",
    },
    "acknowledgment": {
        "en": "You're welcome.",
        "hi": "आपका स्वागत है।",
        "kn": "ಪರವಾಗಿಲ್ಲ, ಸಂತೋಷ.",
    },
    "farewell": {
        "en": "Goodbye!",
        "hi": "अलविदा!",
        "kn": "ಮತ್ತೆ ಸಿಗೋಣ, ನಮಸ್ಕಾರ!",
    },
    "identity": {
        "en": "I am AURA EDGE, your local voice assistant.",
        "hi": "मैं ऑरा एज हूँ, आपका स्थानीय वॉइस असिस्टेंट।",
        "kn": "ನನ್ನ ಹೆಸರು ಆರಾ ಎಡ್ಜ್. ನಾನು ನಿಮ್ಮ ಸ್ಥಳೀಯ ಧ್ವನಿ ಸಹಾಯಕ.",
    },
    "supported_languages": {
        "en": "I support English, Hindi, and Kannada.",
        "hi": "मैं अंग्रेज़ी, हिंदी और कन्नड़ भाषाएं बोलता हूँ।",
        "kn": "ನಾನು ಕನ್ನಡ, ಹಿಂದಿ ಮತ್ತು ಇಂಗ್ಲಿಷ್ ಭಾಷೆಗಳನ್ನು ಮಾತನಾಡಬಲ್ಲೆ.",
    },
    "language_switched": {
        "en": "Okay, I will now speak in English.",
        "hi": "ठीक है, अब मैं हिंदी में बात करूँगा।",
        "kn": "ಸರಿ, ಇನ್ನು ಮುಂದೆ ನಾನು ಕನ್ನಡದಲ್ಲಿ ಮಾತನಾಡುತ್ತೇನೆ.",
    },
    "conversation_started": {
        "en": "Continuous conversation mode is active. Go ahead, I'm listening.",
        "hi": "लगातार बातचीत मोड चालू है। बताइए, मैं सुन रहा हूँ।",
        "kn": "ನಿರಂತರ ಮಾತುಕತೆ ಶುರುವಾಗಿದೆ. ಹೇಳಿ, ನಾನು ಕೇಳಿಸಿಕೊಳ್ಳುತ್ತಿದ್ದೇನೆ.",
    },
}


def get_phrase(key: str, lang: LanguageCode, **kwargs: object) -> str:
    template = _TEMPLATES[key].get(lang) or _TEMPLATES[key]["en"]
    return template.format(**kwargs).replace("  ", " ").strip()
