"""Field-visual creation helpers and stimulus ID constants.

These free functions compose generic show_stimulus / hide_stimulus calls
into field-specific visual setups. They live in the display package
(not tasks/) because the stimulus ID constants are shared with the
per-frame renderers in DisplayProcess.

Tasks import the creation functions; DisplayProcess imports the ID
constants. The IDs are the contract between creation and rendering.
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

def create_cart_pendulum_stimuli(
    show_stimulus: Callable[[str, dict[str, Any]], None],
    *,
    cup_color: list[float] | None = None,
    ball_color: list[float] | None = None,
    cup_thickness: float = 0.003,
    spill_threshold: float = math.pi / 2,
    ball_radius: float = 0.004,
    cup_position: list[float] | None = None,
    initial_phi: float = 0.0,
    pendulum_length: float = 0.3,
) -> None:
    """Create cup and ball stimuli for the cart-pendulum field.

    All positions are in meters (SI). The DisplayProcess converts to cm.
    Ball position is computed from cup_position + pendulum geometry,
    matching the C++ CartPendulumField::pack_state() convention.

    Note: the cart-pendulum simulation is 1D (horizontal/X only). When
    the cart_pendulum field engages, _update_cart_pendulum fixes cup_y to
    the display offset (Y=0 in workspace coordinates). A non-zero
    cup_position[1] in the preview will cause a vertical jump when the
    field takes over. For a smooth transition, always pass
    cup_position=[x, 0.0].
    """
    cup_pos = cup_position if cup_position is not None else [0.0, 0.0]
    cup_x, cup_y = cup_pos[0], cup_pos[1]

    ball_x = cup_x + pendulum_length * math.sin(initial_phi)
    ball_y = cup_y + pendulum_length * (1 - math.cos(initial_phi))

    show_stimulus("__cup", {
        "type": "polygon",
        "vertices": _cup_vertices(
            radius=pendulum_length,
            half_angle=spill_threshold,
            thickness=cup_thickness,
            center_offset=[0, pendulum_length-ball_radius],
        ),
        "color": cup_color or _DEFAULT_CUP_COLOR,
        "fill": True,
        "position": [cup_x, cup_y],
    })
    show_stimulus("__ball", {
        "type": "circle",
        "radius": ball_radius,
        "color": ball_color or _DEFAULT_BALL_COLOR,
        "position": [ball_x, ball_y],
    })

def hide_cart_pendulum_stimuli(
    hide_stimulus: Callable[[str], None],
) -> None:
    """Remove cup and ball stimuli."""
    for sid in CART_PENDULUM_STIM_IDS:
        hide_stimulus(sid)


_SPILL_COLOR: list[float] = [1.0, 0.3, 0.3]


class CartPendulumVisuals:
    """Stateful helper managing cart-pendulum stimulus lifecycle.

    Owns creation, teardown, and semantic color changes (spill indication).
    The task should create one instance per trial (or a persistent instance
    when parameters are constant across trials).

    Usage::

        visuals = CartPendulumVisuals(
            display.show_stimulus, display.hide_stimulus,
            pendulum_length=0.1, ball_radius=0.004,
        )
        visuals.create(cup_position=[lx, 0.0], initial_phi=phi)

        # On spill (before transitioning state):
        visuals.mark_spilled(cart_pendulum_state)

        # On trial end:
        visuals.hide()

    Note: the display process renderer only updates cup/ball *positions*
    from field_state (it no longer changes ball color). This class is the
    sole owner of spill color semantics, avoiding a race between the display
    process reading field_state and the task controller transitioning state.
    """

    def __init__(
        self,
        show_stimulus: Callable[[str, dict[str, Any]], None],
        hide_stimulus: Callable[[str], None],
        *,
        pendulum_length: float = 0.3,
        spill_threshold: float = math.pi / 2,
        ball_radius: float = 0.004,
        cup_thickness: float = 0.003,
        cup_color: list[float] | None = None,
        ball_color: list[float] | None = None,
        spill_color: list[float] | None = None,
    ) -> None:
        self._show = show_stimulus
        self._hide = hide_stimulus
        self._pendulum_length = pendulum_length
        self._spill_threshold = spill_threshold
        self._ball_radius = ball_radius
        self._cup_thickness = cup_thickness
        self._cup_color: list[float] = cup_color or list(_DEFAULT_CUP_COLOR)
        self._ball_color: list[float] = ball_color or list(_DEFAULT_BALL_COLOR)
        self._spill_color: list[float] = spill_color or list(_SPILL_COLOR)
        self._visible = False
        self._last_ball_x: float = 0.0
        self._last_ball_y: float = 0.0

    def create(
        self,
        cup_position: list[float] | None = None,
        initial_phi: float = 0.0,
    ) -> None:
        """Create cup and ball stimuli at the given position.

        All positions are in meters (SI). The DisplayProcess converts to cm.
        """
        cup_pos = cup_position if cup_position is not None else [0.0, 0.0]
        cup_x, cup_y = cup_pos[0], cup_pos[1]
        self._last_ball_x = cup_x + self._pendulum_length * math.sin(initial_phi)
        self._last_ball_y = cup_y + self._pendulum_length * (1 - math.cos(initial_phi))

        create_cart_pendulum_stimuli(
            self._show,
            cup_color=self._cup_color,
            ball_color=self._ball_color,
            cup_thickness=self._cup_thickness,
            spill_threshold=self._spill_threshold,
            ball_radius=self._ball_radius,
            cup_position=cup_position,
            initial_phi=initial_phi,
            pendulum_length=self._pendulum_length,
        )
        self._visible = True

    def mark_spilled(self, cart_pendulum_state: dict[str, Any]) -> None:
        """Change ball color to indicate a spill.

        Re-shows the ball stim with spill color at the ball's current position
        (read from cart_pendulum_state). Should be called by the task controller
        when spill is detected, before transitioning to the spill state.

        The display process renderer only updates ball *position* — it no longer
        sets ball color — so this call is the only mechanism that changes the
        ball color on spill.
        """
        if not self._visible:
            return
        ball_x = cart_pendulum_state.get("ball_x", self._last_ball_x)
        ball_y = cart_pendulum_state.get("ball_y", self._last_ball_y)
        self._show("__ball", {
            "type": "circle",
            "radius": self._ball_radius,
            "color": self._spill_color,
            "position": [ball_x, ball_y],
        })

    def hide(self) -> None:
        """Remove cup and ball stimuli."""
        hide_cart_pendulum_stimuli(self._hide)
        self._visible = False


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
