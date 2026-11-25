"""Typed tool registry, permission validation, and Phase 2 extension boundary."""

from __future__ import annotations

import uuid
from typing import Any

from aura_edge.execution.tools.datetime_tool import DateTimeTool
from aura_edge.execution.tools.timer_tool import TimerManager
from aura_edge.intelligence.response_catalog import get_phrase
from aura_edge.intelligence.schemas import (
    IntentName,
    LanguageCode,
    TypedAction,
    TypedIntent,
    VerifiedActionResult,
)

# Phase 1 allowlisted intents that actually mutate or inspect local state.
_PHASE1_EXECUTABLE_INTENTS = {
    IntentName.GET_TIME,
    IntentName.GET_DATE,
    IntentName.SET_TIMER,
    IntentName.LIST_TIMERS,
    IntentName.GET_TIMER_REMAINING,
    IntentName.PAUSE_TIMER,
    IntentName.RESUME_TIMER,
    IntentName.CANCEL_TIMER,
    IntentName.CANCEL_ALL_TIMERS,
    IntentName.STOP_ASSISTANT,
    IntentName.HELP,
}


class ToolRegistry:
    """Validates and executes typed Phase 1 tools (reserves Phase 2 extension hook)."""

    def __init__(self, datetime_tool: DateTimeTool, timer_manager: TimerManager) -> None:
        self.datetime_tool = datetime_tool
        self.timer_manager = timer_manager
        self._phase2_hooks: dict[str, Any] = {}

    def register_extension(self, intent_name: str, handler: Any) -> None:
        """Reserved extension point for Phase 2 smart-home tools (unused in Phase 1)."""
        self._phase2_hooks[intent_name] = handler

    def validate_intent(self, intent: TypedIntent) -> tuple[bool, str | None]:
        """Validate primary intent and every sub-action in a compound request."""
        actions = intent.sub_actions if intent.sub_actions else [
            TypedAction(
                intent_name=intent.intent_name,
                slots=intent.slots,
                confidence=intent.confidence,
            )
        ]
        for action in actions:
            if action.intent_name in (IntentName.CLARIFY, IntentName.UNSUPPORTED):
                continue
            if action.intent_name not in _PHASE1_EXECUTABLE_INTENTS:
                return False, f"Intent '{action.intent_name.value}' is not enabled in Phase 1."
            if action.intent_name == IntentName.SET_TIMER:
                dur = action.slots.get("duration_seconds")
                if not isinstance(dur, int) or dur <= 0 or dur > 86400:
                    return False, "SetTimer requires integer duration_seconds in 1..86400."
        return True, None

    async def execute_intent(self, intent: TypedIntent) -> VerifiedActionResult:
        """Execute validated intent (including compound sub-actions) and verify result."""
        lang = intent.language
        if intent.intent_name == IntentName.CLARIFY:
            return VerifiedActionResult(
                success=False,
                action="Clarify",
                status="clarification_required",
                message=intent.clarification_question or get_phrase("ambiguous_request", lang),
            )
        if intent.intent_name == IntentName.UNSUPPORTED:
            return VerifiedActionResult(
                success=False,
                action="Unsupported",
                status="refused",
                message=get_phrase("unsupported", lang),
            )

        valid, err = self.validate_intent(intent)
        if not valid:
            return VerifiedActionResult(
                success=False,
                action=intent.intent_name.value,
                status="refused",
                message=get_phrase("unsupported", lang),
                error=err,
            )

        actions = intent.sub_actions if intent.sub_actions else [
            TypedAction(
                intent_name=intent.intent_name,
                slots=intent.slots,
                confidence=intent.confidence,
            )
        ]

        results: list[VerifiedActionResult] = []
        for act in actions:
            res = await self._execute_single(act, lang=lang)
            results.append(res)

        if len(results) == 1:
            return results[0]

        all_ok = all(r.success for r in results)
        any_clarify = any(r.status == "clarification_required" for r in results)
        combined_msg = " ".join(r.message for r in results if r.message)
        status = (
            "verified"
            if all_ok
            else ("clarification_required" if any_clarify else "failed")
        )
        return VerifiedActionResult(
            success=all_ok,
            action="CompoundAction",
            status=status,
            data={"sub_results": [r.model_dump() for r in results]},
            message=combined_msg,
            error=None if all_ok else "; ".join(r.error for r in results if r.error),
        )

    async def _execute_single(
        self,
        action: TypedAction,
        *,
        lang: LanguageCode,
    ) -> VerifiedActionResult:
        slots = action.slots
        name = action.intent_name

        if name == IntentName.GET_TIME:
            return self.datetime_tool.get_time(lang=lang)
        if name == IntentName.GET_DATE:
            return self.datetime_tool.get_date(lang=lang)
        if name == IntentName.SET_TIMER:
            return await self.timer_manager.create_timer(
                duration_seconds=int(slots["duration_seconds"]),
                label=slots.get("label"),
                language=lang,
            )
        if name == IntentName.LIST_TIMERS:
            return self.timer_manager.list_timers(language=lang)
        if name == IntentName.GET_TIMER_REMAINING:
            return self.timer_manager.get_remaining(
                timer_id=_parse_uuid(slots.get("timer_id")),
                label=slots.get("label"),
                language=lang,
            )
        if name == IntentName.PAUSE_TIMER:
            return await self.timer_manager.pause_timer(
                timer_id=_parse_uuid(slots.get("timer_id")),
                label=slots.get("label"),
                language=lang,
            )
        if name == IntentName.RESUME_TIMER:
            return await self.timer_manager.resume_timer(
                timer_id=_parse_uuid(slots.get("timer_id")),
                label=slots.get("label"),
                language=lang,
            )
        if name == IntentName.CANCEL_TIMER:
            return await self.timer_manager.cancel_timer(
                timer_id=_parse_uuid(slots.get("timer_id")),
                label=slots.get("label"),
                language=lang,
            )
        if name == IntentName.CANCEL_ALL_TIMERS:
            return await self.timer_manager.cancel_all(language=lang)
        if name == IntentName.HELP:
            return VerifiedActionResult(
                success=True,
                action="Help",
                status="verified",
                message=get_phrase("help", lang),
            )
        if name == IntentName.STOP_ASSISTANT:
            return VerifiedActionResult(
                success=True,
                action="StopAssistant",
                status="verified",
                message=get_phrase("stopped", lang),
            )
        return VerifiedActionResult(
            success=False,
            action=name.value,
            status="refused",
            message=get_phrase("unsupported", lang),
            error="Unsupported intent.",
        )


def _parse_uuid(raw: object) -> uuid.UUID | None:
    if raw is None:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None
