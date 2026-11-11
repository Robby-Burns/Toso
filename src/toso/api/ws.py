"""TOSO WebSocket Hub for Sequenced Telemetry Broadcasting.

Maintains connected client sockets, sequence numbering, and replay buffers
to support seamless client reconnection and zero-state-loss synchronization.
"""

import asyncio
import json
import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket

from toso.api.models import WebSocketTelemetryMessage

logger = logging.getLogger("toso.api.ws")


class WebSocketHub:
    """Manages active WebSockets and monotonic telemetry event streaming."""

    def __init__(self, max_buffer_size: int = 1000) -> None:
        self._active_connections: set[WebSocket] = set()
        self._sequence_counter: int = 0
        self._event_buffer: deque[WebSocketTelemetryMessage] = deque(maxlen=max_buffer_size)
        self._lock = asyncio.Lock()

    @property
    def current_sequence_id(self) -> int:
        """Return the latest emitted sequence ID."""
        return self._sequence_counter

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new client connection."""
        await websocket.accept()
        async with self._lock:
            self._active_connections.add(websocket)
        logger.info("WebSocket client connected. Total active: %d", len(self._active_connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        """Unregister a disconnected client."""
        async with self._lock:
            self._active_connections.discard(websocket)
        logger.info(
            "WebSocket client disconnected. Total active: %d", len(self._active_connections)
        )

    async def broadcast(
        self, event_type: str, payload: dict[str, Any]
    ) -> WebSocketTelemetryMessage:
        """Emit a sequenced event to all active clients and append to replay buffer."""
        async with self._lock:
            self._sequence_counter += 1
            msg = WebSocketTelemetryMessage(
                sequence_id=self._sequence_counter,
                timestamp=datetime.now(UTC),
                event_type=event_type,
                payload=payload,
            )
            self._event_buffer.append(msg)
            dead_connections = set()

            data_str = json.dumps(
                {
                    "sequence_id": msg.sequence_id,
                    "timestamp": msg.timestamp.isoformat(),
                    "event_type": msg.event_type,
                    "payload": msg.payload,
                }
            )

            for conn in self._active_connections:
                try:
                    await conn.send_text(data_str)
                except Exception as e:
                    logger.warning("Failed to send to websocket: %s", e)
                    dead_connections.add(conn)

            self._active_connections.difference_update(dead_connections)
            return msg

    def get_events_since(self, since_sequence_id: int) -> list[WebSocketTelemetryMessage]:
        """Retrieve all buffered events after a given sequence ID."""
        return [msg for msg in self._event_buffer if msg.sequence_id > since_sequence_id]
