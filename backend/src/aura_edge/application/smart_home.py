"""Persistent, capability-validated virtual smart-home simulator."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aura_edge.api.ws_events import WebSocketEventHub
from aura_edge.data.models import SmartHomeDevice, SmartHomeRoutine, SmartHomeStateHistory
from aura_edge.intelligence.response_catalog import format_duration_spoken
from aura_edge.intelligence.schemas import LanguageCode, VerifiedActionResult

logger = logging.getLogger(__name__)

_ROOM_LABELS: dict[str, dict[LanguageCode, str]] = {
    "living_room": {"en": "living room", "hi": "लिविंग रूम", "kn": "ಲಿವಿಂಗ್ ರೂಮ್"},
    "bedroom": {"en": "bedroom", "hi": "बेडरूम", "kn": "ಬೆಡ್‌ರೂಮ್"},
    "kitchen": {"en": "kitchen", "hi": "किचन", "kn": "ಕಿಚನ್"},
    "study": {"en": "study room", "hi": "स्टडी रूम", "kn": "ಸ್ಟಡಿ ರೂಮ್"},
    "utility_room": {"en": "utility room", "hi": "यूटिलिटी रूम", "kn": "ಯುಟಿಲಿಟಿ ರೂಮ್"},
}
_DEVICE_LABELS: dict[str, dict[LanguageCode, str]] = {
    "light": {"en": "light", "hi": "लाइट", "kn": "ಲೈಟ್"},
    "fan": {"en": "fan", "hi": "पंखा", "kn": "ಫ್ಯಾನ್"},
    "air_conditioner": {"en": "AC", "hi": "एसी", "kn": "ಏಸಿ"},
    "television": {"en": "TV", "hi": "टीवी", "kn": "ಟಿವಿ"},
    "curtain": {"en": "curtain", "hi": "पर्दा", "kn": "ಪರದೆ"},
    "washing_machine": {"en": "washing machine", "hi": "वॉशिंग मशीन", "kn": "ವಾಷಿಂಗ್ ಮೆಷಿನ್"},
    "robot_vacuum": {"en": "robot vacuum", "hi": "रोबोट वैक्यूम", "kn": "ರೋಬೋಟ್ ವ್ಯಾಕ್ಯೂಮ್"},
    "air_purifier": {"en": "air purifier", "hi": "एयर प्यूरीफायर", "kn": "ಏರ್ ಪ್ಯೂರಿಫೈಯರ್"},
    "water_heater": {"en": "water heater", "hi": "वॉटर हीटर", "kn": "ವಾಟರ್ ಹೀಟರ್"},
    "water_pump": {"en": "water pump", "hi": "वॉटर पंप", "kn": "ವಾಟರ್ ಪಂಪ್"},
    "refrigerator": {"en": "refrigerator", "hi": "फ्रिज", "kn": "ಫ್ರಿಜ್"},
    "climate_sensor": {"en": "climate sensor", "hi": "क्लाइमेट सेंसर", "kn": "ಹವಾಮಾನ ಸೆನ್ಸರ್"},
}
_ROUTINE_LABELS: dict[str, dict[LanguageCode, str]] = {
    "morning": {"en": "morning", "hi": "सुबह", "kn": "ಬೆಳಗಿನ"},
    "night": {"en": "night", "hi": "रात", "kn": "ರಾತ್ರಿ"},
    "study": {"en": "study", "hi": "स्टडी", "kn": "ಅಧ್ಯಯನ"},
    "away": {"en": "away", "hi": "अवे", "kn": "ಹೊರಗಿನ"},
}


def _localized_device_name(row: SmartHomeDevice, language: LanguageCode) -> str:
    room = _ROOM_LABELS.get(row.room, {}).get(language, row.room.replace("_", " "))
    dev = _DEVICE_LABELS.get(row.device_type, {}).get(language, row.device_type.replace("_", " "))
    if language == "hi":
        return f"{room} का {dev}"
    if language == "kn":
        return f"{room} {dev}"
    return f"{room} {dev}"


def _format_device_status(row: SmartHomeDevice, language: LanguageCode) -> str:
    label = _localized_device_name(row, language)
    state = row.state or {}

    if row.device_type == "climate_sensor":
        temp = state.get("temperature")
        hum = state.get("humidity")
        if language == "hi":
            return f"{label} का तापमान {temp}°C और नमी {hum}% है।"
        if language == "kn":
            return f"{label}: ತಾಪಮಾನ {temp}°C ಮತ್ತು ಆರ್ದ್ರತೆ {hum}% ಇದೆ."
        return f"The {label} reports temperature {temp}°C and humidity {hum}%."

    if row.device_type == "refrigerator":
        temp = state.get("temperature")
        door = bool(state.get("door_open"))
        if language == "hi":
            door_txt = "खुला है" if door else "बंद है"
            return f"{label} {temp}°C पर है और दरवाज़ा {door_txt}।"
        if language == "kn":
            door_txt = "ತೆರೆದಿದೆ" if door else "ಮುಚ್ಚಿದೆ"
            return f"{label} {temp}°C ನಲ್ಲಿದೆ ಮತ್ತು ಬಾಗಿಲು {door_txt}."
        door_txt = "open" if door else "closed"
        return f"The {label} is at {temp}°C with the door {door_txt}."

    if row.device_type == "curtain":
        pos = int(state.get("position", 0))
        if language == "hi":
            if pos >= 90:
                return f"{label} खुला है ({pos}%)।"
            if pos <= 10:
                return f"{label} बंद है ({pos}%)।"
            return f"{label} {pos}% खुला है।"
        if language == "kn":
            if pos >= 90:
                return f"{label} ತೆರೆದಿದೆ ({pos}%)."
            if pos <= 10:
                return f"{label} ಮುಚ್ಚಿದೆ ({pos}%)."
            return f"{label} {pos}% ತೆರೆದಿದೆ."
        if pos >= 90:
            return f"The {label} is open ({pos}%)."
        if pos <= 10:
            return f"The {label} is closed ({pos}%)."
        return f"The {label} is at {pos}%."

    if row.device_type in {"washing_machine", "robot_vacuum"}:
        act = str(state.get("activity", "idle"))
        rem = int(state.get("remaining_seconds", 0) or 0)
        rem_txt = format_duration_spoken(rem, language) if rem > 0 else ""
        if language == "hi":
            act_map = {
                "idle": "बंद है",
                "running": "चल रहा है",
                "cleaning": "सफ़ाई कर रहा है",
                "paused": "रुका हुआ है",
                "completed": "पूरा हो गया है",
                "docked": "चार्जिंग पर डॉक है",
            }
            desc = act_map.get(act, act)
            return f"{label} {desc} ({rem_txt} बाकी)।" if rem_txt else f"{label} {desc}।"
        if language == "kn":
            act_map = {
                "idle": "ನಿಂತಿದೆ",
                "running": "ನಡೆಯುತ್ತಿದೆ",
                "cleaning": "ಸ್ವಚ್ಛಗೊಳಿಸುತ್ತಿದೆ",
                "paused": "ವಿರಾಮದಲ್ಲಿದೆ",
                "completed": "ಪೂರ್ಣಗೊಂಡಿದೆ",
                "docked": "ಡಾಕ್ ಆಗಿದೆ",
            }
            desc = act_map.get(act, act)
            return f"{label} {desc} ({rem_txt} ಬಾಕಿ)." if rem_txt else f"{label} {desc}."
        return f"The {label} is {act} with {rem_txt} remaining." if rem_txt else f"The {label} is {act}."

    power = state.get("power")
    if isinstance(power, bool):
        details: list[str] = []
        if "brightness" in state:
            if language == "hi":
                details.append(f"रोशनी {state['brightness']}%")
            elif language == "kn":
                details.append(f"ಪ್ರಕಾಶ {state['brightness']}%")
            else:
                details.append(f"brightness {state['brightness']}%")
        if "speed" in state:
            if language == "hi":
                details.append(f"स्पीड {state['speed']}")
            elif language == "kn":
                details.append(f"ವೇಗ {state['speed']}")
            else:
                details.append(f"speed {state['speed']}")
        if "temperature" in state:
            if language == "hi":
                details.append(f"तापमान {state['temperature']}°C")
            elif language == "kn":
                details.append(f"ತಾಪಮಾನ {state['temperature']}°C")
            else:
                details.append(f"temperature {state['temperature']}°C")
        if "volume" in state:
            if language == "hi":
                details.append(f"वॉल्यूम {state['volume']}")
            elif language == "kn":
                details.append(f"ವಾಲ್ಯೂಮ್ {state['volume']}")
            else:
                details.append(f"volume {state['volume']}")
        detail_str = f" ({', '.join(details)})" if details and power else ""
        if language == "hi":
            return f"{label} {'चालू' if power else 'बंद'} है{detail_str}।"
        if language == "kn":
            return f"{label} {'ಆನ್ ಆಗಿದೆ' if power else 'ಆಫ್ ಆಗಿದೆ'}{detail_str}."
        return f"The {label} is {'on' if power else 'off'}{detail_str}."

    return f"{label}."


def _log_simulation_task_done(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("SmartHome simulation background task failed: %s", exc, exc_info=exc)


def _seed_rows() -> list[dict[str, Any]]:
    common_rooms = {
        "living_room": {"en": ["living room"], "hi": ["बैठक"], "kn": ["ಹಾಲ್"]},
        "bedroom": {"en": ["bedroom"], "hi": ["बेडरूम"], "kn": ["ಮಲಗುವ ಕೋಣೆ"]},
        "kitchen": {"en": ["kitchen"], "hi": ["रसोई"], "kn": ["ಅಡುಗೆಮನೆ"]},
        "study": {"en": ["study"], "hi": ["अध्ययन कक्ष"], "kn": ["ಅಧ್ಯಯನ ಕೊಠಡಿ"]},
        "utility_room": {"en": ["utility room"], "hi": ["यूटिलिटी रूम"], "kn": ["ಯುಟಿಲಿಟಿ ಕೊಠಡಿ"]},
    }

    def row(device_id: str, room: str, kind: str, state: dict[str, Any], capabilities: dict[str, Any], watts: float | None, *, essential: bool = False) -> dict[str, Any]:
        return {
            "id": device_id, "room": room, "device_type": kind,
            "name": device_id.replace("_", " ").replace(".", " ").title(),
            "aliases": {"room": common_rooms[room], "device": {"en": [kind.replace("_", " ")] }},
            "capabilities": capabilities, "state": state, "power_rating_w": watts,
            "is_essential": essential,
        }

    power = {"power": {"type": "bool"}}
    return [
        row("light.living_main", "living_room", "light", {"power": False, "brightness": 70}, {**power, "brightness": {"min": 0, "max": 100}}, 12),
        row("fan.living_ceiling", "living_room", "fan", {"power": False, "speed": 2}, {**power, "speed": {"min": 1, "max": 5}}, 75),
        row("ac.living", "living_room", "air_conditioner", {"power": False, "temperature": 24, "mode": "cool"}, {**power, "temperature": {"min": 16, "max": 30}, "mode": {"enum": ["cool", "fan", "dry", "auto"]}}, 1500),
        row("tv.living", "living_room", "television", {"power": False, "volume": 25}, {**power, "volume": {"min": 0, "max": 100}}, 120),
        row("curtain.living", "living_room", "curtain", {"position": 0}, {"position": {"min": 0, "max": 100}}, 20),
        row("purifier.living", "living_room", "air_purifier", {"power": False, "speed": 1}, {**power, "speed": {"min": 1, "max": 5}}, 55),
        row("light.bedroom_main", "bedroom", "light", {"power": False, "brightness": 60}, {**power, "brightness": {"min": 0, "max": 100}}, 10),
        row("fan.bedroom_ceiling", "bedroom", "fan", {"power": False, "speed": 2}, {**power, "speed": {"min": 1, "max": 5}}, 70),
        row("ac.bedroom", "bedroom", "air_conditioner", {"power": False, "temperature": 24, "mode": "cool"}, {**power, "temperature": {"min": 16, "max": 30}, "mode": {"enum": ["cool", "fan", "dry", "auto"]}}, 1400),
        row("curtain.bedroom", "bedroom", "curtain", {"position": 0}, {"position": {"min": 0, "max": 100}}, 20),
        row("light.kitchen_main", "kitchen", "light", {"power": False, "brightness": 80}, {**power, "brightness": {"min": 0, "max": 100}}, 14),
        row("refrigerator.kitchen", "kitchen", "refrigerator", {"temperature": 4, "door_open": False}, {"temperature": {"min": 2, "max": 6}, "door_open": {"type": "bool"}}, 150, essential=True),
        row("light.study_desk", "study", "light", {"power": False, "brightness": 75}, {**power, "brightness": {"min": 0, "max": 100}}, 9),
        row("fan.study", "study", "fan", {"power": False, "speed": 2}, {**power, "speed": {"min": 1, "max": 5}}, 60),
        row("washer.utility", "utility_room", "washing_machine", {"activity": "idle", "cycle": "normal", "remaining_seconds": 0}, {"activity": {"enum": ["start", "pause", "resume", "cancel"]}, "cycle": {"enum": ["quick", "normal", "heavy"]}}, 1800),
        row("vacuum.home", "utility_room", "robot_vacuum", {"activity": "docked", "remaining_seconds": 0}, {"activity": {"enum": ["start", "pause", "resume", "dock", "cancel"]}}, 45),
        row("heater.utility", "utility_room", "water_heater", {"power": False, "temperature": 50}, {**power, "temperature": {"min": 30, "max": 75}}, 2000),
        row("pump.utility", "utility_room", "water_pump", {"power": False}, power, 750),
        row("sensor.bedroom_climate", "bedroom", "climate_sensor", {"temperature": 25.0, "humidity": 48.0}, {}, None),
    ]


class SmartHomeService:
    def __init__(self, engine: Engine, ws_hub: WebSocketEventHub) -> None:
        self.engine = engine
        self.ws_hub = ws_hub
        self._simulation_task: asyncio.Task[None] | None = None

    async def startup(self) -> dict[str, int]:
        try:
            devices = self.seed_if_empty()
            routines = self.seed_routines_if_empty()
        except SQLAlchemyError:
            return {"devices_seeded": 0, "routines_seeded": 0}
        if self._simulation_task is None or self._simulation_task.done():
            self._simulation_task = asyncio.create_task(self._simulation_loop())
            self._simulation_task.add_done_callback(_log_simulation_task_done)
        return {"devices_seeded": devices, "routines_seeded": routines}

    async def shutdown(self) -> None:
        task = self._simulation_task
        self._simulation_task = None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def seed_if_empty(self) -> int:
        with Session(self.engine) as session:
            if session.scalar(select(SmartHomeDevice.id).limit(1)) is not None:
                return 0
            session.add_all(SmartHomeDevice(**row) for row in _seed_rows())
            session.commit()
        return len(_seed_rows())

    def seed_routines_if_empty(self) -> int:
        definitions = [
            SmartHomeRoutine(id="morning", name="Morning", aliases={"hi": ["सुबह"], "kn": ["ಬೆಳಗ್ಗೆ"]}, actions=[
                {"device_id": "curtain.bedroom", "action": "position", "value": 100},
                {"device_id": "light.kitchen_main", "action": "power", "value": True},
            ]),
            SmartHomeRoutine(id="night", name="Night", aliases={"hi": ["रात"], "kn": ["ರಾತ್ರಿ"]}, actions=[
                {"device_id": "light.living_main", "action": "power", "value": False},
                {"device_id": "light.kitchen_main", "action": "power", "value": False},
                {"device_id": "curtain.bedroom", "action": "position", "value": 0},
            ]),
            SmartHomeRoutine(id="study", name="Study", aliases={"hi": ["पढ़ाई"], "kn": ["ಅಧ್ಯಯನ"]}, actions=[
                {"device_id": "light.study_desk", "action": "brightness", "value": 85},
                {"device_id": "fan.study", "action": "speed", "value": 2},
            ]),
            SmartHomeRoutine(id="away", name="Away", aliases={"hi": ["बाहर"], "kn": ["ಹೊರಗೆ"]}, actions=[
                {"device_id": "light.living_main", "action": "power", "value": False},
                {"device_id": "fan.living_ceiling", "action": "power", "value": False},
                {"device_id": "ac.living", "action": "power", "value": False},
                {"device_id": "tv.living", "action": "power", "value": False},
            ]),
        ]
        with Session(self.engine) as session:
            if session.scalar(select(SmartHomeRoutine.id).limit(1)) is not None:
                return 0
            session.add_all(definitions)
            session.commit()
        return len(definitions)

    def list_devices(self, room: str | None = None) -> list[SmartHomeDevice]:
        with Session(self.engine) as session:
            stmt = select(SmartHomeDevice).order_by(SmartHomeDevice.room, SmartHomeDevice.id)
            if room:
                stmt = stmt.where(SmartHomeDevice.room == room)
            rows = list(session.scalars(stmt).all())
            for row in rows:
                session.expunge(row)
            return rows

    def get_device(self, device_id: str) -> SmartHomeDevice | None:
        with Session(self.engine) as session:
            row = session.get(SmartHomeDevice, device_id)
            if row:
                session.expunge(row)
            return row

    async def execute_slots(
        self,
        slots: dict[str, Any],
        *,
        source: str,
        language: LanguageCode,
    ) -> VerifiedActionResult:
        if slots.get("action") == "routine" and slots.get("routine_id"):
            routine_id = str(slots["routine_id"])
            try:
                res = await self.run_routine(routine_id, source=source)
            except (LookupError, RuntimeError) as exc:
                return VerifiedActionResult(
                    success=False,
                    action="RoutineControl",
                    status="failed",
                    message=str(exc),
                    error=str(exc),
                )
            r_label = _ROUTINE_LABELS.get(routine_id, {}).get(language, routine_id)
            msg = {
                "en": f"Activated the {r_label} routine.",
                "hi": f"{r_label} रूटीन शुरू कर दिया गया है।",
                "kn": f"{r_label} ದಿನಚರಿಯನ್ನು ಪ್ರಾರಂಭಿಸಲಾಗಿದೆ.",
            }[language]
            return VerifiedActionResult(
                success=True,
                action="RoutineControl",
                status="verified",
                message=msg,
                data=res,
            )

        candidates = [
            row for row in self.list_devices(slots.get("room"))
            if row.device_type == slots.get("device_type")
        ]
        if not candidates:
            return VerifiedActionResult(
                success=False, action="DeviceControl", status="failed",
                message={"en": "I could not find that device.", "hi": "वह उपकरण नहीं मिला।", "kn": "ಆ ಸಾಧನ ಕಂಡುಬಂದಿಲ್ಲ."}[language],
                error="No matching device.",
            )
        if len(candidates) > 1:
            return VerifiedActionResult(
                success=False, action="DeviceControl", status="clarification_required",
                message={"en": "Which room or device do you mean?", "hi": "कौन सा कमरा या उपकरण?", "kn": "ಯಾವ ಕೊಠಡಿ ಅಥವಾ ಸಾಧನ?"}[language],
                data={"matches": [row.id for row in candidates]},
            )
        row = candidates[0]
        action = str(slots["action"])
        if action == "status":
            return VerifiedActionResult(
                success=True,
                action="DeviceStatus",
                status="verified",
                message=_format_device_status(row, language),
                data={"device": self.to_dict(row)},
            )
        try:
            changed = await self.command(row.id, action, slots.get("value"), source=source)
        except (RuntimeError, ValueError) as exc:
            return VerifiedActionResult(
                success=False, action="DeviceControl", status="failed",
                message=str(exc), error=str(exc),
            )
        label = _localized_device_name(changed, language)
        return VerifiedActionResult(
            success=True, action="DeviceControl", status="verified",
            message={
                "en": f"Updated {label}.",
                "hi": f"{label} अपडेट हो गया।",
                "kn": f"{label} ನವೀಕರಿಸಲಾಗಿದೆ.",
            }[language],
            data={"device": self.to_dict(changed)},
        )

    def list_history(self, device_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            stmt = select(SmartHomeStateHistory).order_by(SmartHomeStateHistory.id.desc()).limit(min(limit, 500))
            if device_id:
                stmt = stmt.where(SmartHomeStateHistory.device_id == device_id)
            rows = list(session.scalars(stmt).all())
            return [{
                "id": row.id, "device_id": row.device_id, "action": row.action,
                "old_state": row.old_state, "new_state": row.new_state,
                "context_id": row.context_id, "context_source": row.context_source,
                "recorded_at_utc": row.recorded_at_utc.isoformat(),
            } for row in rows]

    def list_routines(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = list(session.scalars(select(SmartHomeRoutine).order_by(SmartHomeRoutine.id)).all())
            return [{"id": r.id, "name": r.name, "aliases": r.aliases, "enabled": r.enabled, "actions": r.actions, "last_run_at_utc": r.last_run_at_utc.isoformat() if r.last_run_at_utc else None} for r in rows]

    async def run_routine(self, routine_id: str, *, source: str = "routine") -> dict[str, Any]:
        with Session(self.engine) as session:
            routine = session.get(SmartHomeRoutine, routine_id)
            if routine is None:
                raise LookupError("Routine not found.")
            if not routine.enabled:
                raise RuntimeError("Routine is disabled.")
            actions = list(routine.actions)
        context_id = str(uuid.uuid4())
        results = []
        for item in actions:
            try:
                row = await self.command(item["device_id"], item["action"], item["value"], source=source, context_id=context_id)
                results.append({"device_id": row.id, "status": "verified"})
            except (LookupError, RuntimeError, ValueError) as exc:
                results.append({"device_id": item.get("device_id"), "status": "failed", "error": str(exc)})
        with Session(self.engine) as session:
            routine = session.get(SmartHomeRoutine, routine_id)
            if routine:
                routine.last_run_at_utc = datetime.now(UTC)
                session.commit()
        await self.ws_hub.broadcast("routine_completed", {"routine_id": routine_id, "context_id": context_id, "results": results})
        return {"routine_id": routine_id, "context_id": context_id, "status": "verified" if all(r["status"] == "verified" for r in results) else "partial", "results": results}

    def energy_summary(self) -> dict[str, Any]:
        devices = [self.to_dict(row) for row in self.list_devices()]
        by_room: dict[str, float] = {}
        for device in devices:
            by_room[device["room"]] = by_room.get(device["room"], 0.0) + device["estimated_power_w"]
        return {"source_kind": "estimated", "is_simulated": True, "total_power_w": round(sum(by_room.values()), 2), "by_room_power_w": by_room, "devices": [{"device_id": d["id"], "power_w": d["estimated_power_w"]} for d in devices]}

    async def set_availability(self, device_id: str, available: bool) -> SmartHomeDevice:
        with Session(self.engine) as session:
            row = session.get(SmartHomeDevice, device_id)
            if row is None:
                raise LookupError("Device not found.")
            row.available = available
            row.updated_at_utc = datetime.now(UTC)
            session.commit(); session.refresh(row); session.expunge(row)
        await self.ws_hub.broadcast("device_availability_changed", {"device_id": device_id, "available": available})
        return row

    async def complete_activity(self, device_id: str) -> SmartHomeDevice:
        row = self.get_device(device_id)
        if row is None:
            raise LookupError("Device not found.")
        if row.device_type not in {"washing_machine", "robot_vacuum"}:
            raise ValueError("Device has no completable activity.")
        with Session(self.engine) as session:
            target = session.get(SmartHomeDevice, device_id)
            assert target is not None
            old_state = copy.deepcopy(target.state)
            state = copy.deepcopy(target.state)
            state["activity"] = "completed" if target.device_type == "washing_machine" else "docked"
            state["remaining_seconds"] = 0
            target.state = state; target.updated_at_utc = datetime.now(UTC)
            session.add(SmartHomeStateHistory(device_id=device_id, action="complete", old_state=old_state, new_state=state, context_id=str(uuid.uuid4()), context_source="simulator"))
            session.commit(); session.refresh(target); session.expunge(target)
        await self.ws_hub.broadcast("activity_completed", {"device": self.to_dict(target)})
        return target

    async def inject_sensor(self, device_id: str, state: dict[str, float]) -> SmartHomeDevice:
        row = self.get_device(device_id)
        if row is None:
            raise LookupError("Device not found.")
        if row.device_type != "climate_sensor":
            raise ValueError("Only simulated sensors accept injected values.")
        allowed = {"temperature", "humidity"}
        if not state or set(state) - allowed or not all(isinstance(v, (int, float)) for v in state.values()):
            raise ValueError("Sensor state accepts numeric temperature and humidity only.")
        with Session(self.engine) as session:
            target = session.get(SmartHomeDevice, device_id); assert target is not None
            old_state = copy.deepcopy(target.state); new_state = {**old_state, **state}
            target.state = new_state; target.updated_at_utc = datetime.now(UTC)
            session.add(SmartHomeStateHistory(device_id=device_id, action="inject_sensor", old_state=old_state, new_state=new_state, context_id=str(uuid.uuid4()), context_source="simulator"))
            session.commit(); session.refresh(target); session.expunge(target)
        await self.ws_hub.broadcast("simulated_sensor_changed", {"device": self.to_dict(target), "is_simulated": True})
        return target

    async def _simulation_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            try:
                completed: list[str] = []
                with Session(self.engine) as session:
                    rows = list(session.scalars(select(SmartHomeDevice).where(SmartHomeDevice.device_type.in_(("washing_machine", "robot_vacuum")))).all())
                    for row in rows:
                        state = copy.deepcopy(row.state)
                        if state.get("activity") not in {"running", "cleaning"}:
                            continue
                        state["remaining_seconds"] = max(0, int(state.get("remaining_seconds", 0)) - 1)
                        if state["remaining_seconds"] == 0:
                            state["activity"] = "completed" if row.device_type == "washing_machine" else "docked"
                            completed.append(row.id)
                        row.state = state; row.updated_at_utc = datetime.now(UTC)
                    session.commit()
                for device_id in completed:
                    row = self.get_device(device_id)
                    if row:
                        await self.ws_hub.broadcast("activity_completed", {"device": self.to_dict(row)})
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("SmartHome simulation loop error")

    async def command(self, device_id: str, action: str, value: Any, *, source: str, context_id: str | None = None) -> SmartHomeDevice:
        context_id = context_id or str(uuid.uuid4())
        now = datetime.now(UTC)
        with Session(self.engine) as session:
            row = session.get(SmartHomeDevice, device_id)
            if row is None:
                raise LookupError("Device not found.")
            if not row.available:
                raise RuntimeError("Device is unavailable.")
            old_state = copy.deepcopy(row.state)
            state = copy.deepcopy(row.state)
            capabilities = row.capabilities
            if action not in capabilities:
                raise ValueError(f"{row.device_type} does not support {action}.")
            spec = capabilities[action]
            if "enum" in spec:
                if value not in spec["enum"]:
                    raise ValueError(f"{action} must be one of {spec['enum']}.")
            elif spec.get("type") == "bool":
                if not isinstance(value, bool):
                    raise ValueError(f"{action} must be true or false.")
            elif "min" in spec:
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ValueError(f"{action} must be numeric.")
                if not spec["min"] <= value <= spec["max"]:
                    raise ValueError(f"{action} must be in {spec['min']}..{spec['max']}.")
            if action == "activity":
                self._apply_activity(row.device_type, state, str(value))
            else:
                state[action] = value
                if action in {"brightness", "speed", "temperature", "volume"} and "power" in state:
                    state["power"] = True
            row.state = state
            row.updated_at_utc = now
            session.add(SmartHomeStateHistory(
                device_id=row.id, action=action, old_state=old_state, new_state=state,
                context_id=context_id, context_source=source, recorded_at_utc=now,
            ))
            session.commit()
            session.refresh(row)
            session.expunge(row)
        await self.ws_hub.broadcast("device_state_changed", {"device": self.to_dict(row), "context_id": context_id, "source": source})
        return row

    @staticmethod
    def _apply_activity(kind: str, state: dict[str, Any], action: str) -> None:
        current = state.get("activity")
        if action == "start":
            if current not in {"idle", "completed", "docked"}:
                raise ValueError(f"Cannot start while activity is {current}.")
            state["activity"] = "running" if kind == "washing_machine" else "cleaning"
            state["remaining_seconds"] = 3600 if kind == "washing_machine" else 1800
        elif action == "pause":
            if current not in {"running", "cleaning"}:
                raise ValueError(f"Cannot pause while activity is {current}.")
            state["activity"] = "paused"
        elif action == "resume":
            if current != "paused":
                raise ValueError(f"Cannot resume while activity is {current}.")
            state["activity"] = "running" if kind == "washing_machine" else "cleaning"
        elif action in {"cancel", "dock"}:
            state["activity"] = "idle" if kind == "washing_machine" else "docked"
            state["remaining_seconds"] = 0

    @staticmethod
    def to_dict(row: SmartHomeDevice) -> dict[str, Any]:
        state = dict(row.state)
        power_on = state.get("power", state.get("activity") in {"running", "cleaning"})
        return {
            "id": row.id, "room": row.room, "device_type": row.device_type, "name": row.name,
            "aliases": row.aliases, "capabilities": row.capabilities, "state": state,
            "available": row.available, "source": row.source, "is_essential": row.is_essential,
            "power_rating_w": row.power_rating_w,
            "estimated_power_w": row.power_rating_w if power_on and row.power_rating_w else 0.0,
            "updated_at_utc": row.updated_at_utc.isoformat(),
        }
