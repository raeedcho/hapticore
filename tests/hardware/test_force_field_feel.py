"""User-in-the-loop (UITL) tests for force field feel verification.

These tests require a human operator to interact with the haptic device and
confirm whether each force field feels physically correct.  They are NEVER run
in automated CI — they exist to catch tuning regressions that look fine in
numbers but feel wrong in the hand.

Run with (the ``-s`` flag is required so prompts are visible in the terminal):

    pytest tests/hardware/test_force_field_feel.py -m "hardware and uitl" -v -s

Prerequisites:
    1. The delta.3 is powered on and connected via USB.
    2. The haptic server is already running:
           ./haptic_server                              # default IPC
           ./haptic_server --pub-address tcp://*:5555 \\
                           --cmd-address tcp://*:5556   # TCP (cross-machine)

Override server addresses via environment variables:
    HAPTICORE_PUB_ADDRESS=tcp://rigmachine:5555
    HAPTICORE_CMD_ADDRESS=tcp://rigmachine:5556

Answering prompts:
    y / yes  — the field feels correct; the test passes.
    n / no   — something feels wrong; the test fails with a descriptive message.
    s / skip — you are unsure or cannot evaluate right now; the test is skipped.
"""

from __future__ import annotations

import pytest
import zmq

from .conftest import drain_and_receive_state, send_command

pytestmark = [pytest.mark.hardware, pytest.mark.uitl]


# ---------------------------------------------------------------------------
# Helper: interactive prompt
# ---------------------------------------------------------------------------

def _ask(prompt: str) -> str:
    """Print *prompt* and wait for the operator to type y / n / s.

    Returns
    -------
    "y"
        Operator confirmed the field feels correct.
    "n"
        Operator reported the field does *not* feel correct.
    "s"
        Operator skipped the check (answer ``s`` or ``skip``).
    """
    print()  # blank line for readability
    while True:
        raw = input(f"  {prompt}\n  Feel correct? [y/n/s]: ").strip().lower()
        if raw in ("y", "yes"):
            return "y"
        if raw in ("n", "no"):
            return "n"
        if raw in ("s", "skip"):
            return "s"
        print("  Please enter 'y' (yes), 'n' (no), or 's' (skip).")


def _confirm(
    prompt: str,
    fail_msg: str,
) -> None:
    """Ask the operator and either pass, skip (``pytest.skip``), or fail.

    Parameters
    ----------
    prompt:
        Instructions shown to the operator before they answer.
    fail_msg:
        Message passed to ``pytest.fail`` when the operator answers "n".
    """
    answer = _ask(prompt)
    if answer == "s":
        pytest.skip("Operator chose to skip this check.")
    if answer == "n":
        pytest.fail(fail_msg)
    # "y" → test passes


# ---------------------------------------------------------------------------
# Shared autouse fixture: send a heartbeat before every test so the server
# does not time out mid-interaction, then revert to the null field afterwards
# so the handle is free between tests.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _heartbeat_and_cleanup(
    cmd_dealer: zmq.Socket[bytes],
) -> None:
    """Send a heartbeat before and revert to null field after each test.

    The initial heartbeat also acts as a connectivity check — if the server
    is not running, send_command will raise TimeoutError with a clear message
    before any test-specific setup runs.
    """
    resp = send_command(cmd_dealer, "heartbeat")
    assert resp["success"] is True, "Heartbeat failed — is the haptic server running?"
    yield
    send_command(cmd_dealer, "heartbeat")
    send_command(cmd_dealer, "set_force_field", {"type": "null", "params": {}})


# ---------------------------------------------------------------------------
# Spring-damper field
# ---------------------------------------------------------------------------

class TestSpringDamperFeel:
    """Interactive feel verification for the spring-damper force field."""

    def test_soft_spring_center_pull(
        self,
        cmd_dealer: zmq.Socket[bytes],
        state_sub: zmq.Socket[bytes],
    ) -> None:
        """Soft spring (200 N/m) centred at origin — restoring pull toward [0,0,0].

        Instructions
        ------------
        1. A gentle spring centred at the robot's workspace origin will be activated.
        2. Move the handle a few centimetres away from centre.
        3. Release it — it should drift back toward centre.
        4. Hold it displaced — you should feel a steady, proportional pull.
        """
        resp = send_command(
            cmd_dealer, "set_force_field",
            {
                "type": "spring_damper",
                "params": {"stiffness": 200.0, "damping": 5.0, "center": [0, 0, 0]},
            },
        )
        assert resp["success"] is True
        drain_and_receive_state(state_sub, settle_time=0.1)

        _confirm(
            "SOFT SPRING (200 N/m, damping 5). "
            "Move the handle away from centre and release. "
            "Does it pull gently back toward centre?",
            fail_msg=(
                "Operator reported soft spring does not feel correct. "
                "Check stiffness/damping parameters in SpringDamperField."
            ),
        )

    def test_stiff_spring_center_pull(
        self,
        cmd_dealer: zmq.Socket[bytes],
        state_sub: zmq.Socket[bytes],
    ) -> None:
        """Stiff spring (1000 N/m) centred at origin — stronger restoring force.

        Instructions
        ------------
        1. A stiff spring centred at workspace origin will be activated.
        2. Move the handle slightly (1–2 cm) away from centre.
        3. The resistance should feel noticeably stronger than the soft spring.
        4. Force is clamped at 20 N — do not push hard into the spring.
        """
        resp = send_command(
            cmd_dealer, "set_force_field",
            {
                "type": "spring_damper",
                "params": {"stiffness": 1000.0, "damping": 10.0, "center": [0, 0, 0]},
            },
        )
        assert resp["success"] is True
        drain_and_receive_state(state_sub, settle_time=0.1)

        _confirm(
            "STIFF SPRING (1000 N/m, damping 10). "
            "Displace handle 1-2 cm. "
            "Does it feel noticeably stiffer than the soft spring (200 N/m)?",
            fail_msg=(
                "Operator reported stiff spring does not feel stiffer than soft spring. "
                "Check stiffness scaling in SpringDamperField."
            ),
        )

    def test_spring_with_offset_center(
        self,
        cmd_dealer: zmq.Socket[bytes],
        state_sub: zmq.Socket[bytes],
    ) -> None:
        """Spring centred 5 cm to the right (+X direction) of origin.

        Instructions
        ------------
        1. A spring centred at [+0.05, 0, 0] will be activated.
        2. The equilibrium point is 5 cm to the right of the origin.
        3. When you hold the handle at the workspace origin, you should feel
           a pull toward the right (+X).
        4. Moving 5 cm to the right should relieve all restoring force.
        """
        resp = send_command(
            cmd_dealer, "set_force_field",
            {
                "type": "spring_damper",
                "params": {"stiffness": 300.0, "damping": 5.0, "center": [0.05, 0, 0]},
            },
        )
        assert resp["success"] is True
        drain_and_receive_state(state_sub, settle_time=0.1)

        _confirm(
            "OFFSET SPRING (centre = [+5 cm, 0, 0]). "
            "At the workspace origin you should feel a pull in the +X direction. "
            "Moving ~5 cm in +X should feel neutral. "
            "Does the equilibrium feel displaced to the right?",
            fail_msg=(
                "Operator reported spring center offset does not feel correct. "
                "Check that SpringDamperField uses the 'center' parameter."
            ),
        )

    def test_damping_slows_motion(
        self,
        cmd_dealer: zmq.Socket[bytes],
        state_sub: zmq.Socket[bytes],
    ) -> None:
        """Damping-only field (stiffness=0) — viscous drag without a restoring force.

        Instructions
        ------------
        1. A pure damper (no spring) will be activated.
        2. There should be NO preferred resting position.
        3. Moving slowly should feel nearly free.
        4. Moving quickly should feel like moving through thick fluid.
        """
        resp = send_command(
            cmd_dealer, "set_force_field",
            {
                "type": "spring_damper",
                "params": {"stiffness": 0.0, "damping": 30.0, "center": [0, 0, 0]},
            },
        )
        assert resp["success"] is True
        drain_and_receive_state(state_sub, settle_time=0.1)

        _confirm(
            "PURE DAMPER (stiffness=0, damping=30). "
            "Move the handle slowly then quickly. "
            "Does fast motion feel like moving through thick fluid "
            "with no spring-back toward any fixed point?",
            fail_msg=(
                "Operator reported pure damper does not feel correct. "
                "Check that stiffness=0 produces no restoring force."
            ),
        )


# ---------------------------------------------------------------------------
# Constant field
# ---------------------------------------------------------------------------

class TestConstantFieldFeel:
    """Interactive feel verification for the constant force field."""

    def test_constant_force_in_x(
        self,
        cmd_dealer: zmq.Socket[bytes],
        state_sub: zmq.Socket[bytes],
    ) -> None:
        """Constant force of 3 N in the +X direction.

        Instructions
        ------------
        1. A steady 3 N force in the +X direction will be activated.
        2. You should feel a constant push in one direction regardless of where
           you move the handle.
        3. The force should feel the same everywhere in the workspace — there
           should be no preferred position or spring-like behaviour.
        """
        resp = send_command(
            cmd_dealer, "set_force_field",
            {"type": "constant", "params": {"force": [3.0, 0.0, 0.0]}},
        )
        assert resp["success"] is True
        drain_and_receive_state(state_sub, settle_time=0.1)

        _confirm(
            "CONSTANT FORCE (+3 N in X). "
            "Move the handle around the workspace. "
            "Do you feel a constant push in one direction that does not change "
            "with position?",
            fail_msg=(
                "Operator reported constant force field does not feel constant. "
                "Check ConstantField::compute() — force should not depend on pos/vel."
            ),
        )

    def test_constant_force_in_z(
        self,
        cmd_dealer: zmq.Socket[bytes],
        state_sub: zmq.Socket[bytes],
    ) -> None:
        """Constant downward force of 2 N in the -Z direction.

        Instructions
        ------------
        1. A steady 2 N downward force will be activated.
        2. The handle should feel heavier — as if gravity increased slightly.
        3. Moving up (in +Z) should feel like lifting additional weight.
        """
        resp = send_command(
            cmd_dealer, "set_force_field",
            {"type": "constant", "params": {"force": [0.0, 0.0, -2.0]}},
        )
        assert resp["success"] is True
        drain_and_receive_state(state_sub, settle_time=0.1)

        _confirm(
            "CONSTANT DOWNWARD FORCE (-2 N in Z). "
            "Does the handle feel heavier? "
            "Does lifting it (+Z) feel like lifting added weight?",
            fail_msg=(
                "Operator reported downward constant force does not feel correct. "
                "Check ConstantField with force=[0,0,-2]."
            ),
        )


# ---------------------------------------------------------------------------
# Workspace limit field
# ---------------------------------------------------------------------------

class TestWorkspaceLimitFieldFeel:
    """Interactive feel verification for the workspace limit force field."""

    def test_wall_at_boundary(
        self,
        cmd_dealer: zmq.Socket[bytes],
        state_sub: zmq.Socket[bytes],
    ) -> None:
        """Workspace limit field — stiff wall at ±10 cm in all axes.

        Instructions
        ------------
        1. A workspace limit field with ±10 cm bounds will be activated.
        2. Inside the workspace (within 10 cm of centre) you should feel no forces.
        3. Push toward any edge — at 10 cm you should hit a stiff wall.
        4. The wall should be firm and feel like a hard physical boundary.
        """
        resp = send_command(
            cmd_dealer, "set_force_field",
            {
                "type": "workspace_limit",
                "params": {
                    "bounds_min": [-0.10, -0.10, -0.10],
                    "bounds_max": [0.10, 0.10, 0.10],
                    "stiffness": 2000.0,
                    "damping": 10.0,
                },
            },
        )
        assert resp["success"] is True
        drain_and_receive_state(state_sub, settle_time=0.1)

        _confirm(
            "WORKSPACE LIMIT (±10 cm, stiffness=2000). "
            "Move freely near centre — no forces. "
            "Push toward any edge past 10 cm. "
            "Do you feel a hard wall at the boundary?",
            fail_msg=(
                "Operator reported workspace limit does not feel like a hard wall. "
                "Check WorkspaceLimitField stiffness and boundary logic."
            ),
        )

    def test_small_workspace_is_confining(
        self,
        cmd_dealer: zmq.Socket[bytes],
        state_sub: zmq.Socket[bytes],
    ) -> None:
        """Very small workspace (±3 cm) — you should hit boundaries quickly.

        Instructions
        ------------
        1. A very tight workspace limit of ±3 cm will be activated.
        2. Even small movements should quickly encounter the boundary wall.
        3. The workspace should feel noticeably smaller than the ±10 cm version.
        """
        resp = send_command(
            cmd_dealer, "set_force_field",
            {
                "type": "workspace_limit",
                "params": {
                    "bounds_min": [-0.03, -0.03, -0.03],
                    "bounds_max": [0.03, 0.03, 0.03],
                    "stiffness": 2000.0,
                    "damping": 10.0,
                },
            },
        )
        assert resp["success"] is True
        drain_and_receive_state(state_sub, settle_time=0.1)

        _confirm(
            "TIGHT WORKSPACE LIMIT (±3 cm, stiffness=2000). "
            "Do you hit the boundary wall much sooner than with ±10 cm? "
            "Does the workspace feel tightly confined?",
            fail_msg=(
                "Operator reported tight workspace does not feel more confined. "
                "Check that bounds_min/bounds_max are respected by WorkspaceLimitField."
            ),
        )


# ---------------------------------------------------------------------------
# Cart-pendulum field
# ---------------------------------------------------------------------------

class TestCartPendulumFieldFeel:
    """Interactive feel verification for the cart-pendulum force field."""

    def test_pendulum_dynamics_at_rest(
        self,
        cmd_dealer: zmq.Socket[bytes],
        state_sub: zmq.Socket[bytes],
    ) -> None:
        """Cart-pendulum — handle is the cart; pendulum bob hangs underneath.

        Instructions
        ------------
        1. The cart-pendulum field will be activated (default parameters).
        2. The handle represents a cart on a track.  A pendulum is attached to
           the cart and hangs down (initially at rest).
        3. When the pendulum is balanced, the handle should feel roughly neutral.
        4. Accelerate the handle quickly — you should feel the pendulum's
           inertia resisting the motion (the bob lags behind the cart).
        5. Moving the handle back and forth gently should feel like sloshing.
        """
        resp = send_command(
            cmd_dealer, "set_force_field",
            {
                "type": "cart_pendulum",
                "params": {
                    "ball_mass": 0.6,
                    "cup_mass": 2.4,
                    "pendulum_length": 0.3,
                    "gravity": 9.81,
                    "angular_damping": 0.1,
                },
            },
        )
        assert resp["success"] is True
        drain_and_receive_state(state_sub, settle_time=0.2)

        _confirm(
            "CART-PENDULUM (default params). "
            "Accelerate the handle quickly left/right. "
            "Do you feel the pendulum bob's inertia pulling back against fast motion? "
            "Does gentle back-and-forth feel like sloshing liquid?",
            fail_msg=(
                "Operator reported cart-pendulum does not exhibit expected dynamics. "
                "Check CartPendulumField physics integration."
            ),
        )

    def test_pendulum_spill_detection(
        self,
        cmd_dealer: zmq.Socket[bytes],
        state_sub: zmq.Socket[bytes],
    ) -> None:
        """Cart-pendulum — rapid acceleration should trigger spill (field zeroes out).

        Instructions
        ------------
        1. The cart-pendulum field will be activated with a lower spill threshold.
        2. Jerk the handle sharply — the pendulum should tip past 90° and spill.
        3. After spilling, forces should drop to near zero (spilled state).
        4. The field_state in the state stream will show ``spilled: true``.
        """
        resp = send_command(
            cmd_dealer, "set_force_field",
            {
                "type": "cart_pendulum",
                "params": {
                    "ball_mass": 0.6,
                    "cup_mass": 2.4,
                    "pendulum_length": 0.3,
                    "gravity": 9.81,
                    "angular_damping": 0.05,
                    "spill_threshold": 0.8,  # ~46° — easy to spill
                },
            },
        )
        assert resp["success"] is True
        drain_and_receive_state(state_sub, settle_time=0.2)

        _confirm(
            "CART-PENDULUM with low spill threshold (0.8 rad ≈ 46°). "
            "Jerk the handle sharply. "
            "Does the pendulum spill (forces drop to zero)?",
            fail_msg=(
                "Operator reported pendulum spill did not occur or forces did not "
                "drop after spilling. Check CartPendulumField spill_threshold logic."
            ),
        )


# ---------------------------------------------------------------------------
# Composite field
# ---------------------------------------------------------------------------

class TestCompositeFieldFeel:
    """Interactive feel verification for composite (layered) force fields."""

    def test_spring_plus_workspace_limit(
        self,
        cmd_dealer: zmq.Socket[bytes],
        state_sub: zmq.Socket[bytes],
    ) -> None:
        """Composite: soft spring + workspace limit — spring inside a box.

        Instructions
        ------------
        1. A composite field combining a soft spring (200 N/m at origin) and a
           workspace limit (±8 cm) will be activated.
        2. Near centre you should feel the spring pulling you back.
        3. Approaching ±8 cm you should additionally feel the hard wall.
        4. Both forces should add together near the boundary.
        """
        resp = send_command(
            cmd_dealer, "set_force_field",
            {
                "type": "composite",
                "params": {
                    "fields": [
                        {
                            "type": "spring_damper",
                            "params": {
                                "stiffness": 200.0,
                                "damping": 5.0,
                                "center": [0, 0, 0],
                            },
                        },
                        {
                            "type": "workspace_limit",
                            "params": {
                                "bounds_min": [-0.08, -0.08, -0.08],
                                "bounds_max": [0.08, 0.08, 0.08],
                                "stiffness": 2000.0,
                                "damping": 10.0,
                            },
                        },
                    ]
                },
            },
        )
        assert resp["success"] is True
        drain_and_receive_state(state_sub, settle_time=0.1)

        _confirm(
            "COMPOSITE: spring (200 N/m) + workspace limit (±8 cm). "
            "Near centre: feel spring. Near ±8 cm: feel hard wall. "
            "Do both forces feel active simultaneously?",
            fail_msg=(
                "Operator reported composite spring+limit does not feel like "
                "combined forces. Check CompositeField summation logic."
            ),
        )

    def test_constant_bias_plus_spring(
        self,
        cmd_dealer: zmq.Socket[bytes],
        state_sub: zmq.Socket[bytes],
    ) -> None:
        """Composite: spring + constant bias — spring with a shifted equilibrium.

        Instructions
        ------------
        1. A spring (200 N/m at origin) combined with a constant +X bias (2 N)
           will be activated.
        2. The effective equilibrium should shift slightly in the +X direction
           (bias pushes right; spring pulls left; they cancel at ~1 cm right).
        3. You should notice the equilibrium is NOT at the workspace centre.
        """
        resp = send_command(
            cmd_dealer, "set_force_field",
            {
                "type": "composite",
                "params": {
                    "fields": [
                        {
                            "type": "spring_damper",
                            "params": {
                                "stiffness": 200.0,
                                "damping": 5.0,
                                "center": [0, 0, 0],
                            },
                        },
                        {
                            "type": "constant",
                            "params": {"force": [2.0, 0.0, 0.0]},
                        },
                    ]
                },
            },
        )
        assert resp["success"] is True
        drain_and_receive_state(state_sub, settle_time=0.1)

        _confirm(
            "COMPOSITE: spring (200 N/m) + constant bias (+2 N in X). "
            "The equilibrium should feel shifted ~1 cm to the right of origin. "
            "Does the handle settle slightly right of centre?",
            fail_msg=(
                "Operator reported composite spring+bias equilibrium is not shifted. "
                "Check that CompositeField sums forces correctly."
            ),
        )


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def test_uitl_cleanup_revert_to_null(
    cmd_dealer: zmq.Socket[bytes],
) -> None:
    """Explicit cleanup: revert to NullField so the handle is free-moving.

    The autouse ``_heartbeat_and_cleanup`` fixture already reverts to the null
    field after every individual test, so this test is belt-and-suspenders.
    It ensures the server is left in a safe state even if the test run is
    interrupted mid-collection or if tests are run individually with ``-k``.
    """
    send_command(cmd_dealer, "heartbeat")
    resp = send_command(
        cmd_dealer, "set_force_field",
        {"type": "null", "params": {}},
    )
    assert resp["success"] is True
    print("\n  ✓ Server left in null (free) state.")
