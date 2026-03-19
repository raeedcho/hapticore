"""Hardware-in-the-loop tests for the C++ haptic server with a real delta.3.

Run with:
    pytest tests/hardware/ -m hardware -v

Prerequisites:
    1. The delta.3 is powered on and connected via USB.
    2. The haptic server is running:
           ./haptic_server                              # default IPC
           ./haptic_server --pub-address tcp://*:5555 \
                           --cmd-address tcp://*:5556   # TCP (cross-machine)
    3. The device handle is NOT at the exact workspace center (just leave it
       wherever it naturally rests — that's almost never exactly [0,0,0]).

Override server addresses via environment variables:
    HAPTICORE_PUB_ADDRESS=tcp://rigmachine:5555
    HAPTICORE_CMD_ADDRESS=tcp://rigmachine:5556
"""

from __future__ import annotations

import math
import time

import pytest
import zmq

from .conftest import receive_n_states, receive_state, send_command

pytestmark = pytest.mark.hardware


# ============================================================================
# Stage 2: State stream verification
# ============================================================================


class TestStateStream:
    """Verify the ZeroMQ state stream from the running haptic server."""

    def test_receives_state_messages(
        self, state_sub: zmq.Socket[bytes]
    ) -> None:
        """Server publishes state messages that deserialize to HapticState."""
        state = receive_state(state_sub)
        assert state.timestamp > 0
        assert len(state.position) == 3
        assert len(state.velocity) == 3
        assert len(state.force) == 3
        assert isinstance(state.active_field, str)
        assert isinstance(state.field_state, dict)

    def test_sequence_monotonically_increasing(
        self, state_sub: zmq.Socket[bytes]
    ) -> None:
        """Sequence numbers increase across consecutive messages."""
        states = receive_n_states(state_sub, 20)
        for i in range(1, len(states)):
            assert states[i].sequence > states[i - 1].sequence, (
                f"Sequence not monotonic at index {i}: "
                f"{states[i].sequence} <= {states[i - 1].sequence}"
            )

    def test_position_is_nonzero(
        self, state_sub: zmq.Socket[bytes]
    ) -> None:
        """Device reports a non-origin position (handle resting naturally).

        If this fails, the handle is at exactly [0,0,0] — nudge it slightly
        and re-run.
        """
        state = receive_state(state_sub)
        mag = math.sqrt(sum(p ** 2 for p in state.position))
        assert mag > 1e-6, (
            f"Position {state.position} is at origin — nudge the handle"
        )

    def test_publish_rate(
        self, state_sub: zmq.Socket[bytes]
    ) -> None:
        """State arrives at approximately 200 Hz (100 messages in ~500ms)."""
        n = 100
        states = receive_n_states(state_sub, n)
        dt = states[-1].timestamp - states[0].timestamp
        rate = (n - 1) / dt
        assert 100 < rate < 400, (
            f"Publish rate {rate:.0f} Hz outside expected range 100-400 Hz"
        )


# ============================================================================
# Stage 3: Command round-trip and force field verification
# ============================================================================


class TestCommandRoundTrip:
    """Verify the command interface works with the running server."""

    def test_heartbeat(
        self, cmd_dealer: zmq.Socket[bytes]
    ) -> None:
        """Heartbeat command succeeds."""
        resp = send_command(cmd_dealer, "heartbeat")
        assert resp["success"] is True

    def test_unknown_method_returns_error(
        self, cmd_dealer: zmq.Socket[bytes]
    ) -> None:
        """Unknown command method returns success=false with error."""
        resp = send_command(cmd_dealer, "totally_bogus_method")
        assert resp["success"] is False
        assert resp["error"] is not None

    def test_set_null_field(
        self, cmd_dealer: zmq.Socket[bytes],
        state_sub: zmq.Socket[bytes],
    ) -> None:
        """Switch to null field and verify active_field in state stream."""
        send_command(cmd_dealer, "heartbeat")
        resp = send_command(
            cmd_dealer, "set_force_field",
            {"type": "null", "params": {}},
        )
        assert resp["success"] is True

        # Wait for the next state to reflect the change
        time.sleep(0.05)
        state = receive_state(state_sub)
        assert state.active_field == "null"


class TestSpringField:
    """Verify spring-damper force field behavior with the real device.

    These tests assume the handle is NOT at [0,0,0]. The natural resting
    position of the delta.3 is usually offset from center, which is exactly
    what we need — a nonzero displacement produces a nonzero spring force.
    """

    @pytest.fixture(autouse=True)
    def _set_spring_field(
        self,
        cmd_dealer: zmq.Socket[bytes],
        state_sub: zmq.Socket[bytes],
    ) -> None:
        """Activate a spring-damper field centered at origin before each test."""
        send_command(cmd_dealer, "heartbeat")
        resp = send_command(
            cmd_dealer, "set_force_field",
            {
                "type": "spring_damper",
                "params": {"stiffness": 200, "damping": 10, "center": [0, 0, 0]},
            },
        )
        assert resp["success"] is True
        # Let the field take effect and settle
        time.sleep(0.1)
        # Drain any stale messages
        while True:
            try:
                state_sub.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                break

    def test_active_field_is_spring_damper(
        self, state_sub: zmq.Socket[bytes]
    ) -> None:
        """State stream reports spring_damper as the active field."""
        state = receive_state(state_sub)
        assert state.active_field == "spring_damper"

    def test_restoring_force_direction(
        self, state_sub: zmq.Socket[bytes]
    ) -> None:
        """Force vector points back toward the spring center [0,0,0].

        For a spring centered at origin, F = -K*(pos - center), so each
        force component should have the opposite sign of the corresponding
        position component (for components with meaningful displacement).
        """
        state = receive_state(state_sub)
        for axis in range(3):
            p = state.position[axis]
            f = state.force[axis]
            if abs(p) > 0.005:  # only check axes with >5mm displacement
                assert p * f < 0, (
                    f"Axis {axis}: position={p:.4f}, force={f:.4f} — "
                    f"expected opposite signs (restoring force)"
                )

    def test_force_nonzero_when_displaced(
        self, state_sub: zmq.Socket[bytes]
    ) -> None:
        """Spring produces nonzero force when handle is away from center."""
        state = receive_state(state_sub)
        pos_mag = math.sqrt(sum(p ** 2 for p in state.position))
        force_mag = math.sqrt(sum(f ** 2 for f in state.force))
        if pos_mag > 0.005:
            assert force_mag > 0.1, (
                f"Position magnitude {pos_mag:.4f}m but force magnitude "
                f"only {force_mag:.4f}N — expected meaningful restoring force"
            )


class TestForceClamping:
    """Verify force clamping with high stiffness that would exceed the limit."""

    def test_force_magnitude_clamped(
        self,
        cmd_dealer: zmq.Socket[bytes],
        state_sub: zmq.Socket[bytes],
    ) -> None:
        """With stiffness=2000 N/m, even small displacement exceeds 20N clamp.

        At 2000 N/m, a 2cm displacement produces 40N unclamped. Verify
        the reported force magnitude stays at or below 20N.
        """
        send_command(cmd_dealer, "heartbeat")
        resp = send_command(
            cmd_dealer, "set_force_field",
            {
                "type": "spring_damper",
                "params": {"stiffness": 2000, "damping": 5, "center": [0, 0, 0]},
            },
        )
        assert resp["success"] is True
        time.sleep(0.1)

        # Collect several states and verify clamping
        states = receive_n_states(state_sub, 20)
        force_limit = 20.0
        for state in states:
            mag = math.sqrt(sum(f ** 2 for f in state.force))
            assert mag <= force_limit + 0.01, (
                f"Force magnitude {mag:.2f}N exceeds limit {force_limit}N"
            )


class TestHeartbeatTimeout:
    """Verify the safety fallback when heartbeats stop."""

    def test_reverts_to_safe_field(
        self,
        cmd_dealer: zmq.Socket[bytes],
        state_sub: zmq.Socket[bytes],
    ) -> None:
        """After ~500ms without heartbeat, forces should drop to ~zero.

        We set a spring field, send one heartbeat to arm the timeout, then
        wait for expiry. With the safety field (stiffness=0, damping=10)
        and the handle stationary, force should be near zero.
        """
        # Set spring field so there's initially nonzero force
        send_command(cmd_dealer, "heartbeat")
        send_command(
            cmd_dealer, "set_force_field",
            {
                "type": "spring_damper",
                "params": {"stiffness": 200, "damping": 10, "center": [0, 0, 0]},
            },
        )
        time.sleep(0.1)

        # Now stop sending heartbeats and wait for timeout (500ms + margin)
        time.sleep(0.7)

        states = receive_n_states(state_sub, 10)
        for state in states:
            mag = math.sqrt(sum(f ** 2 for f in state.force))
            assert mag < 1.0, (
                f"Force magnitude {mag:.2f}N after heartbeat timeout — "
                f"expected near-zero (safety fallback)"
            )

    def test_recovers_after_heartbeat_resumes(
        self,
        cmd_dealer: zmq.Socket[bytes],
        state_sub: zmq.Socket[bytes],
    ) -> None:
        """Server accepts new commands after heartbeat timeout recovery."""
        # Resume heartbeats
        resp = send_command(cmd_dealer, "heartbeat")
        assert resp["success"] is True

        # Set a new field — should work
        resp = send_command(
            cmd_dealer, "set_force_field",
            {
                "type": "spring_damper",
                "params": {"stiffness": 100, "damping": 5, "center": [0, 0, 0]},
            },
        )
        assert resp["success"] is True

        time.sleep(0.1)
        state = receive_state(state_sub)
        assert state.active_field == "spring_damper"


# ============================================================================
# Cleanup: always leave server in a safe state
# ============================================================================


def test_cleanup_revert_to_null(
    cmd_dealer: zmq.Socket[bytes],
) -> None:
    """Final test: revert to NullField so the handle is free-moving.

    This runs last (alphabetically after the test classes) to leave the
    server in a safe state regardless of which tests were selected.
    """
    send_command(cmd_dealer, "heartbeat")
    resp = send_command(
        cmd_dealer, "set_force_field",
        {"type": "null", "params": {}},
    )
    assert resp["success"] is True
