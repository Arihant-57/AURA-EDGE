"""Tests for 10-state AssistantStateMachine, turn isolation, and honest cancellation."""

from __future__ import annotations

import pytest

from aura_edge.execution.state_machine import (
    AssistantState,
    AssistantStateMachine,
    InvalidStateTransitionError,
    StaleTurnError,
)


async def test_ten_states_happy_path_and_invalid_transition_guard() -> None:
    events: list[tuple[str, str | None]] = []

    async def on_change(state: AssistantState, turn_id: str | None, _msg, _lang) -> None:
        events.append((state.value, turn_id))

    fsm = AssistantStateMachine(on_change=on_change)
    assert fsm.state == AssistantState.IDLE

    ctx = await fsm.start_turn(source="voice_ptt", language="kn", initial_state=AssistantState.LISTENING)
    assert fsm.state == AssistantState.LISTENING

    await fsm.transition(ctx.turn_id, AssistantState.TRANSCRIBING)
    await fsm.transition(ctx.turn_id, AssistantState.UNDERSTANDING)
    await fsm.transition(ctx.turn_id, AssistantState.EXECUTING)
    ctx.record_completed_action("SetTimer")
    await fsm.transition(ctx.turn_id, AssistantState.VERIFYING)
    await fsm.transition(ctx.turn_id, AssistantState.SPEAKING)
    await fsm.finish_turn(ctx.turn_id)

    assert fsm.state == AssistantState.IDLE
    observed_states = [s for s, _ in events]
    assert observed_states == [
        "LISTENING",
        "TRANSCRIBING",
        "UNDERSTANDING",
        "EXECUTING",
        "VERIFYING",
        "SPEAKING",
        "IDLE",
    ]

    # Illegal direct transition from IDLE -> VERIFYING must fail
    with pytest.raises(InvalidStateTransitionError):
        await fsm.start_turn(initial_state=AssistantState.VERIFYING)


async def test_stale_turn_isolation_and_honest_cancellation() -> None:
    fsm = AssistantStateMachine()
    old_ctx = await fsm.start_turn(initial_state=AssistantState.LISTENING)
    new_ctx = await fsm.start_turn(initial_state=AssistantState.UNDERSTANDING)

    assert old_ctx.is_cancelled is True
    with pytest.raises(StaleTurnError):
        await fsm.transition(old_ctx.turn_id, AssistantState.TRANSCRIBING)

    await fsm.transition(new_ctx.turn_id, AssistantState.EXECUTING)
    new_ctx.record_completed_action("SetTimer")
    cancel_res = await fsm.cancel_active_turn(turn_id=new_ctx.turn_id)

    assert cancel_res["cancelled"] is True
    assert cancel_res["completed_actions"] == ["SetTimer"]
    assert cancel_res["completed_actions_preserved"] is True
    assert fsm.state == AssistantState.IDLE
