"""Photodiode patch for frame-accurate timing verification.

Renders a small high-contrast square in a configurable screen corner
that toggles between black and white on stimulus onset, allowing an
external photodiode sensor to measure true display latency.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from psychopy.visual import Window

logger = logging.getLogger(__name__)

_VALID_CORNERS = {"bottom_left", "bottom_right", "top_left", "top_right"}

_CORNER_POSITIONS: dict[str, list[float]] = {
    "bottom_left": [-1.0 + 0.03 + 0.025, -1.0 + 0.03 + 0.025],
    "bottom_right": [1.0 - 0.03 - 0.025, -1.0 + 0.03 + 0.025],
    "top_left": [-1.0 + 0.03 + 0.025, 1.0 - 0.03 - 0.025],
    "top_right": [1.0 - 0.03 - 0.025, 1.0 - 0.03 - 0.025],
}

_BLACK: list[float] = [-1.0, -1.0, -1.0]
_WHITE: list[float] = [1.0, 1.0, 1.0]


class PhotodiodePatch:
    """High-contrast square that toggles on stimulus onset for timing verification.

    Parameters
    ----------
    win : Window
        PsychoPy Window to draw into.
    corner : str
        Screen corner: ``'bottom_left'``, ``'bottom_right'``,
        ``'top_left'``, or ``'top_right'``.
    enabled : bool
        If ``False``, :meth:`trigger` and :meth:`draw` are no-ops.
    """

    PATCH_SIZE_NORM: float = 0.05  # in norm units

    def __init__(self, win: Window, corner: str = "bottom_left", *, enabled: bool = True) -> None:
        if corner not in _VALID_CORNERS:
            raise ValueError(
                f"Invalid corner {corner!r}. Must be one of {sorted(_VALID_CORNERS)}"
            )
        self._enabled = enabled
        self._is_white = False
        self._rect: Any = None

        if self._enabled:
            from psychopy import visual  # import only inside display process

            pos = _CORNER_POSITIONS[corner]
            self._rect = visual.Rect(
                win,
                width=self.PATCH_SIZE_NORM,
                height=self.PATCH_SIZE_NORM,
                pos=pos,
                fillColor=_BLACK,
                lineColor=_BLACK,
                units="norm",
            )

    @property
    def is_white(self) -> bool:
        """Current state of the patch (``True`` = white, ``False`` = black)."""
        return self._is_white

    def trigger(self) -> None:
        """Toggle between black and white. Call on stimulus onset."""
        if not self._enabled:
            return
        self._is_white = not self._is_white
        color = _WHITE if self._is_white else _BLACK
        self._rect.fillColor = color
        self._rect.lineColor = color

    def draw(self) -> None:
        """Draw the patch after all other stimuli. No-op if disabled."""
        if not self._enabled:
            return
        self._rect.draw()
