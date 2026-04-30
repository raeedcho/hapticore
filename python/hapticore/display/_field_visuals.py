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

CART_PENDULUM_STIM_IDS: tuple[str, str, str] = ("__cup", "__ball")


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
    center_offset: list[float] = [0.0, 0.0],
    n_points: int = 40,
) -> list[list[float]]:
    """Generate vertices for a downward-opening arc.
    
    Vertices are relative to the arc center (the cart/pivot).
    The arc spans from -half_angle to +half_angle, measured
    from the downward vertical.
    """
    thetas = [-half_angle + (2 * half_angle) * i / (n_points - 1) for i in range(n_points)]
    vertices = (
        [
            [center_offset[0] + (radius+thickness) * math.sin(theta), center_offset[1] - (radius+thickness) * math.cos(theta)]
            for theta in thetas
        ]
        + [
            [center_offset[0] + radius * math.sin(theta), center_offset[1] - radius * math.cos(theta)]
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
    """Create cup, ball, and string stimuli for the cart-pendulum field.

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
