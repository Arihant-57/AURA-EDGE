"""Bounded session conversation memory and speech-friendly text sanitization."""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from aura_edge.intelligence.schemas import LanguageCode

_LANGUAGE_NAMES: dict[LanguageCode, str] = {
    "en": "English",
    "hi": "Hindi (Devanagari script only)",
    "kn": "Kannada (Kannada script only)",
}

_CONVERSATION_SYSTEM_PROMPT = """You are AURA EDGE, a helpful, strictly local offline voice assistant created to run on this user's machine.
Active reply language: {lang_name}.
Registered local capabilities: {capabilities_description}
Guidelines:
1. Reply ONLY in {lang_name} unless the user explicitly asks to translate or repeat in another language. Never mix scripts unnecessarily (for Kannada, write Kannada script; for Hindi, write Devanagari script).
2. Supported speech & text languages are strictly English, Hindi, and Kannada ONLY. NEVER claim to support Tagalog, Spanish, French, or any other language.
3. Keep answers crisp and natural for spoken audio: 1 short sentence for greetings or quick factual answers; 1 to 2 short sentences for normal questions. Do NOT repeat introductions ("I am AURA EDGE...") or recite capability lists unless directly asked.
4. No markdown formatting, no asterisks, no bullet lists, no tables, no emojis, and no ASCII symbols.
5. Strictly respect tool verification: NEVER claim you changed a device state, smart-home device, or timer unless a verified tool actually executed. External actions outside local tools (internet search, emails, shopping, banking, door locks) are unsupported—refuse politely and briefly.
6. Use the session conversation history for follow-ups like "explain that again", "make it shorter", "repeat in Kannada", "what did I just ask?", or "continue explaining"."""

_KANNADA_CHAR_RE = re.compile(r"[\u0C80-\u0CFF]")
_DEVANAGARI_CHAR_RE = re.compile(r"[\u0900-\u097F]")
_LATIN_CHAR_RE = re.compile(r"[A-Za-z]")
_UNSUPPORTED_LANG_CLAIM_RE = re.compile(
    r"\b(?:tagalog|spanish|french|german|japanese|chinese|mandarin|arabic|korean|tamil|telugu|malayalam|bengali|marathi|portuguese|russian)\b",
    flags=re.IGNORECASE,
)
_UNVERIFIED_ACTION_CLAIM_RE = re.compile(
    r"(?:\b(?:i(?:'ve|\s+have)?|done[.,!]?\s+i(?:'ve|\s+have)?)\s+"
    r"(?:turned\s+(?:on|off)|switched\s+(?:on|off)|set\s+(?:the\s+|your\s+)?(?:ac|air\s+conditioner|fan|light|tv|temperature|timer|reminder)|"
    r"created|added|deleted|removed|forgot(?:ten)?|cancelled|canceled|updated|completed|scheduled|saved)\b|"
    r"\b(?:has|have)\s+been\s+(?:turned\s+(?:on|off)|set|created|deleted|removed|cancelled|canceled|scheduled|completed|saved)\b|"
    r"(?:मैंने\s+.*(?:कर\s+दिया|बंद\s+कर\s+दिया|चालू\s+कर\s+दिया|सेट\s+कर\s+दिया|हटा\s+दिया|रद्द\s+कर\s+दिया|सहेज\s+लिया|जोड़\s+दिया))|"
    r"(?:आन\s+कर\s+दिया|ऑफ\s+कर\s+दिया|बंद\s+कर\s+दिया\s+है|सेट\s+कर\s+दिया\s+है|हटा\s+दिया\s+है|रद्द\s+कर\s+दिया\s+है)|"
    r"(?:ಆನ್\s+ಮಾಡಲಾಗಿದೆ|ಆಫ್\s+ಮಾಡಲಾಗಿದೆ|ಸೆಟ್\s+ಮಾಡಲಾಗಿದೆ|ಅಳಿಸಲಾಗಿದೆ|ರದ್ದುಗೊಳಿಸಲಾಗಿದೆ|ಉಳಿಸಲಾಗಿದೆ|ಸೇರಿಸಲಾಗಿದೆ))",
    flags=re.IGNORECASE | re.UNICODE,
)


def validate_no_unverified_action_claim(
    text: str,
    target_language: LanguageCode,
) -> tuple[bool, str]:
    """Ensure free-form conversation never claims a tool mutation executed without verification."""
    cleaned = (text or "").strip()
    if cleaned and _UNVERIFIED_ACTION_CLAIM_RE.search(cleaned):
        return False, "unverified_action_claim"
    return True, "ok"


def unverified_action_fallback(language: LanguageCode) -> str:
    return {
        "en": "I did not run that action yet. Could you state the exact device, reminder, or item you want me to change?",
        "hi": "मैंने अभी कोई कार्रवाई नहीं की है। कृपया ठीक-ठीक बताएं कि आप किस उपकरण, रिमाइंडर या आइटम को बदलना चाहते हैं?",
        "kn": "ನಾನು ಆ ಕ್ರಿಯೆಯನ್ನು ಇನ್ನೂ ಮಾಡಿಲ್ಲ. ದಯವಿಟ್ಟು ಯಾವ ಸಾಧನ, ಜ್ಞಾಪನೆ ಅಥವಾ ವಿವರವನ್ನು ಬದಲಾಯಿಸಬೇಕೆಂದು ಸ್ಪಷ್ಟವಾಗಿ ತಿಳಿಸಿ?",
    }[language]


def validate_response_language(
    text: str,
    target_language: LanguageCode,
) -> tuple[bool, str]:
    """Verify reply matches target EN/HI/KN script and contains no unsupported-language claims."""
    cleaned = (text or "").strip()
    if not cleaned:
        return False, "empty_response"

    if target_language == "en":
        # Reject hallucinated language support claims (e.g., Tagalog)
        low = cleaned.lower()
        if _UNSUPPORTED_LANG_CLAIM_RE.search(low) and any(
            kw in low for kw in ("support", "speak", "language", "available", "can ")
        ):
            return False, "unsupported_language_claim"
        return True, "ok"

    kan_count = len(_KANNADA_CHAR_RE.findall(cleaned))
    dev_count = len(_DEVANAGARI_CHAR_RE.findall(cleaned))
    lat_count = len(_LATIN_CHAR_RE.findall(cleaned))
    total_alpha = kan_count + dev_count + lat_count
    if total_alpha == 0:
        return False, "no_alphabetic_chars"

    if target_language == "kn":
        if kan_count < 3 or (kan_count / total_alpha) < 0.35:
            return False, f"expected_kn_script (kn={kan_count}, lat={lat_count})"
        return True, "ok"

    if target_language == "hi":
        if dev_count < 3 or (dev_count / total_alpha) < 0.35:
            return False, f"expected_hi_script (hi={dev_count}, lat={lat_count})"
        return True, "ok"

    return True, "ok"


def strip_markdown_for_speech(text: str) -> str:
    """Clean markdown formatting from LLM output while preserving EN/HI/KN scripts."""
    if not text:
        return ""
    cleaned = re.sub(r"```[\s\S]*?```", " ", text)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"^\s{0,3}#{1,6}\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*[-*•]\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.replace("**", "").replace("__", "").replace("*", "")
    return re.sub(r"\s+", " ", cleaned).strip()


class ConversationMessage(BaseModel):
    """Single message stored inside a bounded conversation session."""

    role: Literal["user", "assistant"]
    content: str
    language: LanguageCode
    intent: str
    route_category: str
    turn_id: str | None = None
    timestamp_utc: str


_DEVICE_TYPE_WORDS: dict[str, str] = {
    "air_conditioner": "air conditioner",
    "light": "light",
    "fan": "fan",
    "television": "tv",
    "curtain": "curtain",
    "washing_machine": "washing machine",
    "robot_vacuum": "robot vacuum",
    "air_purifier": "air purifier",
    "water_heater": "water heater",
    "water_pump": "water pump",
    "refrigerator": "refrigerator",
    "climate_sensor": "climate sensor",
}
_ROOM_WORDS: dict[str, str] = {
    "living_room": "living room",
    "bedroom": "bedroom",
    "kitchen": "kitchen",
    "study": "study",
    "utility_room": "utility room",
}


class ConversationSession:
    """Thread-safe, bounded short-term session history and verified-entity context."""

    def __init__(self, *, max_turns: int = 12, idle_timeout_sec: float = 1800.0) -> None:
        self._max_messages = max(2, int(max_turns) * 2)
        self._idle_timeout_sec = float(idle_timeout_sec)
        self._messages: deque[ConversationMessage] = deque(maxlen=self._max_messages)
        self._last_activity_mono: float = time.monotonic()
        self._lock = threading.Lock()
        self.last_room: str | None = None
        self.last_device_type: str | None = None
        self.last_device_id: str | None = None
        self.last_action: str | None = None
        self.last_reminder_id: str | None = None
        self.last_reminder_title: str | None = None
        self.last_task_id: str | None = None
        self.last_memory_key: str | None = None

    def _expire_if_idle_locked(self) -> None:
        if self._messages and (time.monotonic() - self._last_activity_mono) > self._idle_timeout_sec:
            self._messages.clear()
            self._clear_entities_locked()

    def _clear_entities_locked(self) -> None:
        self.last_room = None
        self.last_device_type = None
        self.last_device_id = None
        self.last_action = None
        self.last_reminder_id = None
        self.last_reminder_title = None
        self.last_task_id = None
        self.last_memory_key = None

    def record_verified_entity(
        self,
        *,
        action: str,
        slots: dict[str, object] | None = None,
        data: dict[str, object] | None = None,
    ) -> None:
        """Update short-term entity references ONLY from verified tool execution."""
        payload = data or {}
        sl = slots or {}
        with self._lock:
            if action in ("DeviceControl", "ScheduleDeviceAction"):
                dev = payload.get("device") if isinstance(payload.get("device"), dict) else {}
                sched = payload.get("schedule") if isinstance(payload.get("schedule"), dict) else {}
                sched_p = sched.get("payload_json") if isinstance(sched.get("payload_json"), dict) else {}
                dev_id = dev.get("id") or sched_p.get("device_id")
                room = dev.get("room") or sl.get("room")
                dev_type = dev.get("device_type") or sl.get("device_type")
                if isinstance(dev_id, str) and "." in dev_id and not dev_type:
                    room = room or dev_id.split(".", 1)[0]
                if isinstance(room, str):
                    self.last_room = room
                if isinstance(dev_type, str):
                    self.last_device_type = dev_type
                if isinstance(dev_id, str):
                    self.last_device_id = dev_id
                self.last_action = str(sl.get("action") or action)
            elif action == "CreateReminder":
                rem = payload.get("reminder") if isinstance(payload.get("reminder"), dict) else {}
                if rem.get("id"):
                    self.last_reminder_id = str(rem["id"])
                if rem.get("title"):
                    self.last_reminder_title = str(rem["title"])
            elif action in ("CancelReminder",):
                self.last_reminder_id = None
                self.last_reminder_title = None
            elif action in ("SaveMemory", "UpdateMemory", "RetrieveMemory"):
                mem = payload.get("memory") if isinstance(payload.get("memory"), dict) else {}
                if not mem and isinstance(payload.get("items"), list) and payload["items"]:
                    first = payload["items"][0]
                    if isinstance(first, dict):
                        mem = first
                if mem.get("canonical_key"):
                    self.last_memory_key = str(mem["canonical_key"])
                sv = mem.get("structured_value") if isinstance(mem.get("structured_value"), dict) else {}
                if isinstance(sv.get("room"), str):
                    self.last_room = sv["room"]
                if isinstance(sv.get("device_type"), str):
                    self.last_device_type = sv["device_type"]
                if isinstance(sv.get("device_id"), str):
                    self.last_device_id = sv["device_id"]
            elif action == "DeleteMemory":
                self.last_memory_key = None
            elif action in ("CreateTask", "CompleteTask"):
                task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
                if task.get("id"):
                    self.last_task_id = str(task["id"])

    def invalidate_memory_context(self) -> None:
        """Scrub stale memory answers from chat history after DeleteMemory so deleted facts cannot leak."""
        with self._lock:
            self.last_memory_key = None
            kept = deque(
                (
                    msg
                    for msg in self._messages
                    if msg.intent not in ("SaveMemory", "UpdateMemory", "RetrieveMemory", "ListMemories")
                ),
                maxlen=self._max_messages,
            )
            self._messages = kept

    def resolve_anaphora(self, text: str) -> str:
        """Resolve unambiguous entity pronouns ('it', 'that reminder') from verified context."""
        cleaned = (text or "").strip()
        if not cleaned:
            return cleaned
        low = cleaned.lower()
        with self._lock:
            room = self.last_room
            dev_type = self.last_device_type
            rem_title = self.last_reminder_title

        if dev_type is not None:
            room_txt = f"{_ROOM_WORDS.get(room, '')} " if room else ""
            dev_txt = _DEVICE_TYPE_WORDS.get(dev_type, dev_type.replace("_", " "))
            target = f"{room_txt}{dev_txt}".strip()

            if re.match(r"^(?:please\s+)?(?:turn|switch)\s+it\s+off[.!?]*$", low):
                return f"turn off the {target}"
            if re.match(r"^(?:please\s+)?(?:turn|switch)\s+it\s+on[.!?]*$", low):
                return f"turn on the {target}"
            if re.match(
                r"^(?:please\s+)?set\s+it\s+to\s+(?:my\s+)?(?:preferred|favorite|usual)\s+(\w+)[.!?]*$",
                low,
            ):
                m_prop = re.search(r"(?:preferred|favorite|usual)\s+(\w+)", low)
                prop = m_prop.group(1) if m_prop else "temperature"
                return f"set the {target} to my preferred {prop}"
            m_val = re.match(
                r"^(?:please\s+)?(?:make|set|change)\s+it\s+(?:to\s+)?(\d{1,3})(?:\s*(?:degrees?|%|percent))?(?:\s+instead)?[.!?]*$",
                low,
            )
            if m_val:
                return f"set the {target} to {m_val.group(1)}"

        if rem_title and re.match(r"^(?:please\s+)?(?:cancel|delete|remove)\s+that\s+reminder[.!?]*$", low):
            return f"cancel reminder {rem_title}"

        return cleaned

    def record_turn(
        self,
        *,
        user_text: str,
        assistant_text: str,
        language: LanguageCode,
        intent: str,
        route_category: str,
        turn_id: str | None = None,
    ) -> None:
        """Atomically append user + assistant pair (called only when turn succeeds)."""
        u_clean = user_text.strip()
        a_clean = assistant_text.strip()
        if not u_clean or not a_clean:
            return
        now_iso = datetime.now(UTC).isoformat()
        with self._lock:
            self._expire_if_idle_locked()
            self._messages.append(
                ConversationMessage(
                    role="user",
                    content=u_clean,
                    language=language,
                    intent=intent,
                    route_category=route_category,
                    turn_id=turn_id,
                    timestamp_utc=now_iso,
                )
            )
            self._messages.append(
                ConversationMessage(
                    role="assistant",
                    content=a_clean,
                    language=language,
                    intent=intent,
                    route_category=route_category,
                    turn_id=turn_id,
                    timestamp_utc=now_iso,
                )
            )
            self._last_activity_mono = time.monotonic()

    def get_history(self) -> list[ConversationMessage]:
        with self._lock:
            self._expire_if_idle_locked()
            return list(self._messages)

    def clear(self) -> int:
        with self._lock:
            count = len(self._messages)
            self._messages.clear()
            self._clear_entities_locked()
            self._last_activity_mono = time.monotonic()
            return count

    def build_llm_messages(
        self,
        user_text: str,
        *,
        reply_language: LanguageCode,
        capability_summary: str | None = None,
        memory_context: str = "",
        max_history_messages: int = 10,
    ) -> list[dict[str, str]]:
        """Assemble bounded chat messages for Ollama with language-targeted system prompt."""
        caps = capability_summary or (
            "Registered local tools: DateTimeTool (time/date), TimerManager (persistent timers), "
            "SmartHomeService (simulated local rooms/devices and routines), "
            "MemoryService (persistent preferences), and SchedulerService (reminders and schedules)."
        )
        sys_prompt = _CONVERSATION_SYSTEM_PROMPT.format(
            lang_name=_LANGUAGE_NAMES.get(reply_language, "English"),
            capabilities_description=caps,
        )
        if memory_context.strip():
            sys_prompt = f"{sys_prompt}\n\n{memory_context.strip()}"
        msgs: list[dict[str, str]] = [{"role": "system", "content": sys_prompt}]
        with self._lock:
            self._expire_if_idle_locked()
            tail = list(self._messages)[-max(0, int(max_history_messages)) :]
        for item in tail:
            msgs.append({"role": item.role, "content": item.content})
        msgs.append({"role": "user", "content": user_text.strip()})
        return msgs
