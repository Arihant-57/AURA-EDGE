"""Typed tool registry, permission validation, and Phase 1-3 execution handlers."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from aura_edge.application.memory_service import (
    MemoryClarificationError,
    MemoryDisabledError,
    MemorySafetyError,
)
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

# Phase 1 allowlisted intents that mutate or inspect local state.
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

_MEMORY_INTENTS = {
    IntentName.SAVE_MEMORY,
    IntentName.RETRIEVE_MEMORY,
    IntentName.UPDATE_MEMORY,
    IntentName.DELETE_MEMORY,
    IntentName.LIST_MEMORIES,
}

_SCHEDULER_INTENTS = {
    IntentName.CREATE_REMINDER,
    IntentName.LIST_REMINDERS,
    IntentName.UPDATE_REMINDER,
    IntentName.CANCEL_REMINDER,
    IntentName.SCHEDULE_DEVICE_ACTION,
    IntentName.SCHEDULE_ROUTINE,
    IntentName.LIST_SCHEDULES,
    IntentName.UPDATE_SCHEDULE,
    IntentName.CANCEL_SCHEDULE,
}

_ROUTINE_INTENTS = {
    IntentName.CREATE_ROUTINE,
    IntentName.UPDATE_ROUTINE,
    IntentName.DELETE_ROUTINE,
    IntentName.RUN_ROUTINE,
}


def _resolve_schedule_timing(
    slots: dict[str, Any],
    *,
    now_utc: datetime,
    tz_name: str = "Asia/Kolkata",
) -> tuple[datetime | None, dict[str, Any]]:
    """Resolve delay_seconds or time_spec slots into (run_at_utc, recurrence_json)."""
    if slots.get("run_at_utc"):
        raw_dt = datetime.fromisoformat(str(slots["run_at_utc"]))
        return (raw_dt if raw_dt.tzinfo else raw_dt.replace(tzinfo=UTC)).astimezone(UTC), dict(
            slots.get("recurrence") or {"kind": "once"}
        )

    delay = slots.get("delay_seconds")
    if isinstance(delay, int) and delay > 0:
        return now_utc + timedelta(seconds=delay), {"kind": "once"}

    ts = slots.get("time_spec")
    if not isinstance(ts, dict) or ts.get("ambiguous"):
        return None, {"kind": "once"}

    tz = ZoneInfo(tz_name)
    now_local = now_utc.astimezone(tz)
    hour = int(ts.get("hour", 0))
    minute = int(ts.get("minute", 0))
    day_offset = int(ts.get("day_offset", 0))
    rec = dict(ts.get("recurrence") or {"kind": "once"})

    target_local = (now_local + timedelta(days=day_offset)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if target_local <= now_local and day_offset == 0:
        if rec.get("kind") == "weekly":
            weekdays = sorted({int(d) % 7 for d in (rec.get("weekdays") or [now_local.weekday()])})
            for off in range(1, 15):
                cand = (now_local + timedelta(days=off)).replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
                if cand.weekday() in weekdays:
                    target_local = cand
                    break
        else:
            target_local += timedelta(days=1)

    return target_local.astimezone(UTC), rec


class ToolRegistry:
    """Validates and executes typed Phase 1-3 local tools with post-execution verification."""

    def __init__(
        self,
        datetime_tool: DateTimeTool,
        timer_manager: TimerManager,
        smart_home: Any | None = None,
        *,
        memory_service: Any | None = None,
        scheduler_service: Any | None = None,
        notification_service: Any | None = None,
        default_timezone: str = "Asia/Kolkata",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.datetime_tool = datetime_tool
        self.timer_manager = timer_manager
        self.smart_home = smart_home
        self.memory_service = memory_service
        self.scheduler_service = scheduler_service
        self.notification_service = notification_service
        self.default_timezone = default_timezone
        self._clock = clock or (lambda: datetime.now(UTC))
        self._phase2_hooks: dict[str, Any] = {}

    def register_extension(self, intent_name: str, handler: Any) -> None:
        self._phase2_hooks[intent_name] = handler

    def describe_capabilities(self, language: LanguageCode = "en") -> str:
        """Return an accurate factual capability summary derived from wired services."""
        has_home = self.smart_home is not None
        has_mem = self.memory_service is not None
        has_sched = self.scheduler_service is not None
        if language == "hi":
            parts = ["पंजीकृत स्थानीय टूल्स: समय व तारीख (DateTimeTool), और स्थायी टाइमर (TimerManager)।"]
            if has_home:
                parts.append(
                    "सिमुलेटेड स्मार्ट होम (SmartHomeService) सक्रिय है: लाइट, पंखा, एसी, टीवी, पर्दे, वॉशिंग मशीन, रोबोट वैक्यूम और रूटीन।"
                )
            if has_mem:
                parts.append("स्थायी व्यक्तिगत मेमोरी (MemoryService) और वरीयताएँ सक्रिय हैं।")
            if has_sched:
                parts.append("स्थायी रिमाइंडर, शेड्यूल्ड कार्य और नोटिफिकेशन सक्रिय हैं।")
            return " ".join(parts)
        if language == "kn":
            parts = ["ನೋಂದಾಯಿತ ಸ್ಥಳೀಯ ಟೂಲ್‌ಗಳು: ಸಮಯ ಮತ್ತು ದಿನಾಂಕ (DateTimeTool), ಮತ್ತು ಟೈಮರ್‌ಗಳು (TimerManager)."]
            if has_home:
                parts.append(
                    "ಸಿಮ್ಯುಲೇಟೆಡ್ ಸ್ಮಾರ್ಟ್ ಹೋಮ್ (SmartHomeService) ಸಕ್ರಿಯವಾಗಿದೆ: ಲೈಟ್, ಫ್ಯಾನ್, ಏಸಿ, ಟಿವಿ, ಪರದೆ, ವಾಷಿಂಗ್ ಮೆಷಿನ್, ರೋಬೋಟ್ ವ್ಯಾಕ್ಯೂಮ್ ಮತ್ತು ದಿನಚರಿಗಳು."
                )
            if has_mem:
                parts.append("ಶಾಶ್ವತ ವೈಯಕ್ತಿಕ ನೆನಪು ಮತ್ತು ಆದ್ಯತೆಗಳು (MemoryService) ಸಕ್ರಿಯವಾಗಿವೆ.")
            if has_sched:
                parts.append("ಶಾಶ್ವತ ಜ್ಞಾಪನೆಗಳು, ವೇಳಾಪಟ್ಟಿಗಳು ಮತ್ತು ಅಧಿಸೂಚನೆಗಳು ಸಕ್ರಿಯವಾಗಿವೆ.")
            return " ".join(parts)
        parts = ["Registered local tools: current time and date (DateTimeTool), and persistent timers (TimerManager)."]
        if has_home:
            parts.append(
                "Simulated smart-home control (SmartHomeService) is active for lights, fans, AC, TV, curtains, "
                "air purifier, washing machine, robot vacuum, water heater, water pump, and custom routines."
            )
        if has_mem:
            parts.append("Persistent personal memory and appliance preferences (MemoryService) are active.")
        if has_sched:
            parts.append("Persistent reminders, scheduled appliance actions, and proactive notifications (SchedulerService) are active.")
        return " ".join(parts)

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
            if action.intent_name == IntentName.DEVICE_CONTROL and self.smart_home is not None:
                continue
            if action.intent_name in _MEMORY_INTENTS and self.memory_service is not None:
                continue
            if action.intent_name in _SCHEDULER_INTENTS and self.scheduler_service is not None:
                continue
            if action.intent_name in _ROUTINE_INTENTS and self.smart_home is not None:
                continue
            if action.intent_name not in _PHASE1_EXECUTABLE_INTENTS:
                return False, f"Intent '{action.intent_name.value}' is not enabled."
            if action.intent_name == IntentName.SET_TIMER:
                dur = action.slots.get("duration_seconds")
                if not isinstance(dur, int) or dur <= 0 or dur > 86400:
                    return False, "SetTimer requires integer duration_seconds in 1..86400."
        return True, None

    async def execute_intent(
        self,
        intent: TypedIntent,
        *,
        reply_language: LanguageCode | None = None,
    ) -> VerifiedActionResult:
        """Execute validated intent (including compound sub-actions) and verify result."""
        lang: LanguageCode = reply_language or intent.target_language or intent.language
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

    async def _execute_device_control(
        self, slots: dict[str, Any], *, lang: LanguageCode
    ) -> VerifiedActionResult:
        if slots.get("use_preferred"):
            if self.memory_service is None:
                return VerifiedActionResult(
                    success=False,
                    action="DeviceControl",
                    status="failed",
                    message={
                        "en": "Personal memory service is unavailable.",
                        "hi": "व्यक्तिगत मेमोरी सेवा उपलब्ध नहीं है।",
                        "kn": "ವೈಯಕ್ತಿಕ ಮೆಮೊರಿ ಸೇವೆ ಲಭ್ಯವಿಲ್ಲ.",
                    }[lang],
                    error="MemoryService not configured.",
                )
            candidates = [
                row
                for row in self.smart_home.list_devices(slots.get("room"))
                if row.device_type == slots.get("device_type")
            ]
            if not candidates:
                return VerifiedActionResult(
                    success=False,
                    action="DeviceControl",
                    status="failed",
                    message={
                        "en": "I could not find that device.",
                        "hi": "वह उपकरण नहीं मिला।",
                        "kn": "ಆ ಸಾಧನ ಕಂಡುಬಂದಿಲ್ಲ.",
                    }[lang],
                    error="No matching device.",
                )
            if len(candidates) > 1:
                return VerifiedActionResult(
                    success=False,
                    action="DeviceControl",
                    status="clarification_required",
                    message={
                        "en": "Which room or device do you mean?",
                        "hi": "कौन सा कमरा या उपकरण?",
                        "kn": "ಯಾವ ಕೊಠಡಿ ಅಥವಾ ಸಾಧನ?",
                    }[lang],
                    data={"matches": [row.id for row in candidates]},
                )
            dev = candidates[0]
            prop = str(slots.get("action") or "temperature")
            mem_enabled, prefs = self.memory_service.resolve_appliance_preference(
                device_id=dev.id, prop=prop
            )
            if not mem_enabled:
                return VerifiedActionResult(
                    success=False,
                    action="DeviceControl",
                    status="failed",
                    message={
                        "en": "Personal memory is currently disabled, so I cannot read your saved preference.",
                        "hi": "व्यक्तिगत मेमोरी बंद है, इसलिए मैं आपकी सहेजी गई वरीयता नहीं पढ़ सकता।",
                        "kn": "ವೈಯಕ್ತಿಕ ಮೆಮೊರಿ ನಿಷ್ಕ್ರಿಯವಾಗಿದೆ, ಆದ್ದರಿಂದ ನಿಮ್ಮ ಆದ್ಯತೆಯನ್ನು ಓದಲು ಸಾಧ್ಯವಿಲ್ಲ.",
                    }[lang],
                    error="Memory disabled.",
                )
            if not prefs:
                return VerifiedActionResult(
                    success=False,
                    action="DeviceControl",
                    status="clarification_required",
                    message={
                        "en": f"You haven't saved a preferred {prop} for {dev.name}. What value should I set?",
                        "hi": f"आपने {dev.name} के लिए पसंदीदा {prop} सहेजा नहीं है। मैं कितना सेट करूँ?",
                        "kn": f"{dev.name} ಗಾಗಿ ನೀವು ಇಷ್ಟದ {prop} ಅನ್ನು ಉಳಿಸಿಲ್ಲ. ಎಷ್ಟು ಸೆಟ್ ಮಾಡಲಿ?",
                    }[lang],
                )
            pref_val = prefs[0]["structured_value"].get("value")
            resolved_slots = {
                "room": dev.room,
                "device_type": dev.device_type,
                "action": prop,
                "value": pref_val,
            }
            res = await self.smart_home.execute_slots(resolved_slots, source="voice", language=lang)
            if res.success:
                unit = "°C" if prop == "temperature" else ""
                res.data["preference_used"] = prefs[0]
                res.message = {
                    "en": f"Set {dev.name} to your preferred {prop} ({pref_val}{unit}).",
                    "hi": f"{dev.name} को आपके पसंदीदा {prop} ({pref_val}{unit}) पर सेट कर दिया गया है।",
                    "kn": f"{dev.name} ಅನ್ನು ನಿಮ್ಮ ಇಷ್ಟದ {prop} ({pref_val}{unit}) ಗೆ ಸೆಟ್ ಮಾಡಲಾಗಿದೆ.",
                }[lang]
            return res

        return await self.smart_home.execute_slots(slots, source="voice", language=lang)

    async def _execute_memory_intent(
        self, action: TypedAction, *, lang: LanguageCode
    ) -> VerifiedActionResult:
        slots = action.slots
        name = action.intent_name
        mem = self.memory_service

        if name == IntentName.SAVE_MEMORY:
            txt = str(slots.get("text") or "").strip()
            try:
                item, created = await mem.save_memory(
                    txt, language=lang, provenance="explicit_voice"
                )
            except MemoryDisabledError as exc:
                return VerifiedActionResult(
                    success=False,
                    action="SaveMemory",
                    status="failed",
                    message={
                        "en": "Personal memory is currently disabled in settings.",
                        "hi": "व्यक्तिगत मेमोरी अभी सेटिंग्स में बंद है।",
                        "kn": "ವೈಯಕ್ತಿಕ ಮೆಮೊರಿ ಪ್ರಸ್ತುತ ಸೆಟ್ಟಿಂಗ್‌ಗಳಲ್ಲಿ ನಿಷ್ಕ್ರಿಯವಾಗಿದೆ.",
                    }[lang],
                    error=str(exc),
                )
            except MemorySafetyError as exc:
                return VerifiedActionResult(
                    success=False,
                    action="SaveMemory",
                    status="refused",
                    message={
                        "en": "I cannot save credentials, secrets, or unsafe instruction overrides.",
                        "hi": "मैं पासवर्ड, गुप्त कुंजी या असुरक्षित निर्देश सहेज नहीं सकता।",
                        "kn": "ಪಾಸ್‌ವರ್ಡ್, ರಹಸ್ಯ ಕೀ ಅಥವಾ ಅಸುರಕ್ಷಿತ ಸೂಚನೆಗಳನ್ನು ನಾನು ಉಳಿಸಲು ಸಾಧ್ಯವಿಲ್ಲ.",
                    }[lang],
                    error=str(exc),
                )
            except ValueError as exc:
                return VerifiedActionResult(
                    success=False,
                    action="SaveMemory",
                    status="failed",
                    message=str(exc),
                    error=str(exc),
                )
            msg = {
                "en": f"Saved to memory: {item['original_text']}.",
                "hi": f"मैंने याद रख लिया है: {item['original_text']}।",
                "kn": f"ನೆನಪಿನಲ್ಲಿ ಉಳಿಸಲಾಗಿದೆ: {item['original_text']}.",
            }[lang]
            return VerifiedActionResult(
                success=True,
                action="SaveMemory",
                status="verified",
                data={"memory": item, "created": created},
                message=msg,
            )

        if name == IntentName.UPDATE_MEMORY:
            txt = str(slots.get("text") or "").strip()
            try:
                updated = await mem.update_preference_from_utterance(txt, language=lang)
            except MemoryDisabledError as exc:
                return VerifiedActionResult(
                    success=False,
                    action="UpdateMemory",
                    status="failed",
                    message={
                        "en": "Personal memory is currently disabled in settings.",
                        "hi": "व्यक्तिगत मेमोरी अभी सेटिंग्स में बंद है।",
                        "kn": "ವೈಯಕ್ತಿಕ ಮೆಮೊರಿ ಪ್ರಸ್ತುತ ನಿಷ್ಕ್ರಿಯವಾಗಿದೆ.",
                    }[lang],
                    error=str(exc),
                )
            except MemoryClarificationError as exc:
                return VerifiedActionResult(
                    success=False,
                    action="UpdateMemory",
                    status="clarification_required",
                    data={"candidates": exc.candidates, "reason": exc.reason},
                    message=str(exc),
                )
            except (MemorySafetyError, ValueError) as exc:
                return VerifiedActionResult(
                    success=False,
                    action="UpdateMemory",
                    status="refused" if isinstance(exc, MemorySafetyError) else "failed",
                    message=str(exc),
                    error=str(exc),
                )
            msg = {
                "en": f"Updated preference: {updated['original_text']}.",
                "hi": f"पसंदीदा सेटिंग अपडेट कर दी गई है: {updated['original_text']}।",
                "kn": f"ಆದ್ಯತೆಯನ್ನು ನವೀಕರಿಸಲಾಗಿದೆ: {updated['original_text']}.",
            }[lang]
            return VerifiedActionResult(
                success=True,
                action="UpdateMemory",
                status="verified",
                data={"memory": updated},
                message=msg,
            )

        if name == IntentName.RETRIEVE_MEMORY:
            query = str(slots.get("query") or "").strip()
            res = await mem.search_memory(query, limit=5)
            if not res["memory_enabled"]:
                return VerifiedActionResult(
                    success=False,
                    action="RetrieveMemory",
                    status="failed",
                    data=res,
                    message={
                        "en": "Personal memory is disabled in settings.",
                        "hi": "सेटिंग्स में व्यक्तिगत मेमोरी बंद है।",
                        "kn": "ಸೆಟ್ಟಿಂಗ್‌ಗಳಲ್ಲಿ ವೈಯಕ್ತಿಕ ಮೆಮೊರಿ ನಿಷ್ಕ್ರಿಯವಾಗಿದೆ.",
                    }[lang],
                    error="Memory disabled.",
                )
            items = res["items"]
            if not items:
                return VerifiedActionResult(
                    success=False,
                    action="RetrieveMemory",
                    status="clarification_required",
                    data=res,
                    message={
                        "en": "I don't have a saved memory for that yet.",
                        "hi": "मेरे पास इसके बारे में कोई सहेजी गई जानकारी नहीं है।",
                        "kn": "ಇದರ ಬಗ್ಗೆ ನನ್ನ ಬಳಿ ಯಾವುದೇ ಉಳಿಸಿದ ನೆನಪಿಲ್ಲ.",
                    }[lang],
                )
            top = items[0]
            sv = top.get("structured_value") or {}
            if top["category"] == "appliance_preference" and "value" in sv:
                val = sv["value"]
                prop = sv.get("property", "setting")
                unit = "°C" if prop == "temperature" else ""
                dev_label = str(sv.get("device_id", "appliance")).replace(".", " ")
                msg = {
                    "en": f"Your preferred {prop} for {dev_label} is {val}{unit}.",
                    "hi": f"{dev_label} के लिए आपका पसंदीदा {prop} {val}{unit} है।",
                    "kn": f"{dev_label} ಗಾಗಿ ನಿಮ್ಮ ಇಷ್ಟದ {prop} {val}{unit} ಆಗಿದೆ.",
                }[lang]
            else:
                msg = top["original_text"]
            return VerifiedActionResult(
                success=True,
                action="RetrieveMemory",
                status="verified",
                data=res,
                message=msg,
            )

        if name == IntentName.LIST_MEMORIES:
            settings = mem.get_settings()
            if not settings["memory_enabled"]:
                return VerifiedActionResult(
                    success=False,
                    action="ListMemories",
                    status="failed",
                    data={"items": [], "memory_enabled": False},
                    message={
                        "en": "Personal memory is disabled in settings.",
                        "hi": "व्यक्तिगत मेमोरी बंद है।",
                        "kn": "ವೈಯಕ್ತಿಕ ಮೆಮೊರಿ ನಿಷ್ಕ್ರಿಯವಾಗಿದೆ.",
                    }[lang],
                    error="Memory disabled.",
                )
            items = mem.list_memories(limit=10)
            if not items:
                msg = {
                    "en": "You have no saved memories yet.",
                    "hi": "अभी कोई सहेजी गई मेमोरी नहीं है।",
                    "kn": "ನೀವು ಇನ್ನೂ ಯಾವುದೇ ನೆನಪುಗಳನ್ನು ಉಳಿಸಿಲ್ಲ.",
                }[lang]
            else:
                joined = "; ".join(it["original_text"] for it in items[:5])
                msg = {
                    "en": f"Saved memories: {joined}.",
                    "hi": f"सहेजी गई यादें: {joined}।",
                    "kn": f"ಉಳಿಸಿದ ನೆನಪುಗಳು: {joined}.",
                }[lang]
            return VerifiedActionResult(
                success=True,
                action="ListMemories",
                status="verified",
                data={"items": items, "count": len(items)},
                message=msg,
            )

        if name == IntentName.DELETE_MEMORY:
            query = str(slots.get("query") or "").strip()
            count = await mem.delete_matching_memory(query)
            if count == 0:
                return VerifiedActionResult(
                    success=False,
                    action="DeleteMemory",
                    status="failed",
                    data={"deleted_count": 0},
                    message={
                        "en": "I could not find a matching memory to delete.",
                        "hi": "हटाने के लिए कोई मिलती-जुलती मेमोरी नहीं मिली।",
                        "kn": "ಅಳಿಸಲು ಹೊಂದುವ ನೆನಪು ಕಂಡುಬಂದಿಲ್ಲ.",
                    }[lang],
                    error="No matching memory.",
                )
            return VerifiedActionResult(
                success=True,
                action="DeleteMemory",
                status="verified",
                data={"deleted_count": count},
                message={
                    "en": f"Deleted {count} matching memory item(s).",
                    "hi": f"{count} मेमोरी आइटम हटा दिए गए हैं।",
                    "kn": f"{count} ನೆನಪುಗಳನ್ನು ಅಳಿಸಲಾಗಿದೆ.",
                }[lang],
            )

        return VerifiedActionResult(
            success=False,
            action=name.value,
            status="refused",
            message=get_phrase("unsupported", lang),
        )

    async def _execute_scheduler_intent(
        self, action: TypedAction, *, lang: LanguageCode
    ) -> VerifiedActionResult:
        slots = action.slots
        name = action.intent_name
        sched = self.scheduler_service
        now_utc = self._clock()
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=UTC)

        if name == IntentName.CREATE_REMINDER:
            rem_msg = str(slots.get("message") or "").strip()
            run_at, rec = _resolve_schedule_timing(
                slots, now_utc=now_utc, tz_name=self.default_timezone
            )
            if not rem_msg or run_at is None:
                return VerifiedActionResult(
                    success=False,
                    action="CreateReminder",
                    status="clarification_required",
                    message={
                        "en": "Please tell me what to remind you about and the exact time.",
                        "hi": "कृपया बताएं कि क्या याद दिलाना है और किस समय।",
                        "kn": "ದಯವಿಟ್ಟು ಏನನ್ನು ಮತ್ತು ಯಾವಾಗ ನೆನಪಿಸಬೇಕೆಂದು ತಿಳಿಸಿ.",
                    }[lang],
                )
            job = await sched.create_reminder(
                message=rem_msg,
                run_at_utc=run_at,
                recurrence=rec,
                language=lang,
                timezone_name=self.default_timezone,
            )
            local_str = run_at.astimezone(ZoneInfo(self.default_timezone)).strftime("%I:%M %p")
            msg = {
                "en": f"Reminder set for {local_str}: {rem_msg}.",
                "hi": f"{local_str} के लिए रिमाइंडर सेट कर दिया गया है: {rem_msg}।",
                "kn": f"{local_str} ಕ್ಕೆ ಜ್ಞಾಪನೆ ಸೆಟ್ ಮಾಡಲಾಗಿದೆ: {rem_msg}.",
            }[lang]
            return VerifiedActionResult(
                success=True,
                action="CreateReminder",
                status="verified",
                data={"reminder": job},
                message=msg,
            )

        if name == IntentName.LIST_REMINDERS:
            jobs = sched.list_jobs(
                job_type="reminder", statuses=("scheduled", "due", "paused"), limit=20
            )
            if not jobs:
                msg = {
                    "en": "You have no upcoming reminders.",
                    "hi": "आपका कोई आने वाला रिमाइंडर नहीं है।",
                    "kn": "ಮುಂಬರುವ ಯಾವುದೇ ಜ್ಞಾಪನೆಗಳಿಲ್ಲ.",
                }[lang]
            else:
                msg = {
                    "en": "Upcoming reminders: " + "; ".join(j["title"] for j in jobs[:5]) + ".",
                    "hi": "आने वाले रिमाइंडर: " + "; ".join(j["title"] for j in jobs[:5]) + "।",
                    "kn": "ಮುಂಬರುವ ಜ್ಞಾಪನೆಗಳು: " + "; ".join(j["title"] for j in jobs[:5]) + ".",
                }[lang]
            return VerifiedActionResult(
                success=True,
                action="ListReminders",
                status="verified",
                data={"reminders": jobs, "count": len(jobs)},
                message=msg,
            )

        if name in (IntentName.CANCEL_REMINDER, IntentName.CANCEL_SCHEDULE):
            target_type = "reminder" if name == IntentName.CANCEL_REMINDER else None
            jobs = sched.list_jobs(
                job_type=target_type, statuses=("scheduled", "due", "paused"), limit=50
            )
            if target_type is None:
                jobs = [j for j in jobs if j["job_type"] in {"device_action", "routine"}]
            if not jobs:
                return VerifiedActionResult(
                    success=False,
                    action=name.value,
                    status="failed",
                    message={
                        "en": "No matching upcoming item found to cancel.",
                        "hi": "रद्द करने के लिए कोई आगामी कार्य नहीं मिला।",
                        "kn": "ರದ್ದುಗೊಳಿಸಲು ಯಾವುದೇ ನಿಗದಿತ ಕಾರ್ಯ ಕಂಡುಬಂದಿಲ್ಲ.",
                    }[lang],
                    error="No scheduled job found.",
                )
            cancelled = await sched.cancel_job(uuid.UUID(jobs[0]["id"]))
            return VerifiedActionResult(
                success=True,
                action=name.value,
                status="verified",
                data={"schedule": cancelled},
                message={
                    "en": f"Cancelled: {jobs[0]['title']}.",
                    "hi": f"रद्द किया गया: {jobs[0]['title']}।",
                    "kn": f"ರದ್ದುಗೊಳಿಸಲಾಗಿದೆ: {jobs[0]['title']}.",
                }[lang],
            )

        if name == IntentName.SCHEDULE_DEVICE_ACTION:
            run_at, rec = _resolve_schedule_timing(
                slots, now_utc=now_utc, tz_name=self.default_timezone
            )
            if run_at is None:
                return VerifiedActionResult(
                    success=False,
                    action="ScheduleDeviceAction",
                    status="clarification_required",
                    message={
                        "en": "Please provide a valid time for scheduling that device action.",
                        "hi": "कृपया इस उपकरण को शेड्यूल करने के लिए सही समय बताएं।",
                        "kn": "ಈ ಸಾಧನವನ್ನು ನಿಗದಿಪಡಿಸಲು ದಯವಿಟ್ಟು ಸರಿಯಾದ ಸಮಯವನ್ನು ತಿಳಿಸಿ.",
                    }[lang],
                )
            candidates = [
                row
                for row in self.smart_home.list_devices(slots.get("room"))
                if row.device_type == slots.get("device_type")
            ]
            if not candidates:
                return VerifiedActionResult(
                    success=False,
                    action="ScheduleDeviceAction",
                    status="failed",
                    message={
                        "en": "I could not find that device to schedule.",
                        "hi": "शेड्यूल करने के लिए वह उपकरण नहीं मिला।",
                        "kn": "ನಿಗದಿಪಡಿಸಲು ಆ ಸಾಧನ ಕಂಡುಬಂದಿಲ್ಲ.",
                    }[lang],
                    error="Device not found.",
                )
            if len(candidates) > 1:
                return VerifiedActionResult(
                    success=False,
                    action="ScheduleDeviceAction",
                    status="clarification_required",
                    message={
                        "en": "Which room or device do you want to schedule?",
                        "hi": "आप किस कमरे या उपकरण को शेड्यूल करना चाहते हैं?",
                        "kn": "ಯಾವ ಕೊಠಡಿ ಅಥವಾ ಸಾಧನವನ್ನು ನಿಗದಿಪಡಿಸಬೇಕು?",
                    }[lang],
                    data={"matches": [c.id for c in candidates]},
                )
            dev = candidates[0]
            try:
                job = await sched.create_device_schedule(
                    device_id=dev.id,
                    action=str(slots["action"]),
                    value=slots.get("value"),
                    run_at_utc=run_at,
                    recurrence=rec,
                    language=lang,
                    timezone_name=self.default_timezone,
                )
            except (LookupError, ValueError) as exc:
                return VerifiedActionResult(
                    success=False,
                    action="ScheduleDeviceAction",
                    status="failed",
                    message=str(exc),
                    error=str(exc),
                )
            local_str = run_at.astimezone(ZoneInfo(self.default_timezone)).strftime("%I:%M %p")
            msg = {
                "en": f"Scheduled {dev.name} for {local_str}.",
                "hi": f"{dev.name} को {local_str} के लिए शेड्यूल कर दिया गया है।",
                "kn": f"{dev.name} ಅನ್ನು {local_str} ಕ್ಕೆ ನಿಗದಿಪಡಿಸಲಾಗಿದೆ.",
            }[lang]
            return VerifiedActionResult(
                success=True,
                action="ScheduleDeviceAction",
                status="verified",
                data={"schedule": job},
                message=msg,
            )

        if name == IntentName.SCHEDULE_ROUTINE:
            run_at, rec = _resolve_schedule_timing(
                slots, now_utc=now_utc, tz_name=self.default_timezone
            )
            if run_at is None:
                return VerifiedActionResult(
                    success=False,
                    action="ScheduleRoutine",
                    status="clarification_required",
                    message={
                        "en": "Please provide a valid time to schedule that routine.",
                        "hi": "कृपया उस रूटीन को शेड्यूल करने का सही समय बताएं।",
                        "kn": "ಆ ದಿನಚರಿಯನ್ನು ನಿಗದಿಪಡಿಸಲು ದಯವಿಟ್ಟು ಸರಿಯಾದ ಸಮಯವನ್ನು ತಿಳಿಸಿ.",
                    }[lang],
                )
            routine_id = str(slots.get("routine_id") or "")
            resolved = self.smart_home.resolve_routine_by_name_or_id(routine_id)
            if resolved is None:
                return VerifiedActionResult(
                    success=False,
                    action="ScheduleRoutine",
                    status="failed",
                    message={
                        "en": f"Routine '{routine_id}' was not found.",
                        "hi": f"'{routine_id}' रूटीन नहीं मिला।",
                        "kn": f"'{routine_id}' ದಿನಚರಿ ಕಂಡುಬಂದಿಲ್ಲ.",
                    }[lang],
                    error="Routine not found.",
                )
            job = await sched.create_routine_schedule(
                routine_id=resolved["id"],
                run_at_utc=run_at,
                recurrence=rec,
                language=lang,
                timezone_name=self.default_timezone,
            )
            local_str = run_at.astimezone(ZoneInfo(self.default_timezone)).strftime("%I:%M %p")
            return VerifiedActionResult(
                success=True,
                action="ScheduleRoutine",
                status="verified",
                data={"schedule": job},
                message={
                    "en": f"Scheduled the {resolved['name']} routine for {local_str}.",
                    "hi": f"{resolved['name']} रूटीन को {local_str} के लिए शेड्यूल कर दिया गया है।",
                    "kn": f"{resolved['name']} ದಿನಚರಿಯನ್ನು {local_str} ಕ್ಕೆ ನಿಗದಿಪಡಿಸಲಾಗಿದೆ.",
                }[lang],
            )

        if name == IntentName.LIST_SCHEDULES:
            jobs = sched.list_upcoming_smart_home_schedules()
            if not jobs:
                msg = {
                    "en": "You have no upcoming scheduled appliance actions or routines.",
                    "hi": "आपका कोई आगामी निर्धारित स्मार्ट होम कार्य नहीं है।",
                    "kn": "ಮುಂಬರುವ ಯಾವುದೇ ನಿಗದಿತ ಉಪಕರಣ ಕ್ರಿಯೆಗಳು ಅಥವಾ ದಿನಚರಿಗಳಿಲ್ಲ.",
                }[lang]
            else:
                msg = {
                    "en": "Upcoming schedules: " + "; ".join(j["title"] for j in jobs[:5]) + ".",
                    "hi": "आगामी शेड्यूल: " + "; ".join(j["title"] for j in jobs[:5]) + "।",
                    "kn": "ಮುಂಬರುವ ವೇಳಾಪಟ್ಟಿಗಳು: " + "; ".join(j["title"] for j in jobs[:5]) + ".",
                }[lang]
            return VerifiedActionResult(
                success=True,
                action="ListSchedules",
                status="verified",
                data={"schedules": jobs, "count": len(jobs)},
                message=msg,
            )

        return VerifiedActionResult(
            success=False,
            action=name.value,
            status="refused",
            message=get_phrase("unsupported", lang),
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
        if name == IntentName.DEVICE_CONTROL and self.smart_home is not None:
            return await self._execute_device_control(slots, lang=lang)
        if name in _MEMORY_INTENTS and self.memory_service is not None:
            return await self._execute_memory_intent(action, lang=lang)
        if name in _SCHEDULER_INTENTS and self.scheduler_service is not None:
            return await self._execute_scheduler_intent(action, lang=lang)
        if name == IntentName.RUN_ROUTINE and self.smart_home is not None:
            r_id = str(slots.get("routine_id") or "")
            resolved = self.smart_home.resolve_routine_by_name_or_id(r_id)
            if resolved is None:
                return VerifiedActionResult(
                    success=False,
                    action="RunRoutine",
                    status="failed",
                    message=f"Routine '{r_id}' was not found.",
                    error="Routine not found.",
                )
            return await self.smart_home.execute_slots(
                {"action": "routine", "routine_id": resolved["id"]},
                source="voice",
                language=lang,
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
