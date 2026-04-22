"""Unit tests for PhotodiodePatch and cursor interpolation (no PsychoPy needed).

PhotodiodePatch tests use mocked PsychoPy via sys.modules.
Cursor interpolation tests verify the dead-reckoning math via
DisplayProcess._interpolate_position.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from hapticore.display.photodiode import _CORNER_POSITIONS, _VALID_CORNERS


def _make_photodiode(corner: str = "bottom_left", *, enabled: bool = True) -> object:
    """Create a PhotodiodePatch with mocked PsychoPy."""
    mock_psychopy = MagicMock()
    mock_visual = MagicMock()
    mock_rect = MagicMock()
    mock_visual.Rect.return_value = mock_rect
    mock_psychopy.visual = mock_visual

    # Patch both psychopy and psychopy.visual in sys.modules
    orig_psychopy = sys.modules.get("psychopy")
    orig_visual = sys.modules.get("psychopy.visual")
    sys.modules["psychopy"] = mock_psychopy
    sys.modules["psychopy.visual"] = mock_visual
    try:
        from hapticore.display.photodiode import PhotodiodePatch

        pp = PhotodiodePatch(MagicMock(), corner, enabled=enabled)
    finally:
        if orig_psychopy is None:
            sys.modules.pop("psychopy", None)
        else:
            sys.modules["psychopy"] = orig_psychopy
        if orig_visual is None:
            sys.modules.pop("psychopy.visual", None)
        else:
            sys.modules["psychopy.visual"] = orig_visual

    return pp


class TestPhotodiodePatchToggle:
    """Verify trigger() toggles between black and white."""

    def test_trigger_toggles_to_white(self) -> None:
        pp = _make_photodiode("bottom_left", enabled=True)
        assert not pp.is_white
        pp.trigger()
        assert pp.is_white

    def test_trigger_toggles_back_to_black(self) -> None:
        pp = _make_photodiode("bottom_left", enabled=True)
        pp.trigger()  # → white
        pp.trigger()  # → black
        assert not pp.is_white


class TestPhotodiodePatchDisabled:
    """Verify enabled=False makes trigger() and draw() no-ops."""

    def test_trigger_noop_when_disabled(self) -> None:
        from hapticore.display.photodiode import PhotodiodePatch

        pp = PhotodiodePatch(MagicMock(), "bottom_left", enabled=False)
        pp.trigger()
        assert not pp.is_white  # stays black

    def test_draw_noop_when_disabled(self) -> None:
        from hapticore.display.photodiode import PhotodiodePatch

        pp = PhotodiodePatch(MagicMock(), "bottom_left", enabled=False)
        pp.draw()  # should not raise


class TestPhotodiodePatchCorners:
    """Verify all four corner values produce valid positions."""

    @pytest.mark.parametrize("corner", sorted(_VALID_CORNERS))
    def test_valid_corner_positions(self, corner: str) -> None:
        pos = _CORNER_POSITIONS[corner]
        assert -1.0 <= pos[0] <= 1.0
        assert -1.0 <= pos[1] <= 1.0

    def test_invalid_corner_raises(self) -> None:
        from hapticore.display.photodiode import PhotodiodePatch

        with pytest.raises(ValueError, match="Invalid corner"):
            PhotodiodePatch(MagicMock(), "center", enabled=True)


class TestCursorInterpolation:
    """Verify dead-reckoning cursor interpolation math."""

    def test_interpolation_extrapolates_position(self) -> None:
        from hapticore.display.process import DisplayProcess

        state = {
            "position": [0.1, 0.2, 0.0],
            "velocity": [0.1, 0.0, 0.0],
        }
        dt = 0.01
        result = DisplayProcess._interpolate_position(state, dt)
        assert pytest.approx(result[0], abs=1e-9) == 0.1 + 0.1 * 0.01
        assert pytest.approx(result[1], abs=1e-9) == 0.2

    def test_interpolation_with_both_axes(self) -> None:
        from hapticore.display.process import DisplayProcess

        state = {
            "position": [0.0, 0.0, 0.0],
            "velocity": [1.0, 2.0, 0.0],
        }
        dt = 0.005
        result = DisplayProcess._interpolate_position(state, dt)
        assert pytest.approx(result[0], abs=1e-9) == 0.005
        assert pytest.approx(result[1], abs=1e-9) == 0.01

    def test_interpolation_zero_velocity(self) -> None:
        from hapticore.display.process import DisplayProcess

        state = {
            "position": [0.5, 0.3, 0.0],
            "velocity": [0.0, 0.0, 0.0],
        }
        result = DisplayProcess._interpolate_position(state, 0.1)
        assert result == [0.5, 0.3]


class TestRemapCornerForMirror:
    """Pure-logic tests for remap_corner_for_mirror (no PsychoPy needed)."""

    @pytest.mark.parametrize("corner", sorted(_VALID_CORNERS))
    def test_no_mirror_is_identity(self, corner: str) -> None:
        from hapticore.display.photodiode import remap_corner_for_mirror

        result = remap_corner_for_mirror(
            corner, mirror_horizontal=False, mirror_vertical=False
        )
        assert result == corner

    @pytest.mark.parametrize("corner,expected", [
        ("bottom_left",  "bottom_right"),
        ("bottom_right", "bottom_left"),
        ("top_left",     "top_right"),
        ("top_right",    "top_left"),
    ])
    def test_mirror_horizontal_swaps_horiz(self, corner: str, expected: str) -> None:
        from hapticore.display.photodiode import remap_corner_for_mirror

        result = remap_corner_for_mirror(
            corner, mirror_horizontal=True, mirror_vertical=False
        )
        assert result == expected

    @pytest.mark.parametrize("corner,expected", [
        ("bottom_left",  "top_left"),
        ("bottom_right", "top_right"),
        ("top_left",     "bottom_left"),
        ("top_right",    "bottom_right"),
    ])
    def test_mirror_vertical_swaps_vert(self, corner: str, expected: str) -> None:
        from hapticore.display.photodiode import remap_corner_for_mirror

        result = remap_corner_for_mirror(
            corner, mirror_horizontal=False, mirror_vertical=True
        )
        assert result == expected

    @pytest.mark.parametrize("corner,expected", [
        ("bottom_left",  "top_right"),
        ("bottom_right", "top_left"),
        ("top_left",     "bottom_right"),
        ("top_right",    "bottom_left"),
    ])
    def test_both_mirrors_swaps_both(self, corner: str, expected: str) -> None:
        from hapticore.display.photodiode import remap_corner_for_mirror

        result = remap_corner_for_mirror(
            corner, mirror_horizontal=True, mirror_vertical=True
        )
        assert result == expected

    @pytest.mark.parametrize("corner", sorted(_VALID_CORNERS))
    def test_horizontal_mirror_is_involutive(self, corner: str) -> None:
        """Applying mirror_horizontal twice returns the original corner."""
        from hapticore.display.photodiode import remap_corner_for_mirror

        once = remap_corner_for_mirror(
            corner, mirror_horizontal=True, mirror_vertical=False
        )
        twice = remap_corner_for_mirror(
            once, mirror_horizontal=True, mirror_vertical=False
        )
        assert twice == corner

    @pytest.mark.parametrize("corner", sorted(_VALID_CORNERS))
    def test_vertical_mirror_is_involutive(self, corner: str) -> None:
        """Applying mirror_vertical twice returns the original corner."""
        from hapticore.display.photodiode import remap_corner_for_mirror

        once = remap_corner_for_mirror(
            corner, mirror_horizontal=False, mirror_vertical=True
        )
        twice = remap_corner_for_mirror(
            once, mirror_horizontal=False, mirror_vertical=True
        )
        assert twice == corner

    @pytest.mark.parametrize("corner", sorted(_VALID_CORNERS))
    def test_both_mirrors_is_involutive(self, corner: str) -> None:
        """Applying both mirrors twice returns the original corner."""
        from hapticore.display.photodiode import remap_corner_for_mirror

        once = remap_corner_for_mirror(
            corner, mirror_horizontal=True, mirror_vertical=True
        )
        twice = remap_corner_for_mirror(
            once, mirror_horizontal=True, mirror_vertical=True
        )
        assert twice == corner

    def test_invalid_corner_raises(self) -> None:
        from hapticore.display.photodiode import remap_corner_for_mirror

        with pytest.raises(ValueError, match="Invalid corner"):
            remap_corner_for_mirror(
                "center", mirror_horizontal=False, mirror_vertical=False
            )
