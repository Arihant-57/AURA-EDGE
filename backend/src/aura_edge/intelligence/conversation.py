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

_CONVERSATION_SYSTEM_PROMPT = """You are AURA EDGE, a helpful, warm, strictly local offline voice assistant created to run cleanly on this user's machine.
Active reply language: {lang_name}.
Guidelines:
1. Reply naturally, concisely, and clearly in {lang_name} unless the user explicitly asks to translate or repeat in another language.
2. Speak like a helpful human assistant (2 to 4 clear sentences for normal questions; expand smoothly only when asked to explain more).
3. Avoid markdown tables, heavy bullet symbols, emojis, code fences, or ASCII decorative characters so text-to-speech sounds natural.
4. Keep factual honesty: never pretend you can control smart-home lights, send emails, browse the internet, search the web, or order food—if asked to perform external actions, politely state that you can converse offline and manage local time, date, and timers.
5. Use the session conversation history to understand follow-ups like "explain that again", "make it shorter", "repeat in Kannada", "what did I just ask?", or "continue explaining"."""


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
        max_history_messages: int = 10,
    ) -> list[dict[str, str]]:
        """Assemble bounded chat messages for Ollama with language-targeted system prompt."""
        sys_prompt = _CONVERSATION_SYSTEM_PROMPT.format(
            lang_name=_LANGUAGE_NAMES.get(reply_language, "English")
        )
        msgs: list[dict[str, str]] = [{"role": "system", "content": sys_prompt}]
        with self._lock:
            self._expire_if_idle_locked()
            tail = list(self._messages)[-max(0, int(max_history_messages)) :]
        for item in tail:
            msgs.append({"role": item.role, "content": item.content})
        msgs.append({"role": "user", "content": user_text.strip()})
        return msgs
