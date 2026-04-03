"""StimulusFactory tests requiring PsychoPy.

Guarded by ``pytest.importorskip("psychopy")`` and ``@pytest.mark.display``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("psychopy")

from psychopy import visual  # noqa: E402

from hapticore.display.stimulus_factory import (  # noqa: E402
    STIMULUS_TYPES,
    create_stimulus,
    update_stimulus,
)


@pytest.fixture(scope="module")
def win():
    """Shared headless PsychoPy Window for all tests in the module."""
    w = visual.Window(
        size=[200, 200],
        fullscr=False,
        color=[0, 0, 0],
        units="m",
        allowGUI=False,
        winType="pyglet",
        checkTiming=False,
    )
    yield w
    w.close()


@pytest.mark.display
class TestCreateStimulus:
    """Test creation of each supported stimulus type."""

    def test_circle_with_params(self, win) -> None:
        stim = create_stimulus(win, "circle", {"radius": 0.02, "position": [0.05, 0.03]})
        assert isinstance(stim, visual.Circle)
        assert stim.radius == pytest.approx(0.02)
        assert list(stim.pos) == pytest.approx([0.05, 0.03])

    def test_circle_defaults(self, win) -> None:
        stim = create_stimulus(win, "circle", {})
        assert isinstance(stim, visual.Circle)
        assert stim.radius == pytest.approx(0.01)

    def test_rectangle(self, win) -> None:
        stim = create_stimulus(win, "rectangle", {"width": 0.04, "height": 0.02})
        assert isinstance(stim, visual.Rect)
        assert stim.width == pytest.approx(0.04)
        assert stim.height == pytest.approx(0.02)

    def test_text(self, win) -> None:
        stim = create_stimulus(win, "text", {"text": "hello"})
        assert isinstance(stim, visual.TextStim)
        assert stim.text == "hello"

    def test_polygon(self, win) -> None:
        verts = [[0, 0], [0.1, 0], [0.05, 0.1]]
        stim = create_stimulus(win, "polygon", {"vertices": verts})
        assert isinstance(stim, visual.ShapeStim)

    def test_line(self, win) -> None:
        stim = create_stimulus(win, "line", {"start": [0, 0], "end": [0.1, 0.05]})
        assert isinstance(stim, visual.Line)

    def test_unknown_type_raises(self, win) -> None:
        with pytest.raises(ValueError, match="Unknown stimulus type"):
            create_stimulus(win, "unknown_type", {})

    def test_stimulus_types_constant(self) -> None:
        expected = {
            "circle", "rectangle", "line", "polygon",
            "text", "image", "grating", "dot_field",
        }
        assert STIMULUS_TYPES == expected


@pytest.mark.display
class TestUpdateStimulus:
    """Test in-place stimulus property updates."""

    def test_update_position(self, win) -> None:
        stim = create_stimulus(win, "circle", {"radius": 0.01})
        update_stimulus(stim, {"position": [0.05, 0.02]})
        assert list(stim.pos) == pytest.approx([0.05, 0.02])

    def test_update_color(self, win) -> None:
        stim = create_stimulus(win, "circle", {"radius": 0.01})
        update_stimulus(stim, {"color": [1, 0, 0]})
        # Circle has fillColor and lineColor
        assert list(stim.fillColor) == pytest.approx([1, 0, 0])
        assert list(stim.lineColor) == pytest.approx([1, 0, 0])

    def test_update_opacity(self, win) -> None:
        stim = create_stimulus(win, "circle", {"radius": 0.01})
        update_stimulus(stim, {"opacity": 0.5})
        assert stim.opacity == pytest.approx(0.5)

    def test_update_orientation(self, win) -> None:
        stim = create_stimulus(win, "rectangle", {})
        update_stimulus(stim, {"orientation": 45.0})
        assert stim.ori == pytest.approx(45.0)
