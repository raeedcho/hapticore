"""Field-visual helpers and stimulus ID constants.

CartPendulumVisuals and free helper functions compose generic
show_stimulus / hide_stimulus calls into field-specific visual setups.
They live in the display package (not tasks/) because the stimulus ID
constants are shared with the per-frame renderers in DisplayProcess.

Tasks import CartPendulumVisuals or the physics-body functions;
DisplayProcess imports the ID constants. The IDs are the contract
between creation and rendering.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

# ---------------------------------------------------------------------------
# Stimulus ID constants — shared between creation helpers and renderers
# ---------------------------------------------------------------------------

CART_PENDULUM_STIM_IDS: tuple[str, str] = ("__cup", "__ball")


def physics_body_stim_id(body_id: str) -> str:
    """Stimulus ID for a physics body."""
    return f"__body_{body_id}"


# ---------------------------------------------------------------------------
# Cart-pendulum visuals
# ---------------------------------------------------------------------------

_DEFAULT_CUP_COLOR: list[float] = [0.8, 0.8, 0.8]
_DEFAULT_BALL_COLOR: list[float] = [0.2, 0.6, 1.0]

def _cup_vertices(
    radius: float,
    half_angle: float,
    thickness: float = 0.003,
    center_offset: list[float] | None = None,
    n_points: int = 40,
) -> list[list[float]]:
    """Generate vertices for a downward-opening arc.

    Vertices are relative to the arc center (the cart/pivot).
    The arc spans from -half_angle to +half_angle, measured
    from the downward vertical.
    """
    if center_offset is None:
        center_offset = [0.0, 0.0]

    thetas = [-half_angle + (2 * half_angle) * i / (n_points - 1) for i in range(n_points)]
    vertices = (
        [
            [
                center_offset[0] + (radius+thickness) * math.sin(theta),
                center_offset[1] - (radius+thickness) * math.cos(theta)
            ]
            for theta in thetas
        ]
        + [
            [
                center_offset[0] + radius * math.sin(theta),
                center_offset[1] - radius * math.cos(theta)
            ]
            for theta in reversed(thetas)
        ]
    )

    return vertices


class CartPendulumVisuals:
    """Stateful visual helper for the cart-pendulum field.

    Owns creation, teardown, and semantic visual changes (e.g., ball
    color) for the cup and ball stimuli. Tasks construct one instance
    per trial and call named methods for visual state changes.

    The per-frame position updates remain in DisplayProcess
    (_update_cart_pendulum), which imports CART_PENDULUM_STIM_IDS
    for the stimulus ID contract.

    Args:
        display: Object with show_stimulus, hide_stimulus, and
            update_scene methods (e.g., DisplayClient, MockDisplay).
        pendulum_length: String length in meters. Default 0.3 matches
            the C++ CartPendulumField default.
        spill_threshold: Angle in radians at which the ball spills.
            Default π/2 matches C++ default.
        ball_radius: Ball radius in meters.
        cup_thickness: Thickness of the arc polygon in meters.
        cup_color: RGB color list for the cup arc.
        ball_color: RGB color list for the ball (default state).
    """

    _CUP_ID = CART_PENDULUM_STIM_IDS[0]
    _BALL_ID = CART_PENDULUM_STIM_IDS[1]

    def __init__(
        self,
        display: Any,
        *,
        pendulum_length: float = 0.3,
        spill_threshold: float = math.pi / 2,
        ball_radius: float = 0.004,
        cup_thickness: float = 0.003,
        cup_color: list[float] | None = None,
        ball_color: list[float] | None = None,
    ) -> None:
        self._display = display
        self._pendulum_length = pendulum_length
        self._spill_threshold = spill_threshold
        self._ball_radius = ball_radius
        self._cup_thickness = cup_thickness
        self._cup_color: list[float] = cup_color or list(_DEFAULT_CUP_COLOR)
        self._ball_color: list[float] = ball_color or list(_DEFAULT_BALL_COLOR)

    def show(
        self,
        *,
        cup_position: list[float] | None = None,
        initial_phi: float = 0.0,
    ) -> None:
        """Create cup and ball stimuli at the given pose.

        All positions in meters. Ball position computed from cup_position
        + pendulum geometry, matching C++ CartPendulumField::pack_state().
        """
        cup_pos = cup_position if cup_position is not None else [0.0, 0.0]
        cup_x, cup_y = cup_pos[0], cup_pos[1]

        ball_x = cup_x + self._pendulum_length * math.sin(initial_phi)
        ball_y = cup_y + self._pendulum_length * (1 - math.cos(initial_phi))

        self._display.show_stimulus(self._CUP_ID, {
            "type": "polygon",
            "vertices": _cup_vertices(
                radius=self._pendulum_length,
                half_angle=self._spill_threshold,
                thickness=self._cup_thickness,
                center_offset=[0, self._pendulum_length - self._ball_radius],
            ),
            "color": self._cup_color,
            "fill": True,
            "position": [cup_x, cup_y],
        })
        self._display.show_stimulus(self._BALL_ID, {
            "type": "circle",
            "radius": self._ball_radius,
            "color": self._ball_color,
            "position": [ball_x, ball_y],
        })

    def hide(self) -> None:
        """Remove cup and ball stimuli."""
        for sid in CART_PENDULUM_STIM_IDS:
            self._display.hide_stimulus(sid)

    def set_ball_color(self, color: list[float]) -> None:
        """Change the ball's color (e.g., to indicate a spill)."""
        self._display.update_scene({
            self._BALL_ID: {"color": color},
        })

    def reset_ball_color(self) -> None:
        """Restore the ball to its default color."""
        self.set_ball_color(self._ball_color)


# ---------------------------------------------------------------------------
# Physics-body visuals
# ---------------------------------------------------------------------------

def create_physics_body_stimuli(
    show_stimulus: Callable[[str, dict[str, Any]], None],
    body_specs: dict[str, dict[str, Any]],
) -> None:
    """Create stimuli for physics body visuals with ``__body_`` prefix."""
    for body_id, spec in body_specs.items():
        show_stimulus(physics_body_stim_id(body_id), spec)


def hide_physics_body_stimuli(
    hide_stimulus: Callable[[str], None],
    body_ids: list[str],
) -> None:
    """Remove stimuli for the specified physics body IDs."""
    for body_id in body_ids:
        hide_stimulus(physics_body_stim_id(body_id))
