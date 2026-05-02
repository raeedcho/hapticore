"""Tests for display._field_visuals helper functions."""

from __future__ import annotations

import math

import pytest

from hapticore.display._field_visuals import (
    CART_PENDULUM_STIM_IDS,
    CartPendulumVisuals,
    create_physics_body_stimuli,
    hide_physics_body_stimuli,
    physics_body_stim_id,
)
from hapticore.display.mock import MockDisplay


class TestCartPendulumVisuals:
    """Tests for the CartPendulumVisuals stateful helper class."""

    def test_show_creates_cup_and_ball(self) -> None:
        mock = MockDisplay()
        vis = CartPendulumVisuals(mock, pendulum_length=0.3, ball_radius=0.004)
        vis.show(cup_position=[0.05, 0.0], initial_phi=0.0)
        assert "__cup" in mock._visible_stimuli
        assert "__ball" in mock._visible_stimuli

    def test_show_with_initial_pose(self) -> None:
        mock = MockDisplay()
        phi = 0.5
        length = 0.3
        cup_pos = [0.05, 0.0]
        vis = CartPendulumVisuals(mock, pendulum_length=length)
        vis.show(cup_position=cup_pos, initial_phi=phi)

        cup = mock._visible_stimuli["__cup"]
        ball = mock._visible_stimuli["__ball"]

        assert cup["position"] == pytest.approx([cup_pos[0], cup_pos[1]])
        expected_bx = cup_pos[0] + length * math.sin(phi)
        expected_by = cup_pos[1] + length * (1 - math.cos(phi))
        assert ball["position"][0] == pytest.approx(expected_bx, abs=1e-9)
        assert ball["position"][1] == pytest.approx(expected_by, abs=1e-9)

    def test_custom_cup_color(self) -> None:
        mock = MockDisplay()
        vis = CartPendulumVisuals(mock, cup_color=[1.0, 0.0, 0.0])
        vis.show()
        assert mock._visible_stimuli["__cup"]["color"] == [1.0, 0.0, 0.0]

    def test_hide_removes_both(self) -> None:
        mock = MockDisplay()
        vis = CartPendulumVisuals(mock)
        vis.show()
        vis.hide()
        assert "__cup" not in mock._visible_stimuli
        assert "__ball" not in mock._visible_stimuli

    def test_set_ball_color(self) -> None:
        mock = MockDisplay()
        vis = CartPendulumVisuals(mock)
        vis.show()
        vis.set_ball_color([1.0, 0.0, 0.0])
        color_calls = [
            args for method, args in mock._call_log
            if method == "update_scene" and isinstance(args, dict) and "__ball" in args
        ]
        assert len(color_calls) >= 1
        assert color_calls[-1]["__ball"]["color"] == [1.0, 0.0, 0.0]

    def test_reset_ball_color(self) -> None:
        mock = MockDisplay()
        vis = CartPendulumVisuals(mock, ball_color=[0.0, 1.0, 0.0])
        vis.show()
        vis.set_ball_color([1.0, 0.0, 0.0])
        vis.reset_ball_color()
        color_calls = [
            args for method, args in mock._call_log
            if method == "update_scene" and isinstance(args, dict) and "__ball" in args
        ]
        assert color_calls[-1]["__ball"]["color"] == [0.0, 1.0, 0.0]

    def test_stim_id_constants(self) -> None:
        assert CART_PENDULUM_STIM_IDS == ("__cup", "__ball")


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
