"""Task controller: orchestrates task execution with state machine and main loop.

The TaskController wires together the task, transitions state machine, timer,
trial manager, haptic interface, and event bus. It runs the main loop that
polls haptic state, checks timers, and dispatches triggers.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from transitions import Machine

from hapticore.core.messages import (
    TOPIC_EVENT,
    StateTransition,
    serialize,
)
from hapticore.tasks.base import BaseTask
from hapticore.tasks.timer import TimerManager
from hapticore.tasks.trial_manager import TrialManager

logger = logging.getLogger(__name__)


class TaskController:
    """Orchestrates task execution: state machine, trial management, and main loop.

    Lifecycle::

        controller = TaskController(config, hardware)
        controller.setup()        # creates Machine, wires task
        controller.run()          # blocks in main loop until session complete or stopped
        controller.teardown()     # cleanup
    """

    def __init__(
        self,
        task: BaseTask,
        haptic: Any,
        display: Any,
        sync: Any,
        event_publisher: Any,
        trial_manager: TrialManager,
        poll_rate_hz: float = 100.0,
    ) -> None:
        self.task = task
        self.haptic = haptic
        self.display = display
        self.sync = sync
        self.event_publisher = event_publisher
        self.trial_manager = trial_manager
        self.timer = TimerManager()
        self.poll_rate_hz = poll_rate_hz
        self._running = False
        self._stop_requested = False
        self._machine: Machine | None = None

    def setup(self) -> None:
        """Wire the task into the runtime.

        1. Validate task params against ParamSpec definitions.
        2. Create a transitions.Machine with the task's STATES and TRANSITIONS.
        3. Call task.setup() with hardware references, timer, trial_manager.
        4. Generate the trial sequence (already done in TrialManager.__init__).
        """
        # Build validated params: merge defaults with any overrides
        validated_params = self._validate_params()

        # Create the transitions Machine on the task instance
        self._machine = Machine(
            model=self.task,
            states=self.task.STATES,
            transitions=self.task.TRANSITIONS,
            initial=self.task.INITIAL_STATE,
            after_state_change=self._on_state_change,
            send_event=True,
        )

        # Wire the task into the runtime
        hardware = {
            "haptic": self.haptic,
            "display": self.display,
            "sync": self.sync,
        }
        self.task.setup(
            hardware=hardware,
            params=validated_params,
            event_bus=self.event_publisher,
            trial_manager=self.trial_manager,
            timer_manager=self.timer,
        )

    def _validate_params(self) -> dict[str, Any]:
        """Validate and merge task parameters against ParamSpec definitions."""
        result: dict[str, Any] = {}
        for name, spec in self.task.PARAMS.items():
            value = spec.default
            result[name] = value
            # Type check
            if not isinstance(value, spec.type):
                raise TypeError(
                    f"Parameter '{name}' must be {spec.type.__name__}, "
                    f"got {type(value).__name__}"
                )
            # Bounds check for numeric types
            if spec.min is not None and isinstance(value, (int, float)) and value < spec.min:
                raise ValueError(
                    f"Parameter '{name}' = {value} is below minimum {spec.min}"
                )
            if spec.max is not None and isinstance(value, (int, float)) and value > spec.max:
                raise ValueError(
                    f"Parameter '{name}' = {value} is above maximum {spec.max}"
                )
        return result

    def run(self) -> None:
        """Main loop. Blocks until the session is complete or stop() is called.

        Each iteration:
        1. Read the latest haptic state.
        2. Check timers and fire expired triggers.
        3. Call task.check_triggers().
        4. Sleep to maintain poll_rate_hz.
        """
        if self._stop_requested:
            return
        self._running = True
        self._stop_requested = False
        tick_duration = 1.0 / self.poll_rate_hz

        # Start the first trial
        if not self._start_next_trial():
            logger.warning("No trials to run")
            self._running = False
            return

        while self._running:
            next_tick = time.monotonic() + tick_duration

            # 1. Read haptic state
            haptic_state = self.haptic.get_latest_state()

            # 2. Check timers — fire expired triggers
            expired = self.timer.check()
            for trigger_name in expired:
                self.task.trigger(trigger_name)

            # 3. Let the task check triggers based on haptic state
            if haptic_state is not None:
                self.task.check_triggers(haptic_state)

            # 4. Check if session is complete
            if self.trial_manager.is_complete and self.task.state == self.task.INITIAL_STATE:
                self._running = False
                break

            # 5. Sleep to maintain poll rate
            remaining = next_tick - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)

    def stop(self) -> None:
        """Signal the main loop to stop after the current iteration."""
        self._running = False
        self._stop_requested = True

    def teardown(self) -> None:
        """Clean up resources."""
        self.task.cleanup()
        self.timer.cancel_all()

    def _on_state_change(self, event: Any) -> None:
        """Callback invoked by the transitions library after every state change.

        Creates and publishes a StateTransition event.
        """
        transition = StateTransition(
            timestamp=time.monotonic(),
            previous_state=event.transition.source,
            new_state=event.transition.dest,
            trigger=event.event.name,
            trial_number=self.task.trial_number,
            event_code=0,
        )
        self.event_publisher.publish(TOPIC_EVENT, serialize(transition))
        logger.info(
            "State: %s -> %s (trigger=%s, trial=%d)",
            transition.previous_state,
            transition.new_state,
            transition.trigger,
            transition.trial_number,
        )

        # If we just transitioned to the initial state and the last trigger was
        # "trial_end", we need to start the next trial or end the session.
        if (
            event.transition.dest == self.task.INITIAL_STATE
            and event.event.name == "trial_end"
        ):
            trial_log = self.trial_manager.get_trial_log()
            outcome = trial_log[-1]["outcome"] if trial_log else ""
            self.task.on_trial_end(outcome)
            if not self.trial_manager.is_complete:
                self._start_next_trial()

    def _start_next_trial(self) -> bool:
        """Advance the trial manager and start the next trial.

        Returns True if a new trial was started, False if the session is complete.
        """
        condition = self.trial_manager.advance()
        if condition is None:
            return False
        self.task.trial_number = self.trial_manager.current_trial
        self.task.on_trial_start(condition)
        self.task.trigger("trial_begin")
        return True
