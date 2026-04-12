"""PhotodiodePatch tests requiring PsychoPy.

Guarded by ``pytest.importorskip("psychopy")`` and ``@pytest.mark.display``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("psychopy")

from psychopy import visual  # noqa: E402

from hapticore.display.photodiode import (  # noqa: E402
    PhotodiodePatch,
    _VALID_CORNERS,
)


@pytest.fixture(scope="module")
def win():
    """Shared headless PsychoPy Window for all tests in the module."""
    w = visual.Window(
        size=[200, 200],
        fullscr=False,
        color=[0, 0, 0],
        units="norm",
        allowGUI=False,
        winType="pyglet",
        checkTiming=False,
    )
    yield w
    w.close()


@pytest.mark.display
class TestPhotodiodePatchWithPsychoPy:
    """Test PhotodiodePatch with real PsychoPy Window."""

    def test_trigger_toggles_to_white(self, win) -> None:
        pp = PhotodiodePatch(win, "bottom_left", enabled=True)
        assert not pp.is_white
        pp.trigger()
        assert pp.is_white

    def test_trigger_toggles_back_to_black(self, win) -> None:
        pp = PhotodiodePatch(win, "bottom_left", enabled=True)
        pp.trigger()  # → white
        pp.trigger()  # → black
        assert not pp.is_white

    def test_draw_does_not_raise(self, win) -> None:
        pp = PhotodiodePatch(win, "bottom_left", enabled=True)
        pp.draw()  # should not raise

    def test_trigger_then_draw(self, win) -> None:
        pp = PhotodiodePatch(win, "bottom_left", enabled=True)
        pp.trigger()
        pp.draw()  # should not raise

    def test_disabled_draw_noop(self, win) -> None:
        pp = PhotodiodePatch(win, "bottom_left", enabled=False)
        pp.draw()  # should not raise

    def test_disabled_trigger_noop(self, win) -> None:
        pp = PhotodiodePatch(win, "bottom_left", enabled=False)
        pp.trigger()
        assert not pp.is_white

    @pytest.mark.parametrize("corner", sorted(_VALID_CORNERS))
    def test_all_corners_valid(self, win, corner: str) -> None:
        pp = PhotodiodePatch(win, corner, enabled=True)
        pp.draw()  # should not raise

    def test_invalid_corner_raises(self, win) -> None:
        with pytest.raises(ValueError, match="Invalid corner"):
            PhotodiodePatch(win, "center", enabled=True)
