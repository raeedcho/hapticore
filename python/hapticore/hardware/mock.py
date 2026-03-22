"""Mock implementations of hardware interfaces for testing and simulation.

Each mock logs all method calls for test verification and returns sensible defaults.
All mocks satisfy their corresponding Protocol from core.interfaces.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from hapticore.core.messages import (
    Command,
    CommandResponse,
    HapticState,
    make_haptic_state,
)


class MockHapticInterface:
    """Mock haptic interface that returns configurable synthetic data."""

    def __init__(self, initial_position: list[float] | None = None) -> None:
        self._position = initial_position or [0.0, 0.0, 0.0]
        self._velocity = [0.0, 0.0, 0.0]
        self._force = [0.0, 0.0, 0.0]
        self._sequence = 0
        self._callback: Callable[[HapticState], None] | None = None
        self._command_log: list[Command] = []
        self._active_field = "null"
        self._field_state: dict[str, Any] = {}

    def get_latest_state(self) -> HapticState | None:
        """Return a HapticState with the current synthetic position."""
        self._sequence += 1
        return make_haptic_state(
            position=list(self._position),
            velocity=list(self._velocity),
            force=list(self._force),
            active_field=self._active_field,
            field_state=dict(self._field_state),
            sequence=self._sequence,
        )

    def send_command(self, cmd: Command) -> CommandResponse:
        """Log the command and return success."""
        self._command_log.append(cmd)
        return CommandResponse(
            command_id=cmd.command_id,
            success=True,
            result={"method": cmd.method, "acknowledged": True},
        )

    def subscribe_state(self, callback: Callable[[HapticState], None]) -> None:
        """Store the callback for state updates."""
        self._callback = callback

    def unsubscribe_state(self) -> None:
        """Remove the state callback."""
        self._callback = None

    def set_position(self, position: list[float]) -> None:
        """Set the mock position (for scripted trajectories in tests)."""
        self._position = list(position)

    def set_velocity(self, velocity: list[float]) -> None:
        """Set the mock velocity (for scripted trajectories in tests)."""
        self._velocity = list(velocity)


class MockNeuralRecording:
    """Mock neural recording interface."""

    def __init__(self) -> None:
        self._recording = False
        self._filename: str | None = None
        self._start_time: float | None = None
        self._call_log: list[tuple[str, Any]] = []

    def start_recording(self, filename: str) -> None:
        """Start a mock recording session."""
        self._recording = True
        self._filename = filename
        self._start_time = time.monotonic()
        self._call_log.append(("start_recording", filename))

    def stop_recording(self) -> None:
        """Stop the mock recording session."""
        self._recording = False
        self._call_log.append(("stop_recording", None))

    def is_recording(self) -> bool:
        """Return whether a recording is in progress."""
        return self._recording

    def get_timestamp(self) -> float:
        """Return the elapsed time since recording started."""
        if self._start_time is None:
            return 0.0
        return time.monotonic() - self._start_time


class MockSync:
    """Mock sync interface for Teensy hardware sync."""

    def __init__(self) -> None:
        self._running = False
        self._event_codes: list[int] = []
        self._call_log: list[tuple[str, Any]] = []

    def send_event_code(self, code: int) -> None:
        """Log an event code."""
        self._event_codes.append(code)
        self._call_log.append(("send_event_code", code))

    def start_sync_pulses(self) -> None:
        """Start generating sync pulses."""
        self._running = True
        self._call_log.append(("start_sync_pulses", None))

    def stop_sync_pulses(self) -> None:
        """Stop generating sync pulses."""
        self._running = False
        self._call_log.append(("stop_sync_pulses", None))

    def is_running(self) -> bool:
        """Return whether sync pulses are being generated."""
        return self._running


class MockDisplay:
    """Mock display interface for visual stimulus rendering."""

    def __init__(self) -> None:
        self._scene_state: dict[str, Any] = {}
        self._visible_stimuli: dict[str, dict[str, Any]] = {}
        self._flip_timestamp: float | None = None
        self._call_log: list[tuple[str, Any]] = []

    def update_scene(self, scene_state: dict[str, Any]) -> None:
        """Update the scene state."""
        self._scene_state = scene_state
        self._flip_timestamp = time.monotonic()
        self._call_log.append(("update_scene", scene_state))

    def show_stimulus(self, stim_id: str, params: dict[str, Any]) -> None:
        """Show a stimulus with given parameters."""
        self._visible_stimuli[stim_id] = params
        self._flip_timestamp = time.monotonic()
        self._call_log.append(("show_stimulus", {"stim_id": stim_id, "params": params}))

    def hide_stimulus(self, stim_id: str) -> None:
        """Hide a stimulus."""
        self._visible_stimuli.pop(stim_id, None)
        self._flip_timestamp = time.monotonic()
        self._call_log.append(("hide_stimulus", stim_id))

    def clear(self) -> None:
        """Clear all stimuli."""
        self._visible_stimuli.clear()
        self._flip_timestamp = time.monotonic()
        self._call_log.append(("clear", None))

    def get_flip_timestamp(self) -> float | None:
        """Return the timestamp of the last display flip."""
        return self._flip_timestamp
