"""Scene manager for tracking and drawing visible stimuli.

Manages the set of active stimuli, their draw order, and coordinates
per-frame updates driven by display commands and haptic state messages.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

from hapticore.core.config import DisplayConfig
from hapticore.display._field_visuals import CART_PENDULUM_STIM_IDS, physics_body_stim_id
from hapticore.display.stimulus_factory import create_stimulus, update_stimulus

if TYPE_CHECKING:
    from psychopy.visual import BaseVisualStim, Window

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fixed meters → cm conversion — property of the PsychoPy backend (units="cm").
# ---------------------------------------------------------------------------
_METERS_TO_CM: float = 100.0

# Spatial parameter key categories for _convert_spatial_params().
_SPATIAL_POSITION_KEYS = frozenset({"position", "start", "end"})
_SPATIAL_DIMENSION_KEYS = frozenset({
    "radius", "width", "height", "size", "field_size", "dot_size",
})
_SPATIAL_VERTEX_KEYS = frozenset({"vertices"})


class SceneManager:
    """Tracks all active stimuli by ID and controls draw order.

    All spatial values accepted by public methods (``show``, ``update``,
    ``set_cursor_position``) must be in meters (SI). The manager applies
    ``display_scale × 100`` plus ``display_offset`` to convert to cm
    before passing values to PsychoPy.

    Parameters
    ----------
    win : Window
        PsychoPy Window to draw into.
    display_config : DisplayConfig
        Display configuration (cursor, scale, offset).
    """

    def __init__(
        self, win: Window, display_config: DisplayConfig,
    ) -> None:
        self._win = win
        self._display_config = display_config
        self._stimuli: dict[str, BaseVisualStim] = {}
        self._draw_order: list[str] = []
        self._cursor_stim: BaseVisualStim | None = None
        self._cursor_hidden = False

    # ------------------------------------------------------------------
    # Spatial conversion helpers
    # ------------------------------------------------------------------

    def _effective_scale(self) -> float:
        """Combined workspace scale × meters-to-cm conversion factor."""
        return self._display_config.display_scale * _METERS_TO_CM

    def _effective_offset_cm(self) -> list[float]:
        """Display offset (meters in config) converted to cm."""
        s = self._effective_scale()
        return [o * s for o in self._display_config.display_offset]

    def _convert_spatial_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Convert spatial parameters from meters to display cm.

        Position-like keys get scale + offset; dimension-like keys get
        scale only; vertex lists get per-vertex position conversion.
        Non-spatial keys pass through unchanged.
        """
        eff = self._effective_scale()
        offset = self._effective_offset_cm()
        out: dict[str, Any] = {}
        for k, v in params.items():
            if k in _SPATIAL_POSITION_KEYS:
                out[k] = [v[0] * eff + offset[0], v[1] * eff + offset[1]]
            elif k in _SPATIAL_DIMENSION_KEYS:
                out[k] = v * eff if isinstance(v, (int, float)) else [c * eff for c in v]
            elif k in _SPATIAL_VERTEX_KEYS:
                out[k] = [
                    [vx * eff, vy * eff]
                    for vx, vy in v
                ]
            else:
                out[k] = v
        return out

    @property
    def effective_scale(self) -> float:
        """Combined workspace scale × meters-to-cm factor (read-only)."""
        return self._effective_scale()

    @property
    def effective_offset_cm(self) -> list[float]:
        """Display offset in cm (read-only)."""
        return self._effective_offset_cm()

    def show(self, stim_id: str, params: dict[str, Any]) -> None:
        """Create or replace a stimulus.

        Parameters
        ----------
        stim_id : str
            Unique identifier for the stimulus.
        params : dict
            Must contain a ``"type"`` key. Remaining keys are passed to
            :func:`~hapticore.display.stimulus_factory.create_stimulus`
            after spatial conversion from meters to cm.

        Raises
        ------
        ValueError
            If *params* does not contain a ``"type"`` key.
        """
        if "type" not in params:
            raise ValueError("show() params must contain a 'type' key")

        # Replace existing stimulus if present
        if stim_id in self._stimuli:
            self.hide(stim_id)

        stim_type = params["type"]
        # Pass all params except 'type' to the factory, converted to cm
        create_params = {k: v for k, v in params.items() if k != "type"}
        create_params = self._convert_spatial_params(create_params)
        stim = create_stimulus(self._win, stim_type, create_params)
        self._stimuli[stim_id] = stim
        self._draw_order.append(stim_id)

    def has_stimulus(self, stim_id: str) -> bool:
        """Check whether a stimulus with the given ID is currently active."""
        return stim_id in self._stimuli

    def get_stimulus(self, stim_id: str) -> BaseVisualStim | None:
        """Return the raw PsychoPy stimulus object, or None if not active.

        Use sparingly — prefer show/hide/update for normal operations.
        Needed for properties like Line.start/Line.end that aren't
        covered by update_stimulus().
        """
        return self._stimuli.get(stim_id)

    def hide(self, stim_id: str) -> None:
        """Remove a stimulus. No-op if *stim_id* doesn't exist."""
        if stim_id not in self._stimuli:
            return
        del self._stimuli[stim_id]
        self._draw_order.remove(stim_id)

    def clear(self) -> None:
        """Remove all stimuli including cursor."""
        self._stimuli.clear()
        self._draw_order.clear()
        self._cursor_stim = None

    def update(self, stim_id: str, params: dict[str, Any]) -> None:
        """Update existing stimulus properties.

        Spatial parameters are converted from meters to cm before
        being passed to the underlying stimulus.

        Logs a warning and skips if *stim_id* is not active.
        """
        stim = self._stimuli.get(stim_id)
        if stim is None:
            logger.warning("update() called for unknown stim_id %r — skipping", stim_id)
            return
        update_stimulus(stim, self._convert_spatial_params(params))

    def set_cursor_position(self, position_m: list[float]) -> None:
        """Set cursor position from meters-space coordinates.

        Creates the cursor stimulus on first call. Respects
        ``DisplayConfig.cursor_visible`` — if False, no cursor is created.
        """
        if not self._display_config.cursor_visible:
            return

        eff = self._effective_scale()
        offset = self._effective_offset_cm()
        pos_cm = [
            position_m[0] * eff + offset[0],
            position_m[1] * eff + offset[1],
        ]

        if self._cursor_stim is None:
            self._cursor_stim = create_stimulus(
                self._win,
                "circle",
                {
                    "radius": self._display_config.cursor_radius * eff,
                    "color": self._display_config.cursor_color,
                    "position": pos_cm,
                },
            )
        else:
            self._cursor_stim.pos = pos_cm

    def set_cursor_visible(self, visible: bool) -> None:
        """Toggle cursor visibility at runtime."""
        self._cursor_hidden = not visible

    def draw_all(self) -> None:
        """Draw all stimuli in insertion order, cursor last (if visible)."""
        for stim_id in self._draw_order:
            self._stimuli[stim_id].draw()
        if self._cursor_stim is not None and not self._cursor_hidden:
            self._cursor_stim.draw()

    @property
    def active_stimuli(self) -> dict[str, str]:
        """Return ``{stim_id: class_name}`` for debugging."""
        return {
            sid: type(stim).__name__
            for sid, stim in self._stimuli.items()
        }

    # ------------------------------------------------------------------
    # Field-state rendering
    # ------------------------------------------------------------------

    def update_from_field_state(self, state: dict[str, Any]) -> None:
        """Update scene visuals from haptic field_state data.

        Dispatches to the appropriate renderer based on the active field
        type. Positions are converted from meters to display cm internally
        via self.update().
        """
        active_field = state.get("active_field", "")
        field_state = state.get("field_state", {})

        if active_field == "cart_pendulum":
            self._update_cart_pendulum(field_state)
        elif active_field == "physics_world":
            self._update_physics_bodies(field_state)
        elif active_field == "composite":
            # Composite wraps multiple children. Scan for recognizable
            # child states and dispatch to the appropriate renderer.
            for child_state in field_state.get("children", []):
                if "cup_x" in child_state:
                    self._update_cart_pendulum(child_state)
                elif "bodies" in child_state:
                    self._update_physics_bodies(child_state)
        # Other field types (null, spring_damper, constant, workspace_limit, channel):
        # no continuous visual updates needed — task controller manages
        # discrete stimuli via show/hide commands.

    def _update_cart_pendulum(self, field_state: dict[str, Any]) -> None:
        """Update cup and ball positions from CartPendulumField state.

        Passes meters-space positions to self.update(), which handles
        the meters-to-cm conversion. Only updates stimuli that already
        exist — the task is responsible for showing and hiding them via
        CartPendulumVisuals.show() / CartPendulumVisuals.hide().

        Note: ball color is NOT changed here. The task controller calls
        CartPendulumVisuals.set_ball_color() / reset_ball_color() to manage
        the ball color on spill and reset, avoiding a race between this
        renderer reading field_state and the task controller transitioning
        state.
        """
        _CUP_ID, _BALL_ID = CART_PENDULUM_STIM_IDS

        cup_x = field_state.get("cup_x", 0.0)
        ball_x = field_state.get("ball_x", 0.0)
        ball_y = field_state.get("ball_y", 0.0)

        if self.has_stimulus(_CUP_ID):
            self.update(_CUP_ID, {"position": [cup_x, 0.0]})
        if self.has_stimulus(_BALL_ID):
            self.update(_BALL_ID, {"position": [ball_x, ball_y]})

    def _update_physics_bodies(self, field_state: dict[str, Any]) -> None:
        """Update positions and angles of physics body stimuli.

        Passes meters-space positions to self.update(), which handles
        the conversion. Orientation is in degrees (non-spatial), so
        _convert_spatial_params passes it through unchanged.
        """
        bodies = field_state.get("bodies", {})
        for body_id, body_state in bodies.items():
            stim_id = physics_body_stim_id(body_id)
            if self.has_stimulus(stim_id):
                pos = body_state.get("position", [0, 0])
                angle_rad = body_state.get("angle", 0.0)
                self.update(stim_id, {
                    "position": [pos[0], pos[1]],
                    "orientation": angle_rad * (180.0 / math.pi),
                })
