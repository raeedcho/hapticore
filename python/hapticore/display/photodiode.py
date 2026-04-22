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

_PATCH_SIZE_NORM: float = 0.05  # single source of truth for patch size
_INSET_NORM: float = 0.03  # offset from screen edge in norm units


def _compute_corner_positions(
    size: float, inset: float,
) -> dict[str, list[float]]:
    """Compute patch center positions for each corner."""
    half = size / 2
    return {
        "bottom_left": [-1.0 + inset + half, -1.0 + inset + half],
        "bottom_right": [1.0 - inset - half, -1.0 + inset + half],
        "top_left": [-1.0 + inset + half, 1.0 - inset - half],
        "top_right": [1.0 - inset - half, 1.0 - inset - half],
    }


_CORNER_POSITIONS = _compute_corner_positions(_PATCH_SIZE_NORM, _INSET_NORM)

_BLACK: list[float] = [-1.0, -1.0, -1.0]
_WHITE: list[float] = [1.0, 1.0, 1.0]


def remap_corner_for_mirror(
    physical_corner: str,
    *,
    mirror_horizontal: bool,
    mirror_vertical: bool,
) -> str:
    """Map a physical screen corner to the render-frame corner that draws there.

    ``photodiode_corner`` in config is the physical location of the sensor.
    When the window is mirrored, the render-frame corner that reaches that
    physical location is swapped. This helper does the swap.

    Parameters
    ----------
    physical_corner : str
        Physical location of the photodiode sensor on the monitor.
        One of ``'bottom_left'``, ``'bottom_right'``, ``'top_left'``,
        ``'top_right'``.
    mirror_horizontal : bool
        Whether the rendered image is flipped left-right.
    mirror_vertical : bool
        Whether the rendered image is flipped top-bottom.

    Returns
    -------
    str
        The render-frame corner to draw the patch in so it ends up at the
        physical sensor location.
    """
    if physical_corner not in _VALID_CORNERS:
        raise ValueError(
            f"Invalid corner {physical_corner!r}. "
            f"Must be one of {sorted(_VALID_CORNERS)}"
        )
    vert, horiz = physical_corner.split("_")  # "bottom_left" -> ("bottom", "left")
    if mirror_horizontal:
        horiz = "right" if horiz == "left" else "left"
    if mirror_vertical:
        vert = "top" if vert == "bottom" else "bottom"
    return f"{vert}_{horiz}"


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

    PATCH_SIZE_NORM: float = _PATCH_SIZE_NORM  # public alias for external reference

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
