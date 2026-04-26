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
from collections.abc import Generator
from typing import Any

import pytest
import zmq

from hapticore.core.messages import Command
from hapticore.haptic import HapticClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def user_confirms(prompt: str) -> bool:
    """Ask the operator a yes/no question via terminal.

    Accepts:
    - y / yes  -> return True
    - n / no   -> return False
    - s / skip / q / quit -> skip the current test
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
        if response in ("s", "skip", "q", "quit"):
            pytest.skip("Operator skipped test before evaluation")
        print("Please respond with 'y' or 'n' (or 's/q' to skip).")


def _wait_for_enter_or_skip(msg: str) -> None:
    """Prompt the operator, waiting for Enter, or allow skipping the test.

    - Normal use: operator presses Enter to continue.
    - Skip inputs: ``s``, ``skip``, ``q``, or ``quit`` cause the test to be skipped.
    - Non-interactive stdin (non-TTY) or EOF also result in the test being skipped.
    """
    if not sys.stdin.isatty():
        pytest.skip("Interactive test requires a TTY (run with -s)")
    try:
        response = input(msg).strip().lower()
    except EOFError:
        pytest.skip("stdin closed — cannot prompt operator (run with -s)")
    if response in ("s", "skip", "q", "quit"):
        pytest.skip("Operator skipped test before evaluation")


def run_timed_evaluation(
    client: HapticClient,
    field_params: dict[str, Any],
    description: str,
    feel_instructions: str,
    prompt: str,
    countdown: int = 5,
    duration: int = 10,
) -> bool:
    """Timed evaluation flow for single-person remote testing.

    1. Show instructions and wait for the operator to press Enter.
    2. Countdown so the operator can walk to the device and grab the handle.
    3. Activate the field for *duration* seconds (heartbeat is kept alive by
       the HapticClient for the full fixture lifetime).
    4. Revert to NullField so the handle goes free.
    """
    print(f"\n--- {description} ---")
    print("What to feel for:")
    print(f"  {feel_instructions}")
    _wait_for_enter_or_skip("\nPress Enter to start countdown (or 's/q' to skip)... ")

    # Countdown
    for i in range(countdown, 0, -1):
        print(f"  {i}...")
        time.sleep(1)
    print("  GO — field is active\n")

    resp = client.send_command(Command(
        command_id="", method="set_force_field", params=field_params,
    ))
    assert resp.success, f"set_force_field failed: {resp}"

    for remaining in range(duration, 0, -1):
        print(f"  {remaining}s remaining...", end="\r", flush=True)
        time.sleep(1)

    revert = client.send_command(Command(
        command_id="", method="set_force_field",
        params={"type": "null", "params": {}},
    ))
    if not revert.success:
        print(f"\n  Warning: NullField revert returned {revert}")

    print("  Field deactivated, handle is free.            ")
    return user_confirms(prompt)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(
    pub_address: str,
    cmd_address: str,
    zmq_context: zmq.Context,  # type: ignore[type-arg]
) -> Generator[HapticClient, None, None]:
    """Function-scoped HapticClient for interactive tests.

    On teardown, best-effort revert to NullField so the handle is free.
    The client's background heartbeat thread keeps the server's watchdog
    satisfied for the full duration of each test.
    """
    with HapticClient(
        pub_address, cmd_address, context=zmq_context,
    ) as c:
        try:
            yield c
        finally:
            # Best-effort revert to NullField on teardown so handle is free.
            try:
                c.send_command(Command(
                    command_id="",
                    method="set_force_field",
                    params={"type": "null", "params": {}},
                ))
            except zmq.ZMQError:
                pass


@pytest.fixture
def countdown(request: pytest.FixtureRequest) -> int:
    """Countdown seconds before field activation (``--countdown``)."""
    return int(request.config.getoption("--countdown"))


@pytest.fixture
def duration(request: pytest.FixtureRequest) -> int:
    """Evaluation window duration in seconds (``--duration``)."""
    return int(request.config.getoption("--duration"))


# ---------------------------------------------------------------------------
# Interactive tests
# ---------------------------------------------------------------------------


@pytest.mark.hardware
@pytest.mark.interactive
class TestSpringDamperFeel:
    """Verify that the spring-damper field feels like a centering spring."""

    def test_light_spring(
        self,
        client: HapticClient,
        countdown: int,
        duration: int,
    ) -> None:
        assert run_timed_evaluation(client,
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
        client: HapticClient,
        countdown: int,
        duration: int,
    ) -> None:
        assert run_timed_evaluation(client,
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
        client: HapticClient,
        countdown: int,
        duration: int,
    ) -> None:
        assert run_timed_evaluation(client,
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
        client: HapticClient,
        countdown: int,
        duration: int,
    ) -> None:
        assert run_timed_evaluation(client,
            field_params={
                "type": "constant",
                "params": {"force": [0, -3.0, 0]},
            },
            description="Constant field (F=[0, -3N, 0])",
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
class TestCompositeFieldFeel:
    """Verify that composite fields combine correctly."""

    def test_spring_plus_workspace_limits(
        self,
        client: HapticClient,
        countdown: int,
        duration: int,
    ) -> None:
        assert run_timed_evaluation(client,
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


@pytest.mark.hardware
@pytest.mark.interactive
class TestChannelFeelPlane:
    """Feel-test: constrain to a horizontal plane (free X/Y, hold Z=0)."""

    def test_constrained_to_plane(
        self,
        client: HapticClient,
        countdown: int,
        duration: int,
    ) -> None:
        assert run_timed_evaluation(client,
            field_params={
                "type": "channel",
                "params": {
                    "axes": [2],
                    "stiffness": 800,
                    "damping": 15,
                    "center": [0, 0, 0],
                },
            },
            description="Channel: horizontal plane constraint (Z=0)",
            feel_instructions=(
                "Move the handle freely in X and Y — there should be no "
                "resistance. Pushing up or down (Z axis) should feel a "
                "smooth spring-damper restoring force pulling back toward Z=0."
            ),
            prompt="Does the handle feel free in X/Y but constrained in Z?",
            countdown=countdown,
            duration=duration,
        ), "Operator rejected channel plane feel"


@pytest.mark.hardware
@pytest.mark.interactive
class TestChannelFeelLine:
    """Feel-test: constrain to a horizontal line (free X, hold Y=0 and Z=0)."""

    def test_constrained_to_line(
        self,
        client: HapticClient,
        countdown: int,
        duration: int,
    ) -> None:
        assert run_timed_evaluation(client,
            field_params={
                "type": "channel",
                "params": {
                    "axes": [1, 2],
                    "stiffness": 800,
                    "damping": 15,
                    "center": [0, 0, 0],
                },
            },
            description="Channel: horizontal line constraint (X-axis only)",
            feel_instructions=(
                "The handle should slide freely left/right (X axis). "
                "Pushing up/down (Y) or forward/back (Z) should feel a "
                "restoring force pulling back toward the X-axis line."
            ),
            prompt="Does the handle slide freely along X but resist Y/Z motion?",
            countdown=countdown,
            duration=duration,
        ), "Operator rejected channel line feel"


@pytest.mark.hardware
@pytest.mark.interactive
class TestCartPendulumFeel:
    """Verify that the cart-pendulum field feels like a swinging weight."""

    def test_inertial_field(
        self,
        client: HapticClient,
        countdown: int,
        duration: int,
    ) -> None:
        assert run_timed_evaluation(client,
            field_params={
                "type": "cart_pendulum",
                "params": {
                    "pendulum_length": 0.6,
                    "ball_mass": 0.0001,
                    "cup_mass": 2.4,
                    "angular_damping": 0.05,
                    "coupling_stiffness": 2000.0,
                    "coupling_damping": 2.0,
                },
            },
            description="Cart-pendulum with little ball mass (L=0.6, m_ball=0.0001, m_cup=2.4, K_vc=2000, B_vc=2)",
            feel_instructions=(
                "Move the handle side to side. You should feel inertial resistance "
                "when accelerating (the simulated cup lags behind your hand). "
                "The handle should NOT buzz or oscillate on its own."
            ),
            prompt="Does the handle feel like it has weight to it?",
            countdown=countdown,
            duration=duration,
        ), "Operator rejected inertial-only cart-pendulum feel"
    def test_pendulum_swing(
        self,
        client: HapticClient,
        countdown: int,
        duration: int,
    ) -> None:
        assert run_timed_evaluation(client,
            field_params={
                "type": "cart_pendulum",
                "params": {
                    "pendulum_length": 0.6,
                    "ball_mass": 0.6,
                    "cup_mass": 1.4,
                    "angular_damping": 0.05,
                    "coupling_stiffness": 2000.0,
                    "coupling_damping": 2.0,
                },
            },
            description="Cart-pendulum virtual coupling (L=0.6, m_ball=0.6, m_cup=1.4, K_vc=2000, B_vc=2)",
            feel_instructions=(
                "Move the handle side to side. You should feel inertial resistance "
                "when accelerating (the simulated cup lags behind your hand). "
                "A swinging weight should lag behind your hand motion. Quick reversals "
                "should feel like the 'ball' swings to the opposite side. "
                "Try holding still — oscillations should slowly decay. "
                "The handle should NOT buzz or oscillate on its own."
            ),
            prompt="Does the handle feel like it has a pendulum weight attached?",
            countdown=countdown,
            duration=duration,
        ), "Operator rejected cart-pendulum pendulum-swing feel"

    def test_composite_pendulum_field(
        self,
        client: HapticClient,
        countdown: int,
        duration: int,
    ) -> None:
        assert run_timed_evaluation(client,
            field_params={
                "type": "composite",
                "params": {
                    "fields": [
                        {
                            "type": "cart_pendulum",
                            "params": {
                                "pendulum_length": 0.6,
                                "ball_mass": 0.6,
                                "cup_mass": 2.4,
                                "angular_damping": 0.05,
                                "coupling_stiffness": 2000.0,
                                "coupling_damping": 2.0,
                            },
                        },
                        {
                            "type": "workspace_limit",
                            "params": {
                                "bounds": {
                                    "x": [-0.1, 0.1],
                                    "y": [-0.1, 0.1],
                                    "z": [-0.1, 0.1],
                                },
                                "stiffness": 2000,
                                "damping": 20,
                            },
                        },
                        {
                            "type": "channel",
                            "params": {
                                "axes": [1, 2],
                                "stiffness": 2000,
                                "damping": 15,
                                "center": [0, 0, 0],
                            },
                        },
                    ],
                },
            },
            description="Composite: cart-pendulum + workspace limits + channel constraint",
            feel_instructions=(
                "Move the handle around. You should feel stiff resistance outside of horizontal movement "
                "and near the edges of the workspace. Within the free horizontal channel, you should feel inertial resistance."
                " A swinging weight should lag behind your hand motion. Quick reversals "
                "should feel like the 'ball' swings to the opposite side. "
                "Try holding still — oscillations should slowly decay. "
                "The handle should NOT buzz or oscillate on its own."
            ),
            prompt="Does the handle feel like the above description?",
            countdown=countdown,
            duration=duration,
        ), "Operator rejected composite cart-pendulum + constraints feel"