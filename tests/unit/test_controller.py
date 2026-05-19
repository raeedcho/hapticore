"""Tests for TaskController."""

from __future__ import annotations

import time
from typing import Any

import pytest
import zmq

from hapticore.core.messages import (
    TOPIC_EVENT,
    TOPIC_PARAM,
    ParamUpdate,
    StateTransition,
    deserialize,
    serialize,
)
from hapticore.core.messaging import EventPublisher, EventSubscriber, make_ipc_address
from hapticore.display.mock import MockDisplay
from hapticore.haptic.mock import MockHapticInterface
from hapticore.sync.mock import MockSync
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
       timing notes in ``docs/adr/001-zeromq-over-rpclib.md``.
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


class TestControllerTick:
    def test_tick_returns_true_while_running(self) -> None:
        """tick() returns True when the session is still active."""
        task = TimerTestTask()
        controller, _, _, pub, tm, ctx = _make_controller(task, num_trials=3)
        try:
            controller.setup()
            assert controller.start_first_trial()
            # First tick — trial 0 just started, session not complete
            assert controller.tick() is True
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_tick_returns_false_when_complete(self) -> None:
        """tick() returns False after all trials complete."""
        task = TimerTestTask()
        controller, _, _, pub, tm, ctx = _make_controller(task, num_trials=1)
        try:
            controller.setup()
            assert controller.start_first_trial()
            # Tick until the session finishes (TimerTestTask uses 1ms timers)
            for _ in range(500):
                if not controller.tick():
                    break
                time.sleep(0.001)
            assert tm.is_complete
            assert controller.tick() is False
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_start_first_trial_returns_false_for_empty_session(self) -> None:
        """start_first_trial() returns False when TrialManager has no more trials."""
        task = SimpleTestTask()
        controller, _, _, pub, tm, ctx = _make_controller(task, num_trials=1)
        try:
            controller.setup()
            # Exhaust the trial manager by requesting an immediate stop.
            # After request_stop(after="trial"), advance() returns None.
            tm.request_stop(after="trial")
            assert controller.start_first_trial() is False
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_tick_driven_full_session(self) -> None:
        """A full session can be driven entirely by tick() calls."""
        task = TimerTestTask()
        controller, _, _, pub, tm, ctx = _make_controller(task, num_trials=3)
        try:
            controller.setup()
            assert controller.start_first_trial()
            for _ in range(2000):
                if not controller.tick():
                    break
                time.sleep(0.001)
            assert tm.is_complete
            log = tm.get_trial_log()
            assert len(log) == 3
            for entry in log:
                assert entry["outcome"] == "success"
        finally:
            controller.teardown()
            pub.close()
            ctx.term()


def _make_controller_with_param_sub(
    task: BaseTask,
    num_trials: int = 3,
    poll_rate_hz: float = 1000.0,
    params: dict[str, Any] | None = None,
) -> tuple[
    TaskController, MockHapticInterface, MockSync,
    EventPublisher, TrialManager, zmq.Context,
]:
    """Like _make_controller, but wires up TOPIC_PARAM subscription."""
    haptic = MockHapticInterface()
    display = MockDisplay()
    sync = MockSync()

    ctx = zmq.Context()
    address = make_ipc_address("test-param")
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
        zmq_context=ctx,
        event_address=address,
    )
    return controller, haptic, sync, publisher, trial_manager, ctx


class TestControllerParamUpdates:
    def test_param_update_applied_at_trial_boundary(self) -> None:
        """ParamUpdate on TOPIC_PARAM takes effect at the next trial start."""
        task = TimerTestTask()
        controller, _, _, pub, tm, ctx = _make_controller_with_param_sub(
            task, num_trials=3,
        )
        try:
            controller.setup()
            assert controller.start_first_trial()
            time.sleep(0.05)  # slow-joiner grace for SUB socket

            # Param starts at default
            assert task.params["hold_time"] == 0.5

            # Publish a param update
            update = ParamUpdate(
                timestamp=time.monotonic(),
                trial_number=0,
                param="hold_time",
                old_value=0.5,
                new_value=0.3,
            )
            pub.publish(TOPIC_PARAM, serialize(update))
            time.sleep(0.01)  # let ZMQ deliver

            # Tick through current trial — param should NOT change yet
            # (it's queued, not applied until next trial start)
            controller.tick()
            assert task.params["hold_time"] == 0.5

            # Run until trial boundary (TimerTestTask auto-completes)
            for _ in range(500):
                if not controller.tick():
                    break
                time.sleep(0.001)
                # Check if we've advanced past trial 0
                if tm.current_trial > 0:
                    break

            # Now the param should be updated
            assert task.params["hold_time"] == 0.3
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_param_update_unknown_param_ignored(self) -> None:
        """ParamUpdate for an unknown param is logged and ignored."""
        task = TimerTestTask()
        controller, _, _, pub, tm, ctx = _make_controller_with_param_sub(
            task, num_trials=2,
        )
        try:
            controller.setup()
            assert controller.start_first_trial()
            time.sleep(0.05)  # slow-joiner grace

            update = ParamUpdate(
                timestamp=time.monotonic(),
                trial_number=0,
                param="nonexistent_param",
                old_value=None,
                new_value=42,
            )
            pub.publish(TOPIC_PARAM, serialize(update))
            time.sleep(0.01)

            # Tick through trial boundary — should not raise
            for _ in range(500):
                if not controller.tick():
                    break
                time.sleep(0.001)
                if tm.current_trial > 0:
                    break

            # Original params unchanged
            assert task.params["hold_time"] == 0.5
            assert task.params["timeout"] == 2.0
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_param_update_without_sub_is_noop(self) -> None:
        """Controller without param subscription ticks normally."""
        task = TimerTestTask()
        controller, _, _, pub, tm, ctx = _make_controller(task, num_trials=1)
        try:
            controller.setup()
            assert controller.start_first_trial()
            for _ in range(500):
                if not controller.tick():
                    break
                time.sleep(0.001)
            assert tm.is_complete
        finally:
            controller.teardown()
            pub.close()
            ctx.term()

    def test_param_update_republished_on_topic_event(self) -> None:
        """Applied ParamUpdate is re-published on TOPIC_EVENT."""
        task = TimerTestTask()
        controller, _, _, pub, tm, ctx = _make_controller_with_param_sub(
            task, num_trials=2,
        )
        sub = EventSubscriber(
            ctx,
            pub._socket.getsockopt_string(zmq.LAST_ENDPOINT),
            topics=[TOPIC_EVENT],
        )
        try:
            controller.setup()
            assert controller.start_first_trial()
            time.sleep(0.05)  # slow-joiner grace

            update = ParamUpdate(
                timestamp=time.monotonic(),
                trial_number=0,
                param="hold_time",
                old_value=0.5,
                new_value=0.3,
            )
            pub.publish(TOPIC_PARAM, serialize(update))
            time.sleep(0.01)

            # Tick through to next trial
            for _ in range(500):
                if not controller.tick():
                    break
                time.sleep(0.001)
                if tm.current_trial > 0:
                    break

            # Drain TOPIC_EVENT messages — one of them should be a ParamUpdate
            found_param_update = False
            for _ in range(50):
                msg = sub.recv(timeout_ms=50)
                if msg is None:
                    break
                _, payload = msg
                try:
                    deserialized = deserialize(payload, ParamUpdate)
                except (TypeError, KeyError):
                    continue  # Not a ParamUpdate — skip
                if deserialized.param == "hold_time":
                    found_param_update = True
                    assert deserialized.new_value == 0.3
                    break
            assert found_param_update, "Expected a ParamUpdate on TOPIC_EVENT"
        finally:
            controller.teardown()
            sub.close()
            pub.close()
            ctx.term()

    def test_zmq_context_and_address_must_be_paired(self) -> None:
        """Providing only one of zmq_context/event_address raises ValueError."""
        task = SimpleTestTask()
        haptic = MockHapticInterface()
        display = MockDisplay()
        sync = MockSync()
        ctx = zmq.Context()
        address = make_ipc_address("test")
        publisher = EventPublisher(ctx, address)
        tm = TrialManager(
            conditions=[{"target_id": 0}],
            block_size=1,
            num_blocks=1,
            randomization="sequential",
        )
        try:
            with pytest.raises(ValueError, match="zmq_context and event_address"):
                TaskController(
                    task=task, haptic=haptic, display=display, sync=sync,
                    event_publisher=publisher, trial_manager=tm,
                    zmq_context=ctx,
                    # event_address intentionally omitted
                )
        finally:
            publisher.close()
            ctx.term()

    def test_param_update_invalid_value_ignored(self) -> None:
        """ParamUpdate with an out-of-bounds value is ignored; param unchanged."""

        class BoundedTask(TimerTestTask):
            PARAMS = {
                "hold_time": ParamSpec(type=float, default=0.5, unit="s", min=0.0, max=1.0),
                "timeout": ParamSpec(type=float, default=2.0, unit="s"),
            }

        task = BoundedTask()
        controller, _, _, pub, tm, ctx = _make_controller_with_param_sub(
            task, num_trials=2,
        )
        try:
            controller.setup()
            assert controller.start_first_trial()
            time.sleep(0.05)  # slow-joiner grace

            # Publish an update with a value below the min.
            # Use a deliberately stale old_value (99.0) to confirm the controller
            # reads the actual current value from task.params, not the message field.
            update = ParamUpdate(
                timestamp=time.monotonic(),
                trial_number=0,
                param="hold_time",
                old_value=99.0,  # intentionally wrong — controller should ignore this
                new_value=-5.0,
            )
            pub.publish(TOPIC_PARAM, serialize(update))
            time.sleep(0.01)

            # Tick through trial boundary — invalid update should be silently ignored
            for _ in range(500):
                if not controller.tick():
                    break
                time.sleep(0.001)
                if tm.current_trial > 0:
                    break

            # Param must be unchanged
            assert task.params["hold_time"] == 0.5
        finally:
            controller.teardown()
            pub.close()
            ctx.term()
