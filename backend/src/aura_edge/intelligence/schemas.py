"""Language-independent typed intent, verification, and turn response schemas."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

LanguageCode = Literal["en", "hi", "kn"]
RequestedLanguage = Literal["auto", "en", "hi", "kn"]
InputScript = Literal["latin", "devanagari", "kannada", "mixed"]
RouteCategory = Literal[
    "greeting",
    "conversation",
    "tool",
    "clarification",
    "refusal",
    "language_switch",
    "cancellation",
    "error",
]


class IntentName(str, Enum):
    """Canonical language-independent Phase 1 + Phase 1.5 intents."""

    CALCULATE = "Calculate"
    GET_TIME = "GetTime"
    GET_DATE = "GetDate"
    SET_TIMER = "SetTimer"
    LIST_TIMERS = "ListTimers"
    GET_TIMER_REMAINING = "GetTimerRemaining"
    PAUSE_TIMER = "PauseTimer"
    RESUME_TIMER = "ResumeTimer"
    CANCEL_TIMER = "CancelTimer"
    CANCEL_ALL_TIMERS = "CancelAllTimers"
    STOP_ASSISTANT = "StopAssistant"
    HELP = "Help"
    CLARIFY = "Clarify"
    UNSUPPORTED = "Unsupported"
    GREETING = "Greeting"
    ACKNOWLEDGMENT = "Acknowledgment"
    FAREWELL = "Farewell"
    IDENTITY = "Identity"
    SUPPORTED_LANGUAGES = "SupportedLanguages"
    SWITCH_LANGUAGE = "SwitchLanguage"
    CONVERSATION = "Conversation"
    DEVICE_CONTROL = "DeviceControl"

    # Phase 3 Personal Memory, Scheduling, Reminders & Custom Routines
    SAVE_MEMORY = "SaveMemory"
    RETRIEVE_MEMORY = "RetrieveMemory"
    UPDATE_MEMORY = "UpdateMemory"
    DELETE_MEMORY = "DeleteMemory"
    LIST_MEMORIES = "ListMemories"
    CREATE_REMINDER = "CreateReminder"
    LIST_REMINDERS = "ListReminders"
    UPDATE_REMINDER = "UpdateReminder"
    CANCEL_REMINDER = "CancelReminder"
    SCHEDULE_DEVICE_ACTION = "ScheduleDeviceAction"
    SCHEDULE_ROUTINE = "ScheduleRoutine"
    LIST_SCHEDULES = "ListSchedules"
    UPDATE_SCHEDULE = "UpdateSchedule"
    CANCEL_SCHEDULE = "CancelSchedule"
    CREATE_ROUTINE = "CreateRoutine"
    UPDATE_ROUTINE = "UpdateRoutine"
    DELETE_ROUTINE = "DeleteRoutine"
    RUN_ROUTINE = "RunRoutine"

    # Phase 4 Personal Secretary
    CREATE_TASK = "CreateTask"
    LIST_TASKS = "ListTasks"
    COMPLETE_TASK = "CompleteTask"
    DELETE_TASK = "DeleteTask"
    CREATE_CALENDAR_EVENT = "CreateCalendarEvent"
    LIST_CALENDAR_EVENTS = "ListCalendarEvents"
    DAILY_BRIEFING = "DailyBriefing"
    ADD_GROCERY_ITEM = "AddGroceryItem"
    LIST_GROCERY_LIST = "ListGroceryList"
    CHECK_INVENTORY = "CheckInventory"
    SUGGEST_RECIPES = "SuggestRecipes"
    ADD_EXPENSE = "AddExpense"
    EXPENSE_SUMMARY = "ExpenseSummary"
    LIST_PARCELS = "ListParcels"
    ADD_PARCEL = "AddParcel"


class TypedAction(BaseModel):
    """Validated atomic sub-action inside a single or compound user request."""

    intent_name: IntentName
    slots: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0


class TypedIntent(BaseModel):
    """Canonical language-neutral intent emitted by Hassil router or Ollama planner."""

    intent_name: IntentName
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["hassil_fast", "llm_tool", "llm_conversation", "clarification", "refusal"]
    route_category: RouteCategory = "tool"
    language: LanguageCode
    target_language: LanguageCode | None = None
    input_script: InputScript = "latin"
    slots: dict[str, Any] = Field(default_factory=dict)
    sub_actions: list[TypedAction] = Field(default_factory=list)
    requires_confirmation: bool = False
    clarification_question: str | None = None
    raw_text: str = ""


class VerifiedActionResult(BaseModel):
    """Result returned by ToolRegistry only after real execution & post-check."""

    success: bool
    action: str
    status: Literal["verified", "failed", "clarification_required", "refused", "cancelled"]
    data: dict[str, Any] = Field(default_factory=dict)
    message: str
    error: str | None = None
