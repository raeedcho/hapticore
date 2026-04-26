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

    def show_cart_pendulum(
        self,
        *,
        cup_color: list[float] | None = None,
        ball_color: list[float] | None = None,
        string_color: list[float] | None = None,
        cup_half_width: float = 0.015,
        cup_depth: float = 0.03,
        ball_radius: float = 0.008,
    ) -> None:
        """Create cup, ball, and string stimuli for the cart-pendulum field."""
        hw = cup_half_width
        d = cup_depth
        self.show_stimulus("__cup", {
            "type": "polygon",
            "vertices": [[-hw, 0.0], [-hw, -d], [hw, -d], [hw, 0.0]],
            "color": cup_color or [0.8, 0.8, 0.8],
            "fill": False,
            "position": [0.0, 0.0],
        })
        self.show_stimulus("__ball", {
            "type": "circle",
            "radius": ball_radius,
            "color": ball_color or [0.2, 0.6, 1.0],
            "position": [0.0, 0.0],
        })
        self.show_stimulus("__string", {
            "type": "line",
            "start": [0.0, 0.0],
            "end": [0.0, 0.0],
            "color": string_color or [0.5, 0.5, 0.5],
            "line_width": 2.0,
        })

    def hide_cart_pendulum(self) -> None:
        """Remove cup, ball, and string stimuli."""
        for sid in ("__cup", "__ball", "__string"):
            self.hide_stimulus(sid)

    def show_physics_bodies(
        self, body_specs: dict[str, dict[str, Any]],
    ) -> None:
        """Create stimuli for physics body visuals with ``__body_`` prefix."""
        for body_id, spec in body_specs.items():
            self.show_stimulus(f"__body_{body_id}", spec)

    def hide_physics_bodies(self, body_ids: list[str]) -> None:
        """Remove physics body stimuli."""
        for body_id in body_ids:
            self.hide_stimulus(f"__body_{body_id}")
