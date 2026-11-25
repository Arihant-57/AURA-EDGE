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


class ConversationSession:
    """Thread-safe, bounded short-term session history (atomic turn commits)."""

    def __init__(self, *, max_turns: int = 12, idle_timeout_sec: float = 1800.0) -> None:
        self._max_messages = max(2, int(max_turns) * 2)
        self._idle_timeout_sec = float(idle_timeout_sec)
        self._messages: deque[ConversationMessage] = deque(maxlen=self._max_messages)
        self._last_activity_mono: float = time.monotonic()
        self._lock = threading.Lock()

    def _expire_if_idle_locked(self) -> None:
        if self._messages and (time.monotonic() - self._last_activity_mono) > self._idle_timeout_sec:
            self._messages.clear()

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
            self._last_activity_mono = time.monotonic()
            return count

    def build_llm_messages(
        self,
        user_text: str,
        *,
        reply_language: LanguageCode,
        capability_summary: str | None = None,
        max_history_messages: int = 10,
    ) -> list[dict[str, str]]:
        """Assemble bounded chat messages for Ollama with language-targeted system prompt."""
        caps = capability_summary or (
            "Registered local tools: DateTimeTool (time/date), TimerManager (persistent timers), "
            "and SmartHomeService (simulated local rooms/devices and routines)."
        )
        sys_prompt = _CONVERSATION_SYSTEM_PROMPT.format(
            lang_name=_LANGUAGE_NAMES.get(reply_language, "English"),
            capabilities_description=caps,
        )
        msgs: list[dict[str, str]] = [{"role": "system", "content": sys_prompt}]
        with self._lock:
            self._expire_if_idle_locked()
            tail = list(self._messages)[-max(0, int(max_history_messages)) :]
        for item in tail:
            msgs.append({"role": item.role, "content": item.content})
        msgs.append({"role": "user", "content": user_text.strip()})
        return msgs
