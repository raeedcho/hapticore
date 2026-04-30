"""MockDisplay: in-process DisplayInterface implementation for testing."""

from __future__ import annotations

import time
from typing import Any


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
