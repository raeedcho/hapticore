"""TeensySync — ZMQ-backed shim satisfying SyncInterface.

Published messages are consumed by ``SyncProcess`` running in its own
process. Methods that track on/off state (sync pulses, camera trigger)
publish ``SessionControl`` on ``TOPIC_SESSION``; per-call actions
(event code, reward pulse, rate setting) publish raw dicts on
``TOPIC_SYNC``. See ``DisplayClient`` for the parallel pattern on the
display side.
"""

from __future__ import annotations

import time
from typing import Any

import msgpack

from hapticore.core.messages import (
    TOPIC_SESSION,
    TOPIC_SYNC,
    SessionControl,
    serialize,
)
from hapticore.core.messaging import EventPublisher
from hapticore.sync import protocol


class TeensySync:
    """ZMQ-backed shim implementing SyncInterface.

    Does not talk to the Teensy directly. All commands are published
    over ZMQ for ``SyncProcess`` to consume. Local flags track the
    running state of sync pulses and camera trigger for the
    ``is_*_running`` query methods, since the shim has no read path
    back from the hardware.

    Range validation on event codes, reward durations, and camera rates
    happens here via the ``protocol`` module so that invalid values
    fail at the call site with a clear ``ValueError`` rather than
    being silently dropped downstream.
    """

    def __init__(self, publisher: EventPublisher) -> None:
        self._publisher = publisher
        self._sync_running = False
        self._camera_trigger_running = False

    # ---- Event codes ------------------------------------------------------

    def send_event_code(self, code: int) -> None:
        protocol.format_event_code(code)  # validates; raises ValueError if out of range
        self._publish_sync({"action": "send_event_code", "code": code})

    # ---- Sync pulses ------------------------------------------------------

    def start_sync_pulses(self) -> None:
        self._publish_session("start_sync")
        self._sync_running = True

    def stop_sync_pulses(self) -> None:
        self._publish_session("stop_sync")
        self._sync_running = False

    def is_sync_running(self) -> bool:
        return self._sync_running

    # ---- Camera trigger ---------------------------------------------------

    def set_camera_trigger_rate(self, rate_hz: float) -> None:
        protocol.format_set_camera_rate(rate_hz)  # validates
        self._publish_sync(
            {"action": "set_camera_trigger_rate", "rate_hz": rate_hz},
        )

    def start_camera_trigger(self) -> None:
        self._publish_session("start_camera_trigger")
        self._camera_trigger_running = True

    def stop_camera_trigger(self) -> None:
        self._publish_session("stop_camera_trigger")
        self._camera_trigger_running = False

    def is_camera_trigger_running(self) -> bool:
        return self._camera_trigger_running

    # ---- Reward -----------------------------------------------------------

    def deliver_reward(self, duration_ms: int) -> None:
        protocol.format_reward_ms(duration_ms)  # validates
        self._publish_sync(
            {"action": "deliver_reward", "duration_ms": duration_ms},
        )

    # ---- Helpers ----------------------------------------------------------

    def _publish_sync(self, cmd: dict[str, Any]) -> None:
        """Publish a per-call command on TOPIC_SYNC."""
        cmd["timestamp"] = time.monotonic()
        self._publisher.publish(TOPIC_SYNC, msgpack.packb(cmd, use_bin_type=True))

    def _publish_session(self, action: str) -> None:
        """Publish a session-level start/stop on TOPIC_SESSION."""
        msg = SessionControl(timestamp=time.monotonic(), action=action, params={})
        self._publisher.publish(TOPIC_SESSION, serialize(msg))
