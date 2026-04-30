"""Tests for CupTask."""

from __future__ import annotations

import math
import time
from typing import Any

import zmq

from hapticore.core.messaging import EventPublisher, make_ipc_address
from hapticore.display.mock import MockDisplay
from hapticore.haptic.mock import MockHapticInterface
from hapticore.sync.mock import MockSync
from hapticore.tasks.controller import TaskController
from hapticore.tasks.cup_task import CupTask
from hapticore.tasks.trial_manager import TrialManager


def _setup_cup_task(
    num_trials: int = 1,
    hold_time: float = 0.001,
    preview_duration: float = 0.001,
    reach_timeout: float = 2.0,
    iti_duration: float = 0.001,
    initial_phi: float = 0.3,
    start_trial: bool = True,
) -> tuple[
    CupTask, TaskController, MockHapticInterface, MockSync,
    MockDisplay, EventPublisher, TrialManager, zmq.Context,
]:
    """Create a fully wired CupTask with mock hardware."""
    task = CupTask()
    haptic = MockHapticInterface(initial_position=[0.1, 0.0, 0.0])
    display = MockDisplay()
    sync = MockSync()

    ctx = zmq.Context()
    address = make_ipc_address("cup")
    publisher = EventPublisher(ctx, address)

    conditions = [
        {"initial_phi": initial_phi} for _ in range(num_trials)
    ]
    trial_manager = TrialManager(
        conditions=conditions,
        block_size=num_trials,
        num_blocks=1,
        randomization="sequential",
    )

    controller = TaskController(
        task=task,
        haptic=haptic,
        display=display,
        sync=sync,
        event_publisher=publisher,
        trial_manager=trial_manager,
        params={
            "hold_time": hold_time,
            "preview_duration": preview_duration,
            "reach_timeout": reach_timeout,
            "iti_duration": iti_duration,
        },
        poll_rate_hz=1000.0,
    )
    controller.setup()

    if start_trial:
        condition = trial_manager.advance()
        if condition is not None:
            task.trial_number = trial_manager.current_trial
            task.on_trial_start(condition)
            task.trigger("trial_begin")  # type: ignore[attr-defined]

    return task, controller, haptic, sync, display, publisher, trial_manager, ctx


def _last_field_command(haptic: MockHapticInterface) -> dict[str, Any]:
    """Return the params of the most recent set_force_field command."""
    cmds = [c for c in haptic._command_log if c.method == "set_force_field"]
    assert cmds, "No set_force_field commands logged"
    return cmds[-1].params


def _assert_composite_contains(
    params: dict[str, Any],
    expected_types: list[str],
) -> dict[str, dict[str, Any]]:
    """Assert params is a composite with the expected child field types.

    Returns a dict mapping type name to the child's params for further
    assertions.
    """
    assert params["type"] == "composite"
    fields = params["params"]["fields"]
    actual_types = [f["type"] for f in fields]
    assert sorted(actual_types) == sorted(expected_types), (
        f"Expected {expected_types}, got {actual_types}"
    )
    return {f["type"]: f.get("params", {}) for f in fields}


class TestCupTaskCorrectSequence:
    def test_full_success_trial(self) -> None:
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_cup_task(
            hold_time=0.001, preview_duration=0.001, iti_duration=0.001,
        )
        try:
            assert task.state == "move_to_left"

            # Move cursor to left target
            haptic.set_position([-0.06, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            assert task.state == "hold_left"

            # Wait for hold timer → preview
            time.sleep(0.01)
            expired = task.timer.check()
            for name in expired:
                task.trigger(name)
            assert task.state == "preview"

            # Wait for go_cue timer → reach
            time.sleep(0.01)
            expired = task.timer.check()
            for name in expired:
                task.trigger(name)
            assert task.state == "reach"

            # Move to right target
            haptic.set_position([0.06, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            assert task.state == "hold_right"

            # Wait for hold timer → success
            time.sleep(0.01)
            expired = task.timer.check()
            for name in expired:
                task.trigger(name)
            assert task.state == "success"

            # Verify reward delivered
            assert len(sync._reward_durations_ms) > 0

            # Wait for trial_end timer → iti
            time.sleep(0.01)
            expired = task.timer.check()
            for name in expired:
                task.trigger(name)
            assert task.state == "iti"
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestCupTaskCompositeFields:
    def test_move_to_left_sets_channeled_null(self) -> None:
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_cup_task()
        try:
            assert task.state == "move_to_left"
            cmd_params = _last_field_command(haptic)
            children = _assert_composite_contains(cmd_params, ["channel", "null"])
            assert children["channel"]["axes"] == [1, 2]
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_preview_sets_channeled_spring_damper(self) -> None:
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_cup_task()
        try:
            # Navigate to preview
            haptic.set_position([-0.06, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            time.sleep(0.01)
            expired = task.timer.check()
            for name in expired:
                task.trigger(name)
            assert task.state == "preview"

            cmd_params = _last_field_command(haptic)
            children = _assert_composite_contains(
                cmd_params, ["channel", "spring_damper"],
            )

            assert children["channel"]["axes"] == [1, 2]
            assert children["spring_damper"]["center"] == [-0.06, 0.0, 0.0]
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_reach_sets_channeled_cart_pendulum(self) -> None:
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_cup_task(
            initial_phi=0.3,
        )
        try:
            # Navigate to reach
            haptic.set_position([-0.06, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            assert task.state == "reach"

            cmd_params = _last_field_command(haptic)
            children = _assert_composite_contains(
                cmd_params, ["channel", "cart_pendulum"],
            )

            assert children["channel"]["axes"] == [1, 2]
            assert children["cart_pendulum"]["initial_phi"] == 0.3
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_success_sets_channeled_null(self) -> None:
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_cup_task(
            hold_time=0.001, preview_duration=0.001,
        )
        try:
            # Navigate to success
            haptic.set_position([-0.06, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            assert task.state == "reach"

            haptic.set_position([0.06, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            assert task.state == "success"

            cmd_params = _last_field_command(haptic)
            children = _assert_composite_contains(cmd_params, ["channel", "null"])
            assert children["channel"]["axes"] == [1, 2]
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestCupTaskSpillDuringReach:
    def test_spill_in_reach(self) -> None:
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_cup_task(
            hold_time=0.001, preview_duration=0.001,
        )
        try:
            # Navigate to reach
            haptic.set_position([-0.06, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            assert task.state == "reach"

            # Set spilled state and trigger check
            haptic._field_state = {"spilled": True}
            task.check_triggers(haptic.get_latest_state())
            assert task.state == "spill"

            # Verify trial was logged with spill outcome
            log = tm.get_trial_log()
            assert len(log) >= 1
            assert log[-1]["outcome"] == "spill"
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestCupTaskSpillDuringHoldRight:
    def test_spill_in_hold_right(self) -> None:
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_cup_task(
            hold_time=0.001, preview_duration=0.001, reach_timeout=2.0,
        )
        try:
            # Navigate to hold_right
            haptic.set_position([-0.06, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            assert task.state == "reach"

            haptic.set_position([0.06, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            assert task.state == "hold_right"

            # Now spill
            haptic._field_state = {"spilled": True}
            task.check_triggers(haptic.get_latest_state())
            assert task.state == "spill"
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestCupTaskTimeout:
    def test_reach_timeout(self) -> None:
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_cup_task(
            hold_time=0.001, preview_duration=0.001, reach_timeout=0.01,
        )
        try:
            # Navigate to reach
            haptic.set_position([-0.06, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            assert task.state == "reach"

            # Don't move to right target — wait for timeout
            time.sleep(0.02)
            expired = task.timer.check()
            for name in expired:
                task.trigger(name)
            assert task.state == "timeout"

            # Verify trial was logged with timeout outcome
            log = tm.get_trial_log()
            assert len(log) >= 1
            assert log[-1]["outcome"] == "timeout"
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestCupTaskBrokeHold:
    def test_broke_hold_left_returns_to_move_to_left(self) -> None:
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_cup_task(
            hold_time=1.0,  # long hold so we can break it
        )
        try:
            assert task.state == "move_to_left"

            # Move to left target
            haptic.set_position([-0.06, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            assert task.state == "hold_left"

            # Verify hold_complete timer is active
            assert "hold_complete" in task.timer._timers

            # Move away during hold
            haptic.set_position([0.1, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            assert task.state == "move_to_left"

            # Verify timer was cancelled
            assert "hold_complete" not in task.timer._timers
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_broke_hold_right_returns_to_reach(self) -> None:
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_cup_task(
            hold_time=0.001, preview_duration=0.001, reach_timeout=2.0,
        )
        try:
            # Navigate to hold_right
            haptic.set_position([-0.06, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            assert task.state == "reach"

            haptic.set_position([0.06, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            assert task.state == "hold_right"

            # Verify hold_complete timer is active
            assert "hold_complete" in task.timer._timers

            # Move away from right target
            haptic.set_position([0.1, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            assert task.state == "reach"

            # Verify timer was cancelled
            assert "hold_complete" not in task.timer._timers
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestCupTaskPreviewVisuals:
    def test_preview_creates_cart_pendulum_stimuli(self) -> None:
        phi = 0.3
        lx = -0.06
        rx = 0.06
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_cup_task(
            initial_phi=phi,
        )
        try:
            # Navigate to preview
            haptic.set_position([lx, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            assert task.state == "preview"

            # Cup, ball, string should be visible
            assert "__cup" in display._visible_stimuli
            assert "__ball" in display._visible_stimuli
            assert "__string" in display._visible_stimuli

            # Cup should be at left_x
            cup_params = display._visible_stimuli["__cup"]
            assert cup_params["position"][0] == lx

            # Ball position should match pendulum geometry
            L = task.params["pendulum_length"]
            expected_ball_x = lx + L * math.sin(phi)
            ball_params = display._visible_stimuli["__ball"]
            assert abs(ball_params["position"][0] - expected_ball_x) < 1e-9

            # Right target and track line should be visible
            assert "right_target" in display._visible_stimuli
            assert display._visible_stimuli["right_target"]["position"][0] == rx
            assert "track_line" in display._visible_stimuli
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestCupTaskSpillPriority:
    def test_spill_beats_at_right(self) -> None:
        """If both spilled and at_right are true, spill should win."""
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_cup_task(
            hold_time=0.001, preview_duration=0.001,
        )
        try:
            # Navigate to reach
            haptic.set_position([-0.06, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            assert task.state == "reach"

            # Position inside right target AND spilled
            haptic.set_position([0.06, 0.0, 0.0])
            haptic._field_state = {"spilled": True}
            task.check_triggers(haptic.get_latest_state())

            # Should go to spill, not hold_right
            assert task.state == "spill"
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestCupTaskClearVisuals:
    def test_success_clears_all_task_visuals(self) -> None:
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_cup_task(
            hold_time=0.001, preview_duration=0.001,
        )
        try:
            # Navigate to success
            haptic.set_position([-0.06, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            assert task.state == "reach"

            haptic.set_position([0.06, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            time.sleep(0.01)
            for name in task.timer.check():
                task.trigger(name)
            assert task.state == "success"

            # All task stimuli should be removed
            for stim_id in ("__cup", "__ball", "__string",
                            "left_target", "right_target", "track_line"):
                assert stim_id not in display._visible_stimuli, (
                    f"Stale stimulus still visible: {stim_id}"
                )
        finally:
            controller.teardown()
            pub.close()
            ctx.term()
