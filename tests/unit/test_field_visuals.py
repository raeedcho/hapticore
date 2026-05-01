"""Tests for display._field_visuals helper functions."""

from __future__ import annotations

import math

import pytest

from hapticore.display._field_visuals import (
    CART_PENDULUM_STIM_IDS,
    CartPendulumVisuals,
    create_cart_pendulum_stimuli,
    create_physics_body_stimuli,
    hide_cart_pendulum_stimuli,
    hide_physics_body_stimuli,
    physics_body_stim_id,
)
from hapticore.display.mock import MockDisplay


class TestCartPendulumStimuli:
    def test_default_positions(self) -> None:
        mock = MockDisplay()
        create_cart_pendulum_stimuli(mock.show_stimulus)
        cup = mock._visible_stimuli["__cup"]
        ball = mock._visible_stimuli["__ball"]

        assert cup["position"] == pytest.approx([0.0, 0.0])
        assert ball["position"] == pytest.approx([0.0, 0.0])

    def test_initial_pose(self) -> None:
        mock = MockDisplay()
        phi = 0.5
        length = 0.3
        cup_pos = [0.05, 0.0]
        create_cart_pendulum_stimuli(
            mock.show_stimulus,
            cup_position=cup_pos, initial_phi=phi, pendulum_length=length,
        )
        cup = mock._visible_stimuli["__cup"]
        ball = mock._visible_stimuli["__ball"]

        assert cup["position"] == pytest.approx([cup_pos[0], cup_pos[1]])

        expected_bx = cup_pos[0] + length * math.sin(phi)
        expected_by = cup_pos[1] + length * (1 - math.cos(phi))
        assert ball["position"][0] == pytest.approx(expected_bx, abs=1e-9)
        assert ball["position"][1] == pytest.approx(expected_by, abs=1e-9)

    def test_hide(self) -> None:
        mock = MockDisplay()
        create_cart_pendulum_stimuli(mock.show_stimulus)
        hide_cart_pendulum_stimuli(mock.hide_stimulus)
        for sid in CART_PENDULUM_STIM_IDS:
            assert sid not in mock._visible_stimuli

    def test_stim_id_constants(self) -> None:
        assert CART_PENDULUM_STIM_IDS == ("__cup", "__ball")


class TestCartPendulumVisuals:
    """Tests for the CartPendulumVisuals stateful helper class."""

    def test_create_makes_cup_and_ball(self) -> None:
        mock = MockDisplay()
        visuals = CartPendulumVisuals(
            mock.show_stimulus, mock.hide_stimulus,
            pendulum_length=0.3, ball_radius=0.004,
        )
        visuals.create(cup_position=[0.05, 0.0], initial_phi=0.0)

        assert "__cup" in mock._visible_stimuli
        assert "__ball" in mock._visible_stimuli

    def test_create_with_initial_pose(self) -> None:
        mock = MockDisplay()
        phi = 0.5
        length = 0.3
        cup_pos = [0.05, 0.0]
        visuals = CartPendulumVisuals(
            mock.show_stimulus, mock.hide_stimulus,
            pendulum_length=length,
        )
        visuals.create(cup_position=cup_pos, initial_phi=phi)

        cup = mock._visible_stimuli["__cup"]
        ball = mock._visible_stimuli["__ball"]

        assert cup["position"] == pytest.approx([cup_pos[0], cup_pos[1]])
        expected_bx = cup_pos[0] + length * math.sin(phi)
        expected_by = cup_pos[1] + length * (1 - math.cos(phi))
        assert ball["position"][0] == pytest.approx(expected_bx, abs=1e-9)
        assert ball["position"][1] == pytest.approx(expected_by, abs=1e-9)

    def test_hide_removes_stimuli(self) -> None:
        mock = MockDisplay()
        visuals = CartPendulumVisuals(mock.show_stimulus, mock.hide_stimulus)
        visuals.create()
        visuals.hide()

        assert "__cup" not in mock._visible_stimuli
        assert "__ball" not in mock._visible_stimuli

    def test_mark_spilled_changes_ball_color(self) -> None:
        """mark_spilled re-shows ball with spill color."""
        from hapticore.display._field_visuals import _SPILL_COLOR

        mock = MockDisplay()
        visuals = CartPendulumVisuals(
            mock.show_stimulus, mock.hide_stimulus,
            pendulum_length=0.3, ball_radius=0.004,
        )
        visuals.create(cup_position=[0.0, 0.0], initial_phi=0.0)

        # Normal ball color should not be spill color
        normal_color = mock._visible_stimuli["__ball"]["color"]
        assert normal_color != _SPILL_COLOR

        cart_state = {"ball_x": 0.05, "ball_y": 0.1, "spilled": True}
        visuals.mark_spilled(cart_state)

        # Ball should now show spill color
        assert mock._visible_stimuli["__ball"]["color"] == _SPILL_COLOR

    def test_mark_spilled_uses_field_state_position(self) -> None:
        """mark_spilled positions ball from cart_pendulum_state, not create() position."""
        mock = MockDisplay()
        visuals = CartPendulumVisuals(
            mock.show_stimulus, mock.hide_stimulus,
            pendulum_length=0.3, ball_radius=0.004,
        )
        # Create at initial position
        visuals.create(cup_position=[0.0, 0.0], initial_phi=0.0)

        # Spill detected at a different ball position (as if ball has swung)
        cart_state = {"ball_x": 0.08, "ball_y": 0.15, "spilled": True}
        visuals.mark_spilled(cart_state)

        ball = mock._visible_stimuli["__ball"]
        assert ball["position"] == pytest.approx([0.08, 0.15])

    def test_mark_spilled_noop_when_not_visible(self) -> None:
        """mark_spilled does nothing if stimuli haven't been created."""
        mock = MockDisplay()
        visuals = CartPendulumVisuals(mock.show_stimulus, mock.hide_stimulus)
        # Never called create() — mark_spilled should not raise or show anything
        visuals.mark_spilled({"ball_x": 0.0, "ball_y": 0.0})
        assert "__ball" not in mock._visible_stimuli

    def test_hide_clears_visible_flag(self) -> None:
        """After hide(), mark_spilled is a no-op."""
        mock = MockDisplay()
        visuals = CartPendulumVisuals(mock.show_stimulus, mock.hide_stimulus)
        visuals.create()
        visuals.hide()
        # mark_spilled after hide should not re-create ball
        visuals.mark_spilled({"ball_x": 0.0, "ball_y": 0.0})
        assert "__ball" not in mock._visible_stimuli


class TestPhysicsBodyStimuli:
    def test_create(self) -> None:
        mock = MockDisplay()
        create_physics_body_stimuli(mock.show_stimulus, {
            "puck": {"type": "circle", "radius": 0.02},
            "wall": {"type": "rect", "width": 0.1, "height": 0.01},
        })
        assert "__body_puck" in mock._visible_stimuli
        assert "__body_wall" in mock._visible_stimuli

    def test_hide(self) -> None:
        mock = MockDisplay()
        create_physics_body_stimuli(mock.show_stimulus, {
            "puck": {"type": "circle", "radius": 0.02},
            "wall": {"type": "rect", "width": 0.1, "height": 0.01},
        })
        hide_physics_body_stimuli(mock.hide_stimulus, ["puck", "wall"])
        assert "__body_puck" not in mock._visible_stimuli
        assert "__body_wall" not in mock._visible_stimuli

    def test_stim_id_function(self) -> None:
        assert physics_body_stim_id("puck") == "__body_puck"
