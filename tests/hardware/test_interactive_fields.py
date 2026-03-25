"""Interactive force field feel-tests.

Run with:  pytest tests/hardware/ -m interactive -v -s
Requires:  a running haptic server with the real delta.3 and a human operator.
"""

from __future__ import annotations

import time

import pytest
import zmq

from tests.hardware.conftest import send_command
from tests.hardware.heartbeat_keeper import heartbeat_keeper


def user_confirms(prompt: str) -> bool:
    """Ask the operator a yes/no question via terminal.

    Accepts:
    - y / yes  -> return True
    - n / no   -> return False
    - q / quit / abort / exit -> skip the current test
    Any other input will cause the prompt to be shown again.
    """
    while True:
        response = input(f"\n>>> {prompt} [y/n/q]: ").strip().lower()
        if response in ("y", "yes"):
            return True
        if response in ("n", "no"):
            return False
        if response in ("q", "quit", "abort", "exit"):
            pytest.skip("Operator aborted interactive feel-test from prompt")
        print("Please respond with 'y' or 'n' (or 'q' to abort).")


@pytest.fixture
def dealer(cmd_address: str, zmq_context: zmq.Context) -> zmq.Socket:  # type: ignore[type-arg]
    """Function-scoped DEALER socket for interactive tests."""
    sock = zmq_context.socket(zmq.DEALER)
    sock.setsockopt(zmq.RCVTIMEO, 3000)
    sock.connect(cmd_address)
    time.sleep(0.1)
    yield sock  # type: ignore[misc]
    # Revert to NullField on teardown so handle is free
    send_command(sock, "set_force_field", {"type": "null", "params": {}})
    sock.close()


# ---------------------------------------------------------------------------
# Interactive tests
# ---------------------------------------------------------------------------


@pytest.mark.hardware
@pytest.mark.interactive
class TestSpringDamperFeel:
    """Verify that the spring-damper field feels like a centering spring."""

    def test_light_spring(
        self,
        dealer: zmq.Socket,  # type: ignore[type-arg]
        cmd_address: str,
        zmq_context: zmq.Context,  # type: ignore[type-arg]
    ) -> None:
        with heartbeat_keeper(cmd_address, ctx=zmq_context):
            resp = send_command(dealer, "set_force_field", {
                "type": "spring_damper",
                "params": {"stiffness": 100, "damping": 5, "center": [0, 0, 0]},
            })
            assert resp["success"]

            print("\n--- Light spring (K=100, B=5, center=[0,0,0]) ---")
            print("The handle should gently pull toward the center of the workspace.")
            print("Moving away should feel like stretching a light rubber band.")

            assert user_confirms(
                "Does the handle pull gently toward center?"
            ), "Operator rejected light spring feel"

    def test_stiff_spring(
        self,
        dealer: zmq.Socket,  # type: ignore[type-arg]
        cmd_address: str,
        zmq_context: zmq.Context,  # type: ignore[type-arg]
    ) -> None:
        with heartbeat_keeper(cmd_address, ctx=zmq_context):
            resp = send_command(dealer, "set_force_field", {
                "type": "spring_damper",
                "params": {"stiffness": 800, "damping": 20, "center": [0, 0, 0]},
            })
            assert resp["success"]

            print("\n--- Stiff spring (K=800, B=20, center=[0,0,0]) ---")
            print("The handle should pull firmly toward center.")
            print("It should feel noticeably harder to displace than the light spring.")
            print("High damping should make it feel sluggish, not buzzy.")

            assert user_confirms(
                "Does the handle pull firmly toward center with heavy damping?"
            ), "Operator rejected stiff spring feel"

    def test_offset_center(
        self,
        dealer: zmq.Socket,  # type: ignore[type-arg]
        cmd_address: str,
        zmq_context: zmq.Context,  # type: ignore[type-arg]
    ) -> None:
        with heartbeat_keeper(cmd_address, ctx=zmq_context):
            resp = send_command(dealer, "set_force_field", {
                "type": "spring_damper",
                "params": {
                    "stiffness": 200,
                    "damping": 10,
                    "center": [0.03, 0.0, 0.0],
                },
            })
            assert resp["success"]

            print("\n--- Offset spring (K=200, B=10, center=[+30mm, 0, 0]) ---")
            print("The handle should pull toward a point offset to the RIGHT")
            print("(positive X) from the workspace center.")

            assert user_confirms(
                "Does the handle pull toward a point to the right of center?"
            ), "Operator rejected offset spring feel"


@pytest.mark.hardware
@pytest.mark.interactive
class TestConstantFieldFeel:
    """Verify that the constant field applies a steady directional force."""

    def test_downward_push(
        self,
        dealer: zmq.Socket,  # type: ignore[type-arg]
        cmd_address: str,
        zmq_context: zmq.Context,  # type: ignore[type-arg]
    ) -> None:
        with heartbeat_keeper(cmd_address, ctx=zmq_context):
            resp = send_command(dealer, "set_force_field", {
                "type": "constant",
                "params": {"force": [0, 0, -3.0]},
            })
            assert resp["success"]

            print("\n--- Constant field (F=[0, 0, -3N]) ---")
            print("You should feel a steady downward push on the handle.")
            print("The force should be constant regardless of position.")

            assert user_confirms(
                "Is there a steady downward push (~3N)?"
            ), "Operator rejected constant field feel"


@pytest.mark.hardware
@pytest.mark.interactive
class TestCartPendulumFeel:
    """Verify that the cart-pendulum field feels like a swinging weight."""

    def test_pendulum_swing(
        self,
        dealer: zmq.Socket,  # type: ignore[type-arg]
        cmd_address: str,
        zmq_context: zmq.Context,  # type: ignore[type-arg]
    ) -> None:
        with heartbeat_keeper(cmd_address, ctx=zmq_context):
            resp = send_command(dealer, "set_force_field", {
                "type": "cart_pendulum",
                "params": {
                    "pendulum_length": 0.6,
                    "ball_mass": 0.6,
                    "cup_mass": 2.4,
                    "damping": 0.05,
                },
            })
            assert resp["success"]

            print("\n--- Cart-pendulum (L=0.6, m_ball=0.6, m_cup=2.4, b=0.05) ---")
            print("Move the handle side to side. You should feel an inertial")
            print("resistance followed by a swinging weight that lags behind")
            print("your hand motion. Quick reversals should feel like the")
            print("'ball' swings to the opposite side.")
            print()
            print("Try holding still — oscillations should slowly decay.")

            assert user_confirms(
                "Does the handle feel like it has a pendulum weight attached?"
            ), "Operator rejected cart-pendulum feel"


@pytest.mark.hardware
@pytest.mark.interactive
class TestCompositeFieldFeel:
    """Verify that composite fields combine correctly."""

    def test_spring_plus_workspace_limits(
        self,
        dealer: zmq.Socket,  # type: ignore[type-arg]
        cmd_address: str,
        zmq_context: zmq.Context,  # type: ignore[type-arg]
    ) -> None:
        with heartbeat_keeper(cmd_address, ctx=zmq_context):
            resp = send_command(dealer, "set_force_field", {
                "type": "composite",
                "params": {
                    "fields": [
                        {
                            "type": "spring_damper",
                            "params": {
                                "stiffness": 150,
                                "damping": 8,
                                "center": [0, 0, 0],
                            },
                        },
                        {
                            "type": "workspace_limit",
                            "params": {
                                "x_min": -0.05, "x_max": 0.05,
                                "y_min": -0.05, "y_max": 0.05,
                                "z_min": -0.05, "z_max": 0.05,
                                "stiffness": 2000,
                                "damping": 20,
                            },
                        },
                    ],
                },
            })
            assert resp["success"]

            print("\n--- Composite: spring + workspace limits ---")
            print("You should feel a centering spring (K=150) inside a")
            print("50mm cube. Near the edges of the cube, you should hit")
            print("a stiff wall that prevents further movement.")

            assert user_confirms(
                "Do you feel a spring inside soft workspace walls?"
            ), "Operator rejected composite field feel"
