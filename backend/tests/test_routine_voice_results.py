import pytest

from aura_edge.application.smart_home import SmartHomeService


@pytest.mark.parametrize("state,verified,total", [("skipped_condition", 0, 0), ("partial", 1, 2), ("failed", 0, 2)])
async def test_voice_routine_respects_execution_status(monkeypatch, state, verified, total):
    async def run(*args, **kwargs):
        return {"status": state, "results": [{"status": "verified" if n < verified else "failed"} for n in range(total)]}

    home = object.__new__(SmartHomeService)
    monkeypatch.setattr(home, "run_routine", run)
    result = await home.execute_slots({"action": "routine", "routine_id": "morning"}, source="voice", language="en")
    assert not result.success and result.status != "verified"
    assert "Activated" not in result.message
    assert result.data["status"] == state
    if total:
        assert f"{verified} of {total}" in result.message
