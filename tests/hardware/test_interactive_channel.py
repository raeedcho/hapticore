"""Interactive feel-tests for the channel force field.

Run with:
    pytest tests/hardware/test_interactive_channel.py -m interactive -v -s

Prerequisites:
    1. The delta.3 is powered on and connected via USB.
    2. The haptic server is running.

These tests set up a channel constraint and pause so the operator can
physically feel the constraint.  Press Enter in the terminal to advance
past each test.
"""

from __future__ import annotations

import pytest

from .conftest import drain_and_receive_state, send_command

pytestmark = pytest.mark.interactive


class TestChannelFeelPlane:
    """Feel-test: constrain to a horizontal plane (free X and Y, hold Z=0)."""

    def test_constrained_to_plane(
        self,
        cmd_dealer,
        state_sub,
    ) -> None:
        """Activate channel constraining Z only — operator should feel a
        smooth 'groove' in the horizontal plane.

        Move the handle freely in X and Y.  Pushing in Z should feel a
        spring-damper restoring force pulling back to Z=0.
        """
        send_command(cmd_dealer, "heartbeat")
        resp = send_command(
            cmd_dealer, "set_force_field",
            {
                "type": "channel",
                "params": {
                    "axes": [2],
                    "stiffness": 800,
                    "damping": 15,
                    "center": [0, 0, 0],
                },
            },
        )
        assert resp["success"] is True

        state = drain_and_receive_state(state_sub, settle_time=0.1)
        assert state.active_field == "channel"

        input(
            "\n╔══════════════════════════════════════════════════════╗\n"
            "║  FEEL TEST: Constrained to horizontal plane (Z=0)  ║\n"
            "║                                                    ║\n"
            "║  • X and Y should feel FREE (no resistance)        ║\n"
            "║  • Pushing in Z should feel a smooth restoring     ║\n"
            "║    force back toward Z=0                           ║\n"
            "║                                                    ║\n"
            "║  Press Enter when done...                          ║\n"
            "╚══════════════════════════════════════════════════════╝\n"
        )


class TestChannelFeelLine:
    """Feel-test: constrain to a horizontal line (free X, hold Y=0 and Z=0)."""

    def test_constrained_to_line(
        self,
        cmd_dealer,
        state_sub,
    ) -> None:
        """Activate channel constraining Y and Z — operator should feel
        movement restricted to a horizontal line along X.
        """
        send_command(cmd_dealer, "heartbeat")
        resp = send_command(
            cmd_dealer, "set_force_field",
            {
                "type": "channel",
                "params": {
                    "axes": [1, 2],
                    "stiffness": 800,
                    "damping": 15,
                    "center": [0, 0, 0],
                },
            },
        )
        assert resp["success"] is True

        state = drain_and_receive_state(state_sub, settle_time=0.1)
        assert state.active_field == "channel"

        input(
            "\n╔══════════════════════════════════════════════════════╗\n"
            "║  FEEL TEST: Constrained to horizontal line (X-axis)║\n"
            "║                                                    ║\n"
            "║  • X should feel FREE (slide left/right)           ║\n"
            "║  • Y and Z should feel a smooth restoring force    ║\n"
            "║    pulling back toward the X-axis line             ║\n"
            "║                                                    ║\n"
            "║  Press Enter when done...                          ║\n"
            "╚══════════════════════════════════════════════════════╝\n"
        )


def test_cleanup_revert_to_null(cmd_dealer) -> None:
    """Revert to NullField so the handle is free-moving after tests."""
    send_command(cmd_dealer, "heartbeat")
    resp = send_command(
        cmd_dealer, "set_force_field",
        {"type": "null", "params": {}},
    )
    assert resp["success"] is True
