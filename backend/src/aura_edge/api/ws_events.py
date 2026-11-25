"""WebSocket live event hub broadcasting assistant state, audio levels, and timer events."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WebSocketEventHub:
    """Manages active WebSocket subscribers and thread-safe event broadcasts."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def broadcast(self, event_type: str, payload: dict[str, Any]) -> None:
        message = {
            "type": event_type,
            "payload": payload,
            "timestamp_utc": datetime.now(UTC).isoformat(),
        }
        async with self._lock:
            targets = list(self._connections)

        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(message)
            except (RuntimeError, OSError, WebSocketDisconnect):
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)


ws_router = APIRouter(prefix="/api/v1", tags=["events"])


@ws_router.websocket("/ws/live")
async def live_events_ws(websocket: WebSocket) -> None:
    hub: WebSocketEventHub = websocket.app.state.ws_hub
    orchestrator = websocket.app.state.orchestrator
    await hub.connect(websocket)
    try:
        # Send immediate initial state snapshot on connect
        await websocket.send_json(
            {
                "type": "assistant_state_change",
                "payload": {
                    "state": orchestrator.fsm.state.value,
                    "turn_id": orchestrator.fsm.active_turn.turn_id
                    if orchestrator.fsm.active_turn
                    else None,
                    "language": orchestrator.session_language,
                    "recognition_mode": getattr(orchestrator, "stt_recognition_mode", "multilingual"),
                },
                "timestamp_utc": datetime.now(UTC).isoformat(),
            }
        )
        while True:
            # Keep socket alive; ignore client pings/text gracefully
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except (RuntimeError, OSError) as exc:
        logger.debug("WebSocket connection closed: %s", exc)
    finally:
        await hub.disconnect(websocket)
