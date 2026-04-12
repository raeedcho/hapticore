"""Stimulus factory for creating PsychoPy visual stimuli.

Creates PsychoPy stimulus objects from declarative parameter dicts.
All PsychoPy imports happen inside function bodies (never at module level).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from psychopy.visual import BaseVisualStim, Window

logger = logging.getLogger(__name__)

STIMULUS_TYPES: set[str] = {
    "circle",
    "rectangle",
    "line",
    "polygon",
    "text",
    "image",
    "grating",
    "dot_field",
}


def create_stimulus(win: Window, stim_type: str, params: dict[str, Any]) -> BaseVisualStim:
    """Create a PsychoPy stimulus from a type name and parameter dict.

    All stimuli use ``units="cm"`` (centimeters in lab frame).

    Raises:
        ValueError: If *stim_type* is not one of the supported types.
    """
    if stim_type not in STIMULUS_TYPES:
        raise ValueError(
            f"Unknown stimulus type {stim_type!r}. "
            f"Supported types: {sorted(STIMULUS_TYPES)}"
        )

    creators = {
        "circle": _create_circle,
        "rectangle": _create_rectangle,
        "line": _create_line,
        "polygon": _create_polygon,
        "text": _create_text,
        "image": _create_image,
        "grating": _create_grating,
        "dot_field": _create_dot_field,
    }
    return creators[stim_type](win, params)


def update_stimulus(stim: BaseVisualStim, params: dict[str, Any]) -> None:
    """Update mutable properties of an existing stimulus in-place.

    Only updates keys that are present in *params*. Silently skips
    keys that don't apply to the stimulus type.

    Supported keys:
        position : list[float]
            ``[x, y]`` in meters (lab frame).
        color : list[float]
            RGB triplet applied to ``fillColor``, ``lineColor``, and
            ``color`` attributes when present on the stimulus.
        opacity : float
            Opacity (0.0–1.0).
        orientation : float
            Orientation in degrees (skipped for stimuli without ``ori``).
    """
    if "position" in params:
        stim.pos = params["position"]

    if "color" in params:
        color = params["color"]
        if hasattr(stim, "fillColor"):
            stim.fillColor = color
        if hasattr(stim, "lineColor"):
            stim.lineColor = color
        if hasattr(stim, "color"):
            stim.color = color

    if "opacity" in params:
        stim.opacity = params["opacity"]

    if "orientation" in params and hasattr(stim, "ori"):
        stim.ori = params["orientation"]


# ---------------------------------------------------------------------------
# Private creator functions — one per stimulus type
# ---------------------------------------------------------------------------


def _create_circle(win: Window, params: dict[str, Any]) -> BaseVisualStim:
    from psychopy import visual

    pos = params.get("position", [0, 0])
    radius = params.get("radius", 0.01)
    color = params.get("color", [1, 1, 1])
    opacity = params.get("opacity", 1.0)
    fill = params.get("fill", True)
    line_width = params.get("line_width", 1.5)
    fill_color = color if fill else None
    return visual.Circle(
        win,
        radius=radius,
        pos=pos,
        fillColor=fill_color,
        lineColor=color,
        lineWidth=line_width,
        opacity=opacity,
        units="cm",
    )


def _create_rectangle(win: Window, params: dict[str, Any]) -> BaseVisualStim:
    from psychopy import visual

    pos = params.get("position", [0, 0])
    width = params.get("width", 0.02)
    height = params.get("height", 0.02)
    color = params.get("color", [1, 1, 1])
    opacity = params.get("opacity", 1.0)
    orientation = params.get("orientation", 0.0)
    return visual.Rect(
        win,
        width=width,
        height=height,
        pos=pos,
        fillColor=color,
        lineColor=color,
        ori=orientation,
        opacity=opacity,
        units="cm",
    )


def _create_line(win: Window, params: dict[str, Any]) -> BaseVisualStim:
    from psychopy import visual

    start = params.get("start", [0, 0])
    end = params.get("end", [0.1, 0])
    color = params.get("color", [1, 1, 1])
    line_width = params.get("line_width", 1.5)
    return visual.Line(
        win,
        start=start,
        end=end,
        lineColor=color,
        lineWidth=line_width,
        units="cm",
    )


def _create_polygon(win: Window, params: dict[str, Any]) -> BaseVisualStim:
    from psychopy import visual

    if "vertices" not in params:
        raise ValueError("'polygon' stimulus requires 'vertices' parameter")
    pos = params.get("position", [0, 0])
    color = params.get("color", [1, 1, 1])
    opacity = params.get("opacity", 1.0)
    fill = params.get("fill", True)
    fill_color = color if fill else None
    return visual.ShapeStim(
        win,
        vertices=params["vertices"],
        pos=pos,
        fillColor=fill_color,
        lineColor=color,
        opacity=opacity,
        units="cm",
    )


def _create_text(win: Window, params: dict[str, Any]) -> BaseVisualStim:
    from psychopy import visual

    pos = params.get("position", [0, 0])
    text = params.get("text", "")
    color = params.get("color", [1, 1, 1])
    height = params.get("height", 0.02)
    font = params.get("font", "Arial")
    return visual.TextStim(
        win,
        text=text,
        pos=pos,
        color=color,
        height=height,
        font=font,
        units="cm",
    )


def _create_image(win: Window, params: dict[str, Any]) -> BaseVisualStim:
    from psychopy import visual

    if "image_path" not in params:
        raise ValueError("'image' stimulus requires 'image_path' parameter")
    pos = params.get("position", [0, 0])
    size = params.get("size")
    return visual.ImageStim(
        win,
        image=params["image_path"],
        pos=pos,
        size=size,
        units="cm",
    )


def _create_grating(win: Window, params: dict[str, Any]) -> BaseVisualStim:
    from psychopy import visual

    pos = params.get("position", [0, 0])
    size = params.get("size")
    sf = params.get("sf")
    ori = params.get("orientation", 0.0)
    phase = params.get("phase", 0.0)
    contrast = params.get("contrast", 1.0)
    mask = params.get("mask")
    return visual.GratingStim(
        win,
        pos=pos,
        size=size,
        sf=sf,
        ori=ori,
        phase=phase,
        contrast=contrast,
        mask=mask,
        units="cm",
    )


def _create_dot_field(win: Window, params: dict[str, Any]) -> BaseVisualStim:
    from psychopy import visual

    pos = params.get("position", [0, 0])
    n_dots = params.get("n_dots", 100)
    field_size = params.get("field_size", 0.1)
    dot_size = params.get("dot_size", 0.002)
    coherence = params.get("coherence", 0.5)
    direction = params.get("direction", 0.0)
    speed = params.get("speed", 0.01)
    color = params.get("color", [1, 1, 1])
    return visual.DotStim(
        win,
        nDots=n_dots,
        fieldSize=field_size,
        dotSize=dot_size,
        coherence=coherence,
        dir=direction,
        speed=speed,
        color=color,
        fieldPos=pos,
        units="cm",
    )
