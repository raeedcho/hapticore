"""Tests for TaskController."""

from __future__ import annotations

import time
from typing import Any

import pytest
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
    params: dict[str, Any] | None = None,
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
        params=params,
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


class TestControllerParamOverrides:
    def test_config_params_override_defaults(self) -> None:
        """Verify config param overrides take effect instead of ParamSpec defaults."""
        task = TimerTestTask()
        controller, _, _, pub, tm, ctx = _make_controller(
            task, num_trials=1, params={"hold_time": 0.1, "timeout": 3.0},
        )
        try:
            controller.setup()
            # Verify the overridden values are used, not the defaults
            assert task.params["hold_time"] == 0.1
            assert task.params["timeout"] == 3.0
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_defaults_used_when_no_overrides(self) -> None:
        """Verify ParamSpec defaults are used when no overrides are provided."""
        task = TimerTestTask()
        controller, _, _, pub, tm, ctx = _make_controller(task, num_trials=1)
        try:
            controller.setup()
            assert task.params["hold_time"] == 0.5  # ParamSpec default
            assert task.params["timeout"] == 2.0    # ParamSpec default
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_partial_overrides(self) -> None:
        """Verify partial overrides merge with remaining defaults."""
        task = TimerTestTask()
        controller, _, _, pub, tm, ctx = _make_controller(
            task, num_trials=1, params={"hold_time": 0.2},
        )
        try:
            controller.setup()
            assert task.params["hold_time"] == 0.2  # overridden
            assert task.params["timeout"] == 2.0    # default
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_unknown_param_raises(self) -> None:
        """Verify unknown parameter names in overrides raise ValueError."""
        task = TimerTestTask()
        controller, _, _, pub, tm, ctx = _make_controller(
            task, num_trials=1, params={"holdtime": 0.2},  # typo
        )
        try:
            with pytest.raises(ValueError, match="Unknown parameter"):
                controller.setup()
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestControllerValidation:
    def test_zero_poll_rate_raises(self) -> None:
        """Verify poll_rate_hz=0 raises ValueError."""
        task = SimpleTestTask()
        with pytest.raises(ValueError, match="poll_rate_hz must be positive"):
            _make_controller(task, poll_rate_hz=0.0)

    def test_negative_poll_rate_raises(self) -> None:
        """Verify negative poll_rate_hz raises ValueError."""
        task = SimpleTestTask()
        with pytest.raises(ValueError, match="poll_rate_hz must be positive"):
            _make_controller(task, poll_rate_hz=-10.0)


# ---------------------------------------------------------------------------
# Helper: controller with an infinite TrialManager
# ---------------------------------------------------------------------------

def _make_infinite_controller(
    task: BaseTask,
    poll_rate_hz: float = 1000.0,
) -> tuple[TaskController, MockHapticInterface, EventPublisher, TrialManager, zmq.Context]:
    """Helper to create a TaskController backed by an open-ended TrialManager."""
    haptic = MockHapticInterface()
    display = MockDisplay()
    sync = MockSync()

    ctx = zmq.Context()
    address = make_ipc_address("test-inf")
    publisher = EventPublisher(ctx, address)

    conditions = [{"target_id": i} for i in range(4)]
    trial_manager = TrialManager(
        conditions=conditions,
        block_size=4,
        num_blocks=None,          # open-ended
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
    return controller, haptic, publisher, trial_manager, ctx


class TestControllerInfiniteSession:
    """Tests for open-ended sessions (num_blocks=None)."""

    def test_infinite_session_stops_via_request_stop_block(self) -> None:
        """After request_stop(after='block') the controller exits at block boundary."""
        task = TimerTestTask()
        controller, _, pub, tm, ctx = _make_infinite_controller(task)
        try:
            controller.setup()

            # Schedule stop after a brief delay (one block worth of time)
            import threading

            def _stop_after_delay() -> None:
                time.sleep(0.05)
                tm.request_stop(after="block")

            t = threading.Thread(target=_stop_after_delay, daemon=True)
            t.start()
            controller.run()
            t.join(timeout=2.0)

            # Session should have stopped at a block boundary
            log = tm.get_trial_log()
            assert len(log) > 0
            assert len(log) % tm.block_size == 0, (
                f"Expected whole blocks logged, got {len(log)}"
            )
            summary = tm.get_summary()
            assert summary["stop_type"] == "stopped_at_block"
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_infinite_session_stops_via_request_stop_trial(self) -> None:
        """After request_stop(after='trial') the controller exits after current trial."""
        task = TimerTestTask()
        controller, _, pub, tm, ctx = _make_infinite_controller(task)
        try:
            controller.setup()

            import threading

            def _stop_after_delay() -> None:
                time.sleep(0.02)
                tm.request_stop(after="trial")

            t = threading.Thread(target=_stop_after_delay, daemon=True)
            t.start()
            controller.run()
            t.join(timeout=2.0)

            log = tm.get_trial_log()
            assert len(log) > 0
            summary = tm.get_summary()
            # stop_type depends on whether the stop happened to land on a
            # block boundary; either is valid for after="trial"
            assert summary["stop_type"] in ("stopped_mid_block", "stopped_at_block")
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


class TestControllerSigint:
    """Tests for escalating Ctrl+C (SIGINT) handling in run().

    .. note:: Inter-signal sleeps (``time.sleep(0.005)``) are vulnerable to
       macOS CI timer coalescing — if two signals arrive in the same poll tick,
       ``_sigint_count`` jumps by 2 and the "block stop" escalation level is
       skipped. This doesn't affect correctness (the result is a more urgent
       stop), but could cause ``test_first_sigint_requests_block_stop`` to
       fail in a future variant with tighter timing.  See also the macOS CI
       timing notes in ``docs/adr/001-zmq-msgpack.md``.
    """

    def _make_sigint_controller(
        self,
        block_size: int = 20,
        timer_delay: float = 0.001,
    ) -> tuple[TaskController, EventPublisher, TrialManager, zmq.Context]:
        """Infinite controller with configurable block size and trial timer."""

        class ConfigurableTimerTask(SimpleTestTask):
            _delay = timer_delay

            def on_enter_active(self, event: Any = None) -> None:
                self.timer.set("completed", self._delay)

            def on_enter_success(self, event: Any = None) -> None:
                self.log_trial(outcome="success")
                self.timer.set("trial_end", self._delay)

            def on_enter_timeout(self, event: Any = None) -> None:
                self.log_trial(outcome="timeout")
                self.timer.set("trial_end", self._delay)

        task = ConfigurableTimerTask()
        haptic = MockHapticInterface()
        display = MockDisplay()
        sync = MockSync()

        ctx = zmq.Context()
        address = make_ipc_address("test-sigint")
        publisher = EventPublisher(ctx, address)

        conditions = [{"target_id": i} for i in range(block_size)]
        trial_manager = TrialManager(
            conditions=conditions,
            block_size=block_size,
            num_blocks=None,
            randomization="sequential",
        )
        controller = TaskController(
            task=task,
            haptic=haptic,
            display=display,
            sync=sync,
            event_publisher=publisher,
            trial_manager=trial_manager,
            poll_rate_hz=1000.0,
        )
        return controller, publisher, trial_manager, ctx

    def test_first_sigint_requests_block_stop(self) -> None:
        """First SIGINT triggers request_stop(after='block')."""
        import os
        import signal
        import threading

        # Large block (50 trials × 2ms ≈ 100 ms) so signal arrives mid-block
        controller, pub, tm, ctx = self._make_sigint_controller(block_size=50)
        try:
            controller.setup()

            def _send_sigint() -> None:
                controller._sigint_handler_ready.wait(timeout=2.0)
                time.sleep(0.01)  # let a few trials run
                os.kill(os.getpid(), signal.SIGINT)

            t = threading.Thread(target=_send_sigint, daemon=True)
            t.start()
            controller.run()
            t.join(timeout=2.0)

            summary = tm.get_summary()
            assert summary["stop_type"] == "stopped_at_block"
            log = tm.get_trial_log()
            assert len(log) % tm.block_size == 0
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_second_sigint_requests_trial_stop(self) -> None:
        """Two SIGINTs (5 ms apart) escalate to request_stop(after='trial')."""
        import os
        import signal
        import threading

        # Large block (50 trials × 2ms ≈ 100 ms) so both signals arrive mid-block
        controller, pub, tm, ctx = self._make_sigint_controller(block_size=50)
        try:
            controller.setup()

            def _send_two_sigints() -> None:
                controller._sigint_handler_ready.wait(timeout=2.0)
                time.sleep(0.01)   # let a few trials run
                os.kill(os.getpid(), signal.SIGINT)
                time.sleep(0.005)  # pause so the first is processed (> 1 loop tick)
                os.kill(os.getpid(), signal.SIGINT)

            t = threading.Thread(target=_send_two_sigints, daemon=True)
            t.start()
            controller.run()
            t.join(timeout=2.0)

            summary = tm.get_summary()
            assert summary["stop_type"] in ("stopped_mid_block", "stopped_at_block")
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_third_sigint_raises_keyboard_interrupt(self) -> None:
        """Third SIGINT causes run() to raise KeyboardInterrupt.

        Uses a slow trial (2 s timer) so the session is still running when the
        3rd SIGINT arrives, regardless of the stop flags set by SIGINT 1 & 2.
        """
        import os
        import signal
        import threading

        # Slow trials: 2 s per phase — far longer than the test duration.
        # This guarantees the session is still inside the active state when
        # the 3rd SIGINT is processed, so is_complete is False and the loop
        # doesn't exit normally before the KeyboardInterrupt is raised.
        controller, pub, tm, ctx = self._make_sigint_controller(
            block_size=2, timer_delay=2.0
        )
        try:
            controller.setup()

            def _send_three_sigints() -> None:
                controller._sigint_handler_ready.wait(timeout=2.0)
                time.sleep(0.01)   # let trial start
                os.kill(os.getpid(), signal.SIGINT)
                time.sleep(0.005)
                os.kill(os.getpid(), signal.SIGINT)
                time.sleep(0.005)
                os.kill(os.getpid(), signal.SIGINT)

            t = threading.Thread(target=_send_three_sigints, daemon=True)
            t.start()
            with pytest.raises(KeyboardInterrupt):
                controller.run()
            t.join(timeout=2.0)
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_sigint_handler_restored_after_run(self) -> None:
        """Original SIGINT handler is restored after run() exits."""
        import signal

        original = signal.getsignal(signal.SIGINT)
        task = TimerTestTask()
        controller, _, _, pub, tm, ctx = _make_controller(task, num_trials=1)
        try:
            controller.setup()
            controller.run()
            assert signal.getsignal(signal.SIGINT) is original
        finally:
            controller.teardown()
            pub.close()
            ctx.term()
