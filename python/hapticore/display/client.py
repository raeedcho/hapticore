"""ZMQ-backed proxy for controlling the DisplayProcess.

Satisfies the DisplayInterface Protocol. Translates method calls into
msgpack-encoded ZMQ messages published on the ``b"display"`` topic.
"""

from __future__ import annotations

import math
import time
from typing import Any

import msgpack

from hapticore.core.messages import TOPIC_DISPLAY
from hapticore.core.messaging import EventPublisher

# Default visual dimensions in meters (matching current display-internal cm values)
_DEFAULT_CUP_HALF_WIDTH: float = 0.015   # 1.5 cm
_DEFAULT_CUP_DEPTH: float = 0.03         # 3.0 cm
_DEFAULT_BALL_RADIUS: float = 0.008      # 0.8 cm
_DEFAULT_CUP_COLOR: list[float] = [0.8, 0.8, 0.8]
_DEFAULT_BALL_COLOR: list[float] = [0.2, 0.6, 1.0]
_DEFAULT_STRING_COLOR: list[float] = [0.5, 0.5, 0.5]


class DisplayClient:
    """ZMQ-backed proxy for controlling the DisplayProcess.

    Satisfies the DisplayInterface Protocol.
    """

    def __init__(self, publisher: EventPublisher) -> None:
        self._publisher = publisher

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
        """Return the timestamp of the last display flip.

        Raises
        ------
        NotImplementedError
            Timing feedback subscription is not yet implemented. Will be
            wired to ``display_event_address`` in a future phase.
        """
        raise NotImplementedError(
            "DisplayClient does not yet subscribe to display timing events. "
            "Use stimulus_onset events from display_event_address directly."
        )

    def show_cart_pendulum(
        self,
        *,
        cup_color: list[float] | None = None,
        ball_color: list[float] | None = None,
        string_color: list[float] | None = None,
        cup_half_width: float = _DEFAULT_CUP_HALF_WIDTH,
        cup_depth: float = _DEFAULT_CUP_DEPTH,
        ball_radius: float = _DEFAULT_BALL_RADIUS,
        cup_position: list[float] | None = None,
        initial_phi: float = 0.0,
        pendulum_length: float = 0.3,
    ) -> None:
        """Create cup, ball, and string stimuli for the cart-pendulum field."""
        cup_pos = cup_position if cup_position is not None else [0.0, 0.0]
        cup_x, cup_y = cup_pos[0], cup_pos[1]

        ball_x = cup_x + pendulum_length * math.sin(initial_phi)
        ball_y = cup_y - pendulum_length * math.cos(initial_phi)

        hw = cup_half_width
        d = cup_depth
        self.show_stimulus("__cup", {
            "type": "polygon",
            "vertices": [[-hw, 0.0], [-hw, -d], [hw, -d], [hw, 0.0]],
            "color": cup_color or _DEFAULT_CUP_COLOR,
            "fill": False,
            "position": [cup_x, cup_y],
        })
        self.show_stimulus("__ball", {
            "type": "circle",
            "radius": ball_radius,
            "color": ball_color or _DEFAULT_BALL_COLOR,
            "position": [ball_x, ball_y],
        })
        self.show_stimulus("__string", {
            "type": "line",
            "start": [cup_x, cup_y],
            "end": [ball_x, ball_y],
            "color": string_color or _DEFAULT_STRING_COLOR,
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

    def _send(self, cmd: dict[str, Any]) -> None:
        """Stamp and publish a display command on the display topic."""
        cmd["timestamp"] = time.monotonic()
        payload: bytes = msgpack.packb(cmd, use_bin_type=True)
        self._publisher.publish(TOPIC_DISPLAY, payload)
