"""Mouse-driven haptic interface for interactive simulation.

Satisfies the HapticInterface Protocol using mouse cursor position as
the position source.  Forces are accepted and silently discarded.

The mouse position is read by the DisplayProcess (which owns the
PsychoPy window) and pushed into a multiprocessing.Queue.  This module
never imports PsychoPy.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from multiprocessing import Queue
from queue import Empty

from hapticore.core.messages import (
    Command,
    CommandResponse,
    HapticState,
    make_haptic_state,
)


class MouseHapticInterface:
    """HapticInterface mock driven by mouse cursor position.

    Satisfies the HapticInterface Protocol.  Position is sourced from a
    multiprocessing.Queue populated by the DisplayProcess each frame.
    Forces are accepted and silently discarded.

    Args:
        mouse_queue: Queue of ``(x_m, y_m)`` tuples in haptic workspace meters.
    """

    def __init__(
        self,
        mouse_queue: Queue[tuple[float, float]],
    ) -> None:
        self._queue = mouse_queue
        self._position: list[float] = [0.0, 0.0, 0.0]
        self._velocity: list[float] = [0.0, 0.0, 0.0]
        self._prev_time: float = time.monotonic()
        self._sequence: int = 0
        self._callback: Callable[[HapticState], None] | None = None

    def get_latest_state(self) -> HapticState | None:
        """Return the latest mouse-derived haptic state."""
        now = time.monotonic()
        dt = max(now - self._prev_time, 1e-6)

        # Drain queue, keep only the latest reading
        latest: tuple[float, float] | None = None
        while True:
            try:
                latest = self._queue.get_nowait()
            except Empty:
                break

        if latest is not None:
            x, y = latest
            new_pos = [x, y, 0.0]
            self._velocity = [
                (new_pos[i] - self._position[i]) / dt for i in range(3)
            ]
            self._position = new_pos
        else:
            self._velocity = [0.0, 0.0, 0.0]

        self._prev_time = now
        self._sequence += 1

        state = make_haptic_state(
            position=list(self._position),
            velocity=list(self._velocity),
            active_field="null",
            sequence=self._sequence,
        )

        if self._callback is not None:
            self._callback(state)

        return state

    def send_command(self, cmd: Command) -> CommandResponse:
        """Accept commands; return success with no side effects."""
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
