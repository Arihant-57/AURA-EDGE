"""Hybrid multilingual planner: normalizer -> Hassil fast router -> validated Qwen fallback."""

from __future__ import annotations

import logging
import re
from typing import Any

from aura_edge.intelligence.fast_router import (
    MultilingualFastRouter,
    extract_requested_target_language,
)
from aura_edge.intelligence.normalizer import normalize_utterance
from aura_edge.intelligence.ollama_client import OllamaClient, OllamaUnavailableError
from aura_edge.intelligence.response_catalog import get_phrase
from aura_edge.intelligence.schemas import (
    IntentName,
    LanguageCode,
    RequestedLanguage,
    TypedAction,
    TypedIntent,
)

logger = logging.getLogger(__name__)

# Trigger tool JSON classification ONLY when utterance resembles an imperative tool/device action;
# general questions, casual chat, and conversational follow-ups stream directly in 1 LLM call.
_TOOL_CLASSIFIER_HINT_RE = re.compile(
    r"(?:\b(?:timm?ers?|alarm|countdown|remind\s+me|unlock|lock|turn\s+(?:on|off)|"
    r"switch\s+(?:on|off)|open\s+(?:my|the)\s+(?:(?:\w+\s+)?door|app|spotify|youtube|browser)|"
    r"set\s+a\s+|pause\s+the|resume\s+the|cancel\s+the|delete\s+my)\b|"
    r"(?:टाइमर|ताइमर|अलार्म|रद्द|रोको|दरवाजा|खोलो|बंद\s+करो)|"
    r"(?:ಟೈಮರ್|ಅಲಾರಾಂ|ರದ್ದು|ನಿಲ್ಲಿಸು|ಬಾಗಿಲು|ತೆಗೆ))",
    flags=re.IGNORECASE | re.UNICODE,
)

_SYSTEM_PROMPT = """You are the strict tool-intent classifier for AURA EDGE, an offline multilingual voice assistant.
Analyze the user utterance (English, Hindi, Kannada, or Hinglish/Kanglish) and return ONLY a JSON object matching this exact schema:
{
  "intent_name": "GetTime" | "GetDate" | "SetTimer" | "ListTimers" | "GetTimerRemaining" | "PauseTimer" | "ResumeTimer" | "CancelTimer" | "CancelAllTimers" | "Help" | "StopAssistant" | "Conversation" | "Clarify" | "Unsupported",
  "duration_seconds": integer or null,
  "label": string or null,
  "clarification_needed": boolean
}
Rules:
1. NEVER invent tools, unregistered smart-home devices, weather APIs, web searches, or execution results.
2. External/unregistered actions (unlock doors, send emails, shop/order, transfer money, web search, open external apps) MUST return "Unsupported".
3. Ambiguous commands ("do the thing", "fix that") MUST return "Clarify" with clarification_needed=true.
4. General conversation, greetings, knowledge questions, explanations, or follow-ups ("explain that again", "make it shorter", "repeat in Kannada") MUST return "Conversation".
5. For SetTimer, duration_seconds must be a positive integer (1..86400). If duration is missing, return "Clarify".
6. Respond with valid JSON only."""


class HybridPlanner:
    """Routes deterministic commands without LLM, falling back safely to local Qwen."""

    def __init__(
        self,
        fast_router: MultilingualFastRouter,
        ollama_client: OllamaClient | None = None,
    ) -> None:
        self.fast_router = fast_router
        self.ollama_client = ollama_client

    async def plan(
        self,
        text: str,
        *,
        requested_language: RequestedLanguage = "auto",
        fallback_language: LanguageCode = "en",
        allow_llm_fallback: bool = True,
    ) -> tuple[TypedIntent, float, float | None, str | None]:
        """Return ``(TypedIntent, router_ms, llm_ms, error_note)``."""
        fast_intent, router_ms = self.fast_router.route(
            text,
            requested_language=requested_language,
            fallback_language=fallback_language,
        )
        if fast_intent is not None:
            return fast_intent, router_ms, None, None

        norm = normalize_utterance(
            text,
            requested_language=requested_language,
            fallback_language=fallback_language,
        )

        if not allow_llm_fallback or self.ollama_client is None:
            return (
                TypedIntent(
                    intent_name=IntentName.UNSUPPORTED,
                    confidence=0.9,
                    source="refusal",
                    route_category="refusal",
                    language=norm.language,
                    input_script=norm.script,
                    raw_text=text,
                ),
                router_ms,
                None,
                None,
            )

        target_lang = extract_requested_target_language(text)

        # Direct streaming conversational fast-path (avoids wasted double-LLM latency)
        if not _TOOL_CLASSIFIER_HINT_RE.search(norm.normalized):
            return (
                TypedIntent(
                    intent_name=IntentName.CONVERSATION,
                    confidence=0.9,
                    source="llm_conversation",
                    route_category="conversation",
                    language=norm.language,
                    target_language=target_lang,
                    input_script=norm.script,
                    raw_text=text,
                ),
                router_ms,
                None,
                None,
            )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"language_hint={norm.language}; utterance={norm.normalized}",
            },
        ]

        try:
            llm_res = await self.ollama_client.chat_json(messages)
        except OllamaUnavailableError as exc:
            logger.info("Ollama fallback unavailable: %s", exc)
            return (
                TypedIntent(
                    intent_name=IntentName.UNSUPPORTED,
                    confidence=1.0,
                    source="refusal",
                    route_category="refusal",
                    language=norm.language,
                    input_script=norm.script,
                    raw_text=text,
                ),
                router_ms,
                None,
                str(exc),
            )

        validated = self._validate_llm_json(
            llm_res.parsed_json,
            language=norm.language,
            target_language=target_lang,
            script=norm.script,
            raw_text=text,
        )
        return validated, router_ms, llm_res.latency_ms, None

    def _validate_llm_json(
        self,
        data: dict[str, Any] | None,
        *,
        language: LanguageCode,
        target_language: LanguageCode | None = None,
        script: Any,
        raw_text: str,
    ) -> TypedIntent:
        if not isinstance(data, dict):
            return TypedIntent(
                intent_name=IntentName.UNSUPPORTED,
                confidence=0.0,
                source="refusal",
                route_category="refusal",
                language=language,
                input_script=script,
                raw_text=raw_text,
            )

        raw_name = str(data.get("intent_name", "Unsupported"))
        try:
            intent_enum = IntentName(raw_name)
        except ValueError:
            return TypedIntent(
                intent_name=IntentName.UNSUPPORTED,
                confidence=0.0,
                source="refusal",
                route_category="refusal",
                language=language,
                input_script=script,
                raw_text=raw_text,
            )

        if intent_enum == IntentName.CLARIFY or bool(data.get("clarification_needed")):
            return TypedIntent(
                intent_name=IntentName.CLARIFY,
                confidence=0.8,
                source="clarification",
                route_category="clarification",
                language=language,
                input_script=script,
                clarification_question=get_phrase("ambiguous_request", language),
                raw_text=raw_text,
            )

        if intent_enum == IntentName.CONVERSATION:
            return TypedIntent(
                intent_name=IntentName.CONVERSATION,
                confidence=0.85,
                source="llm_conversation",
                route_category="conversation",
                language=language,
                target_language=target_language,
                input_script=script,
                raw_text=raw_text,
            )

        if intent_enum == IntentName.UNSUPPORTED:
            return TypedIntent(
                intent_name=IntentName.UNSUPPORTED,
                confidence=0.9,
                source="refusal",
                route_category="refusal",
                language=language,
                input_script=script,
                raw_text=raw_text,
            )

        slots: dict[str, Any] = {}
        raw_label = data.get("label")
        if isinstance(raw_label, str) and raw_label.strip():
            slots["label"] = raw_label.strip()[:64]

        if intent_enum == IntentName.SET_TIMER:
            dur = data.get("duration_seconds")
            if not isinstance(dur, int) or dur <= 0 or dur > 86400:
                return TypedIntent(
                    intent_name=IntentName.CLARIFY,
                    confidence=0.5,
                    source="clarification",
                    language=language,
                    input_script=script,
                    clarification_question=get_phrase("invalid_duration", language),
                    raw_text=raw_text,
                )
            slots["duration_seconds"] = dur

        action = TypedAction(intent_name=intent_enum, slots=slots, confidence=0.82)
        return TypedIntent(
            intent_name=intent_enum,
            confidence=0.82,
            source="llm_tool",
            language=language,
            input_script=script,
            slots=slots,
            sub_actions=[action],
            raw_text=raw_text,
        )
