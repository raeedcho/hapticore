"""MockSync: in-process SyncInterface implementation for testing."""

from __future__ import annotations

from typing import Any


class MockSync:
    """Mock sync interface for Teensy hardware sync.

    Logs command and state-changing method calls to ``_call_log`` for test
    assertions. Tracks the running state of the sync pulse and the camera
    trigger independently.
    """

    def __init__(self) -> None:
        self._sync_running = False
        self._camera_trigger_running = False
        self._camera_trigger_rate_hz: float | None = None
        self._event_codes: list[int] = []
        self._reward_durations_ms: list[int] = []
        self._call_log: list[tuple[str, Any]] = []

    def send_event_code(self, code: int) -> None:
        """Log an event code."""
        self._event_codes.append(code)
        self._call_log.append(("send_event_code", code))

    def start_sync_pulses(self) -> None:
        """Start generating sync pulses."""
        self._sync_running = True
        self._call_log.append(("start_sync_pulses", None))

    def stop_sync_pulses(self) -> None:
        """Stop generating sync pulses."""
        self._sync_running = False
        self._call_log.append(("stop_sync_pulses", None))

    def is_sync_running(self) -> bool:
        """Return whether sync pulses are being generated."""
        return self._sync_running

    def set_camera_trigger_rate(self, rate_hz: float) -> None:
        """Record the requested camera trigger rate."""
        self._camera_trigger_rate_hz = rate_hz
        self._call_log.append(("set_camera_trigger_rate", rate_hz))

    def start_camera_trigger(self) -> None:
        """Start the camera frame trigger."""
        self._camera_trigger_running = True
        self._call_log.append(("start_camera_trigger", None))

    def stop_camera_trigger(self) -> None:
        """Stop the camera frame trigger."""
        self._camera_trigger_running = False
        self._call_log.append(("stop_camera_trigger", None))

    def is_camera_trigger_running(self) -> bool:
        """Return whether the camera frame trigger is active."""
        return self._camera_trigger_running

    def deliver_reward(self, duration_ms: int) -> None:
        """Log a reward delivery request."""
        self._reward_durations_ms.append(duration_ms)
        self._call_log.append(("deliver_reward", duration_ms))
