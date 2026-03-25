"""Interactive force field feel-tests.

Run with:  pytest tests/hardware/ -m interactive -v -s
Requires:  a running haptic server with the real delta.3 and a human operator.

Supports remote operation (robot in a different room) via a timed evaluation
flow: the operator reads instructions, presses Enter, walks to the device,
feels the field for a fixed duration, then walks back and reports the result.

Timing options::

    pytest tests/hardware/ -m interactive -v -s --countdown=5 --duration=15
"""

from __future__ import annotations

import sys
import time
from typing import Any

import pytest
import zmq

from .conftest import send_command
from .heartbeat_keeper import heartbeat_keeper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def user_confirms(prompt: str) -> bool:
    """Ask the operator a yes/no question via terminal.

    Accepts:
    - y / yes  -> return True
    - n / no   -> return False
    - q / quit / abort / exit -> skip the current test
    Any other input will cause the prompt to be shown again.

    Skips automatically when stdin is not a TTY (e.g. CI or missing ``-s``).
    """
    if not sys.stdin.isatty():
        pytest.skip("Interactive test requires a TTY (run with -s)")
    while True:
        try:
            response = input(f"\n>>> {prompt} [y/n/q]: ").strip().lower()
        except EOFError:
            pytest.skip("stdin closed — cannot prompt operator (run with -s)")
        if response in ("y", "yes"):
            return True
        if response in ("n", "no"):
            return False
        if response in ("q", "quit", "abort", "exit"):
            pytest.skip("Operator aborted interactive feel-test from prompt")
        print("Please respond with 'y' or 'n' (or 'q' to abort).")


def _wait_for_enter(msg: str) -> None:
    """Prompt and wait for Enter, handling non-TTY and EOF gracefully."""
    if not sys.stdin.isatty():
        pytest.skip("Interactive test requires a TTY (run with -s)")
    try:
        input(msg)
    except EOFError:
        pytest.skip("stdin closed — cannot prompt operator (run with -s)")


def run_timed_evaluation(
    dealer: zmq.Socket[bytes],
    cmd_address: str,
    zmq_context: zmq.Context[Any],
    field_params: dict[str, Any],
    description: str,
    feel_instructions: str,
    prompt: str,
    countdown: float = 5.0,
    duration: float = 10.0,
) -> bool:
    """Timed evaluation flow for single-person remote testing.

    1. Show instructions and wait for the operator to press Enter.
    2. Countdown so the operator can walk to the device and grab the handle.
    3. Activate the field with a heartbeat for *duration* seconds.
    4. Revert to NullField (handle goes free).
    5. Ask the operator to confirm or reject the feel.
    """
    print(f"\n--- {description} ---")
    print("What to feel for:")
    print(f"  {feel_instructions}")
    _wait_for_enter("\nPress Enter when ready to start the countdown...")

    # Countdown
    for i in range(int(countdown), 0, -1):
        print(f"  {i}...")
        time.sleep(1)
    print("  GO — field is active\n")

    # Activate field with heartbeat
    with heartbeat_keeper(cmd_address, ctx=zmq_context):
        resp = send_command(dealer, "set_force_field", field_params)
        assert resp["success"], f"set_force_field failed: {resp}"

        for remaining in range(int(duration), 0, -1):
            print(f"  {remaining}s remaining...", end="\r", flush=True)
            time.sleep(1)

    print("  Done — field deactivated, handle is free.      ")

    # Revert to NullField so the handle is free while the operator answers
    try:
        send_command(dealer, "set_force_field", {"type": "null", "params": {}})
    except (TimeoutError, zmq.ZMQError):
        pass

    return user_confirms(prompt)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dealer(cmd_address: str, zmq_context: zmq.Context) -> zmq.Socket:  # type: ignore[type-arg]
    """Function-scoped DEALER socket for interactive tests."""
    sock = zmq_context.socket(zmq.DEALER)
    sock.setsockopt(zmq.RCVTIMEO, 3000)
    sock.connect(cmd_address)
    time.sleep(0.1)
    try:
        yield sock  # type: ignore[misc]
    finally:
        # Best-effort revert to NullField on teardown so handle is free.
        # If the server is down/unresponsive, send_command may raise TimeoutError
        # or ZMQError; we ignore this so teardown errors don't mask real test failures.
        try:
            send_command(sock, "set_force_field", {"type": "null", "params": {}})
        except (TimeoutError, zmq.ZMQError):
            pass
        sock.close(linger=0)


@pytest.fixture
def countdown(request: pytest.FixtureRequest) -> float:
    """Countdown seconds before field activation (``--countdown``)."""
    return float(request.config.getoption("--countdown"))


@pytest.fixture
def duration(request: pytest.FixtureRequest) -> float:
    """Evaluation window duration in seconds (``--duration``)."""
    return float(request.config.getoption("--duration"))


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
        countdown: float,
        duration: float,
    ) -> None:
        assert run_timed_evaluation(
            dealer, cmd_address, zmq_context,
            field_params={
                "type": "spring_damper",
                "params": {"stiffness": 100, "damping": 5, "center": [0, 0, 0]},
            },
            description="Light spring (K=100, B=5, center=[0,0,0])",
            feel_instructions=(
                "The handle should gently pull toward the center of the workspace. "
                "Moving away should feel like stretching a light rubber band."
            ),
            prompt="Does the handle pull gently toward center?",
            countdown=countdown,
            duration=duration,
        ), "Operator rejected light spring feel"

    def test_stiff_spring(
        self,
        dealer: zmq.Socket,  # type: ignore[type-arg]
        cmd_address: str,
        zmq_context: zmq.Context,  # type: ignore[type-arg]
        countdown: float,
        duration: float,
    ) -> None:
        assert run_timed_evaluation(
            dealer, cmd_address, zmq_context,
            field_params={
                "type": "spring_damper",
                "params": {"stiffness": 800, "damping": 20, "center": [0, 0, 0]},
            },
            description="Stiff spring (K=800, B=20, center=[0,0,0])",
            feel_instructions=(
                "The handle should pull firmly toward center. "
                "It should feel noticeably harder to displace than the light spring. "
                "High damping should make it feel sluggish, not buzzy."
            ),
            prompt="Does the handle pull firmly toward center with heavy damping?",
            countdown=countdown,
            duration=duration,
        ), "Operator rejected stiff spring feel"

    def test_offset_center(
        self,
        dealer: zmq.Socket,  # type: ignore[type-arg]
        cmd_address: str,
        zmq_context: zmq.Context,  # type: ignore[type-arg]
        countdown: float,
        duration: float,
    ) -> None:
        assert run_timed_evaluation(
            dealer, cmd_address, zmq_context,
            field_params={
                "type": "spring_damper",
                "params": {
                    "stiffness": 200,
                    "damping": 10,
                    "center": [0.03, 0.0, 0.0],
                },
            },
            description="Offset spring (K=200, B=10, center=[+30mm, 0, 0])",
            feel_instructions=(
                "The handle should pull toward a point offset from the workspace "
                "center (positive X direction)."
            ),
            prompt="Does the handle pull toward a point offset from center?",
            countdown=countdown,
            duration=duration,
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
        countdown: float,
        duration: float,
    ) -> None:
        assert run_timed_evaluation(
            dealer, cmd_address, zmq_context,
            field_params={
                "type": "constant",
                "params": {"force": [0, 0, -3.0]},
            },
            description="Constant field (F=[0, 0, -3N])",
            feel_instructions=(
                "You should feel a steady downward push on the handle. "
                "The force should be constant regardless of position."
            ),
            prompt="Is there a steady downward push (~3N)?",
            countdown=countdown,
            duration=duration,
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
        countdown: float,
        duration: float,
    ) -> None:
        assert run_timed_evaluation(
            dealer, cmd_address, zmq_context,
            field_params={
                "type": "cart_pendulum",
                "params": {
                    "pendulum_length": 0.6,
                    "ball_mass": 0.6,
                    "cup_mass": 2.4,
                    "angular_damping": 0.05,
                },
            },
            description="Cart-pendulum (L=0.6, m_ball=0.6, m_cup=2.4, b=0.05)",
            feel_instructions=(
                "Move the handle side to side. You should feel an inertial "
                "resistance followed by a swinging weight that lags behind "
                "your hand motion. Quick reversals should feel like the "
                "'ball' swings to the opposite side. "
                "Try holding still — oscillations should slowly decay."
            ),
            prompt="Does the handle feel like it has a pendulum weight attached?",
            countdown=countdown,
            duration=duration,
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
        countdown: float,
        duration: float,
    ) -> None:
        assert run_timed_evaluation(
            dealer, cmd_address, zmq_context,
            field_params={
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
                                "bounds": {
                                    "x": [-0.05, 0.05],
                                    "y": [-0.05, 0.05],
                                    "z": [-0.05, 0.05],
                                },
                                "stiffness": 2000,
                                "damping": 20,
                            },
                        },
                    ],
                },
            },
            description="Composite: spring + workspace limits",
            feel_instructions=(
                "You should feel a centering spring (K=150) inside a "
                "50mm cube. Near the edges of the cube, you should hit "
                "a stiff wall that prevents further movement."
            ),
            prompt="Do you feel a spring inside soft workspace walls?",
            countdown=countdown,
            duration=duration,
        ), "Operator rejected composite field feel"
