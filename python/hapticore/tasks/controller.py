"""Task controller: orchestrates task execution with state machine and main loop.

The TaskController wires together the task, transitions state machine, timer,
trial manager, haptic interface, and event bus. It runs the main loop that
polls haptic state, checks timers, and dispatches triggers.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from typing import Any

from transitions import Machine

from hapticore.core.interfaces import DisplayInterface, HapticInterface, SyncInterface
from hapticore.core.messages import (
    TOPIC_EVENT,
    StateTransition,
    serialize,
)
from hapticore.core.messaging import EventPublisher
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
        haptic: HapticInterface,
        display: DisplayInterface,
        sync: SyncInterface,
        event_publisher: EventPublisher,
        trial_manager: TrialManager,
        params: dict[str, Any] | None = None,
        poll_rate_hz: float = 100.0,
    ) -> None:
        if poll_rate_hz <= 0:
            raise ValueError(f"poll_rate_hz must be positive, got {poll_rate_hz}")
        self.task = task
        self.haptic = haptic
        self.display = display
        self.sync = sync
        self.event_publisher = event_publisher
        self.trial_manager = trial_manager
        self._param_overrides = params or {}
        self.timer = TimerManager()
        self.poll_rate_hz = poll_rate_hz
        self._running = False
        self._stop_requested = False
        self._trial_ended = False
        self._machine: Machine | None = None
        self._sigint_handler_ready = threading.Event()

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
        """Validate and merge task parameters against ParamSpec definitions.

        Merges config overrides (from ``self._param_overrides``) with defaults
        from the task's ``PARAMS`` definitions. Config values take precedence
        over defaults.
        """
        # Check for unknown parameter names (catches config typos)
        unknown = set(self._param_overrides) - set(self.task.PARAMS)
        if unknown:
            raise ValueError(
                f"Unknown parameter(s): {', '.join(sorted(unknown))}. "
                f"Valid parameters: {', '.join(sorted(self.task.PARAMS))}"
            )
        result: dict[str, Any] = {}
        for name, spec in self.task.PARAMS.items():
            value = self._param_overrides.get(name, spec.default)
            # Type check with numeric special-casing:
            # - allow ints for float params (but not bool) and coerce to float
            # - reject bool for int params
            expected_type = spec.type
            if expected_type is float:
                # Accept ints and floats, but not bools (bool is a subclass of int)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise TypeError(
                        f"Parameter '{name}' must be {expected_type.__name__}, "
                        f"got {type(value).__name__}"
                    )
                value = float(value)
            elif expected_type is int:
                # Require real ints, excluding bool
                if not isinstance(value, int) or isinstance(value, bool):
                    raise TypeError(
                        f"Parameter '{name}' must be {expected_type.__name__}, "
                        f"got {type(value).__name__}"
                    )
            else:
                if not isinstance(value, expected_type):
                    raise TypeError(
                        f"Parameter '{name}' must be {expected_type.__name__}, "
                        f"got {type(value).__name__}"
                    )
            # Bounds check for numeric types (exclude bool, which is a subclass of int)
            is_numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
            if spec.min is not None and is_numeric and value < spec.min:
                raise ValueError(
                    f"Parameter '{name}' = {value} is below minimum {spec.min}"
                )
            if spec.max is not None and is_numeric and value > spec.max:
                raise ValueError(
                    f"Parameter '{name}' = {value} is above maximum {spec.max}"
                )
            result[name] = value
        return result

    def run(self) -> None:
        """Main loop. Blocks until the session is complete or stop() is called.

        Each iteration:
        1. Handle any pending SIGINT escalation (Ctrl+C).
        2. Read the latest haptic state.
        3. Check timers and fire expired triggers.
        4. Call task.check_triggers().
        5. Handle deferred trial advancement (from _on_state_change).
        6. Sleep to maintain poll_rate_hz.

        Ctrl+C escalation:

        * 1st Ctrl+C — finish current block (calls ``request_stop(after="block")``).
        * 2nd Ctrl+C — finish current trial (calls ``request_stop(after="trial")``).
        * 3rd Ctrl+C — hard kill (re-raises ``KeyboardInterrupt``).
        """
        if self._stop_requested:
            return
        self._running = True
        self._stop_requested = False
        tick_duration = 1.0 / self.poll_rate_hz

        # --- SIGINT handler --------------------------------------------------
        _sigint_count = 0

        def _handle_sigint(signum: int, frame: Any) -> None:
            nonlocal _sigint_count
            _sigint_count += 1

        _prev_sigint_handler = signal.signal(signal.SIGINT, _handle_sigint)
        self._sigint_handler_ready.set()
        _last_handled_sigint = 0
        # ---------------------------------------------------------------------

        # Start the first trial
        if not self._start_next_trial():
            logger.warning("No trials to run")
            self._running = False
            signal.signal(signal.SIGINT, _prev_sigint_handler)
            self._sigint_handler_ready.clear()
            return

        try:
            while self._running:
                next_tick = time.monotonic() + tick_duration

                # 1. Handle pending Ctrl+C signals (escalating)
                if _sigint_count > _last_handled_sigint:
                    _last_handled_sigint = _sigint_count
                    if _sigint_count == 1:
                        logger.info(
                            "Ctrl+C received: finishing current block "
                            "(press again to finish trial, 3rd time to force quit)"
                        )
                        self.trial_manager.request_stop(after="block")
                    elif _sigint_count == 2:
                        logger.info(
                            "Ctrl+C received again: finishing current trial "
                            "(press again to force quit)"
                        )
                        self.trial_manager.request_stop(after="trial")
                    else:
                        raise KeyboardInterrupt

                # 2. Read haptic state
                haptic_state = self.haptic.get_latest_state()

                # 3. Check timers — fire expired triggers
                expired = self.timer.check()
                for trigger_name in expired:
                    self.task.trigger(trigger_name)

                # 4. Let the task check triggers based on haptic state
                if haptic_state is not None:
                    self.task.check_triggers(haptic_state)

                # 5. Handle deferred trial advancement
                if self._trial_ended:
                    self._trial_ended = False
                    trial_log = self.trial_manager.get_trial_log()
                    outcome = trial_log[-1]["outcome"] if trial_log else ""
                    self.task.on_trial_end(outcome)
                    if not self.trial_manager.is_complete:
                        self._start_next_trial()

                # 6. Check if session is complete
                if self.trial_manager.is_complete and self.task.state == self.task.INITIAL_STATE:
                    self._running = False
                    break

                # 7. Sleep to maintain poll rate
                remaining = next_tick - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)

        finally:
            signal.signal(signal.SIGINT, _prev_sigint_handler)
            self._sigint_handler_ready.clear()

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

        Creates and publishes a StateTransition event. Trial advancement is
        deferred to the main loop via the ``_trial_ended`` flag to avoid
        reentrant callbacks.
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

        # Defer trial advancement to the main loop to avoid reentrant callbacks.
        if (
            event.transition.dest == self.task.INITIAL_STATE
            and event.event.name == "trial_end"
        ):
            self._trial_ended = True

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
