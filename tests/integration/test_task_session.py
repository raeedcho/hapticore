"""Integration tests for full task sessions using mock hardware.

These tests exercise the full stack from config loading through task
execution to trial logging.
"""

from __future__ import annotations

import time
from typing import Any

import zmq

from hapticore.core.messages import (
    TOPIC_EVENT,
    TOPIC_TRIAL,
)
from hapticore.core.messaging import EventPublisher, EventSubscriber, make_ipc_address
from hapticore.hardware.mock import MockDisplay, MockHapticInterface, MockSync
from hapticore.tasks.base import BaseTask, ParamSpec
from hapticore.tasks.controller import TaskController
from hapticore.tasks.trial_manager import TrialManager


class AutoCompleteTask(BaseTask):
    """Task that auto-completes via timers for integration testing."""

    PARAMS = {
        "delay": ParamSpec(type=float, default=0.001, unit="s"),
    }
    STATES = ["iti", "active", "success"]
    TRANSITIONS = [
        {"trigger": "trial_begin", "source": "iti", "dest": "active"},
        {"trigger": "completed", "source": "active", "dest": "success"},
        {"trigger": "trial_end", "source": "success", "dest": "iti"},
    ]
    INITIAL_STATE = "iti"

    def on_enter_active(self, event: Any = None) -> None:
        self.timer.set("completed", self.params["delay"])

    def on_enter_success(self, event: Any = None) -> None:
        self.log_trial(outcome="success")
        self.timer.set("trial_end", self.params["delay"])


class MixedOutcomeTask(BaseTask):
    """Task that alternates between success and timeout."""

    PARAMS = {
        "delay": ParamSpec(type=float, default=0.001, unit="s"),
    }
    STATES = ["iti", "active", "success", "timeout"]
    TRANSITIONS = [
        {"trigger": "trial_begin", "source": "iti", "dest": "active"},
        {"trigger": "completed", "source": "active", "dest": "success"},
        {"trigger": "time_expired", "source": "active", "dest": "timeout"},
        {"trigger": "trial_end", "source": ["success", "timeout"], "dest": "iti"},
    ]
    INITIAL_STATE = "iti"

    def on_enter_active(self, event: Any = None) -> None:
        # Alternate: even trials succeed, odd trials timeout
        if self.trial_number % 2 == 0:
            self.timer.set("completed", self.params["delay"])
        else:
            self.timer.set("time_expired", self.params["delay"])

    def on_enter_success(self, event: Any = None) -> None:
        self.log_trial(outcome="success")
        self.timer.set("trial_end", self.params["delay"])

    def on_enter_timeout(self, event: Any = None) -> None:
        self.log_trial(outcome="timeout")
        self.timer.set("trial_end", self.params["delay"])


class TestFullSession:
    def test_100_trial_session(self) -> None:
        """Run a 100-trial session with auto-completing task."""
        task = AutoCompleteTask()
        haptic = MockHapticInterface()
        display = MockDisplay()
        sync = MockSync()

        ctx = zmq.Context()
        address = make_ipc_address("int")
        publisher = EventPublisher(ctx, address)

        conditions = [{"target_id": i % 4} for i in range(100)]
        tm = TrialManager(
            conditions=conditions,
            block_size=100,
            num_blocks=1,
            randomization="sequential",
        )

        controller = TaskController(
            task=task,
            haptic=haptic,
            display=display,
            sync=sync,
            event_publisher=publisher,
            trial_manager=tm,
            poll_rate_hz=5000.0,
        )

        try:
            controller.setup()
            controller.run()

            assert tm.is_complete
            log = tm.get_trial_log()
            assert len(log) == 100
            for entry in log:
                assert entry["outcome"] == "success"
        finally:
            controller.teardown()
            publisher.close()
            ctx.term()

    def test_mixed_outcome_session(self) -> None:
        """Run 20 trials with alternating success/timeout."""
        task = MixedOutcomeTask()
        haptic = MockHapticInterface()
        display = MockDisplay()
        sync = MockSync()

        ctx = zmq.Context()
        address = make_ipc_address("int")
        publisher = EventPublisher(ctx, address)

        conditions = [{"target_id": i % 4} for i in range(20)]
        tm = TrialManager(
            conditions=conditions,
            block_size=20,
            num_blocks=1,
            randomization="sequential",
        )

        controller = TaskController(
            task=task,
            haptic=haptic,
            display=display,
            sync=sync,
            event_publisher=publisher,
            trial_manager=tm,
            poll_rate_hz=5000.0,
        )

        try:
            controller.setup()
            controller.run()

            assert tm.is_complete
            summary = tm.get_summary()
            assert summary["completed_trials"] == 20
            assert summary["outcomes"]["success"] == 10
            assert summary["outcomes"]["timeout"] == 10
            assert abs(summary["accuracy"] - 0.5) < 0.01
        finally:
            controller.teardown()
            publisher.close()
            ctx.term()

    def test_event_bus_receives_transitions(self) -> None:
        """Verify EventSubscriber receives state transition events."""
        task = AutoCompleteTask()
        haptic = MockHapticInterface()
        display = MockDisplay()
        sync = MockSync()

        ctx = zmq.Context()
        address = make_ipc_address("int")
        publisher = EventPublisher(ctx, address)
        subscriber = EventSubscriber(ctx, address, topics=[TOPIC_EVENT, TOPIC_TRIAL])

        conditions = [{"target_id": i} for i in range(5)]
        tm = TrialManager(
            conditions=conditions,
            block_size=5,
            num_blocks=1,
            randomization="sequential",
        )

        controller = TaskController(
            task=task,
            haptic=haptic,
            display=display,
            sync=sync,
            event_publisher=publisher,
            trial_manager=tm,
            poll_rate_hz=5000.0,
        )

        try:
            controller.setup()
            time.sleep(0.1)  # slow-joiner

            controller.run()
            time.sleep(0.05)

            # Collect all received events
            events: list[tuple[bytes, bytes]] = []
            while True:
                msg = subscriber.recv(timeout_ms=100)
                if msg is None:
                    break
                events.append(msg)

            # Should have received state transition events
            state_events = [e for e in events if e[0] == TOPIC_EVENT]
            trial_events = [e for e in events if e[0] == TOPIC_TRIAL]

            # Each trial has 3 state transitions: iti→active, active→success, success→iti
            # 5 trials × 3 transitions = 15 events (first trial_begin is also a transition)
            assert len(state_events) >= 10  # at least some events received

            # Should have trial events too
            assert len(trial_events) >= 3  # at least some trial events received
        finally:
            controller.teardown()
            subscriber.close()
            publisher.close()
            ctx.term()


class TestCenterOutIntegration:
    """Integration test using controller.run() with the real CenterOutTask."""

    def test_center_out_via_controller_run(self) -> None:
        """Run CenterOutTask through controller.run() with a state-reactive mock.

        The mock auto-moves to the correct position based on the task's state,
        so the main loop drives the full state machine without manual triggers.
        """
        from hapticore.tasks.center_out import CenterOutTask

        task = CenterOutTask()

        # Create a mock that auto-advances position based on task state
        haptic = MockHapticInterface(initial_position=[0.1, 0.0, 0.0])
        original_get_state = haptic.get_latest_state

        def state_reactive_get_state() -> Any:
            # Move the mock position based on the task's current state
            if hasattr(task, "state"):
                if task.state in ("move_to_center", "hold_center"):
                    haptic.set_position([0.0, 0.0, 0.0])
                elif task.state in ("reach", "hold_target"):
                    target = task.current_condition.get(
                        "target_position", [0.08, 0.0]
                    )
                    if len(target) == 2:
                        target = [target[0], target[1], 0.0]
                    haptic.set_position(target)
            return original_get_state()

        haptic.get_latest_state = state_reactive_get_state  # type: ignore[assignment]

        display = MockDisplay()
        sync = MockSync()

        ctx = zmq.Context()
        address = make_ipc_address("int_co")
        publisher = EventPublisher(ctx, address)

        num_trials = 5
        conditions = [
            {"target_id": i, "target_position": [0.08, 0.0]}
            for i in range(num_trials)
        ]
        tm = TrialManager(
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
            trial_manager=tm,
            params={
                "hold_time": 0.001,
                "reach_timeout": 2.0,
                "iti_duration": 0.001,
            },
            poll_rate_hz=5000.0,
        )

        try:
            controller.setup()
            controller.run()

            assert tm.is_complete
            log = tm.get_trial_log()
            assert len(log) == num_trials
            for entry in log:
                assert entry["outcome"] == "success"
        finally:
            controller.teardown()
            publisher.close()
            ctx.term()


class TestCLISimulate:
    """Integration tests for the CLI simulate command."""

    def test_fast_simulation_completes_quickly(self) -> None:
        """End-to-end: _simulate with --fast finishes in seconds, not minutes."""
        from argparse import Namespace
        from pathlib import Path

        from hapticore.cli import _simulate

        config_path = Path(__file__).parents[2] / "configs" / "center_out_experiment.yaml"
        args = Namespace(config=str(config_path), fast=True)

        start = time.monotonic()
        _simulate(args)
        elapsed = time.monotonic() - start

        assert elapsed < 5.0, (
            f"Fast simulation took {elapsed:.1f}s — timing overrides "
            f"are probably not being applied"
        )