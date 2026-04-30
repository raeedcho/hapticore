"""Tests for display._field_visuals helper functions."""

from __future__ import annotations

import math

import pytest

from hapticore.display._field_visuals import (
    CART_PENDULUM_STIM_IDS,
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
        string = mock._visible_stimuli["__string"]

        assert cup["position"] == pytest.approx([0.0, 0.0])
        assert ball["position"] == pytest.approx([0.0, -0.3])
        assert string["start"] == pytest.approx([0.0, 0.0])
        assert string["end"] == pytest.approx([0.0, -0.3])

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
        string = mock._visible_stimuli["__string"]

        assert cup["position"] == pytest.approx([cup_pos[0], cup_pos[1]])

        expected_bx = cup_pos[0] + length * math.sin(phi)
        expected_by = cup_pos[1] - length * math.cos(phi)
        assert ball["position"][0] == pytest.approx(expected_bx, abs=1e-9)
        assert ball["position"][1] == pytest.approx(expected_by, abs=1e-9)

        assert string["start"] == pytest.approx([cup_pos[0], cup_pos[1]])
        assert string["end"] == pytest.approx([expected_bx, expected_by], abs=1e-9)

    def test_hide(self) -> None:
        mock = MockDisplay()
        create_cart_pendulum_stimuli(mock.show_stimulus)
        hide_cart_pendulum_stimuli(mock.hide_stimulus)
        for sid in CART_PENDULUM_STIM_IDS:
            assert sid not in mock._visible_stimuli

    def test_stim_id_constants(self) -> None:
        assert CART_PENDULUM_STIM_IDS == ("__cup", "__ball", "__string")


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
