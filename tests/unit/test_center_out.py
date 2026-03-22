"""Tests for CenterOutTask."""

from __future__ import annotations

import time

import zmq

from hapticore.core.messaging import EventPublisher, make_ipc_address
from hapticore.hardware.mock import MockDisplay, MockHapticInterface, MockSync
from hapticore.tasks.center_out import CenterOutTask
from hapticore.tasks.controller import TaskController
from hapticore.tasks.trial_manager import TrialManager


def _setup_center_out(
    num_trials: int = 1,
    hold_time: float = 0.01,
    reach_timeout: float = 2.0,
    iti_duration: float = 0.001,
    start_trial: bool = True,
) -> tuple[
    CenterOutTask, TaskController, MockHapticInterface, MockSync,
    MockDisplay, EventPublisher, TrialManager, zmq.Context,
]:
    """Create a fully wired CenterOutTask with mock hardware."""
    task = CenterOutTask()
    haptic = MockHapticInterface(initial_position=[0.1, 0.0, 0.0])
    display = MockDisplay()
    sync = MockSync()

    ctx = zmq.Context()
    address = make_ipc_address("co")
    publisher = EventPublisher(ctx, address)

    conditions = [
        {"target_id": i, "target_position": [0.08, 0.0]}
        for i in range(num_trials)
    ]
    trial_manager = TrialManager(
        conditions=conditions,
        block_size=num_trials,
        num_blocks=1,
        randomization="sequential",
    )

    # Pass param overrides via the params argument
    controller = TaskController(
        task=task,
        haptic=haptic,
        display=display,
        sync=sync,
        event_publisher=publisher,
        trial_manager=trial_manager,
        params={
            "hold_time": hold_time,
            "reach_timeout": reach_timeout,
            "iti_duration": iti_duration,
        },
        poll_rate_hz=1000.0,
    )
    controller.setup()

    if start_trial:
        # Start the first trial manually for scripted testing
        condition = trial_manager.advance()
        if condition is not None:
            task.trial_number = trial_manager.current_trial
            task.on_trial_start(condition)
            task.trigger("trial_begin")  # type: ignore[attr-defined]

    return task, controller, haptic, sync, display, publisher, trial_manager, ctx


class TestCenterOutCorrectSequence:
    def test_full_correct_trial(self) -> None:
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_center_out(
            hold_time=0.001, iti_duration=0.001,
        )
        try:
            # After setup, task should be in move_to_center (first trial started)
            assert task.state == "move_to_center"

            # Move to center
            haptic.set_position([0.0, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            assert task.state == "hold_center"

            # Wait for hold timer
            time.sleep(0.01)
            expired = task.timer.check()
            for name in expired:
                task.trigger(name)
            assert task.state == "reach"

            # Move to target
            haptic.set_position([0.08, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            assert task.state == "hold_target"

            # Wait for hold timer
            time.sleep(0.01)
            expired = task.timer.check()
            for name in expired:
                task.trigger(name)
            assert task.state == "success"

            # Wait for trial_end timer
            time.sleep(0.01)
            expired = task.timer.check()
            for name in expired:
                task.trigger(name)
            assert task.state == "iti"
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestCenterOutTimeout:
    def test_reach_timeout(self) -> None:
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_center_out(
            hold_time=0.001, reach_timeout=0.01, iti_duration=0.001,
        )
        try:
            assert task.state == "move_to_center"

            # Move to center
            haptic.set_position([0.0, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            assert task.state == "hold_center"

            # Wait for hold
            time.sleep(0.01)
            expired = task.timer.check()
            for name in expired:
                task.trigger(name)
            assert task.state == "reach"

            # Don't move to target — wait for timeout
            time.sleep(0.02)
            expired = task.timer.check()
            for name in expired:
                task.trigger(name)
            assert task.state == "timeout"
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestCenterOutBrokeHold:
    def test_broke_hold_returns_to_move_to_center(self) -> None:
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_center_out(
            hold_time=1.0,  # long hold so we can break it
        )
        try:
            assert task.state == "move_to_center"

            # Move to center
            haptic.set_position([0.0, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            assert task.state == "hold_center"

            # Move away during hold
            haptic.set_position([0.1, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            assert task.state == "move_to_center"
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestCenterOutCommands:
    def test_set_force_field_on_move_to_center(self) -> None:
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_center_out()
        try:
            # After entering move_to_center, a set_force_field command should have been sent
            cmds = [c for c in haptic._command_log if c.method == "set_force_field"]
            assert len(cmds) >= 1
            assert cmds[0].params["type"] == "spring_damper"
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestCenterOutEventCodes:
    def test_event_codes_sent(self) -> None:
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_center_out(
            hold_time=0.001, iti_duration=0.001,
        )
        try:
            # move_to_center sends code 10
            assert 10 in sync._event_codes

            # Move through to reach
            haptic.set_position([0.0, 0.0, 0.0])
            task.check_triggers(haptic.get_latest_state())
            time.sleep(0.01)
            expired = task.timer.check()
            for name in expired:
                task.trigger(name)

            # reach sends code 20
            assert 20 in sync._event_codes
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestCenterOutFullSession:
    def test_10_trials_scripted(self) -> None:
        """Run 10 trials with a scripted trajectory."""
        task, controller, haptic, sync, display, pub, tm, ctx = _setup_center_out(
            num_trials=10,
            hold_time=0.001,
            reach_timeout=2.0,
            iti_duration=0.001,
        )
        try:
            for _trial_idx in range(10):
                # Already in move_to_center after trial_begin
                assert task.state == "move_to_center"

                # Move to center
                haptic.set_position([0.0, 0.0, 0.0])
                task.check_triggers(haptic.get_latest_state())
                assert task.state == "hold_center"

                # Wait for hold
                time.sleep(0.01)
                expired = task.timer.check()
                for name in expired:
                    task.trigger(name)
                assert task.state == "reach"

                # Move to target
                haptic.set_position([0.08, 0.0, 0.0])
                task.check_triggers(haptic.get_latest_state())
                assert task.state == "hold_target"

                # Wait for hold
                time.sleep(0.01)
                expired = task.timer.check()
                for name in expired:
                    task.trigger(name)
                assert task.state == "success"

                # Wait for trial_end
                time.sleep(0.01)
                expired = task.timer.check()
                for name in expired:
                    task.trigger(name)

                # _on_state_change sets _trial_ended flag; simulate main loop
                # deferral by handling it here
                assert task.state == "iti"
                if controller._trial_ended:
                    controller._trial_ended = False
                    trial_log = tm.get_trial_log()
                    outcome = trial_log[-1]["outcome"] if trial_log else ""
                    task.on_trial_end(outcome)
                    if not tm.is_complete:
                        controller._start_next_trial()

            assert tm.is_complete
            log = tm.get_trial_log()
            assert len(log) == 10
            for entry in log:
                assert entry["outcome"] == "success"
        finally:
            controller.teardown()
            pub.close()
            ctx.term()
