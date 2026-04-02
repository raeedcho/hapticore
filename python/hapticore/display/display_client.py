"""ZMQ-backed proxy for controlling the DisplayProcess.

Satisfies the DisplayInterface Protocol. Translates method calls into
msgpack-encoded ZMQ messages published on the ``b"display"`` topic.
"""

from __future__ import annotations

import time
from typing import Any

import msgpack

from hapticore.core.messages import TOPIC_DISPLAY
from hapticore.core.messaging import EventPublisher


class DisplayClient:
    """ZMQ-backed proxy for controlling the DisplayProcess.

    Satisfies the DisplayInterface Protocol.
    """

    def __init__(self, publisher: EventPublisher) -> None:
        self._publisher = publisher
        self._last_flip_timestamp: float | None = None

    def show_stimulus(self, stim_id: str, params: dict[str, Any]) -> None:
        """Publish a 'show' command for the given stimulus."""
        self._send({"action": "show", "stim_id": stim_id, "params": params})

    def hide_stimulus(self, stim_id: str) -> None:
        """Publish a 'hide' command for the given stimulus."""
        self._send({"action": "hide", "stim_id": stim_id})

    def clear(self) -> None:
        """Publish a 'clear' command to remove all stimuli."""
        self._send({"action": "clear"})

    def update_scene(self, scene_state: dict[str, Any]) -> None:
        """Publish an 'update_scene' command with the given state."""
        self._send({"action": "update_scene", "params": scene_state})

    def get_flip_timestamp(self) -> float | None:
        """Return the timestamp of the last display flip."""
        return self._last_flip_timestamp

    def _send(self, cmd: dict[str, Any]) -> None:
        """Stamp and publish a display command on the display topic."""
        cmd["timestamp"] = time.monotonic()
        payload: bytes = msgpack.packb(cmd, use_bin_type=True)
        self._publisher.publish(TOPIC_DISPLAY, payload)
