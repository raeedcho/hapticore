"""Mock-based SceneManager logic tests — no PsychoPy needed.

Verifies draw order, show/hide/clear sequencing, cursor visibility gating,
and update delegation using mocked Window and StimulusFactory.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from hapticore.core.config import DisplayConfig
from hapticore.display.scene_manager import SceneManager


class TestShowHideClear:
    """Verify show/hide/clear mutations on SceneManager state."""

    def _make_scene(self, **display_kwargs: object) -> SceneManager:
        """Create a SceneManager with a mock Window."""
        win = MagicMock()
        config = DisplayConfig(**display_kwargs)  # type: ignore[arg-type]
        return SceneManager(win, config)

    def test_show_adds_to_active_stimuli(self) -> None:
        scene = self._make_scene()
        with patch("hapticore.display.scene_manager.create_stimulus") as mock_create:
            mock_stim = MagicMock()
            mock_stim.__class__.__name__ = "Circle"
            mock_create.return_value = mock_stim
            scene.show("target", {"type": "circle", "radius": 0.01})
            assert "target" in scene.active_stimuli

    def test_show_replaces_existing(self) -> None:
        scene = self._make_scene()
        with patch("hapticore.display.scene_manager.create_stimulus") as mock_create:
            stim1 = MagicMock()
            stim2 = MagicMock()
            mock_create.side_effect = [stim1, stim2]
            scene.show("target", {"type": "circle"})
            scene.show("target", {"type": "rectangle"})
            # Should have exactly one entry
            assert len(scene.active_stimuli) == 1

    def test_hide_removes_from_active(self) -> None:
        scene = self._make_scene()
        with patch("hapticore.display.scene_manager.create_stimulus") as mock_create:
            stim = MagicMock()
            mock_create.return_value = stim
            scene.show("target", {"type": "circle"})
            scene.hide("target")
            assert "target" not in scene.active_stimuli
            # draw_all should not attempt to draw the hidden stimulus
            stim.draw.reset_mock()
            scene.draw_all()
            stim.draw.assert_not_called()

    def test_hide_nonexistent_is_noop(self) -> None:
        scene = self._make_scene()
        scene.hide("nonexistent")
        # Should not raise

    def test_clear_empties_all(self) -> None:
        scene = self._make_scene()
        with patch("hapticore.display.scene_manager.create_stimulus") as mock_create:
            mock_create.return_value = MagicMock()
            scene.show("a", {"type": "circle"})
            scene.show("b", {"type": "rectangle"})
            scene.clear()
            assert len(scene.active_stimuli) == 0

    def test_show_missing_type_raises_value_error(self) -> None:
        scene = self._make_scene()
        with pytest.raises(ValueError, match="type"):
            scene.show("target", {"radius": 0.01})

    def test_has_stimulus_true_after_show(self) -> None:
        scene = self._make_scene()
        with patch("hapticore.display.scene_manager.create_stimulus") as mock_create:
            mock_create.return_value = MagicMock()
            scene.show("target", {"type": "circle"})
            assert scene.has_stimulus("target") is True

    def test_has_stimulus_false_after_hide(self) -> None:
        scene = self._make_scene()
        with patch("hapticore.display.scene_manager.create_stimulus") as mock_create:
            mock_create.return_value = MagicMock()
            scene.show("target", {"type": "circle"})
            scene.hide("target")
            assert scene.has_stimulus("target") is False

    def test_has_stimulus_false_for_unknown(self) -> None:
        scene = self._make_scene()
        assert scene.has_stimulus("nonexistent") is False

    def test_get_stimulus_returns_object_after_show(self) -> None:
        scene = self._make_scene()
        with patch("hapticore.display.scene_manager.create_stimulus") as mock_create:
            mock_stim = MagicMock()
            mock_create.return_value = mock_stim
            scene.show("target", {"type": "circle"})
            assert scene.get_stimulus("target") is mock_stim

    def test_get_stimulus_returns_none_after_hide(self) -> None:
        scene = self._make_scene()
        with patch("hapticore.display.scene_manager.create_stimulus") as mock_create:
            mock_create.return_value = MagicMock()
            scene.show("target", {"type": "circle"})
            scene.hide("target")
            assert scene.get_stimulus("target") is None

    def test_get_stimulus_returns_none_for_unknown(self) -> None:
        scene = self._make_scene()
        assert scene.get_stimulus("nonexistent") is None

    def test_clear_makes_has_stimulus_false_for_all(self) -> None:
        scene = self._make_scene()
        with patch("hapticore.display.scene_manager.create_stimulus") as mock_create:
            mock_create.return_value = MagicMock()
            scene.show("a", {"type": "circle"})
            scene.show("b", {"type": "rectangle"})
            scene.clear()
            assert scene.has_stimulus("a") is False
            assert scene.has_stimulus("b") is False


class TestDrawOrder:
    """Verify draw order: insertion order for stimuli, cursor always last."""

    def _make_scene(self, **display_kwargs: object) -> SceneManager:
        win = MagicMock()
        config = DisplayConfig(**display_kwargs)  # type: ignore[arg-type]
        return SceneManager(win, config)

    def test_draw_all_calls_draw_in_order(self) -> None:
        scene = self._make_scene()
        with patch("hapticore.display.scene_manager.create_stimulus") as mock_create:
            stim_a = MagicMock()
            stim_b = MagicMock()
            mock_create.side_effect = [stim_a, stim_b]
            scene.show("a", {"type": "circle"})
            scene.show("b", {"type": "rectangle"})

            # Reset mock call tracking
            stim_a.draw.reset_mock()
            stim_b.draw.reset_mock()

            scene.draw_all()

            stim_a.draw.assert_called_once()
            stim_b.draw.assert_called_once()

    def test_cursor_drawn_last(self) -> None:
        scene = self._make_scene(cursor_visible=True)
        draw_order: list[str] = []

        with patch("hapticore.display.scene_manager.create_stimulus") as mock_create:
            stim = MagicMock()
            stim.draw = MagicMock(side_effect=lambda: draw_order.append("stim"))
            cursor = MagicMock()
            cursor.draw = MagicMock(side_effect=lambda: draw_order.append("cursor"))
            mock_create.side_effect = [stim, cursor]

            scene.show("target", {"type": "circle"})
            scene.set_cursor_position([0.0, 0.0])
            scene.draw_all()

        assert draw_order == ["stim", "cursor"]


class TestCursorVisibility:
    """Verify cursor respects DisplayConfig.cursor_visible."""

    def _make_scene(self, **display_kwargs: object) -> SceneManager:
        win = MagicMock()
        config = DisplayConfig(**display_kwargs)  # type: ignore[arg-type]
        return SceneManager(win, config)

    def test_cursor_visible_true_creates_cursor(self) -> None:
        scene = self._make_scene(cursor_visible=True)
        with patch("hapticore.display.scene_manager.create_stimulus") as mock_create:
            mock_create.return_value = MagicMock()
            scene.set_cursor_position([0.05, 0.03])
            mock_create.assert_called_once()

    def test_cursor_visible_false_is_noop(self) -> None:
        scene = self._make_scene(cursor_visible=False)
        with patch("hapticore.display.scene_manager.create_stimulus") as mock_create:
            scene.set_cursor_position([0.05, 0.03])
            mock_create.assert_not_called()

    def test_cursor_uses_config_radius_and_color(self) -> None:
        scene = self._make_scene(
            cursor_visible=True,
            cursor_radius=0.008,
            cursor_color=[1.0, 0.0, 0.0],
        )
        with patch("hapticore.display.scene_manager.create_stimulus") as mock_create:
            mock_create.return_value = MagicMock()
            scene.set_cursor_position([0.0, 0.0])
            args, _kwargs = mock_create.call_args
            # create_stimulus(win, "circle", params_dict)
            params = args[2]
            assert params["radius"] == 0.008
            assert params["color"] == [1.0, 0.0, 0.0]

    def test_cursor_position_updates_on_subsequent_calls(self) -> None:
        scene = self._make_scene(cursor_visible=True)
        with patch("hapticore.display.scene_manager.create_stimulus") as mock_create:
            cursor_mock = MagicMock()
            mock_create.return_value = cursor_mock
            scene.set_cursor_position([0.05, 0.03])
            scene.set_cursor_position([0.1, 0.0])
            # Second call should update pos, not create new
            assert mock_create.call_count == 1
            assert list(cursor_mock.pos) == [0.1, 0.0]


class TestUpdate:
    """Verify update delegates to update_stimulus."""

    def _make_scene(self) -> SceneManager:
        win = MagicMock()
        config = DisplayConfig()
        return SceneManager(win, config)

    def test_update_existing(self) -> None:
        scene = self._make_scene()
        with (
            patch("hapticore.display.scene_manager.create_stimulus") as mock_create,
            patch("hapticore.display.scene_manager.update_stimulus") as mock_update,
        ):
            stim = MagicMock()
            mock_create.return_value = stim
            scene.show("target", {"type": "circle"})
            scene.update("target", {"position": [0.1, 0.0]})
            mock_update.assert_called_once_with(stim, {"position": [0.1, 0.0]})

    def test_update_unknown_warns(self) -> None:
        scene = self._make_scene()
        with patch("hapticore.display.scene_manager.update_stimulus") as mock_update:
            scene.update("nonexistent", {"position": [0.0, 0.0]})
            mock_update.assert_not_called()
