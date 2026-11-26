"""Phase 2 persistent smart-home simulator acceptance tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from aura_edge.config import Settings
from aura_edge.main import create_app


def test_voice_refuses_external_bookings_and_clarifies_invalid_device_values():
    with TestClient(create_app(Settings(_env_file=None))) as client:
        def history():
            return client.get("/api/v1/smart-home/history?limit=1").json()

        before = history()
        for phrase, outcome in (
            ("Reserve a train ticket for tomorrow", "refusal"),
            ("Set the study fan speed to 99", "clarification"),
            ("Set the bedroom AC to 100 degrees", "clarification"),
        ):
            result = client.post("/api/v1/intent/process", json={
                "text": phrase, "speak": False, "allow_llm_fallback": False,
            }).raise_for_status().json()
            assert result["outcome"] == outcome and not result["action_executed"]
            assert history() == before


def test_seeded_home_validation_persistence_and_websocket() -> None:
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        rooms = client.get("/api/v1/smart-home/rooms")
        assert rooms.status_code == 200
        assert {room["id"] for room in rooms.json()} == {
            "living_room", "bedroom", "kitchen", "study", "utility_room"
        }
        devices = client.get("/api/v1/smart-home/devices").json()
        assert len(devices) >= 19
        assert all(
            {"id", "room", "device_type", "aliases", "capabilities", "state", "available", "source", "updated_at_utc"} <= set(device)
            for device in devices
        )

        with client.websocket_connect("/api/v1/ws/live") as ws:
            ws.receive_json()
            changed = client.post(
                "/api/v1/smart-home/devices/light.bedroom_main/command",
                json={"action": "brightness", "value": 42, "source": "ui"},
            )
            assert changed.status_code == 200
            assert changed.json()["device"]["state"] == {"power": True, "brightness": 42}
            event = ws.receive_json()
            assert event["type"] == "device_state_changed"
            assert event["payload"]["source"] == "ui"

        bad = client.post(
            "/api/v1/smart-home/devices/fan.bedroom_ceiling/command",
            json={"action": "temperature", "value": 45, "source": "ui"},
        )
        assert bad.status_code == 422
        assert "does not support" in bad.json()["detail"]

        sensor_write = client.post(
            "/api/v1/smart-home/devices/sensor.bedroom_climate/command",
            json={"action": "temperature", "value": 99, "source": "ui"},
        )
        assert sensor_write.status_code == 422

    restarted = create_app(Settings(_env_file=None))
    with TestClient(restarted) as client:
        saved = client.get("/api/v1/smart-home/devices/light.bedroom_main").json()
        assert saved["state"]["brightness"] == 42


def test_appliance_cycle_transitions_and_energy_label() -> None:
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        started = client.post(
            "/api/v1/smart-home/devices/washer.utility/command",
            json={"action": "activity", "value": "start", "source": "ui"},
        )
        assert started.status_code == 200
        washer = started.json()["device"]
        assert washer["state"]["activity"] == "running"
        assert washer["state"]["remaining_seconds"] == 3600
        assert washer["estimated_power_w"] == 1800

        paused = client.post(
            "/api/v1/smart-home/devices/washer.utility/command",
            json={"action": "activity", "value": "pause", "source": "ui"},
        )
        assert paused.json()["device"]["state"]["activity"] == "paused"
        resumed = client.post(
            "/api/v1/smart-home/devices/washer.utility/command",
            json={"action": "activity", "value": "resume", "source": "ui"},
        )
        assert resumed.json()["device"]["state"]["activity"] == "running"
        cancelled = client.post(
            "/api/v1/smart-home/devices/washer.utility/command",
            json={"action": "activity", "value": "cancel", "source": "ui"},
        )
        assert cancelled.json()["device"]["state"]["activity"] == "idle"

        energy = client.get("/api/v1/smart-home/energy").json()
        assert energy["source_kind"] == "estimated"
        assert energy["is_simulated"] is True

        routines = client.get("/api/v1/smart-home/routines").json()
        assert {r["id"] for r in routines} == {"morning", "night", "study", "away"}
        study = client.post("/api/v1/smart-home/routines/study/run")
        assert study.status_code == 200
        assert study.json()["status"] == "verified"

        injected = client.post(
            "/api/v1/smart-home/simulator/devices/sensor.bedroom_climate/sensor",
            json={"state": {"temperature": 27.5, "humidity": 55}},
        )
        assert injected.status_code == 200
        assert injected.json()["is_simulated"] is True


def test_multilingual_voice_and_manual_commands_share_dispatcher() -> None:
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        for text, language in [
            ("Turn on the bedroom fan", "en"),
            ("बेडरूम का पंखा चालू करो", "hi"),
            ("ಬೆಡ್ ರೂಮ್ ಫ್ಯಾನ್ ಆನ್ ಮಾಡು", "kn"),
        ]:
            response = client.post(
                "/api/v1/intent/process",
                json={"text": text, "language": language, "speak": False},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["intent"] == "DeviceControl"
            assert body["action_executed"] is True
            assert body["data"]["device"]["id"] == "fan.bedroom_ceiling"
            assert body["data"]["device"]["state"]["power"] is True

        ambiguous = client.post(
            "/api/v1/intent/process",
            json={"text": "Turn on the fan", "language": "en", "speak": False},
        ).json()
        assert ambiguous["status"] == "clarification_required"

        manual = client.post(
            "/api/v1/smart-home/devices/fan.bedroom_ceiling/command",
            json={"action": "speed", "value": 4, "source": "ui"},
        ).json()
        assert manual["device"]["state"]["speed"] == 4
        history = client.get(
            "/api/v1/smart-home/history?device_id=fan.bedroom_ceiling"
        ).json()
        assert {entry["context_source"] for entry in history} >= {"voice", "ui"}

        # Localized DeviceStatus (never raw Python dict) & routine & climate sensor queries
        for query, exp_lang in [
            ("Is the bedroom light on?", "en"),
            ("बेडरूम की लाइट चालू है क्या?", "hi"),
            ("ಬೆಡ್‌ರೂಮ್ ಲೈಟ್ ಆನ್ ಇದೆಯಾ?", "kn"),
            ("What is the bedroom temperature?", "en"),
        ]:
            st = client.post(
                "/api/v1/intent/process",
                json={"text": query, "language": exp_lang, "speak": False},
            )
            assert st.status_code == 200
            st_body = st.json()
            assert st_body["intent"] == "DeviceControl"
            assert st_body["action_executed"] is True
            assert "{" not in st_body["response_text"]
            assert "}" not in st_body["response_text"]

        routine_res = client.post(
            "/api/v1/intent/process",
            json={"text": "Run the study routine", "language": "en", "speak": False},
        )
        assert routine_res.status_code == 200
        assert routine_res.json()["action_executed"] is True
        assert routine_res.json()["data"]["routine_id"] == "study"
