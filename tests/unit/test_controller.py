"""Tests for TaskController."""

from __future__ import annotations

import time
from typing import Any

import zmq

from hapticore.core.messages import TOPIC_EVENT, StateTransition, deserialize
from hapticore.core.messaging import EventPublisher, EventSubscriber, make_ipc_address
from hapticore.hardware.mock import MockDisplay, MockHapticInterface, MockSync
from hapticore.tasks.base import BaseTask, ParamSpec
from hapticore.tasks.controller import TaskController
from hapticore.tasks.trial_manager import TrialManager


class SimpleTestTask(BaseTask):
    """Three-state task for testing: iti → active → done."""

    PARAMS = {
        "hold_time": ParamSpec(type=float, default=0.5, unit="s"),
        "timeout": ParamSpec(type=float, default=2.0, unit="s"),
    }
    STATES = ["iti", "active", "success", "timeout"]
    TRANSITIONS = [
        {"trigger": "trial_begin", "source": "iti", "dest": "active"},
        {"trigger": "completed", "source": "active", "dest": "success"},
        {"trigger": "time_expired", "source": "active", "dest": "timeout"},
        {"trigger": "trial_end", "source": ["success", "timeout"], "dest": "iti"},
    ]
    INITIAL_STATE = "iti"


class TimerTestTask(SimpleTestTask):
    """Task that auto-completes via a short timer."""

    def on_enter_active(self, event: Any = None) -> None:
        self.timer.set("completed", 0.001)

    def on_enter_success(self, event: Any = None) -> None:
        self.log_trial(outcome="success")
        self.timer.set("trial_end", 0.001)

    def on_enter_timeout(self, event: Any = None) -> None:
        self.log_trial(outcome="timeout")
        self.timer.set("trial_end", 0.001)


class PositionTestTask(SimpleTestTask):
    """Task that completes when position is at origin."""

    def check_triggers(self, haptic_state: Any) -> None:
        if (
            self.state == "active"
            and self.distance(haptic_state.position, [0.0, 0.0, 0.0]) < 0.01
        ):
            self.trigger("completed")  # type: ignore[attr-defined]

    def on_enter_success(self, event: Any = None) -> None:
        self.log_trial(outcome="success")
        self.timer.set("trial_end", 0.001)

    def on_enter_timeout(self, event: Any = None) -> None:
        self.log_trial(outcome="timeout")
        self.timer.set("trial_end", 0.001)


def _make_controller(
    task: BaseTask,
    num_trials: int = 3,
    poll_rate_hz: float = 1000.0,
) -> tuple[
    TaskController, MockHapticInterface, MockSync,
    EventPublisher, TrialManager, zmq.Context,
]:
    """Helper to create a TaskController with mock hardware."""
    haptic = MockHapticInterface()
    display = MockDisplay()
    sync = MockSync()

    ctx = zmq.Context()
    address = make_ipc_address("test")
    publisher = EventPublisher(ctx, address)

    conditions = [{"target_id": i} for i in range(num_trials)]
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
        poll_rate_hz=poll_rate_hz,
    )
    return controller, haptic, sync, publisher, trial_manager, ctx


class TestControllerSetup:
    def test_setup_creates_machine(self) -> None:
        task = SimpleTestTask()
        controller, _, _, pub, _, ctx = _make_controller(task)
        try:
            controller.setup()
            assert task.state == "iti"
            assert controller._machine is not None
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_state_transition_event(self) -> None:
        task = SimpleTestTask()
        controller, _, _, pub, _, ctx = _make_controller(task)
        address = make_ipc_address("sub")
        # Re-create publisher on known address for subscriber
        pub.close()
        pub2 = EventPublisher(ctx, address)
        controller.event_publisher = pub2
        sub = EventSubscriber(ctx, address, topics=[TOPIC_EVENT])

        try:
            controller.setup()
            time.sleep(0.05)  # slow-joiner

            # Manually trigger a transition
            task.trigger("trial_begin")
            time.sleep(0.05)

            msg = sub.recv(timeout_ms=200)
            assert msg is not None
            topic, payload = msg
            assert topic == TOPIC_EVENT
            st = deserialize(payload, StateTransition)
            assert isinstance(st, StateTransition)
            assert st.previous_state == "iti"
            assert st.new_state == "active"
            assert st.trigger == "trial_begin"
        finally:
            controller.teardown()
            sub.close()
            pub2.close()
            ctx.term()


class TestControllerTimer:
    def test_timer_fires_transition(self) -> None:
        task = TimerTestTask()
        controller, _, _, pub, tm, ctx = _make_controller(task, num_trials=1)
        try:
            controller.setup()
            controller.run()
            assert tm.is_complete
            log = tm.get_trial_log()
            assert len(log) == 1
            assert log[0]["outcome"] == "success"
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestControllerTrials:
    def test_trial_advancement(self) -> None:
        task = TimerTestTask()
        controller, _, _, pub, tm, ctx = _make_controller(task, num_trials=3)
        try:
            controller.setup()
            controller.run()
            assert tm.is_complete
            log = tm.get_trial_log()
            assert len(log) == 3
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestControllerStop:
    def test_stop_exits_loop(self) -> None:
        task = SimpleTestTask()
        controller, _, _, pub, _, ctx = _make_controller(task, num_trials=100)
        try:
            controller.setup()
            # Stop immediately
            controller.stop()
            controller.run()
            # Should exit without completing all trials
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestControllerHaptic:
    def test_haptic_position_trigger(self) -> None:
        task = PositionTestTask()
        controller, haptic, _, pub, tm, ctx = _make_controller(task, num_trials=1)
        try:
            controller.setup()
            # Set position to origin — should trigger "completed"
            haptic.set_position([0.0, 0.0, 0.0])
            controller.run()
            assert tm.is_complete
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestControllerFullSession:
    def test_full_session_5_trials(self) -> None:
        task = TimerTestTask()
        controller, _, _, pub, tm, ctx = _make_controller(task, num_trials=5)
        try:
            controller.setup()
            controller.run()
            assert tm.is_complete
            log = tm.get_trial_log()
            assert len(log) == 5
            for entry in log:
                assert entry["outcome"] == "success"
        finally:
            controller.teardown()
            pub.close()
            ctx.term()
