"""10-state Voice Assistant FSM with turn IDs, cancellation tokens, and stale-turn guards."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

logger = logging.getLogger(__name__)


class AssistantState(str, Enum):
    """Required Phase 1 assistant lifecycle states."""

    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    UNDERSTANDING = "UNDERSTANDING"
    CLARIFYING = "CLARIFYING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    SPEAKING = "SPEAKING"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


VALID_TRANSITIONS: dict[AssistantState, set[AssistantState]] = {
    AssistantState.IDLE: {
        AssistantState.LISTENING,
        AssistantState.TRANSCRIBING,
        AssistantState.UNDERSTANDING,
        AssistantState.SPEAKING,
        AssistantState.ERROR,
    },
    AssistantState.LISTENING: {
        AssistantState.TRANSCRIBING,
        AssistantState.CLARIFYING,
        AssistantState.CANCELLED,
        AssistantState.ERROR,
        AssistantState.IDLE,
    },
    AssistantState.TRANSCRIBING: {
        AssistantState.UNDERSTANDING,
        AssistantState.CLARIFYING,
        AssistantState.CANCELLED,
        AssistantState.ERROR,
        AssistantState.IDLE,
    },
    AssistantState.UNDERSTANDING: {
        AssistantState.CLARIFYING,
        AssistantState.EXECUTING,
        AssistantState.SPEAKING,
        AssistantState.CANCELLED,
        AssistantState.ERROR,
        AssistantState.IDLE,
    },
    AssistantState.CLARIFYING: {
        AssistantState.SPEAKING,
        AssistantState.LISTENING,
        AssistantState.CANCELLED,
        AssistantState.ERROR,
        AssistantState.IDLE,
    },
    AssistantState.EXECUTING: {
        AssistantState.VERIFYING,
        AssistantState.CANCELLED,
        AssistantState.ERROR,
    },
    AssistantState.VERIFYING: {
        AssistantState.SPEAKING,
        AssistantState.CLARIFYING,
        AssistantState.ERROR,
        AssistantState.CANCELLED,
        AssistantState.IDLE,
    },
    AssistantState.SPEAKING: {
        AssistantState.IDLE,
        AssistantState.LISTENING,
        AssistantState.CANCELLED,
        AssistantState.ERROR,
    },
    AssistantState.CANCELLED: {
        AssistantState.IDLE,
        AssistantState.LISTENING,
    },
    AssistantState.ERROR: {
        AssistantState.IDLE,
        AssistantState.SPEAKING,
        AssistantState.LISTENING,
    },
}


class InvalidStateTransitionError(RuntimeError):
    """Raised when attempting an illegal FSM state jump."""


class StaleTurnError(RuntimeError):
    """Raised when a stale background turn attempts to mutate active FSM state."""


@dataclass
class TurnContext:
    """Isolated per-turn context carrying cancellation and irreversible-action tracking."""

    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    source: str = "text_input"
    language: str = "en"
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    completed_actions: list[str] = field(default_factory=list)
    created_at_utc: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def mark_cancelled(self) -> None:
        self.cancel_event.set()

    def record_completed_action(self, action_name: str) -> None:
        self.completed_actions.append(action_name)


StateChangeCallback = Callable[[AssistantState, str | None, str | None, str | None], Awaitable[None]]


class AssistantStateMachine:
    """Thread/async-safe 10-state FSM enforcing turn isolation & clean cancellation."""

    def __init__(self, on_change: StateChangeCallback | None = None) -> None:
        self._state = AssistantState.IDLE
        self._active_turn: TurnContext | None = None
        self._lock = asyncio.Lock()
        self._on_change = on_change
        self._history: list[tuple[AssistantState, str | None]] = [(AssistantState.IDLE, None)]

    def set_on_change(self, callback: StateChangeCallback | None) -> None:
        self._on_change = callback

    @property
    def state(self) -> AssistantState:
        return self._state

    @property
    def active_turn(self) -> TurnContext | None:
        return self._active_turn

    @property
    def history(self) -> list[tuple[AssistantState, str | None]]:
        return list(self._history)

    async def start_turn(
        self,
        *,
        source: str = "text_input",
        language: str = "en",
        initial_state: AssistantState = AssistantState.UNDERSTANDING,
    ) -> TurnContext:
        """Start a new turn, cancelling any prior active turn cleanly."""
        if initial_state not in VALID_TRANSITIONS[AssistantState.IDLE]:
            raise InvalidStateTransitionError(f"Cannot start turn directly in {initial_state.value}.")

        async with self._lock:
            if self._active_turn is not None:
                self._active_turn.mark_cancelled()
            # Reset back to IDLE first if previous turn hadn't settled
            if self._state != AssistantState.IDLE:
                self._state = AssistantState.IDLE
                self._history.append((AssistantState.IDLE, None))
            ctx = TurnContext(source=source, language=language)
            self._active_turn = ctx
            self._state = initial_state
            self._history.append((initial_state, ctx.turn_id))
            cb = self._on_change

        if cb is not None:
            await cb(initial_state, ctx.turn_id, None, language)
        return ctx

    async def transition(
        self,
        turn_id: str,
        new_state: AssistantState,
        *,
        message: str | None = None,
        language: str | None = None,
    ) -> AssistantState:
        """Transition active turn to *new_state*, rejecting stale turn IDs."""
        async with self._lock:
            if self._active_turn is None or self._active_turn.turn_id != turn_id:
                raise StaleTurnError(f"Ignoring stale turn '{turn_id}' transition to {new_state.value}.")
            if self._active_turn.is_cancelled and new_state not in (
                AssistantState.CANCELLED,
                AssistantState.IDLE,
            ):
                raise StaleTurnError(f"Turn '{turn_id}' is already cancelled.")
            if new_state not in VALID_TRANSITIONS[self._state]:
                raise InvalidStateTransitionError(
                    f"Illegal state transition {self._state.value} -> {new_state.value}."
                )
            self._state = new_state
            self._history.append((new_state, turn_id))
            lang = language or self._active_turn.language
            if new_state == AssistantState.IDLE:
                self._active_turn = None
            cb = self._on_change

        if cb is not None:
            await cb(new_state, turn_id, message, lang)
        return new_state

    async def finish_turn(self, turn_id: str) -> None:
        """Safely settle an active turn back to IDLE."""
        async with self._lock:
            if self._active_turn is None or self._active_turn.turn_id != turn_id:
                return
            if self._state == AssistantState.IDLE:
                self._active_turn = None
                return
            self._state = AssistantState.IDLE
            self._history.append((AssistantState.IDLE, turn_id))
            lang = self._active_turn.language
            self._active_turn = None
            cb = self._on_change

        if cb is not None:
            await cb(AssistantState.IDLE, turn_id, None, lang)

    async def cancel_active_turn(self, turn_id: str | None = None) -> dict[str, object]:
        """Cancel the active turn (or specified turn) without erasing completed-action history."""
        async with self._lock:
            ctx = self._active_turn
            if ctx is None:
                return {"cancelled": False, "turn_id": None, "completed_actions": []}
            if turn_id is not None and ctx.turn_id != turn_id:
                return {"cancelled": False, "turn_id": ctx.turn_id, "completed_actions": []}

            ctx.mark_cancelled()
            completed = list(ctx.completed_actions)
            tid = ctx.turn_id
            lang = ctx.language
            if AssistantState.CANCELLED in VALID_TRANSITIONS[self._state]:
                self._state = AssistantState.CANCELLED
                self._history.append((AssistantState.CANCELLED, tid))
            cb = self._on_change

        if cb is not None:
            await cb(AssistantState.CANCELLED, tid, "Turn cancelled.", lang)

        # Return to IDLE cleanly
        async with self._lock:
            if self._active_turn is not None and self._active_turn.turn_id == tid:
                self._state = AssistantState.IDLE
                self._history.append((AssistantState.IDLE, tid))
                self._active_turn = None
                cb2 = self._on_change
            else:
                cb2 = None

        if cb2 is not None:
            await cb2(AssistantState.IDLE, tid, None, lang)

        return {
            "cancelled": True,
            "turn_id": tid,
            "completed_actions": completed,
            "completed_actions_preserved": True,
        }
