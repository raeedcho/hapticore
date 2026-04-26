"""MockHapticInterface: in-process HapticInterface implementation for testing."""

from __future__ import annotations

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
