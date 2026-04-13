"""Scene manager for tracking and drawing visible stimuli.

Manages the set of active stimuli, their draw order, and coordinates
per-frame updates driven by display commands and haptic state messages.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from hapticore.core.config import DisplayConfig
from hapticore.display.stimulus_factory import create_stimulus, update_stimulus

if TYPE_CHECKING:
    from psychopy.visual import BaseVisualStim, Window

logger = logging.getLogger(__name__)


class SceneManager:
    """Tracks all active stimuli by ID and controls draw order.

    Parameters
    ----------
    win : Window
        PsychoPy Window to draw into.
    display_config : DisplayConfig
        Display configuration (cursor radius, color, visibility).
    """

    def __init__(self, win: Window, display_config: DisplayConfig) -> None:
        self._win = win
        self._display_config = display_config
        self._stimuli: dict[str, BaseVisualStim] = {}
        self._draw_order: list[str] = []
        self._cursor_stim: BaseVisualStim | None = None

    def show(self, stim_id: str, params: dict[str, Any]) -> None:
        """Create or replace a stimulus.

        Parameters
        ----------
        stim_id : str
            Unique identifier for the stimulus.
        params : dict
            Must contain a ``"type"`` key. Remaining keys are passed to
            :func:`~hapticore.display.stimulus_factory.create_stimulus`.

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
        # Pass all params except 'type' to the factory
        create_params = {k: v for k, v in params.items() if k != "type"}
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

        Logs a warning and skips if *stim_id* is not active.
        """
        stim = self._stimuli.get(stim_id)
        if stim is None:
            logger.warning("update() called for unknown stim_id %r — skipping", stim_id)
            return
        update_stimulus(stim, params)

    def set_cursor_position(self, position: list[float]) -> None:
        """Create cursor on first call, update position on subsequent calls.

        Respects ``DisplayConfig.cursor_visible`` — if ``False``, no cursor
        is created or drawn.
        """
        if not self._display_config.cursor_visible:
            return

        if self._cursor_stim is None:
            self._cursor_stim = create_stimulus(
                self._win,
                "circle",
                {
                    "radius": self._display_config.cursor_radius,
                    "color": self._display_config.cursor_color,
                    "position": position,
                },
            )
        else:
            self._cursor_stim.pos = position

    def draw_all(self) -> None:
        """Draw all stimuli in insertion order, cursor last (if visible)."""
        for stim_id in self._draw_order:
            self._stimuli[stim_id].draw()
        if self._cursor_stim is not None:
            self._cursor_stim.draw()

    @property
    def active_stimuli(self) -> dict[str, str]:
        """Return ``{stim_id: class_name}`` for debugging."""
        return {
            sid: type(stim).__name__
            for sid, stim in self._stimuli.items()
        }
