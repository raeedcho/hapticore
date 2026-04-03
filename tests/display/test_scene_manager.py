"""SceneManager tests requiring PsychoPy.

Guarded by ``pytest.importorskip("psychopy")`` and ``@pytest.mark.display``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("psychopy")

from psychopy import visual  # noqa: E402

from hapticore.core.config import DisplayConfig  # noqa: E402
from hapticore.display.scene_manager import SceneManager  # noqa: E402


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
class TestSceneManagerShowHide:
    """Test show/hide/clear lifecycle with real PsychoPy objects."""

    def test_show_adds_to_active(self, win) -> None:
        scene = SceneManager(win, DisplayConfig())
        scene.show("target", {"type": "circle", "radius": 0.01})
        assert "target" in scene.active_stimuli

    def test_show_replaces_existing(self, win) -> None:
        scene = SceneManager(win, DisplayConfig())
        scene.show("target", {"type": "circle", "radius": 0.01})
        scene.show("target", {"type": "rectangle", "width": 0.02})
        assert len(scene.active_stimuli) == 1
        assert scene.active_stimuli["target"] == "Rect"

    def test_hide_removes(self, win) -> None:
        scene = SceneManager(win, DisplayConfig())
        scene.show("target", {"type": "circle"})
        scene.hide("target")
        assert "target" not in scene.active_stimuli

    def test_hide_nonexistent_no_error(self, win) -> None:
        scene = SceneManager(win, DisplayConfig())
        scene.hide("nonexistent")  # Should not raise

    def test_clear_empties(self, win) -> None:
        scene = SceneManager(win, DisplayConfig())
        scene.show("a", {"type": "circle"})
        scene.show("b", {"type": "rectangle"})
        scene.clear()
        assert len(scene.active_stimuli) == 0

    def test_show_missing_type_raises(self, win) -> None:
        scene = SceneManager(win, DisplayConfig())
        with pytest.raises(ValueError, match="type"):
            scene.show("target", {"radius": 0.01})


@pytest.mark.display
class TestSceneManagerDrawAll:
    """Test draw_all calls .draw() on all active stimuli."""

    def test_draw_all_calls_draw(self, win) -> None:
        scene = SceneManager(win, DisplayConfig())
        scene.show("a", {"type": "circle"})
        scene.show("b", {"type": "rectangle"})
        # Should not raise — stimuli draw to the window
        scene.draw_all()


@pytest.mark.display
class TestSceneManagerCursor:
    """Test cursor creation and position updates."""

    def test_set_cursor_creates_on_first_call(self, win) -> None:
        scene = SceneManager(win, DisplayConfig(cursor_visible=True))
        scene.set_cursor_position([0.05, 0.03])
        # Cursor is internal — verify via draw_all not raising
        scene.draw_all()

    def test_set_cursor_updates_position(self, win) -> None:
        scene = SceneManager(win, DisplayConfig(cursor_visible=True))
        scene.set_cursor_position([0.05, 0.03])
        scene.set_cursor_position([0.1, 0.0])
        scene.draw_all()

    def test_cursor_invisible_is_noop(self, win) -> None:
        scene = SceneManager(win, DisplayConfig(cursor_visible=False))
        scene.set_cursor_position([0.05, 0.03])
        # Should not create cursor or raise
        scene.draw_all()
