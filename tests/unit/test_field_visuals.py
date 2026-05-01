"""Tests for display._field_visuals helper functions."""

from __future__ import annotations

import math

import pytest

from hapticore.display._field_visuals import (
    CART_PENDULUM_STIM_IDS,
    CartPendulumVisuals,
    _SPILL_COLOR,
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
            mock,
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
            mock,
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
        visuals = CartPendulumVisuals(mock)
        visuals.create()
        visuals.hide()

        assert "__cup" not in mock._visible_stimuli
        assert "__ball" not in mock._visible_stimuli

    def test_mark_spilled_changes_ball_color(self) -> None:
        """mark_spilled updates ball color via update_scene."""
        mock = MockDisplay()
        visuals = CartPendulumVisuals(
            mock,
            pendulum_length=0.3, ball_radius=0.004,
        )
        visuals.create(cup_position=[0.0, 0.0], initial_phi=0.0)

        visuals.mark_spilled()

        # update_scene should have been called with spill color for __ball
        assert mock._scene_state.get("__ball", {}).get("color") == _SPILL_COLOR

    def test_mark_spilled_noop_when_not_visible(self) -> None:
        """mark_spilled does nothing if stimuli haven't been created."""
        mock = MockDisplay()
        visuals = CartPendulumVisuals(mock)
        # Never called create() — mark_spilled should not raise or call update_scene
        visuals.mark_spilled()
        assert mock._scene_state == {}

    def test_hide_clears_visible_flag(self) -> None:
        """After hide(), mark_spilled is a no-op."""
        mock = MockDisplay()
        visuals = CartPendulumVisuals(mock)
        visuals.create()
        visuals.hide()
        # Reset scene state to verify mark_spilled doesn't change it
        mock._scene_state = {}
        visuals.mark_spilled()
        assert mock._scene_state == {}


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
